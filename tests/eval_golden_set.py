"""Compatibility CLI for the frozen development evaluator fixtures."""
import io
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.sandbox_evaluator import (  # noqa: E402,F401
    _evaluate_binding_pipeline, _select_sandbox_subsets, by_professional, evaluate, main as _run, metrics,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN_PATH = os.getenv("GOLDEN_SET_FILE", str(FIXTURES / "golden_set_v1.json"))
BASELINE_PATH = str(FIXTURES / "golden_baseline.json")
DETAIL_PATH = os.getenv("GOLDEN_DETAIL_FILE", str(FIXTURES / "golden_last_run.json"))


def main():
    _run(golden_path=GOLDEN_PATH, detail_path=DETAIL_PATH, baseline_path=BASELINE_PATH)


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
