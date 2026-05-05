import os
import sys
import tempfile
import shutil
import uuid


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
LOCAL_TMP_DIR = os.path.join(ROOT_DIR, ".tmp", "pytest-temp")

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Windows temp folder ACLs in this environment are restricted.
# Force all tempfile usage to a writable repo-local directory.
os.makedirs(LOCAL_TMP_DIR, exist_ok=True)
os.environ["TMP"] = LOCAL_TMP_DIR
os.environ["TEMP"] = LOCAL_TMP_DIR
tempfile.tempdir = LOCAL_TMP_DIR


class WritableTemporaryDirectory:
    """Temporary directory implementation that avoids restricted ACL behavior."""

    def __init__(self, suffix=None, prefix=None, dir=None, ignore_cleanup_errors=False):
        base_dir = dir or LOCAL_TMP_DIR
        os.makedirs(base_dir, exist_ok=True)
        pfx = prefix or "wdtmp-"
        sfx = suffix or ""
        self.name = os.path.join(base_dir, f"{pfx}{uuid.uuid4().hex}{sfx}")
        os.makedirs(self.name, exist_ok=False)
        self._ignore_cleanup_errors = ignore_cleanup_errors

    def __enter__(self):
        return self.name

    def __exit__(self, exc_type, exc, tb):
        self.cleanup()
        return False

    def cleanup(self):
        shutil.rmtree(self.name, ignore_errors=self._ignore_cleanup_errors or True)


tempfile.TemporaryDirectory = WritableTemporaryDirectory
