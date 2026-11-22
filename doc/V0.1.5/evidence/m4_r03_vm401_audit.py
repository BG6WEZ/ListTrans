"""Independent V-M4-01 evidence audit: immutable image, all layers, no rebuild/download.
Only synthetic accounts in disposable read-only-root/network-none containers are used.
No existing container, image, source, business DB or historical evidence is changed.
"""
from pathlib import Path
import gzip
import hashlib
import io
import json
import re
import secrets
import subprocess
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc/V0.1.5/evidence"
BASE = json.loads((EV / "m4_r03_vm401_audit_baseline.json").read_text(encoding="utf-8"))
DELIVERY = json.loads((EV / "m4_r03_vm401_prod_image_verify.json").read_text(encoding="utf-8"))
IMAGE = BASE["image_id_expected"]
TMP = Path(tempfile.mkdtemp(prefix="m4_vm401_independent_"))
MARKERS = [s.encode() for s in ["SYNTHETIC_ENV_CANARY", "SYNTHETIC_ENV_PROD_CANARY", "SYNTHETIC_AUTH_CANARY",
    "SYNTHETIC_AUTH_WAL_CANARY", "SYNTHETIC_AUTH_BACKUP_CANARY", "SYNTHETIC_BUSINESS_DATA_CANARY",
    "SYNTHETIC_PERSONNEL_CANARY", "SYNTHETIC_USER_SNAPSHOT_CANARY", "SYNTHETIC_CREDENTIAL_CANARY",
    "SYNTHETIC_UNLISTED_CONFIG_CANARY"]]


def docker(args, **kwargs):
    return subprocess.run(["docker"] + args, capture_output=True, text=True, check=True, timeout=300, **kwargs)


class DigestReader:
    def __init__(self, stream):
        self.stream, self.digest = stream, hashlib.sha256()

    def read(self, size=-1):
        chunk = self.stream.read(size)
        self.digest.update(chunk)
        return chunk


