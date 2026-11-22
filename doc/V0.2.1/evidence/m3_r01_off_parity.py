"""Re-exercise both OFF modes using byte-exact recovered M2 Python modules."""

import argparse
from importlib.abc import MetaPathFinder
from importlib.machinery import SourceFileLoader
from importlib.util import spec_from_file_location
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from m3_r01_baseline import ROOT
from m3_r01_rollback import original
import m3_r01_replay


class BaselineLoader(SourceFileLoader):
    def __init__(self, fullname, working_path, archived_path):
        super().__init__(fullname, str(working_path))
        self.archived_path = archived_path

    def get_code(self, fullname):
        return compile(self.archived_path.read_bytes(), self.path, "exec")


class BaselineFinder(MetaPathFinder):
    def __init__(self, archive):
        self.archive = archive

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in ("app.matcher", "app.server"):
            return None
        relative = Path(*fullname.split(".")).with_suffix(".py")
        working_path = ROOT / relative
        loader = BaselineLoader(fullname, working_path, self.archive / relative)
        return spec_from_file_location(fullname, working_path, loader=loader)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("keyword", "vector"))
    parser.add_argument("--projection", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="v021_m2_baseline_") as root:
        original(Path(root))
        if {"app.matcher", "app.server"} & sys.modules.keys():
            raise RuntimeError("Archived modules must be selected before app import")
        sys.meta_path.insert(0, BaselineFinder(Path(root)))
        sys.argv[1:] = ["--mode", args.mode, "--projection", str(args.projection),
                        "--out", str(args.out), "--arm", "off"]
        return m3_r01_replay.main()


if __name__ == "__main__":
    raise SystemExit(main())
