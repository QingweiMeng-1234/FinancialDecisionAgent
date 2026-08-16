# RAG 抓取与检索可靠性修复方案

## 1. 文档状态

- 状态：In progress；工作包 1 已完成工具与隔离 staging 验证，工作包 2 正在实现
- 代码基线：`main@24d2307`
- 审计日期：2026-08-15
- 适用范围：核心 Financial Agent 包，不包含 `QuantGPT/` 与 `rag-from-zero/`
- 当前阶段：已实现 read-only audit、manifest、staging apply/verify 与 canonical content
  存储契约；尚未执行 live migration、Chroma v2 构建、切流或部署

`feature/theme-research-v1@611fa60` 与上述 `main` 在本文涉及的核心源文件上没有差异；`main` 额外包含 README 合并结果。本文记录的 SQLite、正文文件和 Chroma 数量来自当前本地工作区的只读审计，不应视为远端仓库或其他环境的实时状态。

当前实施证据仅覆盖本地 dirty worktree 与隔离 `.tmp` staging：最新计划
`79b92bfa0dba29cba22331bad51edb339496c47569a5b6d766c086e06406d0ee`
验证了 482 条 audit rows、421 条 canonical verified ready articles 和 1 条
持久 quarantine（article 228），verification issues 为 0。它不证明 Chroma v2、
线上 retrieval eligibility、部署或用户可见回答已经完成。

工作包 3 当前只完成了 dependency-independent generation control plane：
`index_generations`、`article_index_manifest`、chunk 双向 reconciliation proof、
`corpus_index_state` 与 verified-only atomic activation。真实 Chroma v2 collection
尚未构建或激活；本机 Python 缺少可用的 `chromadb`/embedding 运行依赖，因此
该数据面步骤保持 pending，旧 v1 collection 与 live pointer 均未改变。

## 2. 决策摘要

修复必须按以下顺序进行：

1. 先恢复 canonical article content 的唯一真相，并完成 SQLite、正文文件与 Chroma 的只读盘点和数据对账。
2. 再从已验证 canonical content 重建 Chroma v2，并以 generation/manifest 完成可回滚切换。
3. 再定义 refresh run、article stage、retry 和 daily gate 的明确状态语义，禁止“文章全部失败但 refresh 成功”。
4. 再提高抓取可靠性，包括失败分类、退避、限流、逐条隔离和幂等更新。
5. 再让 retrieval 只消费通过 SQLite eligibility 检查的数据，并加入时间窗和内容版本校验。
6. 再修复 theme research 的日期、来源类型、证据充足性和逐条故障隔离。
7. 再强制校验回答引用与服务端 evidence 的对应关系。
8. 最后才优化 chunking、embedding、候选池、reranker 和回答质量。

这一顺序的核心理由是：如果 canonical content 与派生索引不能对账，召回指标和模型效果都无法被可靠解释。

## 3. 当前链路

### 3.1 每日新闻刷新

```text
CLI / MCP refresh_news
  -> NewsCollector
  -> NewsAPI sources + everything/top-headlines
  -> RawEventInput(title, description, URL, published_at)
  -> create_or_get_article_reference()
  -> ArticleContentFetcher
  -> requests GET publisher page
  -> trafilatura exact-text extraction
  -> data/articles/{article_id}.txt
  -> SQLite content_path / hash / processing status
  -> SummarizationAgent
  -> fixed-size chunking
  -> sentence-transformer embeddings
  -> Chroma upsert
  -> optional query / rerank / answer
```

主要实现边界：

- `src/event_collector/event_collection.py`：NewsAPI 线索收集
- `src/event_collector/article_content.py`：publisher 页面抓取与正文抽取
- `src/event_collector/news_ingestion.py`：逐篇 ingestion orchestration
- `src/event_collector/news_storage.py`：SQLite identity、状态与正文路径
- `src/event_collector/vector_store.py`：chunk embedding、Chroma 写入与检索
- `src/event_collector/watchlist_workflow.py`：每日 refresh gate

### 3.2 主题研究抓取

```text
theme + analysis goal + optional tickers
  -> build search intents
  -> Serper organic results
  -> URL identity
  -> classified publisher fetch
  -> canonical content / summary / optional vector index
  -> local Chroma support retrieval
  -> evidence dedupe
  -> source classification + freshness sufficiency
  -> persistent theme research assets
```

主要实现边界是 `src/event_collector/theme_research.py`。

### 3.3 查询消费

```text
question / ticker
  -> optional entity resolution and query expansion
  -> Chroma semantic search
  -> low-quality result filtering
  -> company-aware score adjustment
  -> compact LLM rerank
  -> top-k evidence
  -> grounded answer with citations
```

当前查询主要直接消费 Chroma，不以 SQLite 的处理状态、正文存在性和内容 hash 作为强制门禁。

