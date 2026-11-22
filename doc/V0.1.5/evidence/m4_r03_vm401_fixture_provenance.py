"""Read-only provenance checks for public third-party fixtures and CA certificates.
Never emits PEM/key content or executes dependency test modules.
"""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc/V0.1.5/evidence"
raw = json.loads((EV / "m4_r03_vm401_audit_result.json").read_text(encoding="utf-8"))
paths = [x["path"] for x in raw["inspection"]["unexplained_outside_candidates"]]
code = r'''
import ast,base64,hashlib,importlib.metadata as metadata,json,marshal,re,types
from pathlib import Path
paths=PATHS_PLACEHOLDER
d=metadata.distribution('ecdsa')
report={'distribution':d.metadata.get('Name'),'version':d.version,'home':d.metadata.get('Home-page'),'project_urls':d.metadata.get_all('Project-URL'),'tests':[],'ca_candidates':[],'module_candidates':[]}
pattern=br'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{16,}'
def samples(code):
    found=set()
    for c in code.co_consts:
        if isinstance(c,types.CodeType):found.update(samples(c))
        elif isinstance(c,(bytes,str)):
            b=c if isinstance(c,bytes) else c.encode()
            if re.search(pattern,b):found.add(hashlib.sha256(b).hexdigest())
    return sorted(found)
for name in ('test_ecdh','test_keys'):
    source=Path(d.locate_file('ecdsa/'+name+'.py'))
    pyc=source.parent/'__pycache__'/(name+'.cpython-311.pyc')
    content=source.read_bytes()
    record=next(p for p in d.files if str(p)=='ecdsa/'+name+'.py')
    actual=base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip('=')
    a=samples(compile(content,str(source),'exec'))
    b=samples(marshal.loads(pyc.read_bytes()[16:]))
    report['tests'].append({'source':str(source),'source_sha256':hashlib.sha256(content).hexdigest(),
        'source_matches_wheel_record':record.hash.mode=='sha256' and record.hash.value==actual,
        'compiled_sample_hashes':b,'source_sample_hashes':a,'sample_counts':len(b),
        'compiled_samples_equal_source_constants':a==b and bool(a)})
def logical(code):
    return (code.co_code.hex(),code.co_names,code.co_varnames,
            tuple(logical(c) if isinstance(c,types.CodeType) else repr(c) for c in code.co_consts))
for path in paths:
    p=Path('/'+path);target=p.resolve(strict=True);content=target.read_bytes()
    if path.startswith(('usr/local/lib/python3.11/site-packages/openai/','usr/local/lib/python3.11/site-packages/huggingface_hub/')):
        package='openai' if '/openai/' in path else 'huggingface-hub'
        dist=metadata.distribution(package)
        source=target.parent.parent/(target.name.split('.cpython-')[0]+'.py') if target.suffix=='.pyc' else target
        rel=str(source).split('/site-packages/',1)[1]
        entry=next(v for v in dist.files if str(v)==rel)
        source_bytes=source.read_bytes()
        digest=base64.urlsafe_b64encode(hashlib.sha256(source_bytes).digest()).decode().rstrip('=')
        pyc_match=logical(compile(source_bytes,str(source),'exec'))==logical(marshal.loads(content[16:])) if target.suffix=='.pyc' else True
        report['module_candidates'].append({'path':path,'distribution':package,'version':dist.version,'source_sha256':hashlib.sha256(source_bytes).hexdigest(),
            'source_matches_wheel_record':entry.hash.mode=='sha256' and digest==entry.hash.value,'compiled_module_matches_source':pyc_match})
        continue
    public_location=str(target).startswith('/usr/share/ca-certificates/') or str(target)=='/etc/ssl/certs/ca-certificates.crt'
    report['ca_candidates'].append({'path':path,'resolved':str(target),'sha256':hashlib.sha256(content).hexdigest(),
        'public_ca_certificate':public_location and b'-----BEGIN CERTIFICATE-----' in content and b'PRIVATE KEY' not in content})
print(json.dumps(report))
'''.replace("PATHS_PLACEHOLDER", repr(paths))
image = raw["image_id"]
result = subprocess.run(["docker", "run", "--rm", "-i", "--network", "none", "--read-only", "--tmpfs", "/tmp",
                         "--entrypoint", "python", image, "-"], input=code, capture_output=True, text=True, encoding="utf-8", timeout=90, check=True)
report = json.loads(result.stdout.strip().splitlines()[-1])
(EV / "m4_r03_vm401_fixture_provenance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"distribution": report["distribution"], "version": report["version"], "home": report["home"],
                  "project_urls": report["project_urls"], "tests": report["tests"],
                  "ca_count": len(report["ca_candidates"]), "module_count": len(report["module_candidates"]),
                  "all_modules_match_distribution": all(x["source_matches_wheel_record"] and x["compiled_module_matches_source"] for x in report["module_candidates"]),
                  "all_candidates_public_ca": all(x["public_ca_certificate"] for x in report["ca_candidates"])}, ensure_ascii=True))
