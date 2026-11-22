"""Run the serial regression suite with a Train/Dev runtime evaluation asset."""

import hashlib
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "doc/V0.2.0/evidence"))
import m0_r01_data
from m0_r01_data import GOLDEN, GOVERNANCE


class Projection:
    def __init__(self, path, digest):
        self.path = str(path)
        self.digest = digest

    @pytest.hookimpl(wrapper=True)
    def pytest_load_initial_conftests(self):
        yield
        os.environ.update(GOLDEN_SET_FILE=self.path, GOLDEN_SET_SHA256=self.digest)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item):
        if item.name in (
            "test_matrix3_and_4_guard_isolation_five_benefit_classes_and_neutral_approval",
            "test_sandbox_fixture_rank_observable",
        ):
            def projected_open(path, *args, **kwargs):
                if not isinstance(path, int) and Path(path).resolve() == GOLDEN:
                    path = self.path
                return open(path, *args, **kwargs)

            with patch.object(item.module, "open", projected_open, create=True):
                return (yield)
        return (yield)


def main():
    items, labels = m0_r01_data.read_projection()
    with TemporaryDirectory(prefix="v021_regression_", ignore_cleanup_errors=True) as directory:
        path = Path(directory) / "train_dev.json"
        payload = json.dumps({"meta": {"version": "v1-train-dev"}, "items": items},
                             ensure_ascii=False).encode("utf-8")
        path.write_bytes(payload)
        blocked = {os.path.normcase(str(path.resolve())) for path in (GOLDEN, GOVERNANCE)}

        def reject_full_asset(event, args):
            if event == "open" and isinstance(args[0], (str, bytes)):
                target = os.path.normcase(os.path.abspath(os.fsdecode(args[0])))
                if target in blocked:
                    raise PermissionError("Full golden asset blocked during V021 pytest verification")

        sys.addaudithook(reject_full_asset)
        # Verifying the original three-way split necessarily decodes Test.
        exclude = "not test_frozen_assignment_covers_same_sheet_and_near_query_edges"
        with patch.object(m0_r01_data, "read_projection", side_effect=lambda: deepcopy((items, labels))):
            return pytest.main(
                ["-q", "tests", "-k", exclude, *sys.argv[1:]],
                plugins=[Projection(path, hashlib.sha256(payload).hexdigest())],
            )


if __name__ == "__main__":
    sys.exit(main())