## 4. 当前审计事实

以下数据仅描述 2026-08-15 当前本地工作区：

| 检查项 | 结果 |
| --- | ---: |
| SQLite article rows | 482 |
| `content=ready, summary=ready, index=ready` | 419 |
| `content=ready, summary=failed, index=ready` | 3 |
| `content=failed` | 56 |
| 全部 pending | 4 |
| 非空 `content_path` | 423 |
| 指向旧机器 `C:\Users\weber\...` 的 `content_path` | 423 |
| 当前目录下 `data/articles/*.txt` | 427 |
| 422 条 ready rows 中可按 `data/articles/{id}.txt` 找到本地文件 | 422 |
| 上述本地文件与 SQLite `content_sha256` 匹配 | 421 |
| 上述本地文件与 SQLite hash 不匹配 | 1（article 228） |
| Chroma embedding records | 2,817 |
| Chroma distinct article IDs | 426 |
| SQLite 非 ready 但仍存在于 Chroma 的 article IDs | 4 |
| Chroma 无 article ID 的 legacy internal chunk | 1 |
| 已被 Git 跟踪的 DB/Chroma/article runtime files | 412 |
| 加上 refresh state 后已被 Git 跟踪的 runtime files | 413 |

直接影响：

- 422 条标记为 content ready 的记录在当前机器通过 `SQLiteNewsStore` 加载时会得到空正文，因为旧绝对路径不存在，而 SQLite `content` 列又是空字符串。
- 不能把本地同 ID 文件批量视为可信修复来源：article 228 已实测与数据库 hash 不匹配，必须进入 quarantine/review。
- Chroma 仍可返回旧 embedding，使系统表现为“查询还能工作”，但无法从当前 canonical content 重建或验证。
- 当前数据库、正文目录和向量索引不是同一个可证明的一致快照。

## 5. 目标不变量

### 5.1 Article identity 不变量

每篇文章必须有稳定的 `article_id`。identity 的初始 key 是 normalized original URL；抓取成功后必须执行 canonical URL reconciliation。

必须满足：

- 同一 normalized URL 只能映射到一个 article identity。
- 同一 canonical URL 只能映射到一个 active article identity。
- `normalized_url` 必须由数据库唯一约束保护；实现采用 conflict-safe insert 后读取既有 ID，不能继续依赖“先 SELECT 再 INSERT”。
- canonical 冲突必须进入显式 merge/review 流程，不能静默覆盖另一条记录。
- `active_content_sha256` 用于判断当前正文版本，不直接替代 article identity。

### 5.2 Canonical content 不变量

正文是 source of truth；summary、chunks、vectors 和 retrieval snippets 都是可重建派生物。

建议采用不可变、版本化正文路径：

```text
data/articles/{article_id}/{active_content_sha256}.txt
```

SQLite 中只保存相对于配置的 `content_root` 的路径，例如：

```text
articles/181/2f4c...9a.txt
```

必须满足：

- `content_status=ready` 时，文件必须存在、非空且实际 hash 等于 `active_content_sha256`。
- `content_path` 不得包含用户名、盘符或机器相关绝对目录。
- 正文文件先写入同文件系统的临时路径，完成 hash 校验后原子 rename。
- SQLite 指针切换失败时，旧正文版本继续有效；新文件最多成为可清理 orphan，不能破坏旧版本。
- 未知来源、旧文件与 hash 不一致时不得自动标记 ready。

### 5.3 Derived artifact 不变量

每个派生物必须绑定到明确的 `active_content_sha256`：

- summary 记录其输入 content hash、模型和 prompt version。
- vector index 记录其输入 content hash、embedding model、chunk config 和 index generation。
- retrieval 只允许消费与当前 active content hash 一致的派生物。
- 同一 hash 与相同派生配置重复执行必须幂等，不产生重复向量。

### 5.4 迁移候选与在线 eligibility 不变量

v2 构建输入与在线查询 eligibility 是两个不同集合，不能循环定义。

`canonical_verified_migration_candidate` 只用于构建新 index generation：

```text
stable article identity
AND content_status == ready
AND content file exists and is non-empty
AND content file hash == active_content_sha256
AND article is not quarantined or superseded
```

它不依赖旧 `index_status`、旧 Chroma 或尚不存在的 v2 manifest。新 generation 激活后，普通 RAG 查询只允许 `retrieval_eligible`：

```text
content_status == ready
AND index_status == ready
AND content file exists
AND content file hash == active_content_sha256
AND indexed_content_sha256 == active_content_sha256
AND article manifest generation == corpus active generation
AND manifest/chunk config and counts are valid
AND article is not quarantined or superseded
```

summary 不是语义检索的硬依赖；但如果某个下游流程要求 summary，该流程必须显式增加 `summary_status=ready` 条件。

## 6. 目标数据契约

