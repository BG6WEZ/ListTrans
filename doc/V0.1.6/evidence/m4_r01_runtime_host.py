"""Run M4 HTTP checks in a separate tree containing only runtime build inputs."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    project = Path(__file__).resolve().parents[3]
    script = Path(__file__).with_name("m4_r01_runtime_smoke.py").read_bytes()
    manifest = set()
    for line in (project / ".dockerignore").read_text(encoding="utf-8").splitlines():
        if line.startswith(("!config/", "!web/", "!app/eval_assets/")) and not line.endswith("/"):
            manifest.add(line[1:])
    manifest.update(p.relative_to(project).as_posix() for p in (project / "app").glob("*.py"))
    with tempfile.TemporaryDirectory(prefix="listtrans_m4_host_tree_") as work:
        target = Path(work)
        for name in sorted(manifest):
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(project / name, dest)
        return subprocess.run([sys.executable, "-u", "-"], input=script, cwd=target, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
