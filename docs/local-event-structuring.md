# 本地金融新闻事件抽取

本功能单独替换 `EventStructuringAgent` 的模型客户端。默认仍为原来的 DeepSeek；摘要、重排序、问答和研究决策不读取本地事件模型配置。

## 当前交付范围

- loopback OpenAI-compatible 本地推理适配器；固定 JSON Schema、枚举和逐字原文引句校验。
- `local` 与 `local_with_fallback` 两种显式启用模式；远端客户端只在需要回退时创建。
- 独立 JSONL 评测，不打开业务数据库，不读写事件缓存。
- 仅导出已复核 `train` 数据的 SFT messages；不会把模型预测自动升级为训练真值。
- 已下载的 Qwen2.5-1.5B-Instruct Q4_K_M 是未微调基线，不代表已达到生产质量。实测记录见 `reports/local_event_structuring/2026-09-08/`。

## 启动和试跑（PowerShell，在仓库根目录）

当前机器的独立运行时在 `$env:USERPROFILE/Tools/financial-agent-local`，约 1.1 GB 模型文件不放入 Git。使用系统 Python；原仓库 `.venv` 指向另一台机器的 Python，不能在当前电脑运行。

```powershell
./scripts/start-local-event-model.ps1
Invoke-RestMethod http://127.0.0.1:18081/health

$env:PYTHONPATH = 'src'
$env:EVENT_STRUCTURING_PROVIDER = 'local'
$env:LOCAL_EVENT_BASE_URL = 'http://127.0.0.1:18081/v1'
$env:LOCAL_EVENT_MODEL = 'financial-event-qwen-1.5b-q4'
python -m event_collector.event_structuring_eval `
  datasets/event_structuring/2026-09-08/annotation-round1/drafts.jsonl `
  --output reports/local_event_structuring/my-local-smoke.json
```

上例使用随源码提供的 12 篇未复核草稿进行接口试跑；不会以 `proposed_events` 作为金标准，质量分数为 null，不能作为独立盲测。

若端口已有监听，复用并检查 `/health`，不要重复启动。输出报告不覆盖已有文件，重跑需要新文件名。停止服务时，核实 PID 对应 `llama-server.exe` 后用 `Stop-Process -Id <PID>`。只监听 `127.0.0.1`，没有安装系统服务或开机启动。

官方资源：

- https://github.com/ggml-org/llama.cpp/releases/tag/b10809
- https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF

其他 OpenAI-compatible 服务必须支持 `response_format` 的 JSON Schema 约束，或明确返回错误。本地适配器不沿用远端 API key 或 base URL；可选认证读取 `LOCAL_EVENT_API_KEY`。完整配置示例见 `config/local_event_structuring.env.example`。

## 校验和回退的实际边界

`local` 模式遇到请求失败、超出输入预算、输出截断、缺少字段、非法枚举、空文本字段、引句不在正文中等情况直接失败。输入预算按完整格式化文章字符数计算；不会截断文章。字符预算不能代替模型 tokenizer 的上下文限制，服务还需要正确配置上下文窗口。

`local_with_fallback` 只在上述本地失败后调用现有 DeepSeek。远端仍按其原来的校验规则工作，**不能把回退结果也称为通过本地逐字引用校验**。远端失败继续向上传递，不能变成零事件成功。

`{"events": []}` 是合法的无事件结果，不能自动推断为模型失败，所以不会因此回退。模型也可能输出格式正确但主体、方向或因果关系错误的事件；JSON 与逐字引句检查不能检测所有语义问题。这是上线前必须单独测试召回率和语义正确性的原因。

`last_trace` 记录实际来源、模型、prompt 版本、本地 token 用量、延迟及回退原因，不保存原始错误中的密钥或请求正文。混合模式记录本地尝试和最终远端模型；不报告无法获取的远端 token 成本。运行记录在调用完成后读取实际模型与 prompt 版本。

原来的成功缓存（包括零事件）保持原语义。换模型不会自动清除缓存。评测入口绕过缓存；模型对照请使用它，避免 `structure_events.py --force` 重写业务结果。业务启用应在评测合格后另行进行。

## 数据标注和 SFT

后续准备已补充：[标注规范](./event-labeling-policy-v1.md)及[4070 Ti QLoRA 操作说明](./event-qlora-4070ti.md)。CPU 数据预检入口为 `python -m event_collector.event_structuring_training --help`。

本次只读导出的 [200 篇候选](../datasets/event_structuring/2026-09-08/annotation_candidates.jsonl)包含原文并随源码一起管理，全部 `reviewed: false`。[首轮全文与标注草稿](../datasets/event_structuring/2026-09-08/annotation-round1/review-pack.md)可直接阅读，合并复核结果的方法见 [数据说明](../datasets/event_structuring/2026-09-08/README.md)。候选按正文 hash/已有 story group 做初步隔离，跨报道的同一事件仍需人工复核分组。已预留 test/validation；选择较短文章只用于第一轮，不能据此推断长文章表现。

每行格式：

```json
{"case_id":"news-1","group_id":"story-1","split":"train","reviewed":false,"article":{"article_id":1,"title":"标题","description":"","content":"原文正文","url":"来源"}}
```

复核后添加 `expected_events`，再将 `reviewed` 设为 true。确定没有市场相关事件时显式填写 `expected_events: []`，缺少标签不等于无事件。

标注时逐事件确认：主体、事件类型、方向、重要性、期限、解释、原文引句。不要把公司名称自动猜成 ticker；方向是文中事件对该对象的影响，不是对股价的预测。重要性和期限需要统一的标注标准，争议样本先放入待裁决集合，不要直接训练。

```powershell
python -m event_collector.event_structuring_eval path/to/reviewed-cases.jsonl `
  --export-sft --output reports/local_event_structuring/train-messages.jsonl
```

导出只包含已复核 train 数据；test/validation 和未复核预测不会进入训练。加载完整数据集会检查同组或同正文不能跨 split。先完成现成小模型基线，随后在有合适 GPU 的训练环境用 LoRA/SFT 比较 1.5B/3B 等规模。当前电脑无 CUDA GPU，本次没有租卡、执行微调或声称产生了训练权重。DPO 等积累可靠偏好对后再评估。

## 如何解读报告

- success_rate：接口与输出契约成功率，**不是准确率**。
- event precision/recall/F1：对人工复核样本，按主体及四个分类字段的完整组合一对一匹配；重复预测会降低 precision。
- exact_article_accuracy：整篇文章事件标签多重集合完全匹配的比例；请求失败不能算正确。
- verbatim_excerpt_rate：输出引句可在正文找到的比例，不能证明引句支持解释。
- unreviewed 样本的质量分数为 null；无可定义分母的指标为 null。
- 同时报告平均/P95 延迟和回退数量。成本比较需要额外按真实输入输出 token 单价、本地耗时及回退调用计算；不能按文章数直接宣称节省比例。

比较远端、本地原始模型、本地 SFT 和量化后模型时固定数据集与 split，单独保存报告。不能用已经看过并反复调 prompt 的 smoke 数据充当最终盲测。