### 6.1 `articles` 聚合状态

现有 `content_status`、`summary_status`、`index_status` 继续作为当前 aggregate state，避免所有调用方一次性重写。

第一阶段保留三个合法值：

- `pending`：尚无成功结果，或 active content 变更后等待派生处理
- `ready`：当前 active content/version 的该阶段已完成并通过验证
- `failed`：最近一次针对当前版本的尝试失败，详细原因在 attempt ledger

`in_progress`、重试次数和错误详情不塞入 `articles`；它们属于 attempt ledger。

建议新增或明确以下字段：

| 字段 | 生产者 | 消费者 | 说明 |
| --- | --- | --- | --- |
| `active_content_sha256` | content commit stage | summary/index/retrieval | 当前 canonical content version |
| `content_path` | content commit stage | storage loader/audit | 相对路径 |
| `indexed_content_sha256` | index commit stage | retrieval eligibility | 当前 active index 输入版本 |
| `summary_content_sha256` | summary commit stage | summary consumer/audit | 当前 summary 输入版本 |
| `source_published_at` | source parser | time policy/theme/retrieval | nullable UTC-aware 来源发布时间；未知必须为 NULL |
| `published_at_provenance` | source parser | audit/time policy | `source_metadata` / `search_snippet` / `unknown` |
| `published_at_precision` | source parser | time policy | `instant` / `date` / `month` / `unknown` |
| `superseded_by_article_id` | canonical reconciliation | retrieval/audit | canonical identity 合并结果 |
| `quarantined_at` | integrity audit/operator | retrieval eligibility | 隔离异常记录 |

`active_content_sha256` 是目标 schema 中唯一权威 active hash。现有物理列 `content_sha256` 仅作为迁移输入：migration 先校验并 backfill `active_content_sha256`，兼容期旧列只读，所有新写入、eligibility 和 audit 只读取新列，调用方切换完成后删除旧列；禁止长期双写形成两个真相。

当前 `published_at TEXT NOT NULL` 在兼容期继续供旧调用方显示，但不得再用于 freshness。migration 新增上述 nullable source-time 字段并 backfill 能证明的值；未知值保持 NULL，不能以 ingest/fetch 当前时间填充。旧调用方切换后再移除或放宽 legacy 列。

### 6.2 `article_processing_attempts`

建议新增 append-only attempt ledger：

| 字段 | 说明 |
| --- | --- |
| `attempt_id` | UUID 或稳定唯一 ID |
| `run_id` | 所属 refresh/theme/backfill run |
| `article_id` | 文章 identity |
| `stage` | `content_fetch` / `summary` / `index` / `reconcile` |
| `input_content_sha256` | 该阶段输入版本；抓取阶段可为空 |
| `status` | `running` / `succeeded` / `failed` / `retry_scheduled` |
| `failure_code` | 稳定机器可读错误码 |
| `error_detail` | 截断、脱敏的人类可读错误 |
| `retryable` | 是否允许自动重试 |
| `attempt_number` | 同 run/article/stage 的次数 |
| `next_retry_at` | 允许自动重试的时间 |
| `started_at` / `finished_at` | UTC 时间 |
| `worker_id` | 并发诊断信息，可为空 |

### 6.3 `refresh_runs`

建议新增 refresh run ledger：

| 字段 | 说明 |
| --- | --- |
| `run_id` | 唯一 run ID |
| `parent_run_id` | 定向 retry 所属的上一个 terminal run；初次执行为空 |
| `attempt_no` | 相同 scope chain 中从 1 开始递增的执行序号 |
| `scope_key` | 日期、source config 和 corpus config 的稳定 hash |
| `requested_date` | 业务日期 |
| `status` | 持久化 run 状态：`running` / `completed` / `partial` / `failed` |
| `collector_status` | collection stage 状态 |
| `total_inputs` | source 返回的输入数 |
| `accepted_inputs` | 通过基本校验的输入数 |
| `unchanged_ready` | 已有且无需重建的文章数 |
| `content_ready` / `summary_ready` / `index_ready` | 本 run 阶段结果数 |
| `failed_items` | 至少一个 required stage 失败的文章数 |
| `permanent_exclusions` | 达到策略上限后明确终止、不会进入 corpus 的条目数 |
| `retryable_failures` | 尚有重试机会或等待 `next_retry_at` 的条目数 |
| `lease_owner` / `lease_expires_at` | 同 scope 并发 claim 与崩溃接管 |
| `started_at` / `finished_at` | UTC 时间 |
| `config_snapshot` | endpoint、page、days、source list、模型和 chunk config |

### 6.4 Index generation 与 article manifest

`index_status` 只能表示聚合结果，不能单独充当切流凭证。至少需要以下持久化 manifest：

