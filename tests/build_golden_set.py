# -*- coding: utf-8 -*-
"""
黄金测试集构建脚本 (Golden Dataset Builder)

数据来源：doc/真集 下 6 组「原始清单 / 完成清单」配对文件。

标注原理：
  「完成清单」采用 人工费(父) + 材料费(子) 的层级结构。
    - 人工费行 = 工程清单条目（对应原始清单的一行），是 Query
    - 其下紧随的材料费行 = 造价员人工选定的标准物料，其「清单编码」即 target_code
  Query 文本优先取自「原始清单」（避免完成清单被人工加工过的文本泄漏答案）。

输出：tests/fixtures/golden_set_v1.json
"""
import os
import re
import io
import gc
import sys
import json
import time
import sqlite3
import difflib
import collections
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = r"c:\Users\wangd\project\ListTrans"
TRUTH_DIR = os.path.join(ROOT, "doc", "真集")
DB_PATH = os.path.join(ROOT, "app", "materials.db")
OUT_PATH = os.path.join(ROOT, "tests", "fixtures", "golden_set_v1.json")

DOT_CODE = re.compile(r"^\d+(\.\d+){2,3}$")

# Sheet 名 -> 规范专业标签
PROF_MAP = {
    "装饰专业": "装饰", "装饰": "装饰",
    "强电专业": "强电", "强电": "强电", "强电合并版本": "强电",
    "弱电专业": "弱电", "弱电": "弱电",
    "给排水专业": "给排水", "给排水": "给排水", "模板 (2)": "给排水",
    "医气专业": "医用气体", "医气": "医用气体", "医气 (目标成本)": "医用气体",
    "医气工程": "医用气体", "大楼医气": "医用气体", "备用清单": "医用气体",
    "暖通工程发包": "暖通", "多联机特安清单": "暖通",
    "纯水工程发包": "纯水",
    "自控系统": "自控", "自控配线": "自控",
    "PVC、橡胶卷材": "装饰", "打胶": "装饰", "自动门": "装饰",
    "手术室": "洁净装饰", "无影灯": "洁净装饰",
}
# Sheet 名无法识别时（如 Sheet1），按工程清单编码字母前缀兜底判定专业
PREFIX_PROF = {
    "ZS": "装饰", "TJ": "装饰", "QD": "强电", "RD": "弱电",
    "JPS": "给排水", "NT": "暖通", "YQ": "医用气体", "SB": "设备",
}


def prof_of(sheet, eng_code):
    p = PROF_MAP.get(sheet)
    if p:
        return p
    m = re.match(r"[A-Za-z]+", eng_code or "")
    return PREFIX_PROF.get(m.group(0).upper(), "其他") if m else "其他"


# ---------- 标注质检：真集存在造价员填表错行，需自动识别可疑标注 ----------
CN = re.compile(r"[\u4e00-\u9fa5]")
# 计量单位大类：错行常伴随单位大类冲突（如「个」的阀门配到按「m」计的电线）
UNIT_CLASS = {
    "m": "L", "米": "L", "延长米": "L",
    "m2": "A", "㎡": "A", "平方米": "A",
    "m3": "V", "m³": "V", "立方米": "V",
    "个": "C", "只": "C", "台": "C", "套": "C", "组": "C", "件": "C",
    "根": "C", "樘": "C", "副": "C", "块": "C", "对": "C", "座": "C", "把": "C",
    "kg": "W", "t": "W", "吨": "W", "千克": "W",
}


def char_set(s):
    return set(CN.findall(str(s)))


def name_overlap(q_name, t_names):
    """Query 名称与目标名称的中文字符 Jaccard 最大值"""
    a = char_set(q_name)
    if not a:
        return 1.0
    best = 0.0
    for t in t_names:
        b = char_set(t)
        if not b:
            continue
        best = max(best, len(a & b) / len(a | b))
    return best


def unit_conflict(q_unit, t_unit):
    a = UNIT_CLASS.get(key_of(q_unit).lower())
    b = UNIT_CLASS.get(key_of(t_unit).lower())
    return bool(a and b and a != b)


