# 新闻事件标注数据（2026-09-08）

这份数据随源码一起管理，阅读和标注不依赖本地新闻数据库、模型服务或 GPU。

## 从这里开始

打开 [首轮 12 篇全文与标注草稿](./annotation-round1/review-pack.md)，逐篇对照新闻正文检查建议标签和复核提示。此文件是首轮草稿的阅读快照；后续修改 JSONL 不会自动更新它。

| 文件 | 内容 |
| --- | --- |
| [annotation_candidates.jsonl](./annotation_candidates.jsonl) | 完整 200 篇候选，包含新闻标题、正文、来源 URL、正文 hash 和固定划分：152 train、24 validation、24 test |
| [annotation-round1/drafts.jsonl](./annotation-round1/drafts.jsonl) | 12 篇的原文与助手建议标签 `proposed_events`、复核提示；是候选集的子集，不要追加成新样本 |
| [annotation-round1/review-pack.md](./annotation-round1/review-pack.md) | 同一批 12 篇的全文与标签，方便直接阅读 |
| [manifest.json](./manifest.json) | 本次数据快照的数量、状态和文件 SHA-256 |
| [.gitattributes](./.gitattributes) | 固定 UTF-8 文本的 LF 换行，保持 Windows / Linux 克隆后的数据 hash 一致 |

当前 200 篇均为 `reviewed: false`，首轮有 13 个建议事件、2 篇建议无事件。草稿没有 `expected_events`，不能直接作为人工金标准训练。

## 如何复核

1. 按 [标注规范](../../../docs/event-labeling-policy-v1.md)检查新闻与建议标签，修正遗漏、主体、方向、重要性、期限、解释和逐字引句。
2. 用 `case_id` 找到完整 `annotation_candidates.jsonl` 中对应的一行。将修正后的标签写入该行的 `expected_events`，确认整篇标注完成后才设 `reviewed: true`。确认无事件也必须显式填写 `expected_events: []`。
3. 保留原文、来源和 `case_id`。保持既有 `group_id` 和 `split`，同时人工检查同一事件的不同报道是否误分到不同集合；发现问题需明确修正分组与划分并记录变更，不能直接忽略泄漏。
4. 把修改后的完整候选集交给数据预检入口。未复核样本会被排除；test 不参与训练，validation 不导出为 SFT 训练样本。步骤见 [4070 Ti 操作说明](../../../docs/event-qlora-4070ti.md)。

首轮草稿保留为历史参考，正式复核结果以完整候选集中的 `expected_events` / `reviewed` 为准。不要只修改 Markdown，也不要将原始 `proposed_events` 未经检查直接批准。

人工复核后需要同步更新 manifest 中的文件 hash、复核数量和状态，保留原始快照日期并增加修订说明。该文件记录数据版本，不证明模型质量；故事分组仍待人工检查。

本目录保留的是已抓取正文，不保证覆盖来源网页的全部内容。新闻归各来源所有；每条 JSONL 保留原始 URL，标注仅判断给定文本所述事件。运行报告、数据库和模型权重不属于本目录。