| 层级 | 必须记录 |
| --- | --- |
| collection generation | `generation_id`、collection name、embedding model/artifact、chunk config/version、corpus snapshot ID、状态、创建/验证/激活时间 |
| article index manifest | `generation_id`、`article_id`、`indexed_content_sha256`、expected chunk count、actual chunk count、index status、indexed_at |
| chunk metadata | `generation_id`、`article_id`、`indexed_content_sha256`、chunk index、chunk config/version、embedding model/version |

全局拓扑冻结为：独立的 `index_generations` / `article_index_manifest`，以及 singleton `corpus_index_state(corpus_id, active_generation_id)`；不在 `articles` 上另设单篇 active-generation 指针。只有整个 generation 通过双向 coverage、hash 和 chunk-count 验证后，才能在一个短 SQLite transaction 中切换 singleton active generation，并把新近恢复的 article aggregate `index_status` 投影更新为 ready。查询以 active generation 的 manifest 为准，启动时发现配置 collection 与 SQLite active generation 不一致必须 fail closed，不能混合读取。

## 7. Refresh 状态语义

### 7.1 Run 终态与非执行返回

| 状态/返回 | 精确定义 | 是否建立 daily success gate |
| --- | --- | --- |
| `completed` | collector 成功；每个 accepted item 均为 unchanged-ready、required stages 成功，或已成为有明确原因的永久排除项；没有到期或待调度的可重试工作；且 usable items 至少为 1 | 是 |
| `partial` | collector 成功；至少一个 item 可用，同时仍有可重试失败、等待重试的阶段或尚未终止的预期派生失败 | 否，只允许定向重试失败项 |
| `failed` | collector 失败、系统初始化失败，或本 run 没有任何可用 item 且存在处理失败 | 否 |
| `already_completed` | 相同 `scope_key` 已有 completed run；不创建新的执行 run | 已由原 run 建立 |
| `in_progress` | 已有 owner 持有未过期 lease；不创建重复执行 run | 不改变 |

第一阶段将 `content_fetch` 与 `index` 定义为 refresh required stages。summary 是预期阶段，summary 失败应使 run 为 `partial`，但不撤销已经验证的 content/index；这保证用户能看到降级，而不是把文章完全排除。若产品后续决定 summary 是所有消费链路的硬依赖，再提升为 required eligibility 条件。

`completed`、`partial`、`failed` 是已创建 execution run 的持久化终态；`already_completed`、`in_progress` 是调用 disposition，不是新的 run 终态。terminal run 不原地重新打开：`partial` 或 `failed` 后的定向重试创建新的 `run_id`，保存 `parent_run_id`，继承相同 `scope_key`，只处理父链中仍未解决的 article/stage。retry run 只有在对同一 scope 的累计结果证明不存在 retryable failure 时才能成为 `completed` 并建立 gate，不能仅因为它自己的小批次成功就关闭整个 scope。

`usable_items = unchanged_ready + required stages 全部成功的 distinct articles`。所有 accepted items 最终都成为 permanent exclusion 时，即使没有 retryable failure，也必须是 `failed` 且不建立 gate，并触发 source/policy 告警。collector 成功且 `total_inputs=accepted_inputs=0` 只有在所有 source 都返回可验证的成功空结果、没有 rejected/error，并记录 `empty_reason=no_matching_articles` 时，才可作为 `completed` 的唯一零 usable 例外；它表示“当日确无匹配输入”，不是“抓取全失败”。

### 7.2 对外返回示例

```json
{
  "run_id": "run_01J...",
  "status": "partial",
  "refresh_date": "2026-08-15",
  "scope_key": "news:sha256-of-config",
  "attempt_no": 1,
  "retry_after": "2026-08-15T04:30:00Z",
  "counts": {
    "discovered": 100,
    "rejected": 2,
    "unchanged_ready": 20,
    "content_ready": 67,
    "summary_ready": 64,
    "index_ready": 67,
    "permanent_exclusions": 4,
    "retryable_failures": 7
  },
  "failure_summary": [
    {
      "stage": "content_fetch",
      "failure_code": "network_timeout",
      "count": 4
    }
  ]
}
```

### 7.3 Daily gate

Daily gate 不再只存 `last_refresh_date`，而是绑定：

```text
scope_key = hash(
  requested_date,
  news endpoint,
  page/range,
  source selection,
  corpus identity,
  ingestion contract version
)
```

规则：