def scan_layers(archive, inspected):
    expected_sources = {"app/" + p: h for p, h in DELIVERY["frozen_source_binding"]["source_hashes"].items()
                        if p not in ("Dockerfile", ".dockerignore")}
    configs = sorted(p for p in expected_sources if p.startswith("app/config/"))
    rootfs = inspected["RootFS"]["Layers"]
    files, source_hashes, layers, marker_hits, private_hits, key_like_paths, outside = set(), {}, [], [], [], [], []
    forbidden = []
    with tarfile.open(archive) as outer:
        manifests = json.load(outer.extractfile("manifest.json"))
        matching = []
        for m in manifests:
            config_bytes = outer.extractfile(m["Config"]).read()
            config = json.loads(config_bytes)
            if config.get("rootfs", {}).get("diff_ids") == rootfs:
                matching.append((m, config, hashlib.sha256(config_bytes).hexdigest()))
        assert len(matching) == 1, "Archive must uniquely match the actual inspected rootfs"
        manifest, config, config_sha = matching[0]
        config_name = Path(manifest["Config"]).name.removesuffix(".json")
        assert config_name == config_sha
        assert config["config"]["Cmd"] == inspected["Config"]["Cmd"]
        assert len(manifest["Layers"]) == len(rootfs) == 11
        for index, blob in enumerate(manifest["Layers"]):
            raw = io.BufferedReader(outer.extractfile(blob))
            stream = gzip.GzipFile(fileobj=raw) if raw.peek(2)[:2] == b"\x1f\x8b" else raw
            reader = DigestReader(stream)
            count = 0
            with tarfile.open(fileobj=reader, mode="r|") as layer:
                for member in layer:
                    path = member.name.lstrip("./")
                    low = path.lower()
                    if member.isdir():
                        continue
                    count += 1
                    name = low.rsplit("/", 1)[-1]
                    sensitive_name = (name.startswith(".env") or re.search(r"\.(db|sqlite\d?)(?:$|[.\-])", name)
                                      or name.endswith((".backup", ".csv", ".xls", ".xlsx", ".pem", ".key", ".p12", ".pfx"))
                                      or any(s in low for s in ("credential", "cloud_users_snapshot")))
                    if path.startswith("app/"):
                        if sensitive_name or (path.startswith("app/config/") and path not in configs):
                            forbidden.append({"layer": index, "path": path, "type": member.type.decode(errors="replace")})
                        if path.startswith(("app/data/", "app/doc/", "app/scripts/", "app/tests/")):
                            forbidden.append({"layer": index, "path": path})
                        if "/.wh." in path:
                            removed = path.replace("/.wh.", "/")
                            files.discard(removed)
                            source_hashes.pop(removed, None)
                        elif member.isfile():
                            files.add(path)
                    elif sensitive_name:
                        outside.append({"layer": index, "path": path})
                    if name.endswith((".pem", ".key", ".p12", ".pfx")):
                        key_like_paths.append({"layer": index, "path": path})
                    if not member.isfile():
                        continue
                    content = layer.extractfile(member)
                    digest = hashlib.sha256()
                    tail = b""
                    seen = set()
                    private_seen = False
                    while True:
                        chunk = content.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        buf = tail + chunk
                        for marker in MARKERS:
                            if marker in buf:
                                seen.add(marker.decode())
                        if re.search(br"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{16,}", buf):
                            private_seen = True
                        tail = buf[-512:]
                    marker_hits.extend({"layer": index, "path": path, "marker": m} for m in sorted(seen))
                    if private_seen:
                        private_hits.append({"layer": index, "path": path})
                    if path in expected_sources:
                        source_hashes[path] = digest.hexdigest()
            while reader.read(1024 * 1024):
                pass
            actual = "sha256:" + reader.digest.hexdigest()
            assert actual == rootfs[index], "Layer digest differs from actual image rootfs"
            layers.append({"index": index, "diff_id": actual, "file_entries": count})
            print("LAYER_VERIFIED=" + str(index + 1), flush=True)
    mismatches = [p for p, h in expected_sources.items() if source_hashes.get(p) != h]
    actual_configs = sorted(p for p in files if p.startswith("app/config/"))
    dependency_csv = [x for x in outside if x["path"].endswith(".csv") and
                      re.match(r"usr/local/lib/python3\.11/site-packages/(numpy|scipy|sklearn)/.+/(tests?|data)/", x["path"])]
    public_certs = [x for x in outside if x["path"].endswith("/certifi/cacert.pem")]
    accepted = {x["path"] for x in dependency_csv + public_certs}
    unexplained = [x for x in outside if x["path"] not in accepted]
    return {"layers": layers, "config_sha256": config_sha, "source_files_checked": len(expected_sources),
            "source_mismatches": mismatches, "forbidden_application_paths": forbidden,
            "marker_content_hits_all_layers": marker_hits, "private_key_material_hits_all_layers": private_hits,
            "outside_application_sensitive_name_candidates": outside, "dependency_test_csv_count": len(dependency_csv),
            "public_ca_bundle_count": len(public_certs), "unexplained_outside_candidates": unexplained,
            "key_like_paths": key_like_paths, "config_files": actual_configs, "exact_16_configs": actual_configs == configs}