def qc_check(q_name, q_spec, q_unit, targets):
    """返回 (suspect: bool, reason: str)"""
    t_names = [t["name"] for t in targets] + [t["material_type"] for t in targets] + \
              [t.get("_db_name", "") for t in targets]
    ov = name_overlap(q_name, t_names)
    # 名称/材料类型完全无中文字符交集 -> 高度可疑（典型错行）
    if ov <= 0.0:
        blob = key_of(q_spec) + key_of(q_name)
        # 规格文本中若出现目标名称的连续片段，仍视为合理（如「配线」->「WDZC-BVR 6」）
        for t in targets:
            tk = key_of(t["name"])
            if tk and len(tk) >= 2 and tk in blob:
                return False, ""
            # 型号类目标：英文/数字型号出现在 Query 规格中
            m = re.findall(r"[A-Za-z]{2,}[-\w]*", str(t["name"]))
            for tok in m:
                if len(tok) >= 3 and tok.upper() in blob.upper():
                    return False, ""
        return True, f"名称零交集(overlap={ov:.2f})"
    if ov < 0.15 and all(unit_conflict(q_unit, t["unit"]) for t in targets):
        return True, f"名称低交集({ov:.2f})且单位大类冲突"
    return False, ""
# 完成清单文件 -> 该文件所属项目（用于同项目内配对，避免跨项目串号）
PROJECT_OF = {
    "01.装饰-完成清单.xlsx": "P1", "02.强电-完成清单.xlsx": "P1",
    "03.弱电-完成清单.xlsx": "P1", "04.给排水-完成清单.xlsx": "P1",
    "05.动力气体-完成清单.xlsx": "P1", "07.利川项目-完成清单.xlsx": "P2",
    "01.装饰-原始清单.xlsx": "P1", "02.强电-原始清单.xlsx": "P1",
    "03.弱电-原始清单.xlsx": "P1", "04.给排水-原始清单.xlsx": "P1",
    "06.动力气体-原始清单.xlsx": "P1", "07.利川项目-原始清单.xlsx": "P2",
}


def norm(s):
    if s is None:
        return ""
    s = str(s)
    if s.strip().lower() in ("nan", "none", "nat"):
        return ""
    return s.strip()


def key_of(s):
    return re.sub(r"\s+", "", norm(s))


def find_header(df, must_keys, limit=15):
    for i in range(min(limit, len(df))):
        row = [key_of(x) for x in df.iloc[i].tolist()]
        if all(any(k in c for c in row) for k in must_keys):
            return i
    return None


# ==================== 1. 解析原始清单（Query 源） ====================
def load_raw_items():
    items = []
    for fname in sorted(os.listdir(TRUTH_DIR)):
        if "原始清单" not in fname or not fname.endswith(".xlsx"):
            continue
        proj = PROJECT_OF.get(fname, "?")
        fpath = os.path.join(TRUTH_DIR, fname)
        xl = pd.ExcelFile(fpath)
        for sname in xl.sheet_names:
            if sname in ("编制说明", "汇总表"):
                continue
            raw = pd.read_excel(fpath, sheet_name=sname, dtype=str, header=None)
            h = find_header(raw, ["编码", "名称"])
            if h is None:
                continue
            cols = [key_of(x) for x in raw.iloc[h].tolist()]

            def idx(*keys):
                for k in keys:
                    for i, c in enumerate(cols):
                        if k in c:
                            return i
                return None

            i_code, i_name = idx("编码"), idx("名称")
            i_spec = idx("规格型号", "特征")
            i_unit = idx("计量单位", "单位")
            i_cat = idx("甲供材品类", "品类", "分类")
            for _, r in raw.iloc[h + 1:].iterrows():
                v = r.tolist()
                code = norm(v[i_code]) if i_code is not None else ""
                name = norm(v[i_name]) if i_name is not None else ""
                if not code or not name:
                    continue
                items.append({
                    "project": proj, "file": fname, "sheet": sname,
                    "eng_code": code, "name": name,
                    "spec": norm(v[i_spec]) if i_spec is not None else "",
                    "unit": norm(v[i_unit]) if i_unit is not None else "",
                    "category": norm(v[i_cat]) if i_cat is not None else "",
                })
            # 逐 Sheet 释放 DataFrame，避免多文件累积占用内存
            del raw
            gc.collect()
    return items


