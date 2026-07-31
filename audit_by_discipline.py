# -*- coding: utf-8 -*-
"""按专业粗分类核对条目数（依据文件名关键字）。"""
import pandas as pd
df = pd.read_csv(r"c:\Users\wangd\project\ListTrans\_audit_records.csv", encoding="utf-8-sig")
print("总记录:", len(df))

def classify(fname):
    f = fname
    if "强电" in f: return "强电/电气"
    if "弱电" in f or "自控" in f or "自动门" in f: return "弱电/自控"
    if "给排水" in f: return "给排水"
    if "暖通" in f or "净化" in f: return "暖通空调/净化"
    if "医气" in f or "气体" in f or "动力" in f or "蒸汽" in f: return "医用气体/动力"
    if "装饰" in f or "土建" in f or "钢结构" in f: return "装饰与土建"
    if "PVC" in f or "橡胶" in f: return "专项材料发包"
    if "特安" in f: return "弱电/自控(特安)"
    if "利川" in f: return "利川综合"
    if "瑞安项目装饰" in f: return "装饰与土建"
    return "其他/" + f[:15]

df["cat"] = df["file"].map(classify)
g = df.groupby("cat").agg(
    n=("name", "size"),
    name_len=("name", lambda s: s.fillna("").astype(str).str.len().mean()),
    spec_empty=("spec", lambda s: s.isna().sum() + (s.fillna("").astype(str)=="").sum()),
    spec_len=("spec", lambda s: s.fillna("").astype(str).str.len().mean()),
)
g["spec_missing_pct"] = g["spec_empty"]/g["n"]
print(g.round(2).to_string())
