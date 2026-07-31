# -*- coding: utf-8 -*-
"""客观审计 query_data_portrait.md 中的统计数字。"""
import os, re, glob, json
import pandas as pd
from collections import Counter

SAMPLE_DIR = r"c:\Users\wangd\project\ListTrans\ListTrans\doc\原始清单样本"

# 收集文件（xlsx 优先；同名 xls 若已有 xlsx converted 则跳过）
files = []
for p in glob.glob(os.path.join(SAMPLE_DIR, "*.xlsx")):
    files.append(p)
for p in glob.glob(os.path.join(SAMPLE_DIR, "*.xls")):
    # 06.动力气体-原始清单.xls 与 06.动力气体-原始清单_converted.xlsx 是同一份数据
    base = os.path.splitext(os.path.basename(p))[0]
    conv = os.path.join(SAMPLE_DIR, base + "_converted.xlsx")
    if os.path.exists(conv):
        continue
    files.append(p)

files = sorted(set(files))
print(f"总文件数: {len(files)}")
for f in files:
    print(" -", os.path.basename(f))

# 读取所有 sheets
NAME_KEYS = ["名称", "构件名称", "项目名称"]
SPEC_KEYS = ["规格型号/特征", "项目特征", "规格型号", "规格及型号", "规格", "特征"]

def find_header_row(df_raw):
    """在前 15 行内查找包含名称/规格关键字的表头行。返回(header_idx, name_col, spec_col)"""
    max_rows = min(20, len(df_raw))
    for i in range(max_rows):
        row = df_raw.iloc[i].astype(str).tolist()
        row_str = [s.strip() for s in row]
        name_col = None
        spec_col = None
        for j, val in enumerate(row_str):
            v = val.replace("*", "").replace("＊", "").strip()
            if name_col is None:
                if v in NAME_KEYS or v.endswith("名称") and len(v) <= 8:
                    name_col = j
            if spec_col is None:
                if v in SPEC_KEYS or ("规格" in v and "型号" in v) or v == "项目特征":
                    spec_col = j
        if name_col is not None and spec_col is not None:
            return i, name_col, spec_col
        if name_col is not None and spec_col is None and i > 0:
            # 存在名称但没规格：可能规格并入名称
            return i, name_col, None
    return None, None, None

rows_records = []  # list of dict {file, sheet, name, spec}
sheet_count = 0
sheet_meta = []
for fp in files:
    try:
        xl = pd.ExcelFile(fp)
    except Exception as e:
        print("open failed:", fp, e)
        continue
    for sn in xl.sheet_names:
        try:
            df = xl.parse(sn, header=None, dtype=str)
        except Exception as e:
            continue
        if df.empty:
            continue
        sheet_count += 1
        hi, nc, sc = find_header_row(df)
        header_name = None
        header_spec = None
        if hi is not None:
            header_name = str(df.iat[hi, nc]).strip() if nc is not None else None
            header_spec = str(df.iat[hi, sc]).strip() if sc is not None else None
        sheet_meta.append({
            "file": os.path.basename(fp),
            "sheet": sn,
            "header_row": hi,
            "name_header": header_name,
            "spec_header": header_spec,
        })
        if hi is None or nc is None:
            continue
        for r in range(hi + 1, len(df)):
            name_val = df.iat[r, nc] if nc is not None else None
            spec_val = df.iat[r, sc] if (sc is not None) else None
            name_s = "" if pd.isna(name_val) else str(name_val).strip()
            spec_s = "" if (sc is None or pd.isna(spec_val)) else str(spec_val).strip()
            # 过滤空行
            if not name_s and not spec_s:
                continue
            # 过滤明显非清单文本
            if name_s and (name_s.startswith("编制") or name_s.startswith("说明") or name_s.startswith("图纸") or name_s.startswith("总说明")):
                continue
            # 过滤 "序号 / 名称 / ..." 之类残留表头
            rows_records.append({
                "file": os.path.basename(fp),
                "sheet": sn,
                "name": name_s,
                "spec": spec_s,
            })

df_all = pd.DataFrame(rows_records)
print("\n===== 总览 =====")
print(f"Sheets 总数(所有页,含空/无表头): {sheet_count}")
print(f"识别出表头的 sheet 数: {sum(1 for m in sheet_meta if m['header_row'] is not None and m['name_header'])}")
print(f"抓取到的原始记录数: {len(df_all)}")

