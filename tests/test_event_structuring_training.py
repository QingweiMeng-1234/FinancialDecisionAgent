from types import SimpleNamespace
import os

import pytest

from event_collector.event_structuring_training import (
    TrainingConfig, encode_example, prepare_training, AssistantOnlyCollator,
    require_training_ready, run_training,
)


class Tokenizer:
    pad_token_id = 0
    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt, return_dict=False):
        assert tokenize
        assert not return_dict
        return [10, 11, 12] if add_generation_prompt else [10, 11, 12, 21, 22, 99]


def case(split="train", reviewed=True):
    return dict(case_id=f"sample-{split}", group_id=f"story-{split}", split=split, reviewed=reviewed,
        article=dict(article_id=1, title="Closure", description="", content="Acme closed a factory.", url=""),
        expected_events=[dict(event_type="Company", direction="Negative", importance="Medium",
            time_horizon="Long-term", affected_asset="Acme", reasoning="Acme closed a factory.",
            evidence_excerpt="Acme closed a factory.")])


def config(**changes):
    return TrainingConfig(min_train_examples=1, min_validation_examples=1, **changes)


def test_only_assistant_tokens_contribute_to_loss_including_end_token():
    encoded = encode_example(case(), Tokenizer(), config())
    assert encoded["input_ids"] == [10, 11, 12, 21, 22, 99]
    assert encoded["labels"] == [-100, -100, -100, 21, 22, 99]
    assert encoded["attention_mask"] == [1] * 6


def test_context_overflow_is_excluded_without_truncating_target():
    bundle = prepare_training([case(), case("validation")], Tokenizer(), config(max_length=5))
    assert bundle["train"] == []
    assert not bundle["report"]["ready"]
    assert all(r["reason"] == "token_budget_exceeded" for r in bundle["report"]["excluded"])


def test_prompt_template_mismatch_cannot_train_on_wrong_tokens():
    class BadTokenizer(Tokenizer):
        def apply_chat_template(self, messages, **kwargs):
            return [1, 2] if kwargs["add_generation_prompt"] else [4, 5, 6]
    with pytest.raises(ValueError, match="prefix"):
        encode_example(case(), BadTokenizer(), config())


def test_test_and_unreviewed_drafts_never_reach_tokenizer():
    class CountingTokenizer(Tokenizer):
        calls = 0
        def apply_chat_template(self, messages, **kwargs):
            self.calls += 1
            return super().apply_chat_template(messages, **kwargs)
    tokenizer = CountingTokenizer()
    bundle = prepare_training([case(), case("validation"),case("test"),
                               {**case(reviewed=False), "case_id": "draft"}], tokenizer, config())
    assert len(bundle["train"]) == len(bundle["validation"]) == 1
    assert tokenizer.calls == 4
    assert bundle["report"]["heldout_test_cases"] == 1
    assert bundle["report"]["unreviewed_cases"] == 1
    assert bundle["report"]["ready"]


def test_bad_quote_and_missing_output_fields_block_training():
    invalid = case()
    invalid["expected_events"][0]["evidence_excerpt"] = "invented"
    with pytest.raises(ValueError, match="evidence"):
        encode_example(invalid, Tokenizer(), config())
    invalid = case()
    del invalid["expected_events"][0]["affected_asset"]
    with pytest.raises(ValueError):
        encode_example(invalid, Tokenizer(), config())


def test_padding_is_never_supervised():
    batch = AssistantOnlyCollator(0)([
        dict(input_ids=[1,2,3], attention_mask=[1,1,1], labels=[-100,2,3]),
        dict(input_ids=[1,4], attention_mask=[1,1], labels=[-100,4]),
    ])
    assert batch["input_ids"].tolist() == [[1,2,3],[1,4,0]]
    assert batch["labels"].tolist() == [[-100,2,3],[-100,4,-100]]
    assert batch["attention_mask"].tolist() == [[1,1,1],[1,1,0]]


def test_insufficient_reviewed_data_blocks_training():
    bundle = prepare_training([case(reviewed=False)], Tokenizer(), config())
    with pytest.raises(ValueError, match="reviewed"):
        require_training_ready(bundle)


def test_cpu_guard_prevents_weight_loading(tmp_path, monkeypatch):
    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    bundle = prepare_training([case(),case("validation")], Tokenizer(), config())
    with pytest.raises(RuntimeError, match="CUDA"):
        run_training(bundle, Tokenizer(), config(), tmp_path / "adapter")
    assert not (tmp_path / "adapter").exists()


def test_config_rejects_invalid_training_parameters():
    for kwargs in [dict(max_length=0),dict(epochs=0),dict(lora_r=0),dict(learning_rate=float('nan'))]:
        with pytest.raises(ValueError):
            config(**kwargs)


@pytest.mark.skipif(not os.getenv("EVENT_TOKENIZER_PATH"), reason="optional real offline tokenizer")
def test_real_tokenizer_masks_prompt_and_preserves_complete_answer():
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(os.environ["EVENT_TOKENIZER_PATH"], local_files_only=True)
    row = encode_example(case(), tokenizer, config())
    target = tokenizer.decode([token for token in row["labels"] if token != -100])
    assert '"affected_asset": "Acme"' in target
    assert 'Northstar' not in target
    assert tokenizer.eos_token_id == row["labels"][-2] or tokenizer.eos_token_id == row["labels"][-1]


def test_gpu_orchestration_handles_trainer_created_directory_and_saves_adapter(tmp_path, monkeypatch):
    import torch
    import event_collector.event_structuring_training as training
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "is_bf16_supported", lambda: True)
    seen = {}
    def model_loader(*args, **kwargs):
        assert seen.get("seed") == 42, "seed must be set before model/LoRA initialization"
        seen["model_load"] = kwargs
        return SimpleNamespace(config=SimpleNamespace(use_cache=True))
    def training_args(**kwargs):
        seen["args"] = kwargs
        return SimpleNamespace(**kwargs)
    class Trainer:
        def __init__(self, **kwargs):
            seen["trainer"] = kwargs
            # Real Trainer may create output_dir during construction.
            from pathlib import Path
            Path(kwargs["args"].output_dir).mkdir(parents=True, exist_ok=True)
        def train(self):
            seen["trained"] = True
        def save_model(self, path):
            seen["saved"] = path
        def evaluate(self):
            return {"eval_loss": 1.2}
    monkeypatch.setattr(training, "_training_stack", lambda: SimpleNamespace(
        set_seed=lambda seed: seen.update(seed=seed),
        AutoModelForCausalLM=SimpleNamespace(from_pretrained=model_loader),
        BitsAndBytesConfig=lambda **kw: kw, LoraConfig=lambda **kw: kw,
        prepare_model_for_kbit_training=lambda model, **kw: model,
        get_peft_model=lambda model, config: model, TrainingArguments=training_args, Trainer=Trainer))
    tokenizer = Tokenizer()
    tokenizer.save_pretrained = lambda path: None
    bundle = prepare_training([case(),case("validation")], tokenizer, config())
    result = run_training(bundle, tokenizer, config(), tmp_path / "run")
    assert seen["trained"]
    assert seen["model_load"]["quantization_config"]["load_in_4bit"]
    assert seen["args"]["report_to"] == []
    assert seen["trainer"]["eval_dataset"] == bundle["validation"]
    assert seen["saved"].endswith("adapter")
    assert result["eval_loss"] == 1.2
