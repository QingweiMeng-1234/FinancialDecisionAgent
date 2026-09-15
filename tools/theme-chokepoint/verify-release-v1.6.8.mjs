import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const RELEASE_PATH = path.join(
  ROOT,
  "config/theme_chokepoint_release_v1.6.8.json",
);
const SHADOW_POLICY_PATH = path.join(
  ROOT,
  "config/theme_chokepoint_shadow_pool_policy_v1.json",
);
const RELEASE_MANIFEST_PATH = path.join(
  ROOT,
  "outputs/theme-chokepoint-v1.6-r7-evaluation-20260823-001/release-manifest-v1.6.8.json",
);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function bytesAndSha256(filePath) {
  const bytes = await readFile(filePath);
  return {
    bytes,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  };
}

async function readJson(filePath) {
  const { bytes, sha256 } = await bytesAndSha256(filePath);
  return { value: JSON.parse(bytes.toString("utf8")), sha256 };
}

function resolveRepoPath(relativePath) {
  const resolved = path.resolve(ROOT, relativePath);
  const rootWithSeparator = `${ROOT}${path.sep}`;
  assert(
    resolved === ROOT || resolved.startsWith(rootWithSeparator),
    `path escapes repository root: ${relativePath}`,
  );
  return resolved;
}

const releaseRecord = await readJson(RELEASE_PATH);
const release = releaseRecord.value;
assert(release.status === "staging_shadow_authorized", "release is not staging-authorized");
assert(release.environments.staging.enabled === true, "staging must be enabled");
assert(release.environments.staging.execution_mode === "shadow", "staging must run in shadow mode");
assert(
  release.environments.staging.externally_served_output_percentage === 0,
  "staging v1.6 output must not be externally served",
);
assert(release.environments.staging.decision_writes_enabled === false, "staging decision writes must be disabled");
assert(release.environments.production.enabled === false, "production v1.6 must remain disabled");
assert(release.environments.production.traffic_percentage === 0, "production v1.6 traffic must remain zero");
assert(
  release.rollout_stages.filter((stage) => stage.authorized).length === 1 &&
    release.rollout_stages[0].stage === "staging_shadow",
  "only staging shadow may be authorized",
);

const frozen = release.frozen_evaluation;
const finalRecord = await readJson(resolveRepoPath(frozen.final_manifest_path));
assert(finalRecord.sha256 === frozen.final_manifest_sha256, "final evaluation manifest hash mismatch");
assert(finalRecord.value.freeze_authorized === true, "final evaluation did not authorize freeze");
assert(finalRecord.value.failed_automated_gates.length === 0, "final evaluation has failed automated gates");

const freezePath = resolveRepoPath(frozen.freeze_manifest_path);
const freezeRecord = await readJson(freezePath);
assert(freezeRecord.sha256 === frozen.freeze_manifest_sha256, "freeze manifest hash mismatch");
assert(freezeRecord.value.status === "frozen", "freeze manifest is not frozen");
assert(freezeRecord.value.freeze_authorized === true, "freeze manifest is not authorized");

const freezeRoot = path.dirname(freezePath);
for (const asset of freezeRecord.value.locked_assets) {
  const assetPath = path.resolve(freezeRoot, asset.path);
  const rootWithSeparator = `${ROOT}${path.sep}`;
  assert(assetPath.startsWith(rootWithSeparator), `locked asset escapes repository: ${asset.path}`);
  const assetRecord = await bytesAndSha256(assetPath);
  assert(assetRecord.sha256 === asset.sha256, `locked asset hash mismatch: ${asset.path}`);
}

const shadowPolicyRecord = await readJson(SHADOW_POLICY_PATH);
const shadowPolicy = shadowPolicyRecord.value;
assert(shadowPolicy.status === "active", "shadow pool policy is not active");
assert(shadowPolicy.mode === "append_only", "shadow pool must be append-only");
assert(shadowPolicy.mandatory_human_review === false, "shadow pool restored a mandatory Human gate");
assert(shadowPolicy.separation_rules.holdout_items_forbidden === true, "shadow pool must exclude holdout items");
assert(shadowPolicy.separation_rules.gold_answers_forbidden === true, "shadow pool must exclude Gold answers");

const poolRecord = await readJson(resolveRepoPath(shadowPolicy.pool_index_path));
const pool = poolRecord.value;
assert(pool.mode === "append_only", "shadow pool index is not append-only");
assert(pool.holdout_content_included === false, "shadow pool contains holdout content");
assert(pool.gold_answers_included === false, "shadow pool contains Gold answers");
assert(Array.isArray(pool.entries), "shadow pool entries must be an array");
assert(pool.next_sequence === pool.entries.length + 1, "shadow pool sequence is not contiguous");

const releaseManifestRecord = await readJson(RELEASE_MANIFEST_PATH);
const releaseManifest = releaseManifestRecord.value;
assert(releaseManifest.release_id === release.release_id, "release manifest ID mismatch");
assert(
  releaseManifest.release_status === "staging_shadow_ready",
  "release manifest is not staging-shadow ready",
);
assert(
  releaseManifest.deployment_state.production === "not_deployed",
  "release manifest incorrectly claims a production deployment",
);
for (const asset of releaseManifest.release_assets) {
  const assetRecord = await bytesAndSha256(resolveRepoPath(asset.path));
  assert(assetRecord.sha256 === asset.sha256, `release asset hash mismatch: ${asset.path}`);
}

console.log(
  JSON.stringify(
    {
      ok: true,
      release_id: release.release_id,
      release_config_sha256: releaseRecord.sha256,
      freeze_manifest_sha256: freezeRecord.sha256,
      locked_assets_verified: freezeRecord.value.locked_assets.length,
      staging_shadow_enabled: true,
      production_enabled: false,
      shadow_pool_policy_sha256: shadowPolicyRecord.sha256,
      shadow_pool_index_sha256: poolRecord.sha256,
      shadow_pool_entries: pool.entries.length,
      release_manifest_sha256: releaseManifestRecord.sha256,
      release_assets_verified: releaseManifest.release_assets.length,
    },
    null,
    2,
  ),
);
