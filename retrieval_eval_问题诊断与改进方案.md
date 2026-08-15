# Retrieval Eval 问题诊断与改进方案

## 文档元信息

- 生成时间：`2026-05-24 22:47:17 +08:00`
- 适用评估报告：[reports/retrieval_eval/retrieval_eval_report.md](/c:/Users/weber/Documents/GitHub/Financial%20Agent/reports/retrieval_eval/retrieval_eval_report.md)
- 对应结果明细：[reports/retrieval_eval/retrieval_eval_results.json](/c:/Users/weber/Documents/GitHub/Financial%20Agent/reports/retrieval_eval/retrieval_eval_results.json)
- 对应系统状态：
  - 已完成 company entity KB 建模与 SQLite 落库
  - 已接入 `ticker_only` vs `entity_kb_enhanced` 的 retrieval A/B 评估
  - 当前 `entity_kb_enhanced` 采用“知识库扩 query + 归因过滤”的偏严格策略
  - 当前结论主要反映“严格 KB 过滤版本”的检索表现，不代表未来 soft-rerank 版本的最终上限

## 背景

本次评估比较了两条检索链：

- `ticker_only`：不使用知识库，只使用 ticker 做检索
- `entity_kb_enhanced`：使用公司知识库，对查询做 `ticker + company + CEO + products` 扩展，并基于知识库做归因过滤

评估输入来自：

- 报告：[reports/retrieval_eval/retrieval_eval_report.md](/c:/Users/weber/Documents/GitHub/Financial%20Agent/reports/retrieval_eval/retrieval_eval_report.md)
- 明细：[reports/retrieval_eval/retrieval_eval_results.json](/c:/Users/weber/Documents/GitHub/Financial%20Agent/reports/retrieval_eval/retrieval_eval_results.json)
- 黄金集目录：[annotations/retrieval_eval_by_ticker](/c:/Users/weber/Documents/GitHub/Financial%20Agent/annotations/retrieval_eval_by_ticker)

## 结论摘要

当前知识库链的主要收益是“显著降低误召回”，但当前版本过于保守，导致召回下降明显，整体还不能说优于 `ticker_only`。

从 aggregate metrics 看：

- `ticker_only`: `Precision@K = 0.117`, `Recall@K = 0.260`, `Hit@K = 0.500`, `False Positive Rate = 0.883`
- `entity_kb_enhanced`: `Precision@K = 0.138`, `Recall@K = 0.082`, `Hit@K = 0.211`, `False Positive Rate = 0.151`

这说明：

- 知识库链的精度略有提升
- 知识库链的误召回显著下降
- 知识库链的召回和命中率下降很多

## 问题是什么

### 问题 1：知识库链过于保守，导致召回不足

这是当前最核心的问题。

表现为：

- `entity_kb_enhanced` 的 `Recall@K` 从 `0.260` 降到 `0.082`
- `Hit@K` 从 `0.500` 降到 `0.211`
- 38 个 ticker 里，知识库链有 27 个 `returned_count = 0`

这说明知识库链虽然能过滤掉很多错误文章，但也过滤掉了大量本来应该保留的相关文章。

### 问题 2：黄金集正负样本极不平衡，导致分数天然难看且波动大

这是第二个重要问题。

当前很多 ticker 的标注分布大致是：

- 总候选 37 篇
- relevant 只有 1 到 3 篇，部分稍多
- 其余绝大多数都是 non-relevant

这会带来几个后果：

- `false_positive_rate` 很容易偏高
- `precision` 很容易偏低
- 只要漏掉 1 篇 relevant，`recall` 就会大幅下降
- 单 ticker 的分数非常不稳定

例如某个 ticker 只有 1 篇 relevant：

- 找到就是 `recall = 1.0`
- 没找到就是 `recall = 0.0`

所以这份评估里的绝对分数不能被过度解读。

### 问题 3：报告展示口径不一致，容易误导阅读

报告里有一个很容易让人困惑的现象：

- 某些 ticker 的 `Top-K Article IDs` 看起来不为空
- 但同一个 ticker 的 `returned_count` 却是 0

这不是程序报错，而是展示口径不同：

- `Top-K Article IDs` 显示的是检索链实际吐出来的文章 ID
- `returned_count` 统计的是这些文章里，有多少篇出现在当前黄金标注文件中，且按 dedupe 规则被计入评估

因此：

- “有文章 ID” 不等于 “这些文章在当前评估里有分”
- 这会影响对报告的直觉理解

### 问题 4：公司 ticker 与非公司资产混在一起评估，稀释了知识库效果

当前黄金集里不仅有公司 ticker，也有非公司资产：

- `DJI`
- `QQQ`
- `SPY`
- `XLE`

