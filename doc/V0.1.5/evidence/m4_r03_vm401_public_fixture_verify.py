"""Read-only public PyPI wheel provenance verification; no install or key output."""
from pathlib import Path
import hashlib
import io
import json
import urllib.request
import zipfile

EV = Path(__file__).resolve().parent
local = json.loads((EV / "m4_r03_vm401_fixture_provenance.json").read_text(encoding="utf-8"))
metadata_url = "https://pypi.org/pypi/ecdsa/0.19.2/json"
with urllib.request.urlopen(metadata_url, timeout=30) as response:
    metadata = json.load(response)
wheel = next(x for x in metadata["urls"] if x["filename"].endswith("py2.py3-none-any.whl"))
assert wheel["url"].startswith("https://files.pythonhosted.org/")
with urllib.request.urlopen(wheel["url"], timeout=30) as response:
    content = response.read()
assert hashlib.sha256(content).hexdigest() == wheel["digests"]["sha256"]
checks = []
with zipfile.ZipFile(io.BytesIO(content)) as archive:
    for item in local["tests"]:
        name = "ecdsa/" + Path(item["source"]).name
        actual = hashlib.sha256(archive.read(name)).hexdigest()
        checks.append({"source": name, "public_wheel_source_sha256": actual,
                       "matches_image_source": actual == item["source_sha256"]})
report = {"primary_release_page": "https://pypi.org/project/ecdsa/0.19.2/", "metadata_url": metadata_url,
          "filename": wheel["filename"], "public_wheel_sha256": hashlib.sha256(content).hexdigest(),
          "source_checks": checks, "verdict": "PASS" if all(x["matches_image_source"] for x in checks) else "FAIL",
          "operation": "download public wheel into memory only; no installation; no key content persisted"}
(EV / "m4_r03_vm401_public_fixture_verify.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report))
