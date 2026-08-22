import fs from "node:fs";
import path from "node:path";

const [internalPath, externalPath, outputPath] = process.argv.slice(2);
if (!internalPath || !externalPath || !outputPath) {
  console.error("usage: node calculate-agreement-v1.5.mjs <internal.json> <external.json> <output.json>"); process.exit(2);
}
const A = JSON.parse(fs.readFileSync(internalPath, "utf8")); const B = JSON.parse(fs.readFileSync(externalPath, "utf8"));
if (A.task_id !== B.task_id || A.contract_id !== B.contract_id || A.evidence_pack_id !== B.evidence_pack_id) throw new Error("result_binding_mismatch");
const map = (items) => new Map(items.map((item) => [item.item_id, item]));
const am = map(A.ordinal); const bm = map(B.ordinal); const ag = map(A.hard_gate); const bg = map(B.hard_gate); const as = map(A.state); const bs = map(B.state);
const ids = [...am.keys()].filter((id) => bm.has(id)); const rate = (n, d) => d ? n / d : null;
let tuple = 0; let evidence = 0; let bounds = 0; let overlap = 0; const exact = [];
for (const id of ids) {
  const a = am.get(id); const b = bm.get(id);
  if ([a.evidence_state, a.bound_type, a.rating_min, a.rating_max].join("|") === [b.evidence_state, b.bound_type, b.rating_min, b.rating_max].join("|")) tuple++;
  if (a.evidence_state === b.evidence_state) evidence++; if (a.bound_type === b.bound_type) bounds++;
  if (Math.max(a.rating_min, b.rating_min) <= Math.min(a.rating_max, b.rating_max)) overlap++;
  if (a.bound_type === "exact" && b.bound_type === "exact") exact.push([a.rating_min, b.rating_min]);
}
const kappa = (pairs) => { if (!pairs.length) return null; const n = pairs.length; const w = (i, j) => 1 - Math.abs(i - j) / 4; let po = 0; const ca = Array(5).fill(0); const cb = Array(5).fill(0); for (const [a, b] of pairs) { po += w(a, b); ca[a]++; cb[b]++; } po /= n; let pe = 0; for (let i = 0; i < 5; i++) for (let j = 0; j < 5; j++) pe += (ca[i] / n) * (cb[j] / n) * w(i, j); return pe === 1 ? 1 : (po - pe) / (1 - pe); };
const gateIds = [...ag.keys()].filter((id) => bg.has(id)); const stateIds = [...as.keys()].filter((id) => bs.has(id));
const gateAgree = gateIds.filter((id) => ag.get(id).label === bg.get(id).label).length;
const stateValue = (item) => item.primary_state ?? "__WITHHELD__"; const stateAgree = stateIds.filter((id) => stateValue(as.get(id)) === stateValue(bs.get(id))).length;
const high = new Set(["candidate_chokepoint", "strong_candidate_chokepoint"]); const risk = stateIds.filter((id) => high.has(stateValue(as.get(id))) !== high.has(stateValue(bs.get(id))));
const kw = kappa(exact); const gates = { hard_gate: rate(gateAgree, gateIds.length) >= 0.95 ? "pass" : "fail", final_state: rate(stateAgree, stateIds.length) >= 0.90 ? "pass" : "fail", kappa: exact.length >= 10 && kw !== null && kw >= 0.70 ? "pass" : "fail", high_risk_upgrades: risk.length === 0 ? "pass" : "fail" }; gates.overall = Object.values(gates).every((value) => value === "pass") ? "pass" : "fail";
const report = { report_id: "theme-chokepoint-v1.5-agreement", source_results_immutable: true, thresholds: { hard_gate_agreement: 0.95, final_state_agreement: 0.90, exact_exact_pairs: 10, linear_weighted_kappa: 0.70, high_risk_one_sided_upgrades: 0 }, counts: { ordinal_pairs: ids.length, hard_gate_pairs: gateIds.length, state_pairs: stateIds.length, exact_exact_pairs: exact.length }, ordinal_diagnostics: { full_tuple_agreement: rate(tuple, ids.length), evidence_state_agreement: rate(evidence, ids.length), bound_type_agreement: rate(bounds, ids.length), interval_overlap_rate: rate(overlap, ids.length) }, linear_weighted_kappa: kw, hard_gate_agreement: rate(gateAgree, gateIds.length), final_state_agreement: rate(stateAgree, stateIds.length), high_risk_state_disagreement_item_ids: risk, pre_adjudication_labeling_gate: gates };
fs.mkdirSync(path.dirname(outputPath), { recursive: true }); fs.writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`, "utf8"); console.log(JSON.stringify(report, null, 2));

