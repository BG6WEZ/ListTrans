"""Audit the frozen RC COPY instructions and ignore rules in a local scratch image."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
from tempfile import TemporaryDirectory
import uuid


EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[2]
CALIBRATION = ("v019_m2_policy.json", "v019_m2_equivalences.json")
RUNTIME_EVAL = ("golden_runtime_v1.json", "report_template_v1.json", "smoke_materials_v1.json")
CANARIES = (
    ".env", "app/auth.db", "app/materials.db-wal", "app/vector_cache/canary",
    "app/calibration_assets/additional.json", "app/calibration_assets/private.pem",
    "app/eval_assets/additional.json", "config/runtime_credentials.json",
    "config/additional.json", "tests/holdout.json", "doc/private.md", "model_offline/canary",
    "data/personnel.csv", "web/private.txt",
)


def run(args, **options):
    return subprocess.check_output(args, cwd=ROOT, **options)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stage(context, *, probe=True):
    load = lambda name: json.loads((EVIDENCE / name).read_bytes())
    dispatch = load("m3_r01_dispatch_baseline.json")
    delivery = load("m3_r01_delivery_sha256.json")
    head = run(["git", "rev-parse", "HEAD"], text=True).strip()
    signed_base = dispatch["head"]
    if signed_base != delivery["git_head"]:
        raise ValueError("RC Git base differs from the signed source")
    run(["git", "merge-base", "--is-ancestor", signed_base, head])
    freeze = {**dispatch["effective_baseline_sha256"], **delivery["files"]}
    for name in ("Dockerfile", ".dockerignore"):
        if sha(ROOT / name) != freeze[name]:
            raise ValueError(f"Signed build instruction changed: {name}")
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    allowed = {f"app/{path.name}" for path in (ROOT / "app").glob("*.py")}
    allowed.update(re.findall(r"^!((?:web|config)/[^/*?\r\n]+)$", ignore, re.M))
    allowed.update(f"app/calibration_assets/{name}" for name in CALIBRATION)
    allowed.update(f"app/eval_assets/{name}" for name in RUNTIME_EVAL)
    allowed.add("requirements.txt")
    expected = {}
    for name in sorted(allowed):
        source = ROOT / name
        if name in {"web/logo.png", "web/logo500.png"}:
            baseline = run(["git", "rev-parse", f"{signed_base}:{name}"], text=True).strip()
            actual = run(["git", "hash-object", "--no-filters", "--", name], text=True).strip()
            if baseline != actual:
                raise ValueError(f"Logo differs from the signed Git base: {name}")
        elif sha(source) != freeze.get(name):
            raise ValueError(f"Image input is absent from or differs from the RC manifest: {name}")
        destination = context / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        expected[name] = sha(destination)
    shutil.copyfile(ROOT / ".dockerignore", context / ".dockerignore")
    if not probe:
        return head, freeze, expected
    for name in CANARIES:
        destination = context / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"synthetic-ignored-path-canary\n")
    commands = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    copies = [line for line in commands if line.startswith("COPY ")]
    if len(copies) != 6:
        raise ValueError("Frozen release Dockerfile COPY instruction count changed")
    (context / "Dockerfile").write_text(
        "FROM scratch\n" + "\n".join(copies) + "\nCOPY . /context-check/\n",
        encoding="utf-8", newline="\n",
    )
    return head, freeze, expected


def layers(path, *, app_only=False):
    manifest, found = None, {}
    with tarfile.open(path, "r|*") as archive:
        for member in archive:
            name = member.name.removeprefix("./")
            if name == "manifest.json":
                manifest = json.load(archive.extractfile(member))
            if not member.isfile() or not (name.endswith("/layer.tar") or name.startswith("blobs/sha256/")):
                continue
            try:
                layer = tarfile.open(fileobj=archive.extractfile(member), mode="r|*")
            except tarfile.ReadError:
                continue
            with layer:
                files = {}
                for entry in layer:
                    if entry.isdir():
                        continue
                    label = PurePosixPath(entry.name.removeprefix("./").lstrip("/")).as_posix()
                    if app_only and not label.startswith("app/"):
                        continue
                    if not entry.isfile() or label in files:
                        raise ValueError(f"Unexpected entry kind or duplicate inscribed path: {label}")
                    files[label] = hashlib.file_digest(layer.extractfile(entry), "sha256").hexdigest()
            found[name] = files
    if manifest is None or len(manifest) != 1 or set(manifest[0]["Layers"]) != found.keys():
        raise ValueError("Could not account for every layer in the image")
    return found


def audit(context_name):
    docker = ["docker", "--context", context_name]
    tag = f"listtrans:v021-context-audit-{uuid.uuid4().hex[:8]}"
    with TemporaryDirectory(prefix="v021-context-") as directory:
        root = Path(directory)
        context = root / "context"
        context.mkdir()
        head, freeze, expected = stage(context)
        run([*docker, "build", "--pull=false", "--no-cache", "--network=none",
             "--progress=plain", "-t", tag, str(context)], stderr=subprocess.STDOUT)
        image = json.loads(run([*docker, "image", "inspect", tag]))[0]
        archive = root / "image.tar"
        run([*docker, "image", "save", "-o", str(archive), tag])
        audited = layers(archive)
        assert len(audited) == len(image["RootFS"]["Layers"])
        seen = {}
        for files in audited.values():
            if seen.keys() & files.keys():
                raise ValueError("Scratch image replaced a file in a later layer")
            seen.update(files)
        app = {name.removeprefix("app/"): value for name, value in seen.items() if name.startswith("app/")}
        contents = {name.removeprefix("context-check/"): value for name, value in seen.items()
                    if name.startswith("context-check/")}
        optional = {"Dockerfile": sha(context / "Dockerfile"), ".dockerignore": sha(context / ".dockerignore")}
        expected_context = {**expected, **{name: digest for name, digest in optional.items() if name in contents}}
        if (app != expected or contents != expected_context or len(seen) != len(app) + len(contents)
                or any(name in contents for name in CANARIES)):
            raise ValueError("Scratch image differs from the expected RC files or admits a canary")
    return {
        "exit_code": 0, "scope": "COPY and ignore-only scratch image, ineligible as final runtime evidence",
        "git_head": head, "rc_dockerfile_sha256": freeze["Dockerfile"],
        "rc_dockerignore_sha256": freeze[".dockerignore"], "image_id": image["Id"],
        "platform": f"{image['Os']}/{image['Architecture']}", "layer_diff_ids": image["RootFS"]["Layers"],
        "audited_layers": len(audited), "source_files": len(expected), "tested_canaries_excluded": len(CANARIES),
        "calibration_sha256": {name: app[f"app/calibration_assets/{name}"] for name in CALIBRATION},
        "files": expected,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="desktop-linux")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    proof = audit(args.context)
    args.out.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print({name: proof[name] for name in ("exit_code", "platform", "source_files", "tested_canaries_excluded")})
