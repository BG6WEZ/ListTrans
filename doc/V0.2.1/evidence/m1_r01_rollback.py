"""Verify exact M1 baseline reconstruction and reverse the workbench patch outside the repo."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[2]
WEB = ("web/app.js", "web/index.css", "web/index.html")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run():
    baseline = json.loads((EVIDENCE / "m1_r01_dispatch_baseline.json").read_bytes())
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert head == baseline["head"]
    assert sha((ROOT / baseline["plan_r2_path"]).read_bytes()) == baseline["plan_r2_sha256"]
    frozen = baseline["effective_baseline_sha256"]
    for name, expected in frozen.items():
        if name not in WEB:
            assert sha((ROOT / name).read_bytes()) == expected, name
    patch = b""
    for name in WEB:
        original = subprocess.check_output(["git", "show", f"{head}:{name}"], cwd=ROOT)
        assert sha(original.replace(b"\n", b"\r\n")) == frozen[name], name
        diff = subprocess.check_output(["git", "diff", "--binary", head, "--", name], cwd=ROOT)
        header, delimiter, body = diff.partition(b"@@")
        assert delimiter
        patch += header + delimiter + b"".join(
            line.replace(b"\n", b"\r\n") if line[:1] in (b" ", b"+", b"-") else line
            for line in body.splitlines(keepends=True)
        )
    patch_path = EVIDENCE / "m1_r01_workbench.patch"
    patch_path.write_bytes(patch)
    with TemporaryDirectory(prefix="v021_m1_rollback_") as directory:
        checkout = Path(directory)
        (checkout / "web").mkdir()
        for name in WEB:
            shutil.copyfile(ROOT / name, checkout / name)
        subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--reverse", "--whitespace=nowarn", str(patch_path)],
                       cwd=checkout, check=True)
        restored = {name: sha((checkout / name).read_bytes()) for name in WEB}
        assert restored == {name: frozen[name] for name in WEB}
    (EVIDENCE / "m1_r01_rollback.json").write_text(json.dumps({
        "exit_code": 0, "head": head, "verified_baseline_count": len(frozen),
        "patch_sha256": sha(patch), "byte_identical_reverse": restored,
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    run()