# ==================== 2. 解析完成清单（标注源） ====================
def load_done_groups():
    groups = []
    for fname in sorted(os.listdir(TRUTH_DIR)):
        if "完成清单" not in fname or not fname.endswith(".xlsx"):
            continue
        proj = PROJECT_OF.get(fname, "?")
        fpath = os.path.join(TRUTH_DIR, fname)
        xl = pd.ExcelFile(fpath)
        for sname in xl.sheet_names:
            raw = pd.read_excel(fpath, sheet_name=sname, dtype=str, header=None)
            h = find_header(raw, ["清单编码"])
            if h is None:
                continue
            cols = [key_of(x) for x in raw.iloc[h].tolist()]

            def idx(*keys):
                for k in keys:
                    for i, c in enumerate(cols):
                        if k in c:
                            return i
                return None

            i_fee, i_mtype = idx("费用类型"), idx("材料类型")
            i_code, i_name = idx("清单编码"), idx("清单名称")
            i_spec, i_unit = idx("特征", "规格型号"), idx("单位")
            cur = None
            for _, r in raw.iloc[h + 1:].iterrows():
                v = r.tolist()
                g = lambda i: norm(v[i]) if i is not None and i < len(v) else ""
                fee = g(i_fee)
                if fee == "人工费":
                    cur = {
                        "project": proj, "done_file": fname, "done_sheet": sname,
                        "eng_code": g(i_code), "done_name": g(i_name),
                        "done_spec": g(i_spec), "done_unit": g(i_unit),
                        "targets": [],
                    }
                    groups.append(cur)
                elif fee == "材料费":
                    if cur is None:
                        continue
                    cur["targets"].append({
                        "code": g(i_code), "name": g(i_name),
                        "spec": g(i_spec), "unit": g(i_unit),
                        "material_type": g(i_mtype),
                    })
                elif not fee or fee == "/":
                    cur = None   # 遇到分组/标题行，断开父子关系
            del raw
            gc.collect()
    return groups


