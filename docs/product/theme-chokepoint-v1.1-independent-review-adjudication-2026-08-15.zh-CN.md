# Theme Chokepoint v1.1 独立审查裁决记录

## 1. 裁决结论

| 字段 | 值 |
| --- | --- |
| 审查输入 | `pasted-text.txt`，attachment `592c8d70-516e-41d8-9151-a4cd071dc5a3` |
| 裁决日期 | 2026-08-15 |
| 裁决对象 | 当前v1.1候选、PRD v0.5、Golden v0.2与边界回归 |
| 结论 | 撤回`frozen`；恢复`freeze_candidate` |
| 原因 | 当前边界回归不足以验证谓词拼接、同源重复、正向状态召回、跨主题排序和标注一致性 |

独立报告的大部分P0/P1引用历史v1与Golden v0.1，未读取或未承认当前v1.1/v0.2已经完成的修订。因此不能把全部问题当成当前缺陷；但报告指出的三个现存冻结阻断项成立：谓词级复合条件合同缺失、同源事件/证据家族去重缺失、Golden缺正向跨主题校准。

## 2. P0裁决

| ID | 当前裁决 | 理由与动作 |
| --- | --- | --- |
| P0-1 未审先冻 | **接受** | 单主题反例回归被错误提升为完整冻结证明。合同、PRD、回归和Manifest撤回`frozen`。 |
| P0-2 最高支持档误作精确值 | **当前已解决** | v1.1已经使用`evidence_state + bound_type`；某档全部支持默认只形成下限，exact必须排除相邻更高档。 |
| P0-3 Replacement可被总分补偿 | **当前已解决并加强** | Ready原本已有Performance/Ecosystem等硬门；本轮进一步规定Realized的Performance、Qualification、Capacity、Adoption、Ecosystem和Displacement均须supported且`rating_min>=3`。 |
| P0-4 缺谓词级同范围聚合 | **部分接受** | Scope拆卡和原子`fact_key`已经存在，但复合锚点required/alternative/exclusion谓词尚未结构化。本轮补入条件合同，Condition Coverage不得按比例通过。 |
| P0-5 同一事实重复计分 | **部分接受** | 单一主要高档归属及`floor_only/context_only`已经阻止高档重复；本轮再加入`origin_event_id/evidence_family_id`，防止同一披露的段落、页面和转载伪装独立证据。 |
| P0-6 Persistence量纲/递推不足 | **当前主体已解决** | v1.1第12节已有统一时间桶、有效产出、库存、订单/预测去重、backlog递推、一次性库存释放、node/system relief和约束迁移。后续Golden仍需检验真实长短周期案例。 |
| P0-7 旧证据误报业务迁移 | **当前已解决** | `knowledge_revision`只能产生`assessment_revised`；只有`industry_event`可改变业务趋势。 |

## 3. P1裁决

| ID | 当前裁决 | 理由与动作 |
| --- | --- | --- |
| P1-1 三种Materiality不可互换 | **已解决** | issuer、reportable segment和market presence已经拆分；market presence不得证明公司/分部财务重要性。 |
| P1-2 两个时点不能证明持续份额 | **已解决** | sustained gain要求至少三个同口径时点和两个连续正向区间。 |
| P1-3 搜索完成不能证明无替代者 | **已解决** | Substitute Weakness=4还要求冻结集合中各路线存在明确失败、不合格、取消或产能不足证据；未搜到仍unknown。 |
| P1-4 有序等级被线性等距使用 | **接受为冻结阻断项** | 当前合同虽声明分数不是经济距离或概率，但权重和阈值仍会影响排序。重新冻结必须完成跨主题成对排序和Top-10人工复核校准。 |
| P1-5 Coverage不可实现 | **当前已解决** | v1.1已有Presence/Resolved/Decision完整公式、冲突处理和不变量；本轮增加Condition Coverage且明确它不能通过锚点。 |
| P1-6 Golden只有防过度升级 | **接受** | HBM继续作为边界集；AI数据中心电力/冷却成为必做跨主题集。若仍缺Durable、Realized、Weak Earnings真实正例，必须补真实案例。 |
| P1-7 时间语义/来源独立性不足 | **部分接受** | event/published/retrieved/assessment时间已分离；来源独立性本轮通过事件ID和证据家族补齐。 |
| P1-8 Criticality事件上限过保守 | **当前已解决** | 当前4分可由约束性工程/监管原文支持，不要求具名停产事件。 |

## 4. P2裁决

- 时间边界已经使用互斥开闭区间并通过临界点探针。
- “缺证据不是0”继续成立；本轮补充进度型维度的例外：原文明示未开始或当前产出为零可以支持0，但没搜到进展不能。
- PRD的人类反馈字段已从`true/false/unknown/conflicted`改为`evidence_state + bound_type + interval + predicate/Gate correction`。
- Evidence Card新增`origin_event_id`、`evidence_family_id`和`condition_ids`。

## 5. 对建议锚点的处理

本轮直接采纳：

- Realized Replacement六项不可补偿3分门；
- Share Trajectory与Displacement独立0–4轴；
- 复合锚点按谓词完整性形成下限，未排除更高档时不能exact；
- 同源披露去重和跨期间/跨客户条件不能由同一事件满足。

本轮不采纳或暂不改动：

- 不把“期限内无可行绕行”重新放入Criticality 4分，因为绕行/替代已经由Substitute Weakness独立评估，重复加入会双计；
- 不用存在转录歧义的Amkor `final qualification`把Qualification直接提高到2，继续保持`context_only/unknown`；
- 不立即把Revenue Materiality改成新的0.25%/1%档和强制TTM，因为这会改变产品负责人已确认的1%公司/5%分部门槛，需要单独批准和跨行业回测；
- 不把本轮文档修订包装成权重有效性的经验事实。

## 6. 五个产品问题的当前答案

1. **规范基线：** 已撤回“v1.1已冻结”；当前为`freeze_candidate`。
2. **证据区间：** 已采用`evidence_state + bound_type`，最高完整支持档默认是下限。
3. **Replacement硬门：** Ready保留原五门；Realized增加Performance、Qualification、Capacity、Adoption、Ecosystem、Displacement六项supported且`rating_min>=3`的不可补偿门。
4. **Material Revenue：** 三种口径已拆分；是否以TTM为默认仍待产品负责人批准。
5. **冻结验收：** 已接受跨主题正例、双标注、分歧裁决和混淆/排序校准为必要条件；具体数值验收阈值仍待产品负责人批准。

## 7. 剩余阻断项

在以下事项完成前不得恢复`frozen`：

1. 为四套分数的每个复合锚点建立谓词模板并完成Schema示例；
2. 将新增3条规则加入HBM可执行边界回归，使总数达到15；
3. 完成AI数据中心电力/冷却Golden与所需补充正例；
4. 两名标注员独立盲评同一集合并完成裁决；
5. 产品负责人批准Material Revenue默认期间及标注/状态/排序验收阈值。
