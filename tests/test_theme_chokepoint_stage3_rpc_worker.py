"""Stdlib-only worker fixture: exercises the real subprocess/JSON transport."""
import time


def acquire(packet):
    config = packet["worker_config"]
    field = packet["material_field"]
    if field in config.get("block", []):
        while True:
            time.sleep(.1)
    if field in config.get("fail_first", []) and packet["attempt_number"] == 1:
        raise TimeoutError("fixture transient failure")
    time.sleep(config.get("delays", {}).get(field, 0))
    return {"candidates": config.get("candidates", {}).get(field, []),
            "cost_usd": config.get("cost_usd", 0), "request_receipt_ids": []}