# 名称列画像
n = df_all["name"].fillna("")
n_nonempty = n[n.astype(bool)]
print("\n[名称列]")
print(f"总条目: {len(n)}")
print(f"名称填充率: {len(n_nonempty)/len(n):.4f}")
print(f"名称去重数: {n_nonempty.nunique()}")
lens = n_nonempty.str.len()
print(f"名称长度 均值={lens.mean():.2f}, 中位={lens.median()}, P25={lens.quantile(.25)}, P75={lens.quantile(.75)}, P95={lens.quantile(.95)}, max={lens.max()}")
bins = [(1,5),(6,10),(11,20),(21,999)]
for a,b in bins:
    c = ((lens>=a)&(lens<=b)).sum()
    print(f"  长度 {a}-{b if b<999 else '20+'}: {c}  ({c/len(n_nonempty):.2%})")

print("\n[名称 Top 20]")
for name, cnt in Counter(n_nonempty.tolist()).most_common(20):
    print(f"  {cnt:>5}  {name}")

# 括号占比
paren = n_nonempty.str.contains(r"[（(].*[）)]", regex=True).sum()
print(f"\n名称括号占比: {paren}/{len(n_nonempty)} = {paren/len(n_nonempty):.2%}")

# 规格混入
mix_regex = re.compile(r"DN\d+|Φ\d+|φ\d+|\d+\s*[×xX*]\s*\d+|\d+\s*mm|YJV|BV|电缆.*\*")
mix = n_nonempty.str.contains(mix_regex).sum()
print(f"名称含规格特征占比: {mix}/{len(n_nonempty)} = {mix/len(n_nonempty):.2%}")

# 序号前缀
seq_regex = re.compile(r"^(\d+[\.．、\)]|[①②③④⑤⑥⑦⑧⑨⑩])")
seq = n_nonempty.str.match(seq_regex).sum()
print(f"名称序号前缀占比: {seq}/{len(n_nonempty)} = {seq/len(n_nonempty):.2%}")

# 规格列画像
s = df_all["spec"].fillna("")
s_nonempty = s[s.astype(bool)]
print("\n[规格列]")
print(f"规格填充率: {len(s_nonempty)/len(s):.4f}  空占比: {1-len(s_nonempty)/len(s):.4f}")
slens = s_nonempty.str.len()
print(f"规格长度 均值={slens.mean():.2f}, 中位={slens.median()}, P25={slens.quantile(.25)}, P75={slens.quantile(.75)}, P90={slens.quantile(.9)}, P95={slens.quantile(.95)}, max={slens.max()}")

s_all_len = s.str.len()
bounds = [(0,0),(1,20),(21,50),(51,100),(101,200),(201,10**6)]
for a,b in bounds:
    c = ((s_all_len>=a)&(s_all_len<=b)).sum() if a>0 else (s_all_len==0).sum()
    label = "0(空)" if a==0 and b==0 else f"{a}-{b if b<10**5 else '200+'}"
    print(f"  长度 {label}: {c}  ({c/len(s):.2%})")

# 换行
nl = s_nonempty.str.contains("\n").sum()
print(f"规格含换行占比: {nl}/{len(s)} = {nl/len(s):.2%} (相对非空: {nl/max(1,len(s_nonempty)):.2%})")
lines = s_nonempty.str.count("\n") + 1
print(f"规格平均行数: {lines.mean():.2f}, 最多行: {lines.max()}")

# 冒号 & 序号 & 乘号
colon = s.str.contains(r"[:：]", regex=True).sum()
seq2 = s.str.contains(r"(?m)^\s*(\d+[\.．、\)]|[①②③④⑤⑥⑦⑧⑨⑩])", regex=True).sum()
mul = s.str.contains(r"[*×]", regex=True).sum()
print(f"规格含冒号占比: {colon/len(s):.2%}")
print(f"规格含序号占比: {seq2/len(s):.2%}")
print(f"规格含乘号占比: {mul/len(s):.2%}")

# Key 频次
key_re = re.compile(r"([\u4e00-\u9fa5A-Za-z]{1,12})[\s]*[:：]")
key_counter = Counter()
for v in s_nonempty:
    for m in key_re.finditer(v):
        k = m.group(1).strip()
        if 1 <= len(k) <= 10:
            key_counter[k] += 1
print("\n[规格 Key Top 20]")
for k, c in key_counter.most_common(20):
    print(f"  {c:>5}  {k}")

# 输出保存
df_all.to_csv(r"c:\Users\wangd\project\ListTrans\_audit_records.csv", index=False, encoding="utf-8-sig")
with open(r"c:\Users\wangd\project\ListTrans\_audit_sheet_meta.json", "w", encoding="utf-8") as f:
    json.dump(sheet_meta, f, ensure_ascii=False, indent=2)
print("\n已导出 _audit_records.csv, _audit_sheet_meta.json")
