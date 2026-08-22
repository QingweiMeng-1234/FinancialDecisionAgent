import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));

test("calculates the pre-adjudication v1.5 gates from immutable raw submissions", () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "theme-v15-agreement-"));
  const ordinal = Array.from({ length: 12 }, (_, index) => ({ item_id: `O${index}`, evidence_state: "supported", bound_type: "exact", rating_min: index % 5, rating_max: index % 5 }));
  const result = { task_id: "t", contract_id: "theme-chokepoint-scoring-v1.5", evidence_pack_id: "p", ordinal, hard_gate: [{ item_id: "H1", label: "pass" }, { item_id: "H2", label: "fail" }], state: [{ item_id: "S1", primary_state: "watch_segment" }, { item_id: "S2", primary_state: "not_supported" }] };
  const internal = path.join(temp, "internal.json"); const external = path.join(temp, "external.json"); const output = path.join(temp, "agreement.json");
  fs.writeFileSync(internal, JSON.stringify(result)); fs.writeFileSync(external, JSON.stringify(result));
  const run = spawnSync(process.execPath, [path.join(here, "calculate-agreement-v1.5.mjs"), internal, external, output], { encoding: "utf8" });
  assert.equal(run.status, 0, run.stderr); const report = JSON.parse(fs.readFileSync(output, "utf8"));
  assert.equal(report.counts.exact_exact_pairs, 12); assert.equal(report.linear_weighted_kappa, 1);
  assert.equal(report.hard_gate_agreement, 1); assert.equal(report.final_state_agreement, 1);
  assert.equal(report.pre_adjudication_labeling_gate.overall, "pass");
});

