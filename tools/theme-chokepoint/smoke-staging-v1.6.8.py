"""Read-only smoke check for the isolated Theme Chokepoint v1.6 staging scorer."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from event_collector.theme_chokepoint.scoring_v16 import (
    V16ScoringContract,
    score_qualification_barrier,
)


ROOT = Path(__file__).resolve().parents[2]
RELEASE_CONFIG = ROOT / "config" / "theme_chokepoint_release_v1.6.8.json"
CONTRACT = ROOT / "tools" / "theme-chokepoint" / "semantic-task-contract-v1.6.json"


def main() -> None:
    release = json.loads(RELEASE_CONFIG.read_text(encoding="utf-8"))
    staging = release["environments"]["staging"]
    if not staging["enabled"] or staging["execution_mode"] != "shadow":
        raise SystemExit("v1.6.8 staging shadow is not enabled")
    if staging["externally_served_output_percentage"] != 0:
        raise SystemExit("staging smoke refuses externally served v1.6 output")

    contract_hash = sha256(CONTRACT.read_bytes()).hexdigest()
    contract = V16ScoringContract(CONTRACT, expected_sha256=contract_hash)
    qualification = score_qualification_barrier(
        duration_months=18,
        qualification_required=True,
        explicit_no_special_qualification=False,
        process_signals=("type_test", "factory_acceptance_test"),
    )
    if (qualification.rating_min, qualification.rating_max) != (4, 4):
        raise SystemExit("v1.6 deterministic qualification smoke result changed")

    print(
        json.dumps(
            {
                "ok": True,
                "release_id": release["release_id"],
                "execution_mode": staging["execution_mode"],
                "contract_id": contract.contract_id,
                "contract_sha256": contract_hash,
                "qualification_smoke": [
                    qualification.rating_min,
                    qualification.rating_max,
                ],
                "production_enabled": release["environments"]["production"][
                    "enabled"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
