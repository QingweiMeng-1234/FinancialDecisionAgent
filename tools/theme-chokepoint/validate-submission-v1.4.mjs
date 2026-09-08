import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import {
  loadRepositoryVerificationAuthority,
  validateSubmissionSemantics,
  validateTaskContract,
} from "./semantic-validator-v1.4.mjs";

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length;) {
    const key = argv[index];
    if (!key?.startsWith("--")) return null;
    if (key === "--allow-unfrozen-overlay") {
      args["allow-unfrozen-overlay"] = true;
      index += 1;
      continue;
    }
    const value = argv[index + 1];
    if (!value) return null;
    args[key.slice(2)] = value;
    index += 2;
  }
  return args;
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function loadRuntimeGovernance(args, semanticContractPath, errors) {
  const bundlePath = args["runtime-governance-bundle"];
  if (!bundlePath) {
    errors.push("runtime_governance_bundle:required");
    return { bundle: null, sha256: null, decisionTable: null };
  }
  const actualBundleSha256 = sha256(bundlePath);
  const expectedBundleSha256 = args["expected-runtime-governance-bundle-sha256"];
  const expectedBundleId = args["expected-runtime-governance-bundle-id"];
  if (!expectedBundleSha256) {
    errors.push("runtime_governance_bundle:expected_sha256_required");
  } else if (expectedBundleSha256 !== actualBundleSha256) {
    errors.push("runtime_governance_bundle:sha256_mismatch");
  }
  const bundle = readJson(bundlePath);
  if (!expectedBundleId) {
    errors.push("runtime_governance_bundle:expected_id_required");
  } else if (bundle.bundle_id !== expectedBundleId) {
    errors.push("runtime_governance_bundle:id_mismatch");
  }
  const base = path.dirname(path.resolve(bundlePath));
  const resolvedArtifacts = {};
  for (const name of [
    "runtime_overlay",
    "business_fact_decision_table",
    "source_identity_schema",
    "source_identity_policy",
  ]) {
    const artifact = bundle.artifacts?.[name];
    if (!artifact?.path || !/^[0-9a-f]{64}$/.test(artifact?.sha256 ?? "")) {
      errors.push(`runtime_governance_bundle:${name}:invalid_binding`);
      continue;
    }
    const resolved = path.resolve(base, artifact.path);
    const relative = path.relative(base, resolved);
    if (relative.startsWith("..") || path.isAbsolute(relative)) {
      errors.push(`runtime_governance_bundle:${name}:path_escape`);
      continue;
    }
    resolvedArtifacts[name] = resolved;
    if (!fs.existsSync(resolved) || sha256(resolved) !== artifact.sha256) {
      errors.push(`runtime_governance_bundle:${name}:sha256_mismatch`);
    }
  }
  if (resolvedArtifacts.runtime_overlay
    && path.resolve(semanticContractPath) !== resolvedArtifacts.runtime_overlay) {
    errors.push("runtime_governance_bundle:runtime_overlay_path_mismatch");
  }
  let decisionTable = null;
  if (resolvedArtifacts.business_fact_decision_table) {
    decisionTable = readJson(resolvedArtifacts.business_fact_decision_table);
  }
  return { bundle, sha256: actualBundleSha256, decisionTable };
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
  if (!result.task_id) errors.push("result_task_binding_required");
  else if (result.task_id !== task.task_id) errors.push("result_task_binding_mismatch");
  if (!result.contract_id) errors.push("result_contract_binding_required");
  else if (result.contract_id !== task.contract_id) errors.push("result_contract_binding_mismatch");
  if (!result.evidence_pack_id) errors.push("result_pack_binding_required");
  else if (result.evidence_pack_id !== pack.evidence_pack_id) errors.push("result_pack_binding_mismatch");
  exactIds(result.ordinal, task.ordinal_tasks ?? [], "ordinal", errors);
  exactIds(result.hard_gate, task.hard_gate_tasks ?? [], "hard_gate", errors);
  exactIds(result.state, task.state_tasks ?? [], "state", errors);
  return errors;
}

