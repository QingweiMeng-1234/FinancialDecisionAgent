# Theme Research 检索规则 v1

状态：本地已实现；本规则约束默认 Stage 3 直接检索路径。独立 RPC/shadow
实验不构成默认路径替代，接入默认路径前必须通过同等验证。

## 四条必须保留的行为

1. **按事实缺口搜索。** 六维分别查询需求/订单增速、下游产出依赖、合格供应商
   及产能、认证周期、扩产周期、替代技术等可验证事实。查询携带产品范围、地区
   和评估日期。下一轮更换事实问题或来源路线，不能只重复维度名称。锁定的是
   这个行为，查询措辞和地区适用的来源列表可以按证据缺口改进。
2. **Round Robin 与补证额度。** 每个“节点 × 待补维度”通道每次接收至多一张
   唯一证据卡，缓存同批余下结果，空通道让位；重复事实不计新额度。背景、来源
   含糊或不可评分的卡合计不超过 `floor(max_sources / 2)`，跨轮累积，剩余容量
   留给可评分证据。总卡数、时间、费用和轮数限制仍有效。预算不足时保留未知。
   常规真实验证至少配置两轮机会；单轮仅用于明确标注的诊断，不作为充分检索证明。
3. **来源约束在 API 中落实。** 显式 `site:` 范围须转换为 Tavily
   `include_domains`，返回主机名仍须本地核对，防止相似域名绕过。后续可改用
   企业披露等来源路线；无显式来源限制的查询不继承上一轮过滤。域名可信不等于
   每一条事实可靠，企业资料和监管资料仍须匹配研究范围与时间。
4. **原文解析与证据资格。** 读取原始 HTML 的发布日期元数据及 JSON-LD
   `datePublished`（含 `@graph`）；有 URL/ID 时必须匹配当前文章。其他文章、
   组织信息、修改日期或冲突日期不能替代发布日期。PDF 逐页规范排版空白后再
   计算内容哈希、存储文本、提取引文；不改写实质字符，也不凭 URL 猜日期。
   PDF 抽取必须具备 `requirements.txt` 中已有的 `pypdf>=5.0` 依赖。

## 自动检查

本地和 CI 使用相同入口：

```text
python tools/theme-chokepoint/check-retrieval-policy.py
```

入口运行完整 `test_theme_chokepoint_stage3.py` 和
`test_theme_chokepoint_tavily_provider.py`，包括：

| 规则 | 关键行为测试 |
|---|---|
| 事实查询 | `test_stage3_gap_queries_ask_for_scoring_facts_and_change_on_followup` |
| 公平与预算 | `test_stage3_round_robin_shares_card_budget_across_missing_dimensions`、`test_stage3_reserves_card_budget_for_scoring_evidence_after_context_round` |
| 来源过滤 | `test_site_scoped_search_uses_api_domain_filter_and_rejects_off_domain_hits` |
| 日期与 PDF | `test_original_jsonld_publication_date_is_verified`、`test_jsonld_unrelated_modified_or_conflicting_dates_cannot_verify`、`test_pdf_page_wraps_are_normalized_before_hashing_and_quote_extraction` |

CI 工作流 `Theme Research retrieval policy` 在 PR 和主分支推送时运行，任何测试
失败都会使该检查失败。测试使用受控响应，不需要 Tavily 或模型密钥。
入口只隔离顶层包对无关 RAG/训练模块的预加载，实际检索、解析和持久化代码不替换。
它不验证整个应用的顶层导出初始化；应用集成测试仍单独执行。

新增行为遵守仓库 TDD。修改规则时须同步更新版本、理由和行为测试；禁止通过
跳过测试、放宽日期/引用/评分归属校验来掩盖失败。是否将 CI 设为禁止合并的
required status check，由 GitHub 分支保护另行控制；本地文件不等于远端已启用。

## 结果边界

这些规则防止已知工程退化，不能保证网页存在、外部服务可访问或资料足以评分。
403/429、空结果、未知日期、证据缺口和预算停止必须与“命题已被否定”区分。
搜索、原文抓取、评分和反证分别报告；不能把一次运行的成功步骤拼到另一次结果中。
背景材料不能自动升级，跨维度引用不能自动改属；未知保持未知。

当前评分模型跨维度引用的阻断点仍未修复，见
[检索修复记录](theme-fact-search-repair-20260914.md)。本规则的通过不表示 E2E 通过。

## 本次固化验证

2026-09-14：现有环境和仅安装 CI 所列依赖的全新虚拟环境均通过同一入口的
66 项测试。全新环境清空了 `PYTHONPATH`，未借用工作区额外测试依赖。
另在独立进程中临时恢复“仅搜索维度名称”的旧查询行为，入口返回测试失败
（1 项失败、65 项通过），确认可拦截该退化；该演练没有修改生产源文件。
工作流 YAML、入口编译和差异空白检查通过。

本次为开发约束与 CI 配置固化，没有改动应用行为，不新增 TDD RED/GREEN 声称。
远端 Actions 执行和分支保护尚未验证，文件尚未提交或推送。
