"""V015-M3 R01 改动前后对照：M2 R01 三规则报告(M3 改动前 matcher) vs M3 R01 三规则报告。
只读两份既有证据 JSON，输出对照 JSON；不运行模型、不写业务资产。"""
import hashlib
import json
import sys
from pathlib import Path

EV = Path(r"c:\Users\tlyer\project\ListTrans\doc\V0.1.5\evidence")
pre_p, post_p = EV / "m2_r01_gate_reports.json", EV / "m3_r01_gate_reports.json"
out_p = EV / "m3_r01_pre_post_compare.json"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


pre = json.loads(pre_p.read_text(encoding="utf-8"))
post = json.loads(post_p.read_text(encoding="utf-8"))
result = {
    "task": "V015-M3 R01 改动前后对照（同 35 行合成 fixture、同固定金标、同本地离线模型、LLM_ENABLED=false）",
    "pre_report": {"path": pre_p.name, "sha256": sha(pre_p), "generated": pre.get("generated"),
                   "matcher_sha256_note": "M2 R01 交付时 matcher=48780dfe…e796（即 M3 改动前）"},
    "post_report": {"path": post_p.name, "sha256": sha(post_p), "generated": post.get("generated"),
                    "matcher_sha256_note": "M3 R01 当前 matcher=fe08ca6a…a946"},
    "cases": {},
}
for name in ("harmful", "beneficial", "neutral"):
    a, b = pre["reports"][name], post["reports"][name]
    ca = {c["id"]: c for c in a["score_comparisons"]}
    cb = {c["id"]: c for c in b["score_comparisons"]}
    diffs = []
    for gid in sorted(set(ca) | set(cb)):
        x, y = ca.get(gid), cb.get(gid)
        if x is None or y is None:
            diffs.append({"id": gid, "only_in": "pre" if y is None else "post"})
            continue
        row = {"id": gid}
        changed = False
        for side in ("baseline", "candidate"):
            for k in ("target_codes", "T", "W", "margin"):
                if x[side].get(k) != y[side].get(k):
                    row[f"{side}.{k}"] = [x[side].get(k), y[side].get(k)]
                    changed = True
        if changed:
            diffs.append(row)
    result["cases"][name] = {
        "passed": [a["passed"], b["passed"]],
        "targeted_count": [a["targeted_count"], b["targeted_count"]],
        "improved_count": [a["improved_count"], b["improved_count"]],
        "degraded_count": [a["degraded_count"], b["degraded_count"]],
        "degraded_from_top1": [a["degraded_from_top1"], b["degraded_from_top1"]],
        "violations": [[(v["type"], v["id"]) for v in a["violations"]],
                       [(v["type"], v["id"]) for v in b["violations"]]],
        "snapshot_errors": [a["snapshot"]["errors"], b["snapshot"]["errors"]],
        "score_comparisons": [len(ca), len(cb)],
        "changed_rows": diffs,
    }
out_p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=1))
