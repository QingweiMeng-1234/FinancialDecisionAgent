# Retrieval 召回优化 Roadmap

## 文档元信息

- 生成时间：`2026-05-24 23:04:00 +08:00`
- 适用项目状态：
  - 已完成 `ticker_only` vs `entity_kb_enhanced` A/B 评估
  - 已有 company entity KB、seed 文件、SQLite entity store
  - 当前 `entity_kb_enhanced` 采用“KB 扩 query + 强归因过滤”的偏保守策略
- 关联文档：
  - [retrieval_eval_问题诊断与改进方案.md](/c:/Users/weber/Documents/GitHub/Financial%20Agent/retrieval_eval_问题诊断与改进方案.md)
  - [reports/retrieval_eval/retrieval_eval_report.md](/c:/Users/weber/Documents/GitHub/Financial%20Agent/reports/retrieval_eval/retrieval_eval_report.md)

## 目标

下一阶段的核心目标不是继续压误召回，而是：

- 在尽量保留当前 precision 优势的前提下，把 recall 从当前 KB 版本明显拉回去
- 让知识库从“硬过滤器”演进为“召回增强和排序增强器”
- 建立一条适合面试讲述、也适合工程迭代的清晰演进路线

## 当前问题复盘

我们已经确认了两个事实：

1. `ticker_only` 很脏
   - Aggregate `False Positive Rate = 0.883`
   - 说明 baseline 会召回大量不相关文章

2. `entity_kb_enhanced` 太保守
   - Aggregate `Recall@K = 0.082`
   - 38 个 ticker 里有 27 个 `returned_count = 0`
   - 说明当前知识库介入方式牺牲了太多召回

因此下一步不能继续沿着“更强过滤”走，而要改成“保留召回 + 知识库加权 + 多路召回”。

## 设计原则

### 原则 1：先保证候选集不空，再谈排序

Rerank 解决的是“候选集里谁排前面”，不是“候选集里根本没有对文”的问题。

### 原则 2：知识库应该参与排序，不应该一开始就决定生死

当前知识库最适合做：

- query expansion
- feature enrichment
- attribution scoring
- rerank boosting

而不适合继续做：

- strict gate
- unmatched 就直接丢弃

### 原则 3：多路召回比单路 expanded query 更稳

当前是一条 expanded query 直接搜到底。下一阶段更稳的做法是：

- 原始 ticker 搜一次
- company canonical name 搜一次
- product 搜一次
- CEO 搜一次
- 结果融合去重后再排序

### 原则 4：先解决结构性问题，再上复杂 trick

在当前阶段，不建议优先做：

- HyDE
- embedding 微调
- 复杂 intent router
- 大规模 prompt query rewrite

先把最核心的召回链路改对，收益通常最大。

## Roadmap

## 阶段 1：把 KB 从硬过滤改成软加权

### 目标

恢复召回，同时保留 KB 对 precision 的帮助。

### 当前问题

当前 `retrieval_eval` 里使用的是显式硬过滤：

- 文章不能被归因为公司
- 直接剔除
- 不进入后续 rerank

这会直接压缩候选集。

### 改造方向

把当前逻辑改成：

1. 保留原始向量召回结果
2. 对每条结果计算 KB attribution 特征
3. 不匹配也先保留
4. 在排序阶段对匹配结果加分

### 建议实现

为每条候选结果计算这些 feature：

- `matches_ticker`
- `matches_company`
- `matches_product`
- `matches_ceo`
- `matched_alias_count`

再基于 feature 做一个简单的加权分数，例如：

- `ticker/company` 命中：高权重
- `product` 命中：中高权重
- `CEO` 命中：中权重

最终排序分数可以先从简单版本开始：

- `final_score = vector_score + kb_boost`

如果已有 reranker，则先保留粗排，再把 KB feature 作为 rerank 前的 boost 或 rerank prompt 输入。

### 预期收益

- 大幅缓解 `returned_count = 0`
- Recall 明显高于当前 strict KB 版本
- Precision 可能比当前 strict 版本略降，但应优于纯 `ticker_only`

## 阶段 2：从单 query 扩展到多 query 召回

### 目标

提升覆盖率，减少因为单一表达方式导致的漏召回。

### 当前问题

当前 `entity_kb_enhanced` 本质上是：

- 构造一个 expanded query
- 只搜一次

这种方式有两个问题：

- 所有 alias 被压进一个 query，语义空间容易混
- 某些专属实体词可能被其他词稀释

### 改造方向

把单 query 改成多 query：

1. `ticker query`
2. `company name query`
3. `product queries`
4. `CEO query`

分别检索，再融合。

### 建议实现

每种 query 单独取小规模 top-k，例如：

- ticker：top 15
- company：top 15
- top products：每个 top 8
- CEO：top 8

然后：

- 合并结果
- 按 article_id 去重
- 保留每条结果来自哪些 query 的命中来源
- 再统一做排序

### 预期收益