这类标的不走公司知识库主路径，通常是 fallback 行为。
如果把它们和公司 ticker 混在一起看 aggregate metrics，会稀释知识库对公司实体检索的真实效果。

## 这些判断是怎么得出的

### 依据 1：aggregate metrics 的方向很明确

总表说明：

- `entity_kb_enhanced` 的 `False Positive Rate` 从 `0.883` 大幅降到 `0.151`
- 但 `Recall@K` 从 `0.260` 降到 `0.082`

这不是“知识库整体变强”，而是“知识库变得更严，但漏得更多”。

### 依据 2：按 ticker 看，收益集中在少数票，更多票直接被筛空

从明细统计看：

- 38 个 ticker 中，`false_positive_rate` 下降的有 33 个
- `precision` 提升的有 6 个：`META`, `MSFT`, `NEE`, `NVDA`, `RBLX`, `TSLA`
- `recall` 提升的只有 2 个：`NEE`, `TSLA`
- 知识库链 `returned_count = 0` 的有 27 个

这说明当前知识库的主要作用是“砍掉错误结果”，但它还没有稳定带来召回收益。

### 依据 3：典型 ticker 的行为印证了“过严过滤”

正向例子：

- `NVDA`
  - `ticker_only`: `precision = 0.400`, `recall = 0.143`
  - `entity_kb_enhanced`: `precision = 1.000`, `recall = 0.143`
  - 说明知识库去掉了错文，且没有损失召回

- `TSLA`
  - `ticker_only`: `precision = 0.000`, `recall = 0.000`
  - `entity_kb_enhanced`: `precision = 1.000`, `recall = 0.250`
  - 说明知识库帮助命中了真正有用的文章

负向例子：

- `AAPL`
  - `ticker_only`: `precision = 0.143`, `recall = 0.333`
  - `entity_kb_enhanced`: `precision = 0.000`, `recall = 0.000`

- `AMZN`
  - `ticker_only`: `precision = 0.286`, `recall = 0.167`
  - `entity_kb_enhanced`: `precision = 0.000`, `recall = 0.000`

这类结果说明：当前知识库链在不少 ticker 上已经不是“变严格但还留下一部分对文”，而是“直接筛空”。

### 依据 4：数据不平衡确实会放大分数波动，但不能单独解释 A/B 差异

黄金集不平衡会让两个链的绝对分数都变难看，这是事实。

但这不是全部解释，因为：

- `ticker_only` 和 `entity_kb_enhanced` 使用的是同一份黄金集
- 如果只是数据分布问题，两边都会受影响
- 现在最大的差异恰好发生在 `Recall@K` 和 `Hit@K`

因此可以判断：

- 分数难看，部分是数据问题
- KB 链召回显著变差，仍然是系统行为问题

## 解决方案

### 方案 1：把知识库从“硬过滤器”改成“软加权器”

这是最优先的改动。

当前路径更像：

- 先检索
- 再用知识库做严格归因过滤
- 不符合规则的结果直接被丢掉

建议改成：

- 保留 `ticker_only` 的基础召回
- 知识库命中只作为 boost 或 rerank 特征
- 对 company / CEO / product 命中的文章加分，而不是先砍掉

目标是：

- 尽量保留 baseline 的召回
- 同时用知识库改善排序和精度

### 方案 2：把知识库归因规则分层，而不是一刀切

建议把信号分成强弱两层：

- 强信号：高辨识度 product、CEO、ticker、公司名
- 弱信号：业务线、主题词、泛行业词

使用规则建议：

- 强信号可以参与排序增强
- 弱信号只做辅助解释或轻微加权
- 不要让弱信号直接决定是否丢弃文章

### 方案 3：把评估结果分组看，而不是只看一张 aggregate 表

建议至少拆成以下几组：

- 公司 ticker
- 非公司资产 ticker
- `relevant_count = 1`
- `relevant_count >= 3`

这样可以更清楚地区分：

- 是知识库逻辑有问题
- 还是该 ticker 的黄金集过于稀疏

### 方案 4：调整报告，让“检索结果”和“计分结果”分开展示

报告应增加更明确的字段，例如：

- `retrieved_top_k_count`
- `scored_returned_count`
- `retrieved_but_unlabeled_count`

这样就不会再出现“明明列了文章 ID，但 returned 是 0”这种阅读障碍。

### 方案 5：继续补高质量产品词，但不要重新引入泛词

当前 seed 已经做过一轮清洗，方向是对的。
接下来应该补的不是泛业务词，而是新闻里真实高频出现的专属名词，例如：

- 品牌名
- 产品名
- 平台名
- 常用缩写

不应该重新加入：

- `computer hardware`
- `software`
- `platform`
- `electricity`

这类词更适合进入 `business_lines` 或 `themes`，不适合作为高精度产品别名参与强归因。

