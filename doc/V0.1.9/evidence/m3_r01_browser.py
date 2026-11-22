"""Host the frozen M2 workbench and M3 workbench against one isolated local service."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
from tempfile import TemporaryDirectory
import time

from m3_r01_quality import ROOT, SWITCHES, V017
from m0_r01_benchmark import block_external_network, make_workbook, stage
from m0_r01_build_governance import read_json, sha

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MATERIALS = (
    ("CDP-AUTO", "复核配电箱", "AL-411 配电箱 400x300x200", "个", "强电", "样品牌"),
    ("CDP-ONE", "复核组合配电箱", "AL-422 配电箱 400x300x200", "个", "强电", "样品牌"),
    ("CDP-TWO", "复核组合配电箱", "AL-422 配电箱 400x300x200", "个", "强电", "样品牌"),
    ("CDP-FAN", "复核轴流风机", "风量 3000m³/h 防爆", "台", "暖通", ""),
    ("CDP-FAN-2", "复核轴流风机", "风量 4000m³/h 防爆", "台", "暖通", ""),
)


def prepare_materials(root):
    import pandas as pd
    from app.db_manager import import_materials_from_excel
    path = root / "synthetic-catalogue.xlsx"
    pd.DataFrame([
        {"物料编码": code, "物料名称": name, "规格/型号": spec, "计量单位": unit,
         "物料分类": category, "常用品牌": brand, "描述": ""}
        for code, name, spec, unit, category, brand in MATERIALS
    ]).to_excel(path, index=False, startrow=1)
    import_materials_from_excel(str(path), mode="replace", filename=path.name)
    code, name, spec, unit, category, brand = MATERIALS[0]
    rules = [{
        "original_name": name, "original_spec": "AL-411", "professional": "强电",
        "bound_materials": [{"code": code, "name": name, "specification": spec, "unit": unit,
                             "brand": brand, "category": category, "qty_ratio": 1.0}],
    }]
    (Path(os.environ["CONFIG_DIR"]) / "auto_binding_rules.json").write_text(
        json.dumps(rules, ensure_ascii=False), encoding="utf-8",
    )
    items = [
        {"id": gid, "name": name, "specification": spec, "unit": unit, "professional": category,
         "supply_category": category, "brand": brand, "qty": 2.0}
        for gid, name, spec, unit, category, brand in (
            ("INPUT-A", MATERIALS[0][1], MATERIALS[0][2], "个", "强电", "样品牌"),
            ("INPUT-B", MATERIALS[1][1], MATERIALS[1][2], "个", "强电", "样品牌"),
            ("INPUT-C", MATERIALS[3][1], "风量 3000m³/h", "台", "暖通", ""),
        )
    ]
    for name, rows in (("native-flow.xlsx", items), ("paired.xlsx", items[:2])):
        (root / name).write_bytes(make_workbook(rows))


def run(backup, output):
    if output.exists():
        raise FileExistsError(output)
    release = read_json(ROOT / "doc/V0.1.9/evidence/m2_r01_coord_release.json")
    baseline = release["m3_frontend_and_docker_baseline_sha256"]
    changed_web = ("web/index.html", "web/index.css", "web/app.js")
    if any(sha(backup / p) != baseline[p] for p in changed_web):
        raise RuntimeError("M2 baseline frontend SHA256 mismatch")
    os.environ.update(SWITCHES)
    os.environ["LISTTRANS_V019_M2_CALIBRATION"] = "on"
    with TemporaryDirectory(prefix="v019_m3_browser_", ignore_cleanup_errors=True) as directory:
        root = Path(directory).resolve()
        stage(root / "service", "keyword", read_json(V017 / "m0_r01_before.json"))
        old_web = root / "m2_web"
        shutil.copytree(ROOT / "web", old_web)
        for p in changed_web:
            shutil.copy2(backup / p, root / "m2_web" / Path(p).name)
        baseline_sha = {f"web/{p.name}": sha(p) for p in old_web.iterdir() if p.is_file()}
        if any(baseline_sha.get(p) != digest for p, digest in baseline.items() if p.startswith("web/")):
            raise RuntimeError("M2 baseline static files differ")
        sys.addaudithook(block_external_network)
        import uvicorn
        from starlette.routing import Mount
        from starlette.staticfiles import StaticFiles
        from app.server import app
        from app.auth_manager import get_auth_connection, get_password_hash

        at = next(i for i, route in enumerate(app.router.routes) if getattr(route, "name", None) == "static")
        app.router.routes.insert(at, Mount("/baseline", app=StaticFiles(directory=old_web, html=True)))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as bound:
            bound.bind(("127.0.0.1", 0))
            port = bound.getsockname()[1]
            service = uvicorn.Server(uvicorn.Config(app, lifespan="on", log_level="warning"))
            thread = threading.Thread(target=service.run, kwargs={"sockets": [bound]}, daemon=True)
            thread.start()
            try:
                end = time.monotonic() + 50
                while not service.started and thread.is_alive() and time.monotonic() < end:
                    time.sleep(.025)
                if not service.started:
                    raise RuntimeError("Isolated service did not start")
                prepare_materials(root)
                password = secrets.token_urlsafe(25)
                with get_auth_connection() as db:
                    db.execute("""INSERT INTO users
                        (username,password_hash,name,role,is_active,must_change_password,account_epoch)
                        VALUES ('review-cdp',?,'复核验收账号','employee',1,0,'cdp-fixture')""",
                               (get_password_hash(password),))
                env = {**os.environ, "M3_CDP_PASSWORD": password}
                subprocess.run(["node", str(Path(__file__).with_name("m3_r01_browser.mjs")), CHROME,
                                f"http://127.0.0.1:{port}", str(root), str(output)], env=env, check=True)
                verify_downloads(root, output)
            finally:
                service.should_exit = True
                thread.join(timeout=20)


def verify_downloads(root, output):
    from openpyxl import load_workbook

    result = read_json(output)
    by_code = {material[0]: material for material in MATERIALS}
    for probe in result["exports"]:
        path = root / "downloads" / probe["file"]
        book = load_workbook(path)
        try:
            sheet = book.active
            data = {r[4].value: r for r in sheet.iter_rows(min_row=2) if r[4].value}
            checked = {}
            for code, qty in probe.pop("bindings").items():
                _, name, spec, unit, _, _ = by_code[code]
                row = data[code]
                actual = [c.value for c in row[4:9]]
                assert actual == [code, name, spec, unit, qty], (code, actual)
                ratio = float(row[8].comment.text.split("：", 1)[1])
                assert ratio == qty / 2
                checked[code] = {"cells_e_to_i": actual, "ratio_in_quantity_cell_note": ratio}
            assert data["INPUT-A"][3].value == "强电" and data["INPUT-A"][10].value == "样品牌"
            probe["xlsx_verified"] = checked
            probe["sha256"] = sha(path)
        finally:
            book.close()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.baseline, args.out.resolve())