- 召回更稳定
- 对产品名、品牌名、公司名写法差异更鲁棒
- 更容易定位“到底是哪路召回带来了收益”

## 阶段 3：引入 lexical/BM25 通道，做 hybrid retrieval

### 目标

弥补纯向量检索对专有名词、简称、产品名、精确字面匹配场景的不足。

### 为什么这一步很重要

你的项目里有很多典型的“字面匹配很重要”的查询对象：

- ticker
- 公司名
- CEO 全名
- 产品名
- 品牌名
- 缩写

这些词对 lexical retrieval 非常友好。

### 建议实现

至少增加一条简单的 keyword / BM25 路：

1. 用 `title + summary + content` 建倒排索引
2. 对 ticker/company/product/CEO query 做 BM25 检索
3. 与向量召回结果融合

融合方式可以先简单做：

- weighted sum
- reciprocal rank fusion
- 或先拼接再统一 rerank

### 预期收益

- 提升 exact alias 命中
- 改善产品名、简称类召回
- 减少“向量语义接近但字面不对”的噪声

## 阶段 4：检查离线内容质量

### 目标

排除“召回差其实是因为索引内容就不好”的可能性。

### 为什么必须做

虽然当前系统抓的是网页正文，但仍可能存在：

- 正文抽取失败
- 动态页只抓到壳
- 抓到的是截断 preview
- chunk 切分后关键实体被切散
- 文章正文完整，但向量索引用到的 top chunks 不够好

### 建议检查方式

对低召回 ticker 做抽样排查：

- `AAPL`
- `AMZN`
- `AMD`
- `AVGO`
- `ORCL`

每个 ticker 随机抽几篇：

1. 黄金集 relevant 但没召回的文章
2. 实际已抓取的对应 article 内容
3. 看正文是否完整
4. 看 chunk 是否包含核心实体词
5. 看搜索结果里返回的是哪个 chunk

### 预期收益

- 明确当前瓶颈是不是还包含 ingestion/indexing 质量问题
- 防止在线检索优化做了很多，但其实问题在离线层

## 阶段 5：增强 alias，但只补高精度别名

### 目标

提高知识库覆盖率，而不重新引入泛词噪声。

### 建议补充范围

- 新闻常见产品简称
- 品牌名
- 平台名
- 常见缩写
- 行业内默认叫法

### 不建议补充

- 泛业务词
- 行业词
- 容易误匹配的宽泛概念词

例如：

- 不要把 `computer hardware` 重新放回 Apple products
- 不要把 `platform`、`software`、`electricity` 当成强 identity alias

### 预期收益

- 提高 KB 命中率
- 降低 “文章相关，但 KB 认不出来” 的情况

## 阶段 6：升级评估体系

### 目标

让我们能更准确看出每一步优化到底有没有帮助。

### 建议新增的评估维度

1. 分组评估
   - 公司 ticker
   - ETF/index
   - `relevant_count = 1`
   - `relevant_count >= 3`

2. 召回来源分析
   - 哪些文章来自 ticker route
   - 哪些来自 product route
   - 哪些来自 CEO route
   - 哪些来自 lexical route

3. 诊断指标
   - 候选集大小
   - KB 命中率
   - rerank 前后的 relevant 位次变化
   - 被 KB boost 的 relevant 数量

### 建议保留的 A/B 结构

- `baseline`: ticker-only
- `v2`: multi-route + soft KB boost

后续再继续演进：

- `v3`: hybrid retrieval
- `v4`: alias enhanced

## 推荐执行顺序

建议严格按这个顺序做：

1. `soft attribution / soft boost`
2. `multi-query retrieval`
3. `hybrid retrieval`
4. `offline quality audit`
5. `alias refinement`
6. `eval/report upgrade`

原因很简单：

- 阶段 1 和阶段 2 最可能最快带来 recall 改善
- 阶段 3 提供结构性增益
- 阶段 4 帮你排除地基问题
- 阶段 5 和阶段 6 让系统更稳、更可解释

## 面试叙事模板

这条 roadmap 很适合整理成一个完整的项目演进故事：

1. 初始系统采用 ticker-only retrieval，发现误召回过高
2. 为了提升 precision，引入 company entity KB，并使用强归因过滤
3. A/B 结果显示 false positives 大幅下降，但 recall 明显下降
4. 由此判断知识库接入方式过于保守，不应作为 hard gate
5. 下一阶段将知识库改造成 soft boost + multi-route retrieval
6. 再进一步演进到 hybrid retrieval 和更细粒度评估

这个叙事既能体现：

- 你有问题定位能力
- 你会做指标驱动分析
- 你理解 retrieval 和 rerank 的边界
- 你知道如何把 KB 融入 RAG，而不是机械堆功能

## 一句话结论

下一阶段的方向不是“让 KB 更会砍”，而是：

- 让召回链路更宽
- 让知识库更会加分
- 让排序更会利用实体信号

也就是说，要从：

- `strict filter retrieval`

演进到：

- `recall-preserving, multi-route, KB-aware retrieval`