## 推荐的执行顺序

1. 先把知识库链从硬过滤改成软加权 / rerank
2. 再重跑 retrieval eval，看召回是否恢复
3. 把报告改成分组版，并区分 company vs ETF/index
4. 最后针对召回仍然差的 ticker，补精确产品词和别名

## 当前阶段的正确解读

当前这版知识库不是失败了，而是说明方向已经清楚：

- 知识库确实能降低误召回
- 但当前接法太硬，牺牲了太多召回

因此下一阶段的重点不是“继续堆更多种子数据”，而是“改变知识库介入检索的方式”。

更具体地说：

- 现在的问题主要不是 seed 完全不行
- 而是 retrieval integration 策略过于保守
- 需要从 strict filter 调整为 recall-preserving rerank

----------------------------------------------------------------------------------------------
硬过滤代码：
···
def _hard_filter_results(
    search_results: list[dict[str, Any]],
    company: CompanyContext,
    *,
    company_kb: SQLiteEntityStore,
    snippet_chars: int,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for result in search_results:
        text_parts = [
            result.get("title", ""),
            result.get("summary", "") or "",
            _build_match_snippet(result, snippet_chars),
        ]
        attribution = company_kb.attribute_article_to_company(
            " ".join(part for part in text_parts if part),
            company,
        )
        if not attribution.is_match:
            continue
        enriched = dict(result)
        enriched["company_id"] = company.company_id
        enriched["attribution_match_types"] = list(attribution.match_types)
        enriched["attribution_matched_aliases"] = list(attribution.matched_aliases)
        filtered.append(enriched)
    return filtered
···

为什么做硬过滤？
1. FP 太高。
2. 验证知识库有没有价值

为什么precision@k很低：
1. 因为k太大了。k=12但是黄金测试集里面，、
2. 分布不平均：1,2个relevant占50%，3-6的占50%
38 个 ticker
median = 2
mean = 3.553
min = 1
max = 14

解决方案：
保留relevant 数>=3的ticker，然后k=4
----------------------------------------------------
又遇到了召回差的问题，且ticker-only > kb增强的Recall：

所以现在回头总结，之前 ticker_only recall 总比 KB 高，主要是这三句话：

1. 评估协议以前有问题

max_article_id 是 post-filter，不是 pre-filter
这会系统性低估 KB recall

2. 之前用的是硬fIlter，现在用软filter.


对，这个点你说得对，而且是个真实问题。

当前实现是：

先在整个 vector DB 里搜
拿到 top-k
再用 allowed_article_ids 把 >230 过滤掉
也就是现在代码里的行为：

search_results = vector_store.search(query, top_k=retrieval_top_k)
if allowed_article_ids is not None:
    search_results = [item for item in search_results if _result_article_id(item) in allowed_article_ids]
这和你想要的语义不是一回事。

你想要的是：

检索语料库本身就只包含 <=230
>230 根本不能参与召回
也不能参与排序
RAG 从一开始就“不知道它们存在”
而当前实现其实是：

>230 先参与了向量召回竞争
只是后来被丢掉了
这会带来两个偏差：

1. 候选集被污染
如果全库 top 4 里有 3 篇都是 >230，那过滤后就只剩 1 篇。
但如果你一开始就在 <=230 的子库里搜，本来可能能拿到另外 4 篇 <=230 的文章。

所以现在的结果会低估真实 recall。

2. 排序语义不对
你现在评估的不是：

“在 <=230 这个受限知识库里，系统表现如何”
而是：

“在全库里先搜，再把不允许看的文删掉之后，系统表现如何”
这两者差别很大。

所以结论很明确：

如果你的实验定义是“RAG 只能看 <=230 的内容”，那当前实现本身是有问题的。

正确做法应该是二选一：

检索阶段就加 corpus filter

vector search 只在 article_id <= 230 的集合里做
这是最理想的
如果底层 vector store 不方便过滤

那就必须大幅 over-fetch
比如先搜 top 50 / top 100
再过滤到 <=230
再从里面取前 4
这只是近似方案，不如真正 pre-filter 干净
你现在说的这个判断非常好，面试里也很加分，因为这体现的是：

你不是只会看指标
你会检查 evaluation protocol 是否自洽
你知道“post-filter candidate set” 和 “filtered retrieval corpus” 不是一回事

python run_retrieval_eval.py evaluate --db-path news_articles.db --entity-db-path company_entities.db --annotation-input-dir annotations/retrieval_eval_by_ticker --output-dir reports/retrieval_eval_2026-05-27_soft_boost_deepseek_k4_max230_minrel3 --max-article-id 230 --min-relevant 3

对，现在改完之后，这个命令里的：

--max-article-id 230
会让评估链在检索一开始就只在 article_id <= 230 的语料子集里找。

也就是说现在语义是：

RAG 检索时只看 <=230
>230 不会参与向量召回
>230 不会进入 rerank 候选集
>230 也不会影响 top-k 排序
这和之前不一样。之前是：

先全库搜
再把 >230 过滤掉
现在已经变成你想要的版本：

先限制语料库
再检索
所以对这条命令来说，你可以把它理解成：

“在 <=230 的受限知识库里，评估 ticker_only 和 soft_boost + deepseek 的表现。”

改善结果：

 Chain | Macro Precision@K | Macro Recall@K | Micro Precision@K | Micro Recall@K | Hit@K | False Positive Rate | Retrieved Top-K | Scored Returned | Relevant Hits | Retrieved But Unlabeled |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ticker_only | 0.198 | 0.116 | 0.194 | 0.110 | 0.562 | 0.802 | 62 | 62 | 12 | 0 |
| deepseek_entity_kb_enhanced | 0.354 | 0.161 | 0.328 | 0.174 | 0.750 | 0.646 | 59 | 58 | 19 | 1 |

C:\Users\weber\Documents\GitHub\Financial Agent\reports\retrieval_eval_2026-05-27_soft_boost_deepseek_k4_max230_minrel3
------------------------------------------------------------------------

召回改善（0.07 -> 0.16）但是依旧低怎么办？

dynamic top k.
结果：
| Chain | Macro Precision@K | Macro Recall@K | Micro Precision@K | Micro Recall@K | Hit@K | False Positive Rate | Retrieved Top-K | Scored Returned | Relevant Hits | Retrieved But Unlabeled |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ticker_only | 0.266 | 0.247 | 0.270 | 0.248 | 0.875 | 0.734 | 102 | 100 | 27 | 2 |
| deepseek_entity_kb_enhanced | 0.319 | 0.274 | 0.358 | 0.312 | 0.875 | 0.681 | 100 | 95 | 34 | 5 |
C:\Users\weber\Documents\GitHub\Financial Agent\reports\retrieval_eval_2026-05-27_soft_boost_deepseek_dynamic_relk_max230_minrel3
--------------------------------------------------------------------------------
但是单ticker的命中率很低，比如Appl, goog

解法：需要拆分任务
因为“RAG”不是一个单一任务，它至少包含两件完全不同的事：

找到和目标实体直接相关的证据
找到虽然没点名，但对目标判断有影响的背景/上下文证据

这两件事：

信息信号不同
检索方法不同
评估标准也不同
如果你硬把它们混成一个 retrieval task，通常会出两个问题：

系统设计会拧巴
评估结果会误导

比如说Apple和goog没搜到是因为是indirectly relevant的。

为什么拆分是合理的
1. 直接证据和间接证据，本来就是不同类型的相关性
比如你问：

“Apple 最近值得关注的新闻是什么？”
这里可能同时需要：

直接证据

Apple 发布新品
Tim Cook 发言
App Store 新政策
iPhone 需求变化
间接证据

中美政策变化
智能手机供应链受扰动
半导体出口限制
消费者支出降温
油价影响运输/成本/宏观风险偏好
这两类都可能对最终回答有价值。
但它们不是同一种“相关”。

所以拆分不是把任务切碎，而是把不同类型的 relevance 显式建模。

2. 不拆的话，你很难知道系统到底哪里出了问题
如果你只用一个 relevant：

某篇 indirect article 没被召回
你会以为 entity KB 不行
但实际上可能只是：

这篇文章本来就不应该靠 entity alias 检索
它应该靠 sector / macro / ecosystem / competitor 信号检索
所以不拆分，你会把：

entity retrieval 的失败
和
context retrieval 的失败
混为一谈。
3. 很多成熟 RAG 系统本来就是多路检索
广义上说，RAG in general 很少真的只有一条 retrieval path。

更常见的是：

entity-specific retrieval
semantic background retrieval
keyword retrieval
metadata / graph retrieval
recent news / time-sensitive retrieval
最后再融合。

你现在说的 direct 和 indirect，其实就是在把这种多路 retrieval 的目标讲清楚。

-----------------------------------------------------------------------
Rag 延迟高怎么解决？

1. 问题：5个ticker跑了接近5分钟，但是其他rag很快，导致openclaw超时。
2. 诊断: 串行检索rag耗费过长时间。
3. 方案：并行检索

但是还有一个问题，就是：
watchlist triage 的 orchestration 分散在多个 Module 里
financial_agent_mcp.py 已经开始承载 batching policy、timeout pressure、transport concerns
你如果直接往里塞并行检索，短期能跑，后面很容易把并发、重试、部分结果、超时策略全摊到调用者身上，Interface 会更浅


并行检索还有可能触发并发问题。因为chromadb不保证线程安全。

常见并发问题：
1. 

