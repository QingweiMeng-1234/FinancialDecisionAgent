# 在 4070 Ti 上微调事件抽取模型

当前交付：CPU 可执行的数据预检，以及 CUDA QLoRA 训练入口。尚未运行真实 GPU 训练，未测量显存峰值，未产出 LoRA 权重。现有 GGUF 文件用于推理；训练入口使用原始 Hugging Face 模型权重，不能直接训练该 GGUF。

## 本机现在可以完成的准备

完整候选：[200 篇新闻及固定划分](../datasets/event_structuring/2026-09-08/annotation_candidates.jsonl)（152 train、24 validation、24 test）。首轮 [12 篇全文与标注草稿](../datasets/event_structuring/2026-09-08/annotation-round1/review-pack.md)均未批准用于训练。这些数据随源码一起管理，复核方法见 [数据说明](../datasets/event_structuring/2026-09-08/README.md)。先按 [标注规范](./event-labeling-policy-v1.md)复核，再生成带 expected_events 的完整数据集。

```powershell
$env:PYTHONPATH = 'src'
python -m event_collector.event_structuring_training path/to/reviewed-cases.jsonl `
  --config config/event_qlora_4070ti.json --dry-run `
  --tokenizer-path "$env:USERPROFILE/Tools/financial-agent-local/qwen2.5-1.5b-tokenizer" `
  --output reports/local_event_structuring/preflight-new.json
```

本机已下载该模型的分词器。`--tokenizer-path` 只读取本地文件；省略它会从官方仓库下载分词器配置，不下载模型权重。dry-run 不执行训练或远端LLM调用。报告 ready=false 表示数据尚未满足条件，不能将其描述为可以直接训练。

检查内容：

- 全量数据的 case ID、分组及正文 hash 跨 split 冲突。
- 只使用已复核 train/validation；test 和未复核草稿不进入分词或训练。
- 输出字段、枚举、非空主体/解释、逐字引句。
- 用模型真实 chat template 构造输入，确认 prompt 是完整序列的前缀。
- system/user token 的 labels=-100；只有 assistant 答案和结束标记参与损失，padding 同样为 -100。
- 超过2048 token的完整样本明确排除并列出case ID，不截掉新闻后半段或JSON答案。

初始配置要求至少50篇 train 和10篇 validation，这是阻止误运行的最低数量条件，不是“足够学好”的保证。200篇只能作为先导实验，后续仍需补充样本、覆盖长文和困难负例。

## 显卡机器的环境

优先使用 Linux 或带 NVIDIA CUDA 支持的 WSL2，并建立独立 Python 环境。不要覆盖 Financial Agent 服务环境。先从 https://pytorch.org/get-started/locally/ 安装与驱动兼容的 CUDA 版 PyTorch，再安装：

```bash
python -m pip install -r requirements-event-training.txt
python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

依赖文件是兼容范围，不是已经在4070 Ti验收的锁文件。GPU环境验证后保存 `pip freeze` 和驱动/CUDA版本。本轮未在当前核显电脑安装这些GPU训练依赖。

## 开始训练

将仓库代码、配置和复核后的完整JSONL复制到显卡机器。训练入口保持原有划分，不重新随机切分。

```bash
export PYTHONPATH=src
python -m event_collector.event_structuring_training path/to/reviewed-cases.jsonl \
  --config config/event_qlora_4070ti.json --dry-run \
  --output reports/local_event_structuring/gpu-preflight.json

# 先检查预检报告 ready=true，再执行。output 必须是不存在的新目录。
python -m event_collector.event_structuring_training path/to/reviewed-cases.jsonl \
  --config config/event_qlora_4070ti.json \
  --output reports/local_event_structuring/qlora-run-001
```

起始方案为 Qwen2.5-1.5B-Instruct，固定模型 revision，NF4 4-bit＋double quant，batch=1、梯度累积16、LoRA rank16、上下文2048、2 epochs。启用梯度检查点；支持时用BF16，否则FP16。设计面向12GB级显卡的小规模实验，是否适配还需真实GPU预检。

需要测试3B时另建配置，修改 model 并固定其实际revision，不能沿用1.5B的commit。不要一次改模型、数据、上下文和训练轮数，否则难以解释提升来源。

训练结果保存到新目录：预检报告、训练配置、checkpoint，以及最终 `adapter/`。不会自动推送模型、调用外部实验跟踪服务、修改业务 `.env` 或替换事件缓存。选择最低验证loss的checkpoint只能说明该损失更低，不代表事件抽取最准确。

## 训练后验收

在固定的独立评测集上比较：未微调模型、LoRA模型、量化后的部署模型、原远端模型。关注事件召回、完整标签F1、主体错误、时间/计划混淆、证据支持率及延迟；将模型格式成功率与语义质量分开。

本入口产出PEFT adapter。实际加载adapter做评测、合并成完整权重及GGUF转换部署，需要在显卡机器训练完成后继续验证；本轮没有宣称这些步骤已跑通。DPO 不属于当前起始方案。