HTTP_PROBE = r'''
import json,os,time,urllib.request,urllib.error
def call(method,path,body=None,token=None):
    hdr={'Content-Type':'application/json'}
    if token:hdr['Authorization']='Bearer '+token
    req=urllib.request.Request('http://127.0.0.1:18080'+path,method=method,headers=hdr,data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req,timeout=8) as resp:
            raw=resp.read();return resp.status,json.loads(raw) if raw.startswith((b'{',b'[')) else {}
    except urllib.error.HTTPError as e:
        return e.code,json.loads(e.read())
for _ in range(90):
    try:
        if call('GET','/login.html')[0]==200:break
    except Exception:pass
    time.sleep(.5)
else:raise RuntimeError('Image server did not become ready')
r={}
r['login_html']=call('GET','/login.html')[0]
r['unauth_me']=call('GET','/api/auth/me')[0]
r['wrong_password']=call('POST','/api/auth/login',{'username':'admin','password':'SyntheticWrong#2026!'})[0]
status,adm=call('POST','/api/auth/login',{'username':'admin','password':os.environ['ADMIN_PASSWORD']})
r['admin_login']=status;a=adm['access_token']
r['admin_rules']=call('GET','/api/llm-rules',token=a)[0]
status,user=call('POST','/api/admin/users',{'username':'audit_vm401_employee','employee_no':'AUDIT-VM401','role':'employee'},a)
r['create_employee']=status;r['employee_requires_change']=user['must_change_password']
temp=user['temp_password']
status,login=call('POST','/api/auth/login',{'username':user['username'],'password':temp})
r['temporary_login']=status;e=login['access_token'];r['temporary_must_change']=login['must_change_password']
r['limited_me']=call('GET','/api/auth/me',token=e)[0]
r['limited_rules']=call('GET','/api/llm-rules',token=e)[0]
new=os.environ['AUDIT_EMPLOYEE_NEW_PASSWORD']
r['change_employee_password']=call('PUT','/api/auth/profile',{'old_password':temp,'new_password':new},e)[0]
r['old_employee_token']=call('GET','/api/auth/me',token=e)[0]
r['old_temp_password']=call('POST','/api/auth/login',{'username':user['username'],'password':temp})[0]
status,login=call('POST','/api/auth/login',{'username':user['username'],'password':new})
r['new_employee_login']=status;r['new_employee_must_change']=login['must_change_password']
r['new_employee_rules']=call('GET','/api/llm-rules',token=login['access_token'])[0]
r['admin_password_change']=call('PUT','/api/auth/profile',{'old_password':os.environ['ADMIN_PASSWORD'],'new_password':os.environ['AUDIT_ADMIN_NEW_PASSWORD']},a)[0]
r['old_admin_token']=call('GET','/api/auth/me',token=a)[0]
r['old_admin_password']=call('POST','/api/auth/login',{'username':'admin','password':os.environ['ADMIN_PASSWORD']})[0]
status,adm=call('POST','/api/auth/login',{'username':'admin','password':os.environ['AUDIT_ADMIN_NEW_PASSWORD']})
r['new_admin_login']=status
processes=[]
from pathlib import Path
for path in Path('/proc').glob('[0-9]*/cmdline'):
    try:
        args=path.read_bytes().split(b'\0')
        if b'uvicorn' in b' '.join(args[:3]) and b'app.server:app' in args:processes.append([v.decode() for v in args if v])
    except OSError:pass
r['uvicorn_processes']=processes
r['app_db_files']=[str(p) for p in Path('/app/app').glob('*.db*')]
r['config_count']=len(list(Path('/app/config').glob('*.json')))
print(json.dumps(r))
'''


def runtime_check():
    env = {"TESTING": "0", "APP_ENV": "production", "AUTH_DEV_MODE": "false", "LLM_ENABLED": "false",
           "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HOME": "/tmp/hf",
           "MATERIALS_DB_PATH": "/tmp/materials.db", "AUTH_DB_PATH": "/tmp/auth.db", "LOG_DIR": "/tmp/logs",
           "UPLOAD_DIR": "/tmp/uploads", "OUTPUT_DIR": "/tmp/outputs", "VECTOR_CACHE_DIR": "/tmp/vector",
           "BINDING_FEEDBACK_FILE": "/tmp/binding_feedback.json", "FEEDBACK_SAMPLES_FILE": "/tmp/feedback.json",
           "GOLDEN_DETAIL_FILE": "/tmp/golden_last_run.json"}
    isolation = ["--network", "none", "--read-only", "--tmpfs", "/tmp:rw,exec,size=256m"]
    args = []
    for k, v in env.items():
        args += ["-e", k + "=" + v]
    fail = subprocess.run(["docker", "run", "--rm"] + isolation + args + [IMAGE], capture_output=True, text=True, timeout=90)
    fail_report = {"exit_code": fail.returncode, "fatal_security_error": "FATAL SECURITY ERROR" in fail.stdout + fail.stderr}
    env.update({"SECRET_KEY": secrets.token_hex(32), "ADMIN_PASSWORD": "A!9" + secrets.token_urlsafe(20),
                "AUDIT_EMPLOYEE_NEW_PASSWORD": "E!9" + secrets.token_urlsafe(16),
                "AUDIT_ADMIN_NEW_PASSWORD": "N!9" + secrets.token_urlsafe(16)})
    args = []
    for k, v in env.items():
        args += ["-e", k + "=" + v]
    container = docker(["run", "-d"] + isolation + args + [IMAGE]).stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{64}", container), "Invalid new audit container identifier"
    try:
        result = docker(["exec", "-i", container, "python", "-"], input=HTTP_PROBE)
        flow = json.loads(result.stdout.strip().splitlines()[-1])
        metadata = json.loads(docker(["inspect", container]).stdout)[0]
        flow["network_mode"] = metadata["HostConfig"]["NetworkMode"]
        flow["root_read_only"] = metadata["HostConfig"]["ReadonlyRootfs"]
        flow["host_mounts"] = metadata["HostConfig"].get("Binds") or []
        return fail_report, flow
    finally:
        docker(["rm", "-f", container])