- 只有 `completed` run 可以阻止同 scope 的完整重复刷新。
- `partial` 只能触发失败 article/stage 的定向 retry，不能重新处理 unchanged-ready items。
- `failed` 不建立 success gate。
- 定向 retry 使用新的 child run；daily gate 对同一 `scope_key` 的整条 parent chain 做累计对账。
- 同一 scope 同时只允许一个 owner；其他调用返回结构化 `in_progress`，不得并发写同一 SQLite/Chroma。
- gate 与 run ledger 的提交必须在同一 SQLite transaction 中完成。
- claim 使用独立 SQLite connection 与短 `BEGIN IMMEDIATE` transaction：检查 completed run、检查/接管过期 lease、创建 running run 后立即 commit。
- HTTP、LLM、embedding 和 Chroma 操作不得发生在 SQLite transaction 或全局进程锁内。
- worker 崩溃后，只有 lease 过期且已持久化 stage state 可对账时，其他 worker 才能接管。

## 8. 抓取失败分类与重试

### 8.1 稳定 failure codes

| failure code | 默认重试策略 |
| --- | --- |
| `network_timeout` | 指数退避重试 |
| `network_connection_error` | 指数退避重试 |
| `http_429` | 尊重 `Retry-After` |
| `http_5xx` | 有上限的指数退避 |
| `http_401_403` | 默认不自动高频重试，进入 provider/domain policy |
| `paywall_suspected` | 不盲重试，等待替代来源或人工策略 |
| `dynamic_page_suspected` | 转入支持 JS 的独立抓取策略，不能循环 requests |
| `extract_empty` | 限次重试，随后等待替代 extractor/source |
| `truncated_preview_suspected` | 不进入 canonical ready，等待替代来源 |
| `invalid_or_private_url` | 永不重试，安全拒绝 |
| `summary_provider_error` | provider 退避重试 |
| `embedding_error` | 可重试，不删除或切换旧 active generation |
| `vector_write_error` | 可重试并执行 index reconciliation |
| `content_integrity_mismatch` | 隔离并要求对账，不自动覆盖 |

### 8.2 抓取执行规则

- NewsAPI collection failure与单篇 publisher fetch failure分开记录。
- 每篇文章独立失败，不能中止整个 batch 或 theme research run。
- 使用复用的 HTTP session、明确 User-Agent、连接/读取 timeout、域名级限流和有界重试。
- 校验 `http/https` scheme、重定向目标和解析后的公网地址；阻止 localhost、私网、link-local 和 metadata endpoint。
- 记录最终 response URL、status code、content type、extractor version 和抓取耗时。
- 自动抓取策略必须遵守来源条款、robots 和内容授权边界；策略限制要可配置和可审计。

## 9. 幂等更新与派生重建

### 9.1 内容未变化

如果抓取后的 normalized content hash 等于 `active_content_sha256`：

- 更新 `last_checked_at` 与 source metadata；
- 不清空 summary；
- 不重算 embedding；
- 计入 `unchanged_ready`；
- 不改变 active content/index version。

### 9.2 内容变化

如果 hash 变化：

1. 将新正文写入不可变版本路径并校验 hash。
2. SQLite transaction 切换 active content pointer，并将 summary/index aggregate state 置为 pending；该 article 立即退出在线 eligibility。
3. summary 针对新 hash 独立处理；index builder 将该变化登记为 successor generation 的输入。
4. 基于当时的完整 `canonical_verified_migration_candidate` snapshot 构建 successor full generation。未变化 article 只有在 content hash、embedding artifact 与 chunk config 完全一致时才可复用已验证的 chunks/embeddings，但 successor manifest 必须完整且所有 records 都属于新 generation。
5. successor generation 完成全量 coverage/hash/chunk-count 验证后，在一个短 SQLite transaction 中切换 `corpus_index_state.active_generation_id`，并把新近恢复的 article `indexed_content_sha256` / `index_status` 投影更新为新 hash / ready。
6. 旧正文与旧派生物保留到 retention/rollback 窗口结束，但内容已变化的 article 在 successor 激活前不得继续以旧 index 对外返回。

### 9.3 安全 reindex

禁止当前的“先删除旧 chunks，再生成新 embedding”。最低要求：

1. 冻结本次 successor generation 的完整 canonical candidate snapshot。
2. 对变化内容先完成 chunking 与 embedding；对未变化内容只允许复用配置和 hash 都一致的已验证派生物。
3. 物化一个覆盖完整 snapshot 的 successor generation/collection，不向 active generation append 或原地覆盖。
4. 验证 candidate 与 manifest 的集合相等、记录数、article ID、hash、chunk index 连续性和配置一致性。
5. 在一个短 SQLite transaction 中提交 singleton active-generation pointer 与必要的 aggregate state 投影。
6. 最后按 retention 策略清理旧 generation；不得先删旧 chunks。

任一步失败时，旧 active generation 对未变化且仍 eligible 的 articles 继续可查询；内容已变化或处于 pending 的 article 暂时不可检索。本文不选择“旧内容继续服务直到新正文+全局 generation 一次性切换”的替代语义。

## 10. 数据迁移与对账计划

### Phase 0：冻结证据与备份

