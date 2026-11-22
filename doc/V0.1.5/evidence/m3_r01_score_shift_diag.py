# -*- coding: utf-8 -*-
"""V015-M3 R01 诊断（只读业务资产）：在 conftest 隔离布局内播种 35 行合成 fixture，
对 M3 前后分数发生变化的金标难例打印 top 候选及逐步 reasons，解释 W/T 变化来源。"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(r"c:\Users\tlyer\project\ListTrans")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
import tests.conftest as cf  # noqa: E402  导入即注入会话隔离环境

assert not (cf.MISSING_CORE_DEPS + cf.MISSING_AUTH_DEPS + cf.MISSING_VECTOR_DEPS)
assert not cf.run_guardrail_checks()["gaps"]
print("[diag] MATERIALS_DB_PATH=", os.environ["MATERIALS_DB_PATH"])

import pandas as pd  # noqa: E402
from app.db_manager import import_materials_from_excel, get_all_materials, init_db  # noqa: E402

materials = json.loads((ROOT / "tests/fixtures/sandbox_materials_v1.json").read_text(encoding="utf-8"))["materials"]
xlsx = Path(os.environ["UPLOAD_DIR"]) / "m3_diag_seed.xlsx"
pd.DataFrame([{"物料分类": m["category"], "物料编码": m["code"], "物料名称": m["name"],
               "规格/型号": m["spec"], "计量单位": "台", "常用品牌": "", "描述": ""} for m in materials]
             ).to_excel(xlsx, index=False, startrow=1)
import_materials_from_excel(str(xlsx), mode="replace", filename=xlsx.name)
init_db()
from app.vector_index import index_manager  # noqa: E402
if not index_manager.load_cache() or index_manager.numpy_embeddings is None:
    index_manager.build_index(get_all_materials(limit=10 ** 9))
from app.query_preprocessor import prepare_query  # noqa: E402
from app.matcher import match_item  # noqa: E402

golden = json.loads((ROOT / "tests/fixtures/golden_set_v1.json").read_text(encoding="utf-8"))
items = golden["items"] if isinstance(golden, dict) and "items" in golden else golden
IDS = sys.argv[1:] or ["GT-0674", "GT-0677", "GT-0676", "GT-0681", "GT-0313"]
for it in items:
    if it["id"] not in IDS:
        continue
    qi = prepare_query(it["name"], it["specification"], it["professional"], category=it.get("supply_category", ""),
                       brand="", unit=it.get("unit", ""), preserve_original=False)
    vec = index_manager.encode_single_query(f"{qi['name']} {qi['specification']}".strip())
    res = match_item(dict(qi), vec)
    print("=" * 80)
    print(it["id"], it["name"], "target=", it["target_codes"])
    print("  q_attrs=", json.dumps((qi.get("parsed_params") or {}).get("attrs", qi.get("attrs", {})), ensure_ascii=False)[:300])
    for c in res.get("top_candidates", [])[:3]:
        print(f"  {c['code']} {c['name']} | {str(c.get('specification'))[:40]} score={c['score']}"
              f" blocked={c.get('hard_blocked')} max={c.get('max_score')}")
        for r in c.get("match_reasons", []):
            print(f"      - {r}")
assert not cf.run_guardrail_checks()["gaps"]
print("[diag] done")
