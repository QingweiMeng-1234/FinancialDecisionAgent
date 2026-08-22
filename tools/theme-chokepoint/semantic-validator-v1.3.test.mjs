import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  validateSubmissionSemantics,
  validateTaskContract,
} from "./semantic-validator-v1.3.mjs";

const dimensions = [
  ["revenue_materiality", 20, ["accounting_revenue_confirmed"]],
  ["volume_realization_leverage", 15, []],
  ["pricing_power", 15, []],
  ["margin_transmission", 20, []],
  ["time_to_revenue", 10, ["accounting_revenue_confirmed"]],
  ["capital_cash_burden", 10, []],
  ["customer_concentration_risk", 5, []],
  ["earnings_persistence", 5, ["multi_period_revenue_confirmed"]],
];

function makeTask() {
  const ordinalTasks = dimensions.map(([dimension], index) => ({
    item_id: `O${index + 1}`,
    case_id: "C1",
    score_family: "earnings",
    dimension,
  }));
  return {
    task_id: "semantic-validator-fixture-v1.3",
    contract_id: "theme-chokepoint-scoring-v1.3",
    evidence_pack_id: "fixture-pack-v1.3",
    cases: [{ case_id: "C1", evidence_ids: ["E-REV", "E-PROD", "E-GENERAL"] }],
    semantic_contract: {
      version: "theme-chokepoint-semantic-task-contract-v1.3",
      score_families: {
        earnings: {
          dimensions: Object.fromEntries(dimensions.map(([name, weight, capabilities]) => [name, {
            weight,
            supported_evidence_any_of: capabilities,
          }])),
          state_policy: {
            type: "earnings_v1.3",
            mandatory_dimensions: ["revenue_materiality", "margin_transmission", "time_to_revenue"],
            eligibility_decision_coverage_min: 0.65,
            hard_fail_rating_max_below: 2,
            material_score_min: 65,
            material_decision_coverage_min: 0.70,
            material_mandatory_rating_min: 2,
            weak_score_max_below: 50,
            states: {
              weak: "weak_earnings_capture",
              material: "material_earnings_path",
              moderate: "moderate_earnings_path"
            }
          }
        }
      }
    },
    ordinal_tasks: ordinalTasks,
    hard_gate_tasks: [
      { item_id: "G1", case_id: "C1", gate: "earnings_revenue_non_unknown", predicate: { op: "ordinal_resolved", dimension: "revenue_materiality" } },
      { item_id: "G2", case_id: "C1", gate: "earnings_margin_min_2", predicate: { op: "ordinal_min", dimension: "margin_transmission", min: 2 } },
    ],
    state_tasks: [{
      item_id: "S1",
      case_id: "C1",
      state_family: "earnings",
      ordinal_item_ids: ordinalTasks.map((item) => item.item_id),
      hard_gate_item_ids: ["G1", "G2"],
    }],
  };
}

function supported(itemId, evidenceIds = ["E-GENERAL"]) {
  return {
    item_id: itemId,
    evidence_state: "supported",
    bound_type: "exact",
    rating_min: 4,
    rating_max: 4,
    primary_evidence_ids: evidenceIds,
    withheld_reason: null,
    rationale: "固定fixture提供完整下限和自然封顶。",
  };
}

function makeResult(task) {
  const ordinal = task.ordinal_tasks.map((item) => supported(
    item.item_id,
    ["revenue_materiality", "time_to_revenue", "earnings_persistence"].includes(item.dimension)
      ? ["E-REV"]
      : ["E-GENERAL"],
  ));
  return {
    ordinal,
    hard_gate: [
      { item_id: "G1", label: "pass", evidence_ids: ["E-REV"], withheld_reason: null, rationale: "收入维度已经由受支持证据解析。" },
      { item_id: "G2", label: "pass", evidence_ids: ["E-GENERAL"], withheld_reason: null, rationale: "利润传导下限达到合同阈值。" },
    ],
    state: [{
      item_id: "S1",
      primary_state: "material_earnings_path",
      achieved_hard_gates: ["G1", "G2"],
      derived_metrics: {
        score_min: 100,
        score_max: 100,
        presence_coverage: 1,
        resolved_coverage: 1,
        decision_coverage: 1,
      },
      withheld_reason: null,
      rationale: "全部加权维度和强制谓词均机械满足。",
    }],
  };
}