- 暂停 refresh、theme ingestion、backfill 和其他写路径。
- 记录 Git SHA、工作区状态、DB 文件 hash、Chroma 文件 hash、正文目录 manifest。
- 创建可恢复的 SQLite、Chroma、正文文件备份；不以 Git 历史作为运行数据备份。
- 所有迁移工具先提供 `dry-run`、`apply`、`verify` 三种模式。

### Phase 1：Canonical content 修复

逐条分类：

| 分类 | 条件 | 动作 |
| --- | --- | --- |
| A 可直接修复 | `data/articles/{id}.txt` 存在且 hash 与 DB 相符 | 迁移到版本路径，写相对路径，保持 ready |
| B 可验证但 DB 缺 hash | 文件存在、来源可追溯、内容通过质量校验 | 计算 hash，记录迁移 provenance 后再 ready |
| C hash 冲突 | 文件存在但 hash 与 DB 不同 | quarantine，不自动选一方 |
| D ready 但文件缺失 | DB ready、没有可验证正文 | content failed，错误码 `canonical_content_missing` |
| E DB failed 但文件存在 | 文件可能是跨快照遗留 | 不自动提升；先验证 URL、provenance、hash |
| F legacy SQLite content | SQLite `content` 非空且文件缺失 | 写入版本文件，计算 hash，再清空 legacy content |

迁移后必须为每条记录生成审计结果，不能只输出总数。

当前快照的已知处置必须显式写入迁移 manifest：

- 421 条 ready 且本地 hash 匹配的记录可进入版本化相对路径迁移。
- article 228 进入 quarantine/review；不得用本地文件或 SQLite hash 任一方静默覆盖另一方。
- article 177–180 即使仍有 Chroma chunks，也保持非 ready；只有正文 provenance/hash 和 required stages 重新验证后才可晋级。
- 无 `article_id` 的 legacy Chroma chunk 不进入 v2；记录为 orphan 并在 v2 验证完成后随旧 generation 退役。

### Phase 2：新 Chroma collection 重建

- 不在原 collection 上原地修补。
- 从 `canonical_verified_migration_candidate` 构建新 collection，例如 `news_articles_v2`；不得用旧 index eligibility 筛选 v2 输入。
- index metadata 必须包含 article ID、content hash、chunk config、embedding model 和 index generation。
- 每篇 article 的 expected/actual chunk count 写入 manifest；任何缺块、重复 chunk index 或 hash 混用都阻止 generation 激活。
- 重建完成后执行完整 reconciliation 和 Golden Set 回归。
- 验证通过后，在一个短 SQLite transaction 中切换 `corpus_index_state.active_generation_id`；旧 collection 保留到回滚窗口结束。

### Phase 3：Git 与环境清理

- 使用只影响 Git 索引的方式停止跟踪运行 DB、Chroma 和正文文件；不得删除本地唯一副本。
- 仓库只保留最小、脱敏、确定性的测试 fixtures。
- 重建本机 `.venv`，不复制机器绑定的虚拟环境。
- 锁定关键依赖版本并记录 embedding model artifact/version。

## 11. Retrieval 修复

### 11.1 SQLite eligibility gate

每次 query 先从 SQLite 获取 eligible article IDs，再传给 vector search。普通 query 不允许直接无条件扫描整个 collection。

`VectorStore.search(..., allowed_article_ids=...)` 已经存在，修复应复用这一接口并把 eligibility provider 接到所有生产消费者，而不是另造一套 vector filter。

必须统一覆盖：

| 消费入口 | 必须接入的门控 |
| --- | --- |
| `query_news` CLI / `query_workflow.run_question` | SQLite eligibility + time policy |
| `rag_answering.answer_query` | eligibility provider 与 corpus snapshot |
| recommendation | 与问答相同的 eligible evidence contract |
| watchlist / `retrieval_execution` | provider 必须穿过 batch work-item seam |
| MCP query/retrieval tools | 不得只创建 vector store 后绕过 SQLite |
| theme local support retrieval | content/index/freshness/source policy |

每次检索应记录 `eligible_count`、`excluded_count_by_reason`、`corpus_as_of` 和 `eligibility_policy_version`。eligible set 为空时返回明确的 insufficient/integrity 结果，不得回退为无过滤扫描 Chroma。

如果 DB 与 Chroma 对账失败：

- 不返回可能陈旧的“正常答案”；
- 返回结构化 `corpus_integrity_error` 或 `insufficient_evidence`；
- 记录缺失、stale 和 orphan index 数量。

integrity mismatch 时不得调用 reranker 或 answer LLM，不得返回任何未通过 eligibility 的 sources。允许的“降级”仅是零 evidence 的固定错误说明，不是低置信度 RAG answer。

### 11.2 时间窗口

