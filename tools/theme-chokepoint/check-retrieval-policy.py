"""Run the offline retrieval regression gate without unrelated RAG preloading."""

from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec
from pathlib import Path
import os
import sys


ROOT = Path(__file__).resolve().parents[2]


def main():
    os.chdir(ROOT)
    # Load actual submodules from src; avoid event_collector.__init__ importing
    # the unrelated embedding/training stack into this focused CI check.
    spec = ModuleSpec("event_collector", loader=None, is_package=True)
    spec.submodule_search_locations = [str(ROOT / "src/event_collector")]
    sys.modules["event_collector"] = module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    import pytest

    return pytest.main([
        "tests/test_theme_chokepoint_stage3.py",
        "tests/test_theme_chokepoint_tavily_provider.py",
        "-q",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