const pack = {
  evidence_pack_id: "fixture-pack-v1.3",
  evidence: [
    { evidence_id: "E-REV", claim_capabilities: ["accounting_revenue_confirmed", "multi_period_revenue_confirmed"] },
    { evidence_id: "E-PROD", claim_capabilities: ["commercial_production_stage", "nonzero_ramp_output"] },
    { evidence_id: "E-GENERAL", claim_capabilities: ["general_scoring_evidence"] },
  ],
};

function makeCanonicalContract() {
  return {
    contract_id: "theme-chokepoint-semantic-task-contract-v1.3",
    score_families: {
      earnings: {
        weighted_dimensions: Object.fromEntries(dimensions.map(([name, weight]) => [name, weight])),
        supported_evidence_requirements: {
          revenue_materiality: ["accounting_revenue_confirmed"],
          time_to_revenue: ["accounting_revenue_confirmed"],
          earnings_persistence: ["multi_period_revenue_confirmed"],
        },
      },
    },
  };
}

test("TD-01: state task缺少任一加权维度时拒绝任务合同", () => {
  const task = makeTask();
  task.ordinal_tasks = task.ordinal_tasks.filter((item) => item.dimension !== "capital_cash_burden");
  task.state_tasks[0].ordinal_item_ids = task.ordinal_tasks.map((item) => item.item_id);

  const errors = validateTaskContract(task);
  assert(errors.includes("S1:missing_weighted_dimension:capital_cash_burden"));
});

test("ED-01: revenue production不能支持会计收入类维度", () => {
  const task = makeTask();
  const result = makeResult(task);
  const revenue = result.ordinal.find((item) => item.item_id === "O1");
  revenue.primary_evidence_ids = ["E-PROD"];

  const report = validateSubmissionSemantics({ task, pack, result });
  assert.equal(report.valid, false);
  assert(report.errors.includes("O1:missing_supported_evidence_capability:accounting_revenue_confirmed"));
});

test("TV-01: Hard Gate和State必须与Ordinal机械重算一致", () => {
  const task = makeTask();
  const result = makeResult(task);
  result.ordinal[0] = {
    ...result.ordinal[0],
    evidence_state: "unknown",
    bound_type: "none",
    rating_min: 0,
    rating_max: 4,
    withheld_reason: "insufficient_evidence",
  };

  const report = validateSubmissionSemantics({ task, pack, result });
  assert.equal(report.valid, false);
  assert(report.errors.includes("G1:semantic_label_mismatch:expected_unknown:actual_pass"));
  assert(report.errors.includes("S1:semantic_state_mismatch:expected_withheld:actual_material_earnings_path"));
  assert(report.errors.some((error) => error.startsWith("S1:derived_metric_mismatch:decision_coverage:")));
});

test("完整任务、证据能力、Gate与State一致时通过", () => {
  const task = makeTask();
  const result = makeResult(task);

  assert.deepEqual(validateTaskContract(task), []);
  const report = validateSubmissionSemantics({ task, pack, result });
  assert.equal(report.valid, true, report.errors.join("\n"));
  assert.deepEqual(report.errors, []);
});

test("通用有序状态规则可由分数、Coverage、Ordinal和Hard Gate机械求值", () => {
  const task = makeTask();
  task.semantic_contract.score_families.earnings.state_policy = {
    type: "ordered_rules_v1.3",
    eligibility: {
      op: "and",
      args: [
        { op: "metric_gte", metric: "decision_coverage", value: 0.65 },
        { op: "ordinal_resolved", dimension: "revenue_materiality" },
        { op: "gate_is", gate: "earnings_revenue_non_unknown", label: "pass" },
      ],
    },
    rules: [
      {
        state: "material_earnings_path",
        when: {
          op: "and",
          args: [
            { op: "metric_gte", metric: "score_min", value: 65 },
            { op: "ordinal_min", dimension: "margin_transmission", min: 2 },
          ],
        },
      },
      { state: "moderate_earnings_path", when: { op: "constant", value: true } },
    ],
  };
  const result = makeResult(task);

  const report = validateSubmissionSemantics({ task, pack, result });
  assert.equal(report.valid, true, report.errors.join("\n"));
  assert.equal(report.derived.S1.primary_state, "material_earnings_path");
});