- “latest/recent/今天/最近 N 天”必须转换为明确 UTC 范围。
- vector metadata filter 与最终 evidence shaping 都要复核 `source_published_at`，并依据 `published_at_provenance/precision` 判断能否满足 latest 窗口。
- 日期未知不能自动视为最新。
- 非时间敏感 query 可以不设时间窗，但答案必须披露已知 `source_published_at` 的 evidence 时间范围与 unknown-date 数量。
- `source_published_at` 表示来源声称的业务发布时间；`fetched_at` 表示系统观测/抓取时间。两者必须分别保存，不能互相替代。legacy `published_at` 仅是兼容显示字段，不参与 freshness。

### 11.3 Grounding 校验

回答阶段必须校验：

- 返回 source ID 必须属于本次输入 evidence IDs。
- title、URL、snippet 使用服务端输入值，不接受模型改写为权威值。
- supporting/counter points 的 citations 必须属于输入 evidence。
- answer 正文中的 `[n]` 必须引用存在的输入 evidence。
- 所有 material claims 必须以结构化 claim 携带至少一个非空 citation；material claims 包括财务事实、事件、时间、趋势判断、风险判断和建议依据。
- 只有连接语、对回答限制的说明和明确的“不足以判断”可以没有 citation；服务端校验 structured claims 后再渲染 answer 与 sources。

## 12. Theme research 修复

### 12.1 日期语义

- Serper 日期无法解析时保存 `source_published_at=NULL` 且 provenance 为 `unknown`，不得替换成当前时间。
- 所有时间规范化为 UTC-aware datetime。
- freshness 只统计已知且在 lookback 内的 evidence。
- 三条旧 secondary/commentary 不能使 `fresh_monitoring_sufficient=true`。

### 12.2 来源分类

- `source_kind` 允许值冻结为 `company_primary`、`regulator_primary`、`exchange_primary`、`credible_secondary`、`commentary`、`unknown`。
- 未知域名严格归为 `unknown`，权重为 0，不计入 secondary、fresh authority 或 sufficiency；不能因为域名层级少就自动判为 company。
- primary/company 分类依赖显式 allowlist、已验证 IR domain 或 entity KB mapping。
- `co.uk` 等 public suffix 使用可靠解析，不用简单最后两段规则。
- 每条 evidence 保存 `source_kind_reason` 和 registry/rule version；sufficiency 输出必须同时披露计数、具体来源和分类理由。

### 12.3 故障隔离

- 单个 search intent、URL、summary 或 index failure 形成结构化 evidence acquisition failure。
- 一个坏 URL 不终止整个 theme run。
- run 结果必须披露 attempted、accepted、failed、fresh、unknown-date 和 source-kind counts。
- 已有 `content_status=ready` 但正文无法加载的记录不允许直接作为 ready evidence。

## 13. 验收标准

### 13.1 数据一致性

- `content_status=ready` 且文件缺失：0
- `content_status=ready` 且 hash 不匹配：0
- 机器相关绝对 `content_path`：0
- `indexed_content_sha256 != active_content_sha256` 的 eligible rows：0
- active generation 中 article manifest 的 expected/actual chunk count 不一致：0
- Chroma orphan article IDs：0
- active generation 中无 article ID、错误 generation 或重复 chunk index 的 records：0
- SQLite eligible 但 Chroma 缺失的 article IDs：0
- stale ready summaries（`summary_content_sha256 != active_content_sha256`）：0
- Git 跟踪的 runtime DB/Chroma/canonical article files：0
- 将 corpus 复制到不同绝对目录后，relative-path load、hash audit 与受控检索全部通过

### 13.2 Refresh 行为

必须覆盖：

1. 全部成功：`completed`，建立 gate。
2. 全部 content fetch 失败：`failed`，不建立 gate。
3. 一篇成功、一篇失败：`partial`，只重试失败项。
4. 相同正文：不调用 summary/embed，计入 unchanged。
5. embedding 失败：旧 active generation 对未变化且仍 eligible 的 articles 仍可查询；内容已变化的 article 保持不可检索。
6. 进程在正文写入后、DB commit 前崩溃：旧 pointer 仍有效，orphan 可检测。
7. 同 scope 并发两次：只有一个 owner 执行，另一个得到结构化 skip/in-progress。
8. `Retry-After`、超时、403、付费墙和动态页面进入不同 failure codes。
9. `partial` 的 child retry 只处理未解决项；未完全解决前不得因子批次成功建立 gate。
10. 全部 accepted items 都成为 permanent exclusion：`failed`，不建立 gate并告警。
11. collector 明确成功且所有 source 均为可验证空结果：允许 `completed`，必须记录 `empty_reason`；任何 source error 都不得走此例外。
12. 单篇内容变化后必须产生覆盖完整 canonical snapshot 的 successor generation；切换前该篇不可查，切换后只返回新 hash，且 active generation 中无混代或 coverage 缺口。

### 13.3 Retrieval 与回答

