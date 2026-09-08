"""Verify the freeze-candidate receipt without changing repository state."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
RECEIPT = ROOT / "tools/theme-chokepoint/coverage-receipt-v1.5.freeze-candidate.json"


def main() -> int:
    payload = json.loads(RECEIPT.read_text(encoding="utf-8"))
    errors = []
    for label, asset in payload["assets"].items():
        path = ROOT / asset["path"]
        actual = sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual != asset["sha256"]:
            errors.append(f"{label}: expected={asset['sha256']} actual={actual}")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if commit != payload["git_commit_sha"]:
        errors.append(
            f"git_commit_sha: expected={payload['git_commit_sha']} actual={commit}"
        )
    if payload["contract_status"] != "freeze_candidate":
        errors.append("receipt must remain freeze_candidate")
    if payload["freeze_decision"] != "not_eligible":
        errors.append("unmet human gates require freeze_decision=not_eligible")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("v1.5 freeze-candidate coverage receipt verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
