"""Stdlib-only local worker bootstrap. Invoke by file, avoiding package startup.

The configured trusted callable accepts a JSON task packet and returns a JSON
EvidenceAcquisitionBatch. It must not create child processes. Deadline includes
imports/startup. stdout is the protocol; incidental prints go to discarded stderr.
"""
import contextlib
import importlib
import json
import os
import sys
import threading
import time


def _guard(parent_pid, deadline):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        handle = kernel.OpenProcess(0x00100000, False, parent_pid)
        if not handle:
            os._exit(124)
        alive = lambda: kernel.WaitForSingleObject(handle, 0) == 258
    else:
        alive = lambda: os.getppid() == parent_pid
    end = time.monotonic() + max(0, deadline - time.time())
    while time.monotonic() < end and alive():
        time.sleep(.02)
    os._exit(124)


def main():
    envelope = json.load(sys.stdin)
    threading.Thread(target=_guard,
        args=(envelope["parent_pid"], envelope["deadline"]), daemon=True).start()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            module, name = envelope["entrypoint"].split(":")
            worker = getattr(importlib.import_module(module), name)
            result = worker(envelope["packet"])
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    except TimeoutError:
        encoded = '{"error_code":"PROVIDER_TIMEOUT"}'
    except (ConnectionError, OSError):
        encoded = '{"error_code":"PROVIDER_TRANSPORT_ERROR"}'
    except Exception:
        encoded = '{"error_code":"PROVIDER_BAD_RESPONSE"}'
    sys.stdout.buffer.write(encoded.encode("utf-8"))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