- 非 eligible article 不会被 query 返回。
- latest query 不返回时间窗外 evidence。
- 未知日期不会绕过 latest filter。
- 模型返回不存在或被篡改的 source 时，response validation 失败。
- 任一 material claim 缺少 citation 时，response validation 失败。
- Golden Set 必须至少不低于迁移前已记录 baseline；任何指标变化都报告样本数和未标注数量。

### 13.4 Theme research

- 单 URL 失败不会终止 run。
- aware/naive datetime 不会混用。
- 三条 stale secondary evidence 的 fresh sufficiency 为 false。
- 未知日期计入 unknown，不计入 fresh。
- 未知普通域名不会自动判为 company。

## 14. 测试与证明层级

### 14.1 单元测试

- URL normalization、canonical reconciliation、relative path resolution
- content hash/version、状态转换、failure classification
- refresh terminal status 与 gate eligibility
- theme date/source/sufficiency semantics
- response source/citation validation

### 14.2 集成测试

- 临时 SQLite + 临时 content root + 临时 Chroma
- fault injection：文件写、DB commit、summary、embedding、Chroma write 分别失败
- partial batch 与 targeted retry
- migration dry-run/apply/verify
- collection v1 -> v2 切换与回滚
- 同 scope 并发 refresh

### 14.3 E2E 验收

- 使用受控 HTTP fixtures，不依赖不稳定外网完成基础 gate。
- 单独执行真实 NewsAPI/publisher smoke test，明确它只证明当时的外部可达性。
- 查询结果必须绑定代码 SHA、DB snapshot ID、collection generation、content hash 和 evidence IDs。
- 本地测试通过不等于生产抓取、外部来源授权或长期质量已证明。

## 15. 可观测性

每个 run 至少输出：

- `run_id`、scope、代码 SHA、config snapshot
- collector 请求数、输入数、去重数
- content/summary/index 各阶段成功、失败、重试、unchanged 数
- failure code 分布和 top failing domains
- corpus eligible count、orphan count、stale index count
- duration 与 provider rate-limit 信息
- refresh 终态与 daily gate 决策原因

日志不能包含 API key、完整异常凭证、敏感 headers 或未脱敏页面内容。

## 16. 回滚策略

- 数据迁移前保留 DB、Chroma 和正文 manifest/备份。
- canonical content 使用不可变版本文件，回滚只切换 active pointer。
- 新 Chroma collection 验证后才切流；回滚恢复旧 collection name/generation。
- embedding、upsert 或 generation 验证失败时不切换 active generation；切换后发现异常只回切 SQLite active pointer，不在旧 collection 上反向修补。
- schema migration 必须向前兼容旧读取路径，直到新路径验收通过。
- Git 停止跟踪 runtime files 时只修改索引，不删除本地数据；确认独立备份后再清理历史资产。
- 不允许用 `git reset --hard` 或删除本地 runtime 目录作为回滚手段。

## 17. 明确不做

本轮修复暂不包括：

- 引入分布式数据库、消息队列或微服务拆分
- 为了性能先做无界并发抓取
- 直接更换 embedding/LLM 来掩盖数据不一致
- 自动绕过付费墙或来源访问策略
- 声称历史 retrieval eval 等于当前生产质量
- 在没有 canonical content integrity 证明前继续扩大 corpus

## 18. 实施工作包

| 顺序 | 工作包 | 主要交付物 | 开始下一包的门槛 |
| ---: | --- | --- | --- |
| 1 | Inventory + migration tooling | read-only audit、manifest、dry-run/apply/verify、backup/rollback runbook | 当前数据分类完成且可恢复 |
| 2 | Canonical content contract | 相对/版本路径、hash invariant、loader compatibility、reconciliation | ready rows 全部通过文件/hash 验证 |
| 3 | Chroma v2 rebuild | content-hash-bound metadata、新 collection、reconciliation | eligible 与 index 双向零缺口 |
| 4 | Refresh state machine | run ledger、attempt ledger、terminal semantics、daily gate、lock | partial/failed 不再伪成功 |
| 5 | Fetch reliability | classified errors、bounded retry、session、domain limits、SSRF guard | 故障矩阵测试通过 |
| 6 | Retrieval eligibility | SQLite allowed IDs、时间窗、integrity fail-closed | 非 eligible/stale evidence 被阻断 |
| 7 | Theme research correctness | unknown date、UTC、source classification、partial failures | theme 验收场景通过 |
| 8 | Grounding enforcement | server-owned sources、citation validation | 伪造 source/citation 被拒绝 |
| 9 | Quality optimization | chunking/embedding/rerank experiments、Golden Set | 基于一致 corpus 报告指标 |

每个工作包单独提交、单独验证；不得把运行数据迁移、schema 变更、召回调参和模型升级混在一个不可回滚的大提交中。