const args = parseArgs(process.argv.slice(2));
if (!args?.task || !args?.pack || !args?.result || !args?.["semantic-contract"]) {
  console.error(
    "usage: node validate-submission-v1.4.mjs --task <task.json> --pack <evidence-pack.json> --result <result.json> --semantic-contract <semantic-task-contract.json> --verification-repository <theme.db> --verification-run-id <run-id> --runtime-governance-bundle <bundle.json> --expected-runtime-governance-bundle-id <id> --expected-runtime-governance-bundle-sha256 <sha256>",
  );
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
const actualSemanticContractSha256 = sha256(args["semantic-contract"]);
const authorizationErrors = [];
const runtimeGovernance = loadRuntimeGovernance(
  args,
  args["semantic-contract"],
  authorizationErrors,
);
let verificationAuthority = null;
if (!args["verification-repository"]) {
  authorizationErrors.push("verification_repository:required");
} else if (!args["verification-run-id"]) {
  authorizationErrors.push("verification_repository:run_id_required");
} else if (runtimeGovernance.bundle && runtimeGovernance.sha256) {
  try {
    verificationAuthority = loadRepositoryVerificationAuthority({
      repositoryPath: args["verification-repository"],
      runId: args["verification-run-id"],
      evidencePackId: pack.evidence_pack_id,
      runtimeGovernanceBundlePath: args["runtime-governance-bundle"],
      expectedRuntimeGovernanceBundleId:
        args["expected-runtime-governance-bundle-id"],
      expectedRuntimeGovernanceBundleSha256:
        args["expected-runtime-governance-bundle-sha256"],
    });
  } catch (error) {
    authorizationErrors.push(`verification_repository:${error.message}`);
  }
}
const expectedSemanticContractSha256 = args["expected-semantic-contract-sha256"];
if (!expectedSemanticContractSha256) {
  authorizationErrors.push("semantic_contract:expected_sha256_required");
} else if (expectedSemanticContractSha256 !== actualSemanticContractSha256) {
  authorizationErrors.push(
    `semantic_contract:sha256_mismatch:expected_${expectedSemanticContractSha256}:actual_${actualSemanticContractSha256}`,
  );
}
if (canonicalContract.status === "implementation_candidate_unfrozen"
  && args["allow-unfrozen-overlay"] !== true) {
  authorizationErrors.push("semantic_contract:unfrozen_overlay_not_authorized");
}
const arraysValid = [result.ordinal, result.hard_gate, result.state].every(Array.isArray);
const semantic = arraysValid
  ? validateSubmissionSemantics({
    task,
    pack,
    result,
    canonicalContract,
    verificationAuthority,
  })
  : { errors: validateTaskContract(task, canonicalContract), derived: {} };
const errors = [...authorizationErrors, ...shapeErrors, ...semantic.errors];
const report = {
  valid: errors.length === 0,
  semantic_contract_version: task.semantic_contract?.version ?? null,
  files: {
    task: path.resolve(args.task),
    pack: path.resolve(args.pack),
    result: path.resolve(args.result),
    semantic_contract: path.resolve(args["semantic-contract"]),
    verification_repository: args["verification-repository"]
      ? path.resolve(args["verification-repository"])
      : null,
    runtime_governance_bundle: args["runtime-governance-bundle"]
      ? path.resolve(args["runtime-governance-bundle"])
      : null,
  },
  sha256: {
    task: sha256(args.task),
    pack: sha256(args.pack),
    result: sha256(args.result),
    semantic_contract: actualSemanticContractSha256,
    verification_repository: args["verification-repository"]
      ? sha256(args["verification-repository"])
      : null,
    runtime_governance_bundle: runtimeGovernance.sha256,
  },
  counts: {
    ordinal: Array.isArray(result.ordinal) ? result.ordinal.length : null,
    hard_gate: Array.isArray(result.hard_gate) ? result.hard_gate.length : null,
    state: Array.isArray(result.state) ? result.state.length : null,
  },
  derived: semantic.derived,
  errors,
  executable_contract: {
    id: canonicalContract.contract_id ?? null,
    sha256: actualSemanticContractSha256,
    governance_status: canonicalContract.status === "implementation_candidate_unfrozen"
      ? "controlled_unfrozen"
      : canonicalContract.status ?? "unknown",
  },
  runtime_governance: {
    bundle_id: runtimeGovernance.bundle?.bundle_id ?? null,
    bundle_sha256: runtimeGovernance.sha256,
  },
};

console.log(JSON.stringify(report, null, 2));
process.exit(report.valid ? 0 : 1);
