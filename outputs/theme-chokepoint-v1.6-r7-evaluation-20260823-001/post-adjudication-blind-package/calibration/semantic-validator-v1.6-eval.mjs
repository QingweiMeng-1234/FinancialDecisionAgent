import { validateSubmission as validateBase } from "./semantic-validator-base-v1.5.mjs";

export function validateSubmission(args) {
  const report = validateBase(args);
  const allowed = new Set(["direct_measurement", "rule_derived", "human_inferred", "unresolved"]);
  for (const item of args.result.ordinal ?? []) {
    if (!allowed.has(item.bound_derivation)) report.errors.push(`${item.item_id}:bound_derivation`);
    if (item.evidence_state === "unknown" && item.bound_derivation !== "unresolved") report.errors.push(`${item.item_id}:unknown_derivation`);
    if (item.evidence_state !== "unknown" && item.bound_derivation === "unresolved") report.errors.push(`${item.item_id}:resolved_derivation`);
  }
  report.valid = report.errors.length === 0;
  return report;
}
