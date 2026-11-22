"""Stream the local Docker image archive and verify every application layer."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile


def inspect_image(ref):
    root = Path.cwd()
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", ref]))[0]
    expected = {p.as_posix() for p in Path("app").glob("*.py")}
    expected.update(p.as_posix() for p in Path("web").glob("*.*"))
    expected.update(p.as_posix() for p in Path("config").glob("*.json") if p.is_file())
    expected.update({"app/eval_assets/" + name for name in
                     ("golden_runtime_v1.json", "report_template_v1.json", "smoke_materials_v1.json")})
    expected.add("requirements.txt")
    forbidden_workspaces = {"tests", "doc", "data", "model_offline", ".local-archive", "logs"}
    flagged = re.compile(r"(^\.env|\.db($|[.-])|\.sqlite[0-9]?$|\.(csv|xls[xm]?|pem|key|pfx|p12|jks)$|credential|password|secret|人员清单)", re.I)
    layers, violations, all_payloads = {}, [], {}
    manifest = None
    proc = subprocess.Popen(["docker", "image", "save", ref], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                name = member.name.removeprefix("./")
                if name == "manifest.json":
                    manifest = json.load(archive.extractfile(member))
                if not (name.endswith("/layer.tar") or name.startswith("blobs/sha256/")):
                    continue
                data = archive.extractfile(member)
                try:
                    layer = tarfile.open(fileobj=data, mode="r|*")
                except tarfile.ReadError:
                    continue  # image config/OCI manifest JSON blobs
                layer_paths = {}
                file_count = 0
                with layer:
                    for item in layer:
                        p = PurePosixPath(item.name.removeprefix("./").lstrip("/"))
                        if not item.isdir():
                            file_count += 1
                        if not p.parts or p.parts[0] != "app":
                            continue  # standard base/dependency files outside the project workdir
                        if any(part in forbidden_workspaces and part not in {"model_offline", "logs"}
                               for part in p.parts[1:]):
                            violations.append(p.as_posix())
                        if item.isdir():
                            continue
                        rel = PurePosixPath(*p.parts[1:]).as_posix()
                        if (any(part in forbidden_workspaces for part in p.parts[1:])
                                or flagged.search(p.name) or rel not in expected or not item.isfile()):
                            violations.append(p.as_posix())
                        if not item.isfile():
                            continue
                        stream = layer.extractfile(item)
                        h = hashlib.sha256()
                        for chunk in iter(lambda: stream.read(1 << 20), b""):
                            h.update(chunk)
                        layer_paths[rel] = h.hexdigest()
                all_payloads.update(layer_paths)
                layers[name] = {"files_total": file_count, "app_files": layer_paths}
    finally:
        proc.stdout.close()
        code = proc.wait()
    assert code == 0 and manifest and len(manifest) == 1
    assert set(manifest[0]["Layers"]) == set(layers), (manifest[0]["Layers"], sorted(layers))
    assert len(layers) == len(image["RootFS"]["Layers"])
    assert not violations, sorted(set(violations))
    assert set(all_payloads) == expected, (sorted(expected - set(all_payloads)), sorted(set(all_payloads) - expected))
    for name, digest in all_payloads.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    print(json.dumps({"image_id": image["Id"], "rootfs_diff_ids": image["RootFS"]["Layers"],
                      "layers": layers, "application_files": len(all_payloads), "violations": violations,
                      "source_hashes_match": True, "cmd": image["Config"]["Cmd"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    inspect_image(sys.argv[1])
