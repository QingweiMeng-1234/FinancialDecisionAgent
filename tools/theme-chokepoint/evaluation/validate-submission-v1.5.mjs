import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { validateSubmission } from "./semantic-validator-v1.5.mjs";

const [taskPath, packPath, resultPath] = process.argv.slice(2);
if (!taskPath || !packPath || !resultPath) {
  console.error("usage: node validate-submission-v1.5.mjs <annotation-task.json> <evidence-pack.json> <annotator-result.json>");
  process.exit(2);
}
const read = (file) => JSON.parse(fs.readFileSync(file, "utf8"));
const sha = (file) => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
let task; let pack; let result;
try { task = read(taskPath); pack = read(packPath); result = read(resultPath); }
catch (error) { console.log(JSON.stringify({ valid: false, errors: [`invalid_json:${error.message}`] }, null, 2)); process.exit(1); }
const errors = [];
if (result.input_hashes?.task_sha256 !== sha(taskPath)) errors.push("input_hash_mismatch:task_sha256");
if (result.input_hashes?.evidence_pack_sha256 !== sha(packPath)) errors.push("input_hash_mismatch:evidence_pack_sha256");
if (result.input_hashes?.machine_contract_sha256 !== task.machine_contract_sha256) errors.push("input_hash_mismatch:machine_contract_sha256");
const semantic = validateSubmission({ task, pack, result }); errors.push(...semantic.errors);
const report = { valid: errors.length === 0, contract_id: task.contract_id, files: { task: path.resolve(taskPath), pack: path.resolve(packPath), result: path.resolve(resultPath) }, sha256: { task: sha(taskPath), pack: sha(packPath), result: sha(resultPath) }, counts: { ordinal: result.ordinal?.length ?? null, hard_gate: result.hard_gate?.length ?? null, state: result.state?.length ?? null }, errors, derived: semantic.derived };
console.log(JSON.stringify(report, null, 2)); process.exit(report.valid ? 0 : 1);

