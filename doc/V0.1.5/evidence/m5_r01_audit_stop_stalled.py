"""Stop only this audit's stalled sandbox pytest child, retaining its log."""
import json
import time
from pathlib import Path
import psutil

roots=[]
for process in psutil.process_iter(['cmdline','create_time']):
    try:
        args=process.info['cmdline'] or []
        if args[-1:]==['full'] and any(s.endswith('m5_r01_audit_run.py') for s in args):
            roots.append(process)
    except (psutil.NoSuchProcess,psutil.AccessDenied):
        pass
targets={}
for root in roots:
    for process in root.children(recursive=True):
        args=process.cmdline()
        if '-m' in args and 'pytest' in args and 'tests/' in args:
            targets[process.pid]=process
stopped=[]
for pid,process in targets.items():
    age=round(time.time()-process.create_time(),3)
    process.terminate()
    stopped.append(dict(pid=pid,elapsed_seconds=age))
report=dict(reason='Sandbox run stalled; separate approved outside-sandbox isolated rerun completed 269P.',
            stopped_pytest_children=stopped,log_preserved='m5_r01_audit_full.log',
            outcome='INTERRUPTED_NOT_PASS')
path=Path(__file__).resolve().with_name('m5_r01_audit_sandbox_interruption.json')
path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
