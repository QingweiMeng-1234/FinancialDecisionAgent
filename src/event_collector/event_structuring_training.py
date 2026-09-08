"""CPU data preflight and an explicitly invoked CUDA QLoRA training entrypoint."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path

from event_collector.event_structuring import ArticleForStructuring, _format_article
from event_collector.event_structuring_eval import load_cases
from event_collector.local_event_structuring import (
    LOCAL_EVENT_PROMPT_VERSION, LOCAL_EVENT_SYSTEM_PROMPT, _LocalResponse,
)


@dataclass(frozen=True)
class TrainingConfig:
    model: str = "Qwen/Qwen2.5-1.5B-Instruct"
    revision: str = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
    max_length: int = 2048
    epochs: float = 2.0
    learning_rate: float = 0.0002
    gradient_accumulation_steps: int = 16
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    min_train_examples: int = 50
    min_validation_examples: int = 10
    seed: int = 42

    def __post_init__(self):
        for key in ("max_length", "gradient_accumulation_steps", "lora_r", "lora_alpha",
                    "min_train_examples", "min_validation_examples"):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError(f"{key} must be a positive integer")
        for key in ("epochs", "learning_rate"):
            if not math.isfinite(getattr(self, key)) or getattr(self, key) <= 0:
                raise ValueError(f"{key} must be positive and finite")
        if not 0 <= self.lora_dropout < 1:
            raise ValueError("lora_dropout must be in [0, 1)")
        if not self.model.strip() or not self.revision.strip():
            raise ValueError("model and pinned revision are required")


class TokenBudgetExceeded(ValueError):
    pass


def encode_example(case, tokenizer, config):
    """Mask the entire prompt; reject overflow rather than chopping the answer."""
    article = ArticleForStructuring(**case["article"])
    response = _LocalResponse.model_validate({"events": case["expected_events"]})
    for event in response.events:
        if event.evidence_excerpt not in article.content:
            raise ValueError(f"{case['case_id']}: unsupported evidence excerpt")
    messages = [
        {"role": "system", "content": LOCAL_EVENT_SYSTEM_PROMPT},
        {"role": "user", "content": _format_article(article)},
        {"role": "assistant", "content": json.dumps(response.model_dump(mode="json"), ensure_ascii=False)},
    ]
    prompt = tokenizer.apply_chat_template(messages[:2], tokenize=True, add_generation_prompt=True, return_dict=False)
    full = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=False, return_dict=False)
    if not prompt or full[:len(prompt)] != prompt or len(full) <= len(prompt):
        raise ValueError("chat template prompt prefix does not match the supervised sequence")
    if len(full) > config.max_length:
        raise TokenBudgetExceeded("token_budget_exceeded")
    return dict(input_ids=full, attention_mask=[1] * len(full),
                labels=[-100] * len(prompt) + full[len(prompt):])


def prepare_training(cases, tokenizer, config):
    train, validation, excluded = [], [], []
    heldout = unreviewed = 0
    for case in cases:
        if case["split"] == "test":
            heldout += 1
            continue
        if case.get("reviewed") is not True:
            unreviewed += 1
            continue
        try:
            encoded = encode_example(case, tokenizer, config)
        except TokenBudgetExceeded:
            excluded.append({"case_id": case["case_id"], "reason": "token_budget_exceeded"})
            continue
        (train if case["split"] == "train" else validation).append(encoded)
    ready = len(train) >= config.min_train_examples and len(validation) >= config.min_validation_examples
    report = dict(ready=ready, train_examples=len(train), validation_examples=len(validation),
                  heldout_test_cases=heldout, unreviewed_cases=unreviewed, excluded=excluded,
                  supervised_tokens=sum(sum(x != -100 for x in row["labels"]) for row in train),
                  max_train_tokens=max((len(row["input_ids"]) for row in train), default=0),
                  required_train_examples=config.min_train_examples,
                  required_validation_examples=config.min_validation_examples,
                  prompt_version=LOCAL_EVENT_PROMPT_VERSION,
                  limitation="Dataset size thresholds only; readiness does not prove label quality or VRAM fit.")
    return dict(train=train, validation=validation, report=report)


def require_training_ready(bundle):
    if not bundle["report"]["ready"]:
        raise ValueError("insufficient reviewed train/validation examples after token-budget checks")


class AssistantOnlyCollator:
    def __init__(self, pad_token_id):
        self.pad_token_id = pad_token_id

    def __call__(self, features):
        import torch

        width = max(len(row["input_ids"]) for row in features)
        pads = dict(input_ids=self.pad_token_id, attention_mask=0, labels=-100)
        return {key: torch.tensor([row[key] + [pad] * (width - len(row[key])) for row in features],
                                  dtype=torch.long) for key, pad in pads.items()}


def _training_stack():
    from types import SimpleNamespace
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig, Trainer, TrainingArguments, set_seed

    return SimpleNamespace(**locals())


def run_training(bundle, tokenizer, config, output_dir):
    require_training_ready(bundle)
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU required; use --dry-run on the current CPU machine")
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise ValueError("training output directory already exists; use a fresh run directory")
    stack = _training_stack()
    stack.set_seed(config.seed)
    bf16 = torch.cuda.is_bf16_supported()
    dtype = torch.bfloat16 if bf16 else torch.float16
    quantization = stack.BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype)
    model = stack.AutoModelForCausalLM.from_pretrained(config.model, revision=config.revision,
        quantization_config=quantization, device_map={"": 0}, torch_dtype=dtype, trust_remote_code=False)
    model.config.use_cache = False
    model = stack.prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model = stack.get_peft_model(model, stack.LoraConfig(r=config.lora_r, lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout, bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "preflight.json").write_text(json.dumps(bundle["report"], indent=2), encoding="utf-8")
    (output_dir / "training-config.json").write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
    args = stack.TrainingArguments(output_dir=str(output_dir), num_train_epochs=config.epochs,
        per_device_train_batch_size=1, per_device_eval_batch_size=1,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        learning_rate=config.learning_rate, warmup_ratio=0.05, lr_scheduler_type="cosine",
        optim="paged_adamw_8bit", bf16=bf16, fp16=not bf16,
        gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False},
        eval_strategy="epoch", save_strategy="epoch", save_total_limit=2,
        load_best_model_at_end=True, metric_for_best_model="eval_loss", greater_is_better=False,
        logging_steps=10, report_to=[], seed=config.seed, data_seed=config.seed,
        remove_unused_columns=False)
    trainer = stack.Trainer(model=model, args=args, train_dataset=bundle["train"],
        eval_dataset=bundle["validation"], data_collator=AssistantOnlyCollator(tokenizer.pad_token_id),
        processing_class=tokenizer)
    trainer.train()
    trainer.save_model(str(output_dir / "adapter"))
    tokenizer.save_pretrained(output_dir / "adapter")
    return trainer.evaluate()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="No model weights, GPU training or remote LLM calls")
    parser.add_argument("--tokenizer-path", help="Optional local tokenizer directory for offline checks")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output exists; choose a new path")
    config = TrainingConfig(**json.loads(args.config.read_text(encoding="utf-8-sig")))
    cases = load_cases(args.dataset)  # Check all split boundaries before filtering.
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer_path or config.model,
        **({"local_files_only": True} if args.tokenizer_path else {"revision": config.revision}),
        trust_remote_code=False)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    bundle = prepare_training(cases, tokenizer, config)
    bundle["report"]["dataset_sha256"] = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    bundle["report"]["config"] = asdict(config)
    if args.dry_run:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(bundle["report"], stream, ensure_ascii=False, indent=2)
        print(json.dumps(bundle["report"], ensure_ascii=False, indent=2))
    else:
        run_training(bundle, tokenizer, config, args.output)


if __name__ == "__main__":
    main()