def main():
    conn = sqlite3.connect(DB_PATH)
    db_rows = {c: (cat, nm, sp) for c, cat, nm, sp in
               conn.execute("select code, category, name, specification from materials")}
    print(f"物料库: {len(db_rows)} 条")

    raw_items = load_raw_items()
    groups = load_done_groups()
    print(f"原始清单有效行: {len(raw_items)}")
    print(f"完成清单人工费条目: {len(groups)}")

    # ---------- 3. 配对 ----------
    raw_idx = collections.defaultdict(list)
    for it in raw_items:
        raw_idx[(it["project"], it["eng_code"], key_of(it["name"]))].append(it)

    stat = collections.Counter()
    records = []
    for g in groups:
        cands = raw_idx.get((g["project"], g["eng_code"], key_of(g["done_name"])), [])
        src = "raw"
        if not cands:
            cands = [it for it in raw_items
                     if it["project"] == g["project"] and key_of(it["name"]) == key_of(g["done_name"])]
            src = "raw_name_only" if cands else "done_fallback"
        if cands:
            best = cands[0] if len(cands) == 1 else max(
                cands, key=lambda x: difflib.SequenceMatcher(
                    None, key_of(x["spec"]), key_of(g["done_spec"])).ratio())
            q_name, q_spec, q_unit = best["name"], best["spec"], best["unit"]
            q_cat = best.get("category", "")
            sheet = best["sheet"]
        else:
            q_name, q_spec, q_unit = g["done_name"], g["done_spec"], g["done_unit"]
            q_cat = ""
            sheet = g["done_sheet"]
        stat[src] += 1

        tgts = [t for t in g["targets"] if DOT_CODE.match(t["code"])]
        dropped = len(g["targets"]) - len(tgts)
        if dropped:
            stat["target_code_invalid"] += dropped
        tgts = [t for t in tgts if t["code"] in db_rows]

        if not tgts:
            stat["no_target(纯人工/无效)"] += 1
            continue

        for t in tgts:
            t["_db_name"] = db_rows[t["code"]][1]
        suspect, qc_reason = qc_check(q_name, q_spec, q_unit, tgts)
        if suspect:
            stat["qc_suspect(疑似源表错行)"] += 1

        records.append({
            "professional": prof_of(sheet, g["eng_code"]),
            "source_sheet": sheet,
            "project": g["project"],
            "query_source": src,
            "eng_code": g["eng_code"],
            "name": q_name,
            "specification": q_spec,
            "unit": q_unit,
            "supply_category": q_cat,
            "qc_suspect": suspect,
            "qc_reason": qc_reason,
            "target_codes": [t["code"] for t in tgts],
            "target_ref": [{
                "code": t["code"], "list_name": t["name"], "list_spec": t["spec"],
                "material_type": t["material_type"],
                "db_category": db_rows[t["code"]][0],
                "db_name": db_rows[t["code"]][1],
                "db_spec": db_rows[t["code"]][2],
            } for t in tgts],
        })

    print("\n配对来源分布:", dict(stat))
    print(f"有目标物料的条目: {len(records)}")

    # ---------- 4. 去重 + 冲突检测 ----------
    bucket = collections.OrderedDict()
    for r in records:
        k = (r["professional"], key_of(r["name"]), key_of(r["specification"]), key_of(r["unit"]))
        bucket.setdefault(k, []).append(r)

    items = []
    n_conflict = 0
    for k, rs in bucket.items():
        sigs = collections.defaultdict(list)
        for r in rs:
            sigs[tuple(sorted(set(r["target_codes"])))].append(r)
        conflict = len(sigs) > 1
        if conflict:
            n_conflict += 1
        for sig, rr in sigs.items():
            base = rr[0]
            items.append({
                "id": None,
                "professional": base["professional"],
                "source_sheet": base["source_sheet"],
                "project": base["project"],
                "query_source": base["query_source"],
                "eng_code": base["eng_code"],
                "name": base["name"],
                "specification": base["specification"],
                "unit": base["unit"],
                "supply_category": base["supply_category"],
                "target_codes": list(sig),
                "label_type": "1to1" if len(sig) == 1 else "1toN",
                "freq": len(rr),
                "ambiguous": conflict,
                "qc_suspect": base["qc_suspect"],
                "qc_reason": base["qc_reason"],
                "target_ref": base["target_ref"],
            })

    # 排序：专业 -> 类型 -> 名称，稳定编号
    items.sort(key=lambda x: (x["professional"], x["label_type"], x["name"], x["specification"]))
    for i, it in enumerate(items, 1):
        it["id"] = f"GT-{i:04d}"

    prof_dist = collections.Counter(x["professional"] for x in items)
    type_dist = collections.Counter(x["label_type"] for x in items)
    amb = sum(1 for x in items if x["ambiguous"])
    qc = sum(1 for x in items if x["qc_suspect"])
    core = [x for x in items
            if x["label_type"] == "1to1" and not x["ambiguous"] and not x["qc_suspect"]]
    core_prof = collections.Counter(x["professional"] for x in core)

    out = {
        "meta": {
            "name": "ListTrans 黄金测试集",
            "version": "v1.0",
            "build_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "doc/真集（6 组 原始清单/完成清单 配对文件，2 个真实项目）",
            "material_db_snapshot": {"path": "app/materials.db", "rows": len(db_rows)},
            "label_rule": "完成清单『人工费』行为 Query 载体，其下紧随的『材料费』行清单编码为 target_code；"
                          "Query 文本优先回溯自原始清单，避免答案泄漏。",
            "core_set_def": "label_type=1to1 且 ambiguous=false 且 qc_suspect=false，"
                            "评测主指标（Hit@1 / Recall@K / MRR）以核心集为准。",
            "total": len(items),
            "core_set_size": len(core),
            "label_type_dist": dict(type_dist),
            "ambiguous_items": amb,
            "qc_suspect_items": qc,
            "professional_dist": dict(prof_dist.most_common()),
            "core_professional_dist": dict(core_prof.most_common()),
            "query_source_dist": dict(collections.Counter(x["query_source"] for x in items)),
            "field_desc": {
                "target_codes": "标准物料库编码列表（点分段新编码），已校验全部存在于 materials.db",
                "supply_category": "原始清单『甲供材品类』列原文，生产流程会作为 category 传入匹配器，评测须一致",
                "label_type": "1to1=单一目标物料（核心评测集）；1toN=一条清单拆成多个物料（套装，选做）",
                "freq": "该 (name, spec, unit) 组合在真集中出现次数，可作加权指标权重",
                "ambiguous": "true 表示同一 Query 文本在真集中被标注到不同物料，属人工标注歧义，主指标默认剔除",
                "qc_suspect": "true 表示自动质检判定该标注疑似源表填写错行（Query 与目标物料名称零交集），主指标默认剔除",
                "query_source": "raw=Query 取自原始清单；raw_name_only=仅按名称回溯；done_fallback=回退用完成清单文本",
            },
        },
        "items": items,
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("\n" + "=" * 62)
    print(f"黄金测试集已生成: {OUT_PATH}")
    print(f"  总条目             : {len(items)}")
    print(f"  核心集(可用于主指标): {len(core)}")
    print(f"  类型分布           : {dict(type_dist)}")
    print(f"  歧义条目(剔除)     : {amb}")
    print(f"  质检可疑(剔除)     : {qc}")
    print(f"  全集专业分布       : {dict(prof_dist.most_common())}")
    print(f"  核心集专业分布     : {dict(core_prof.most_common())}")
    print("=" * 62)


if __name__ == "__main__":
    main()
