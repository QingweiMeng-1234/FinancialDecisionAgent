"""Hash-pinned runtime governance for the Theme Chokepoint evidence boundary."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path


REQUIRED_GOVERNANCE_ARTIFACTS = frozenset(
    {
        "runtime_overlay",
        "business_fact_decision_table",
        "source_identity_schema",
        "source_identity_policy",
    }
)
CANONICAL_GOVERNANCE_BUNDLE_ID = "theme-chokepoint-evidence-trust-runtime-v1"
CANONICAL_GOVERNANCE_BUNDLE_SHA256 = (
    "de5e95275285132e9d147b3d586056fbf3b75de2617b5423d28a6bc320c59e63"
)
CANONICAL_SOURCE_IDENTITY_POLICY_ID = (
    "theme-chokepoint-source-identity-policy-v1"
)
CANONICAL_SOURCE_IDENTITY_POLICY_SHA256 = (
    "8bf45510ca03a416f1a3d43105cb5d129547c7368583790d157dc20afae43a69"
)


class RuntimeGovernanceBundle:
    """Load one immutable bundle and verify every governed child by SHA-256."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        expected_sha256: str | None = None,
    ) -> None:
        if expected_sha256 is None:
            raise ValueError("expected governance bundle SHA-256 is required")
        if path is None:
            path = (
                Path(__file__).resolve().parents[3]
                / "tools"
                / "theme-chokepoint"
                / "runtime-governance-bundle-v1.json"
            )
        bundle_path = Path(path).resolve()
        raw = bundle_path.read_bytes()
        actual_sha256 = sha256(raw).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                "runtime governance bundle SHA-256 mismatch: "
                f"expected={expected_sha256} actual={actual_sha256}"
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("runtime governance bundle is invalid JSON") from error
        if (
            payload.get("schema_version")
            != "theme-chokepoint-runtime-governance-bundle-v1"
        ):
            raise ValueError("runtime governance bundle schema is not supported")
        if payload.get("status") != "frozen_for_implementation":
            raise ValueError("runtime governance bundle is not frozen for implementation")
        bundle_id = payload.get("bundle_id")
        if not isinstance(bundle_id, str) or not bundle_id.strip():
            raise ValueError("runtime governance bundle ID is required")
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ValueError("runtime governance artifacts are required")
        if set(artifacts) != REQUIRED_GOVERNANCE_ARTIFACTS:
            raise ValueError("runtime governance artifact set is not canonical")

        base_path = bundle_path.parent
        verified_paths: dict[str, Path] = {}
        artifact_sha256: dict[str, str] = {}
        for name in sorted(REQUIRED_GOVERNANCE_ARTIFACTS):
            entry = artifacts[name]
            if not isinstance(entry, dict):
                raise ValueError(f"{name} governance entry is invalid")
            relative_path = entry.get("path")
            expected_child_sha256 = entry.get("sha256")
            if (
                not isinstance(relative_path, str)
                or not relative_path.strip()
                or Path(relative_path).is_absolute()
            ):
                raise ValueError(f"{name} governance path must be relative")
            child_path = (base_path / relative_path).resolve()
            try:
                child_path.relative_to(base_path)
            except ValueError as error:
                raise ValueError(f"{name} governance path escapes bundle directory") from error
            if (
                not isinstance(expected_child_sha256, str)
                or len(expected_child_sha256) != 64
            ):
                raise ValueError(f"{name} SHA-256 is required")
            actual_child_sha256 = sha256(child_path.read_bytes()).hexdigest()
            if actual_child_sha256 != expected_child_sha256:
                raise ValueError(
                    f"{name} SHA-256 mismatch: "
                    f"expected={expected_child_sha256} actual={actual_child_sha256}"
                )
            verified_paths[name] = child_path
            artifact_sha256[name] = actual_child_sha256

        self.path = bundle_path
        self.bundle_id = bundle_id
        self.sha256 = actual_sha256
        self.artifact_paths = verified_paths
        self.artifact_sha256 = artifact_sha256

    def artifact_path(self, name: str) -> Path:
        try:
            return self.artifact_paths[name]
        except KeyError as error:
            raise ValueError(f"unknown governance artifact: {name}") from error