test("任务合同拒绝未知State policy和未声明Gate引用", () => {
  const task = makeTask();
  task.semantic_contract.score_families.earnings.state_policy = {
    type: "ordered_rules_v1.3",
    eligibility: { op: "gate_is", gate: "not_declared", label: "pass" },
    rules: [{ state: "material_earnings_path", when: { op: "constant", value: true } }],
  };
  let errors = validateTaskContract(task);
  assert(errors.includes("S1:state_expression_unknown_gate:not_declared"));

  task.semantic_contract.score_families.earnings.state_policy = { type: "free_text_policy" };
  errors = validateTaskContract(task);
  assert(errors.includes("S1:unsupported_state_policy:free_text_policy"));
});

test("TD-01: task内嵌权重或Revenue能力要求不得偏离canonical机器合同", () => {
  const task = makeTask();
  task.semantic_contract.score_families.earnings.dimensions.revenue_materiality.weight = 21;
  task.semantic_contract.score_families.earnings.dimensions.revenue_materiality.supported_evidence_any_of = [];

  const errors = validateTaskContract(task, makeCanonicalContract());
  assert(errors.includes("semantic_contract:weight_mismatch:earnings:revenue_materiality:expected_20:actual_21"));
  assert(errors.includes("semantic_contract:evidence_requirement_mismatch:earnings:revenue_materiality"));
});

test("canonical机器合同的每个加权评分族必须合计100", () => {
  const canonical = makeCanonicalContract();
  canonical.score_families.earnings.weighted_dimensions.revenue_materiality = 19;

  const errors = validateTaskContract(makeTask(), canonical);
  assert(errors.includes("semantic_contract:canonical_weight_sum:earnings:expected_100:actual_99"));
});

test("CLI对固定task、pack和result输出可封存JSON报告", () => {
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "theme-chokepoint-v13-"));
  const taskPath = path.join(fixtureDir, "task.json");
  const packPath = path.join(fixtureDir, "pack.json");
  const resultPath = path.join(fixtureDir, "result.json");
  const semanticContractPath = path.join(fixtureDir, "semantic-contract.json");
  fs.writeFileSync(taskPath, `${JSON.stringify(makeTask(), null, 2)}\n`);
  fs.writeFileSync(packPath, `${JSON.stringify(pack, null, 2)}\n`);
  fs.writeFileSync(resultPath, `${JSON.stringify(makeResult(makeTask()), null, 2)}\n`);
  fs.writeFileSync(semanticContractPath, `${JSON.stringify(makeCanonicalContract(), null, 2)}\n`);

  const here = path.dirname(fileURLToPath(import.meta.url));
  const run = spawnSync(process.execPath, [
    path.join(here, "validate-submission-v1.3.mjs"),
    "--task", taskPath,
    "--pack", packPath,
    "--result", resultPath,
    "--semantic-contract", semanticContractPath,
  ], { encoding: "utf8" });

  assert.equal(run.status, 0, run.stderr || run.stdout);
  const report = JSON.parse(run.stdout);
  assert.equal(report.valid, true);
  assert.equal(report.semantic_contract_version, "theme-chokepoint-semantic-task-contract-v1.3");
  assert.equal(report.sha256.semantic_contract.length, 64);
  assert.equal(report.derived.S1.primary_state, "material_earnings_path");
});

test("CLI遇到损坏的submission数组时返回JSON错误而不是崩溃", () => {
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "theme-chokepoint-v13-invalid-"));
  const files = {
    task: path.join(fixtureDir, "task.json"),
    pack: path.join(fixtureDir, "pack.json"),
    result: path.join(fixtureDir, "result.json"),
    semantic: path.join(fixtureDir, "semantic-contract.json"),
  };
  const invalidResult = makeResult(makeTask());
  invalidResult.ordinal = {};
  fs.writeFileSync(files.task, `${JSON.stringify(makeTask(), null, 2)}\n`);
  fs.writeFileSync(files.pack, `${JSON.stringify(pack, null, 2)}\n`);
  fs.writeFileSync(files.result, `${JSON.stringify(invalidResult, null, 2)}\n`);
  fs.writeFileSync(files.semantic, `${JSON.stringify(makeCanonicalContract(), null, 2)}\n`);

  const here = path.dirname(fileURLToPath(import.meta.url));
  const run = spawnSync(process.execPath, [
    path.join(here, "validate-submission-v1.3.mjs"),
    "--task", files.task,
    "--pack", files.pack,
    "--result", files.result,
    "--semantic-contract", files.semantic,
  ], { encoding: "utf8" });

  assert.equal(run.status, 1);
  const report = JSON.parse(run.stdout);
  assert.equal(report.valid, false);
  assert(report.errors.includes("ordinal:not_array"));
});
