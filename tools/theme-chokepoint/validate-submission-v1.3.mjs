import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { validateSubmissionSemantics, validateTaskContract } from "./semantic-validator-v1.3.mjs";

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || !value) return null;
    args[key.slice(2)] = value;
  }
  return args;
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function exactIds(actual, expected, label, errors) {
  if (!Array.isArray(actual)) {
    errors.push(`${label}:not_array`);
    return;
  }
  const actualIds = actual.map((item) => item.item_id);
  const expectedIds = expected.map((item) => item.item_id);
  if (actualIds.length !== new Set(actualIds).size) errors.push(`${label}:duplicate_ids`);
  for (const id of expectedIds) if (!actualIds.includes(id)) errors.push(`${label}:missing:${id}`);
  for (const id of actualIds) if (!expectedIds.includes(id)) errors.push(`${label}:unexpected:${id}`);
}

function validateShape(task, pack, result) {
  const errors = [];
  if (task.evidence_pack_id !== pack.evidence_pack_id) errors.push("task_pack_binding_mismatch");
  if (result.task_id && result.task_id !== task.task_id) errors.push("result_task_binding_mismatch");
  if (result.contract_id && result.contract_id !== task.contract_id) errors.push("result_contract_binding_mismatch");
  if (result.evidence_pack_id && result.evidence_pack_id !== pack.evidence_pack_id) errors.push("result_pack_binding_mismatch");
  exactIds(result.ordinal, task.ordinal_tasks ?? [], "ordinal", errors);
  exactIds(result.hard_gate, task.hard_gate_tasks ?? [], "hard_gate", errors);
  exactIds(result.state, task.state_tasks ?? [], "state", errors);
  return errors;
}

const args = parseArgs(process.argv.slice(2));
if (!args?.task || !args?.pack || !args?.result || !args?.["semantic-contract"]) {
  console.error("usage: node validate-submission-v1.3.mjs --task <task.json> --pack <evidence-pack.json> --result <result.json> --semantic-contract <semantic-task-contract.json>");
  process.exit(2);
}

let task;
let pack;
let result;
let canonicalContract;
try {
  task = readJson(args.task);
  pack = readJson(args.pack);
  result = readJson(args.result);
  canonicalContract = readJson(args["semantic-contract"]);
} catch (error) {
  console.log(JSON.stringify({ valid: false, errors: [`invalid_json:${error.message}`] }, null, 2));
  process.exit(1);
}

const shapeErrors = validateShape(task, pack, result);
const arraysValid = [result.ordinal, result.hard_gate, result.state].every(Array.isArray);
const semantic = arraysValid
  ? validateSubmissionSemantics({ task, pack, result, canonicalContract })
  : { errors: validateTaskContract(task, canonicalContract), derived: {} };
const errors = [...shapeErrors, ...semantic.errors];
const report = {
  valid: errors.length === 0,
  semantic_contract_version: task.semantic_contract?.version ?? null,
  files: {
    task: path.resolve(args.task),
    pack: path.resolve(args.pack),
    result: path.resolve(args.result),
    semantic_contract: path.resolve(args["semantic-contract"]),
  },
  sha256: {
    task: sha256(args.task),
    pack: sha256(args.pack),
    result: sha256(args.result),
    semantic_contract: sha256(args["semantic-contract"]),
  },
  counts: {
    ordinal: Array.isArray(result.ordinal) ? result.ordinal.length : null,
    hard_gate: Array.isArray(result.hard_gate) ? result.hard_gate.length : null,
    state: Array.isArray(result.state) ? result.state.length : null,
  },
  derived: semantic.derived,
  errors,
};

console.log(JSON.stringify(report, null, 2));
process.exit(report.valid ? 0 : 1);
