"""Build and audit the RC on the authorized local Linux Docker engine."""

import argparse
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import uuid

from packaging.utils import parse_wheel_filename

from v021_preproduction_context import ROOT, layers, run, sha, stage


DOCKER = ["docker", "--context", "desktop-linux"]
BASE = "docker.io/library/python:3.11-slim"
DIGEST = "sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535"


def lock_wheels(path):
    wheels = {}
    for wheel in sorted(path.glob("*.whl")):
        name, version, _, _ = parse_wheel_filename(wheel.name)
        if name in wheels:
            raise ValueError(f"Wheelhouse has multiple candidates for {name}")
        wheels[name] = {"filename": wheel.name, "version": str(version), "sha256": sha(wheel)}
    if "torch" not in wheels or not wheels["torch"]["version"].endswith("+cpu"):
        raise ValueError("A locked CPU torch wheel is required")
    (path / "requirements.lock").write_text(
        "".join(f"{name}=={info['version']} --hash=sha256:{info['sha256']}\n"
                for name, info in sorted(wheels.items())), encoding="utf-8", newline="\n",
    )
    return wheels


def build_definition():
    contents = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    if contents.count("FROM python:3.11-slim\n") != 1:
        raise ValueError("The RC Dockerfile baseline changed")
    directive = """RUN --mount=type=bind,from=wheelhouse,target=/offline,ro \
    python -m pip --disable-pip-version-check --no-cache-dir install \
        --no-index --find-links=/offline --require-hashes --only-binary=:all: -r /offline/requirements.lock && \
    python -m pip --disable-pip-version-check --no-cache-dir install --no-index --no-deps -r /app/requirements.txt && \
    python -m pip check
"""
    converted, replacements = re.subn(r"(?ms)^RUN pip install .*?(?=\n# Allow-list)", lambda _: directive, contents)
    if replacements != 1:
        raise ValueError("Expected a single RC dependency installation block")
    return converted


def main(wheelhouse, output):
    base = json.loads(run([*DOCKER, "image", "inspect", f"{BASE}@{DIGEST}"]))[0]
    if (base["Os"], base["Architecture"]) != ("linux", "amd64"):
        raise ValueError("Base platform differs from the selected local Linux target")
    wheels = lock_wheels(wheelhouse)
    tag = f"listtrans:v0.2.1-local-rc-{uuid.uuid4().hex[:8]}"
    with TemporaryDirectory(prefix="v021-offline-") as directory:
        work = Path(directory)
        context = work / "context"
        context.mkdir()
        head, frozen, expected = stage(context, probe=False)
        definition = context / "Dockerfile"
        definition.write_text(build_definition(), encoding="utf-8", newline="\n")
        # Retain the offline definition as auditable evidence, while keeping the frozen source unchanged.
        output.with_suffix(".Dockerfile").write_bytes(definition.read_bytes())
        # Only the dependency install is transformed. The image is resolved from the cached base
        # and each RUN is confined to a network-disabled build sandbox.
        log = output.with_suffix(".build.log")
        import subprocess

        command = [*DOCKER, "buildx", "build", "--load", "--pull=false", "--no-cache",
                   "--platform", "linux/amd64", "--network", "none", "--progress", "plain",
                   "--build-context", f"wheelhouse={wheelhouse}", "-t", tag, str(context)]
        with log.open("wb") as stream:
            subprocess.run(command, cwd=ROOT, check=True, stdout=stream, stderr=subprocess.STDOUT)
        image = json.loads(run([*DOCKER, "image", "inspect", tag]))[0]
        base_layers, image_layers = base["RootFS"]["Layers"], image["RootFS"]["Layers"]
        if image_layers[:len(base_layers)] != base_layers or image["Architecture"] != "amd64":
            raise ValueError("Built image uses an unexpected base")
        archive = work / "candidate.tar"
        run([*DOCKER, "image", "save", "-o", str(archive), tag])
        audited = layers(archive, app_only=True)
        if len(audited) != len(image_layers):
            raise ValueError("Full image layer count differs")
        files = {}
        for group in audited.values():
            if files.keys() & group.keys():
                raise ValueError("Application payload was replaced in a later image layer")
            files.update(group)
        if files != {f"app/{name}": digest for name, digest in expected.items()}:
            raise ValueError("Image application files differ from the frozen allowlist")
        report = {
            "exit_code": 0, "tag": tag, "image_id": image["Id"], "platform": "linux/amd64",
            "source": {"head": head, "dockerfile_sha256": frozen["Dockerfile"],
                       "dockerignore_sha256": frozen[".dockerignore"],
                       "offline_definition_sha256": sha(definition)},
            "base": {"digest": DIGEST, "id": base["Id"], "layer_diff_ids": base_layers},
            "wheel_count": len(wheels), "wheel_lock_sha256": sha(wheelhouse / "requirements.lock"),
            "wheel_lock": wheels, "audited_layers": len(audited), "layer_diff_ids": image_layers,
            "container_default_cmd": image["Config"]["Cmd"], "application_files": files,
        }
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print({key: report[key] for key in ("exit_code", "image_id", "wheel_count", "audited_layers")})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    main(args.wheelhouse.resolve(), args.out.resolve())