def main():
    start = time.monotonic()
    inspected = json.loads(docker(["image", "inspect", IMAGE]).stdout)[0]
    assert inspected["Id"] == IMAGE
    assert inspected["Config"]["Cmd"] == ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "18080", "--workers", "1"]
    archive = TMP / "image.tar"
    docker(["image", "save", "-o", str(archive), IMAGE])
    report = {"image_id": IMAGE, "image_size": inspected["Size"], "rebuild_executed": False,
              "inspection": scan_layers(archive, inspected)}
    (EV / "m4_r03_vm401_audit_result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    history = docker(["history", "--no-trunc", "--format", "{{json .}}", IMAGE]).stdout
    env_history = json.dumps(inspected["Config"].get("Env", [])) + history
    leak_labels = [p for p in ("SECRET_KEY=", "ADMIN_PASSWORD=", "OPENAI_API_KEY=", "DASHSCOPE_API_KEY=", "DEEPSEEK_API_KEY=") if p in env_history]
    report["image_env_history_leak_labels"] = leak_labels
    freeze = docker(["run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp", "--entrypoint", "python", IMAGE, "-m", "pip", "freeze"]).stdout.splitlines()
    report["installed_dependencies"] = sorted(freeze)
    fail, flow = runtime_check()
    report["missing_secret_fail_closed"] = fail
    report["isolated_runtime"] = flow
    expected = {"login_html": 200, "unauth_me": 401, "wrong_password": 400, "admin_login": 200,
                "admin_rules": 200, "create_employee": 200, "employee_requires_change": 1,
                "temporary_login": 200, "temporary_must_change": True, "limited_me": 200, "limited_rules": 403,
                "change_employee_password": 200, "old_employee_token": 401, "old_temp_password": 400,
                "new_employee_login": 200, "new_employee_must_change": False, "new_employee_rules": 200,
                "admin_password_change": 200, "old_admin_token": 401, "old_admin_password": 400, "new_admin_login": 200}
    scan = report["inspection"]
    checks = {"layer_digests_verified": len(scan["layers"]) == 11, "source_48_files": scan["source_files_checked"] == 48 and not scan["source_mismatches"],
              "all_layers_canaries_excluded": not scan["marker_content_hits_all_layers"], "app_forbidden_absent": not scan["forbidden_application_paths"],
              "private_key_material_absent": not scan["private_key_material_hits_all_layers"], "global_candidates_accounted": not scan["unexplained_outside_candidates"],
              "exact_16_configs": scan["exact_16_configs"], "image_env_history_clean": not leak_labels,
              "fail_closed": fail["exit_code"] != 0 and fail["fatal_security_error"], "http_auth_flow": all(flow.get(k) == v for k, v in expected.items()),
              "single_worker": len(flow["uvicorn_processes"]) == 1 and flow["uvicorn_processes"][0][-2:] == ["--workers", "1"],
              "runtime_isolated": flow["network_mode"] == "none" and flow["root_read_only"] and not flow["host_mounts"] and not flow["app_db_files"]}
    report["checks"] = checks
    report["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    report["wall_seconds"] = round(time.monotonic() - start, 3)
    (EV / "m4_r03_vm401_audit_result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["verdict"] == "PASS":
        assert archive.resolve().parent == TMP.resolve() and TMP.name.startswith("m4_vm401_independent_")
        archive.unlink()
    print(json.dumps({"verdict": report["verdict"], "checks": checks, "wall_seconds": report["wall_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
