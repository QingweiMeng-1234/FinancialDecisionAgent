import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const policyPath = path.join(repo, "config", "theme_chokepoint_automated_freeze_thresholds_v3.json");
const manifestPath = path.join(here, "final-evaluation-manifest-v1.6.8.json");
const freezePath = path.join(here, "freeze-manifest-v1.6.8.json");
const previousPath = path.join(here, "final-evaluation-manifest-v1.6.7.json");
const read = (file) => JSON.parse(fs.readFileSync(file, "utf8"));
const sha = (file) => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
const fail = (message) => {
  console.error(message);
  process.exit(1);
};

if (!fs.existsSync(policyPath)) fail("RED: high-risk threshold policy v3 is missing");
if (!fs.existsSync(manifestPath)) fail("v1.6.8 final manifest is missing");
if (!fs.existsSync(freezePath)) fail("v1.6.8 freeze manifest is missing");
if (sha(previousPath) !== "5e3e87eb4bc8d59323405ed9b17feefb77a3265211ba6b2b018f27ff43e41305") {
  fail("immutable v1.6.7 manifest changed");
}

const policy = read(policyPath);
const manifest = read(manifestPath);
const freeze = read(freezePath);
const gates = new Map(manifest.required_automated_freeze_gates.map((gate) => [gate.gate_id, gate]));
const riskGate = gates.get("high_risk_boundary_disagreements");
if (policy.thresholds.high_risk_boundary_disagreements_max !== 1) fail("high-risk maximum is not 1");
if (riskGate?.observed !== 1 || riskGate?.threshold !== 1 || riskGate?.status !== "pass") fail("high-risk gate did not pass at 1/1");
if (manifest.failed_automated_gates.length !== 0) fail("automated gates still fail");
if (manifest.freeze_authorized !== true || manifest.status !== "automated_policy_frozen") fail("final manifest is not frozen");
if (freeze.status !== "frozen" || freeze.freeze_authorized !== true) fail("freeze receipt is not frozen");
if (freeze.final_evaluation_manifest_sha256 !== sha(manifestPath)) fail("freeze receipt final-manifest hash mismatch");
for (const asset of freeze.locked_assets) {
  const file = path.resolve(here, asset.path);
  if (!fs.existsSync(file)) fail(`locked asset missing:${asset.path}`);
  if (sha(file) !== asset.sha256) fail(`locked asset hash mismatch:${asset.path}`);
}
const sidecarPath = path.join(here, "freeze-manifest-v1.6.8.sha256");
const sidecar = fs.readFileSync(sidecarPath, "utf8").trim().split(/\s+/)[0];
if (sidecar !== sha(freezePath)) fail("freeze-manifest sidecar mismatch");

console.log(JSON.stringify({
  valid: true,
  policy_sha256: sha(policyPath),
  final_manifest_sha256: sha(manifestPath),
  freeze_manifest_sha256: sha(freezePath),
  locked_assets: freeze.locked_assets.length,
  high_risk_gate: riskGate,
  freeze_authorized: true,
  repository_clean: freeze.repository_state.clean
}, null, 2));
