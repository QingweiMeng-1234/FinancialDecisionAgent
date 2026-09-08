import test from "node:test";
import assert from "node:assert/strict";

import { evaluateGoldenAssertion } from "./hbm-golden-regression-v1.4.mjs";

const cases = [
  ["G14-01 HBM与2.5D必须分Segment", {
    rule: "segment_scope_separation",
    left: { segment_id: "seg_hbm_stack" },
    right: { segment_id: "seg_2_5d_integration" }
  }, true],
  ["G14-02 Samsung量产销售不代理收入、性能或替代", {
    rule: "forbid_capability_implication",
    allowed: ["production", "adoption", "financial_floor"],
    requested: ["material_revenue", "performance_gate", "displacement"]
  }, true],
  ["G14-03 Micron单季收入不代理持续份额或替代", {
    rule: "forbid_capability_implication",
    allowed: ["single_period_material_revenue"],
    requested: ["sustained_share_gain", "displacement"]
  }, true],
  ["G14-04 SK hynix量产准备不等于Mass Production", {
    rule: "readiness_not_production",
    evidence: ["development_complete", "mass_production_readiness"]
  }, true],
  ["G14-05 Amkor既有HDFO量产不得迁移到CoWoS替代关系", {
    rule: "project_scope_non_transfer",
    evidence_scope: "amkor_hdfo_existing_projects",
    target_scope: "amkor_hdfo_replaces_cowos"
  }, true],
  ["G14-06 歧义transcript不得提高Qualification下限", {
    rule: "ambiguous_source_floor",
    source_ambiguity: true,
    human_review_required: true,
    scoring_role: "context_only",
    before: 0,
    proposed: 3
  }, 0],
  ["G14-07 Performance=0不可被总分补偿", {
    rule: "replacement_noncompensatory",
    score_min: 100,
    performance: { evidence_state: "supported", rating_min: 0 },
    requested_state: "realized_replacement"
  }, false],
  ["G14-08 全unknown Segment不能进入Watch", {
    rule: "segment_watch_eligibility",
    dimensions: ["unknown", "unknown", "unknown", "unknown"]
  }, null],
  ["G14-09 客户采购unknown时Defensibility withheld", {
    rule: "defensibility_customer_procurement_gate",
    customer_procurement: "unknown",
    score_min: 100
  }, null],
  ["G14-10 Margin unknown时Earnings withheld", {
    rule: "earnings_margin_gate",
    margin_transmission: "unknown",
    score_min: 100
  }, null],
  ["G14-11 High Defensibility遇Realized Replacement为vulnerable", {
    rule: "competition_mapping",
    defensibility: "high_defensibility",
    challenger: "realized_replacement"
  }, "vulnerable_incumbent"],
  ["G14-12 Knowledge Revision不得触发Strengthening", {
    rule: "industry_event_type",
    event_type: "knowledge_revision"
  }, false],
  ["G14-13 复合锚点缺任一required谓词不得形成下限", {
    rule: "compound_anchor",
    required_predicates: ["supported", "supported", "unknown"],
    condition_coverage: 1
  }, false],
  ["G14-14 同源转载只计一个独立证据", {
    rule: "independent_evidence_count",
    evidence: [
      { origin_event_id: "event-1", evidence_family_id: "family-1" },
      { origin_event_id: "event-1", evidence_family_id: "family-1" },
      { origin_event_id: "event-1", evidence_family_id: "family-1" }
    ]
  }, 1],
  ["G14-15 Realized要求六项强制维度全部达3且resolved", {
    rule: "realized_replacement_gates",
    dimensions: {
      performance: [3, "supported"], qualification: [3, "supported"], capacity: [3, "supported"],
      adoption: [3, "supported"], ecosystem: [3, "supported"], displacement: [3, "unknown"]
    }
  }, false],
  ["G14-16 明确取消商业化必须failed_or_withdrawn", {
    rule: "lifecycle_state",
    commercialization: "withdrawn"
  }, "failed_or_withdrawn"],
  ["G14-17 当前部署与未来GW承诺不可拆分时不证明当前高档", {
    rule: "mixed_current_future",
    separable: false,
    statements: ["being_deployed", "future_gw_commitment"]
  }, { production: false, capacity_3: false, adoption_3: false }],
  ["G14-18 无增长分母的订单只支持Discovery presence", {
    rule: "demand_without_growth_denominator",
    absolute_backlog: true,
    same_scope_growth_denominator: false
  }, { demand_pressure: "unknown", positive_demand_presence: true }],
  ["G14-19 payload拒绝capital_intensity别名", {
    rule: "canonical_payload_fields",
    payload: { capital_intensity: 3 }
  }, "reject:capital_intensity"],
  ["G14-20 earnings_overlay不得改变Segment score与Coverage", {
    rule: "earnings_overlay_isolation",
    segment: { score_min: 55, score_max: 85, coverage: 0.6 },
    earnings_overlay: { score_min: 10, score_max: 20, coverage: 1 }
  }, { score_min: 55, score_max: 85, coverage: 0.6 }],
  ["G14-21 AND/OR三态真值表保留unknown", {
    rule: "three_valued_logic",
    checks: [["and", "pass", "unknown"], ["or", "fail", "unknown"]]
  }, ["unknown", "unknown"]],
  ["G14-22 当前产品与继任产品混评返回原子Scope错误", {
    rule: "atomic_product_scope",
    products: ["current_product", "successor_product"]
  }, "atomic_product_scope_error"]
];

for (const [name, fixture, expected] of cases) {
  test(name, () => {
    assert.deepEqual(evaluateGoldenAssertion(fixture), expected);
  });
}

