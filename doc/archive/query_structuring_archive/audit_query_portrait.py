import os
import glob
import pandas as pd
import numpy as np
import re
import json
from collections import Counter

folder = r"c:\Users\wangd\project\ListTrans\ListTrans\doc\原始清单样本"
files = [f for f in glob.glob(os.path.join(folder, "*")) if not os.path.basename(f).startswith("~$")]
if os.path.exists(os.path.join(folder, "06.动力气体-原始清单_converted.xlsx")):
    files = [f for f in files if not f.endswith(".xls")]

NAME_KEYWORDS = ["名称", "项目名称", "工程名称", "材料名称", "设备名称", "清单名称", "名称及规格"]
SPEC_KEYWORDS = ["规格", "项目特征", "规格型号", "特征", "技术参数", "规格及技术条件", "规格型号及技术要求", "技术特征", "规格及参数"]
UNIT_KEYWORDS = ["单位", "计量单位"]
CODE_KEYWORDS = ["编码", "项目编码", "序号", "编号", "清单编码"]

def find_header_and_cols(df):
    best_row, name_col, spec_col, unit_col, code_col = -1, -1, -1, -1, -1
    max_matches = 0
    for r in range(min(20, len(df))):
        row_vals = [str(val).strip() if pd.notna(val) else "" for val in df.iloc[r]]
        c_name, c_spec, c_unit, c_code = -1, -1, -1, -1
        matches = 0
        for idx, val in enumerate(row_vals):
            if any(k in val for k in NAME_KEYWORDS) and c_name == -1: c_name = idx; matches += 1
            elif any(k in val for k in SPEC_KEYWORDS) and c_spec == -1: c_spec = idx; matches += 1
            elif any(k in val for k in UNIT_KEYWORDS) and c_unit == -1: c_unit = idx; matches += 1
            elif any(k in val for k in CODE_KEYWORDS) and c_code == -1: c_code = idx; matches += 1
        if matches > max_matches and (c_name != -1 or c_spec != -1):
            max_matches = matches
            best_row, name_col, spec_col, unit_col, code_col = r, c_name, c_spec, c_unit, c_code
    return best_row, name_col, spec_col, unit_col, code_col

raw_records = []
filtering_log = Counter()

for fpath in files:
    fname = os.path.basename(fpath)
    try:
        excel = pd.ExcelFile(fpath)
        for sheet_name in excel.sheet_names:
            df = excel.parse(sheet_name, header=None)
            if df.empty: continue
            header_row, name_col, spec_col, unit_col, code_col = find_header_and_cols(df)
            if header_row == -1 or (name_col == -1 and spec_col == -1): continue
            for r in range(header_row + 1, len(df)):
                row_vals = df.iloc[r]
                name_val = str(row_vals[name_col]).strip() if name_col != -1 and name_col < len(row_vals) and pd.notna(row_vals[name_col]) else ""
                spec_val = str(row_vals[spec_col]).strip() if spec_col != -1 and spec_col < len(row_vals) and pd.notna(row_vals[spec_col]) else ""
                unit_val = str(row_vals[unit_col]).strip() if unit_col != -1 and unit_col < len(row_vals) and pd.notna(row_vals[unit_col]) else ""
                code_val = str(row_vals[code_col]).strip() if code_col != -1 and code_col < len(row_vals) and pd.notna(row_vals[code_col]) else ""

                if not name_val and not spec_val:
                    filtering_log["全空行"] += 1
                    continue
                if name_val in ["小计", "合计", "总计", "合计：", "小计：", "名称", "*名称", "项目名称", "构件名称"]:
                    filtering_log["小计/合计/二次表头重复行"] += 1
                    continue
                if "编制依据" in name_val or "图纸号" in name_val or "计量依据" in name_val:
                    filtering_log["编制依据/图纸说明等文档说明行"] += 1
                    continue
                if (name_val.startswith("一、") or name_val.startswith("二、") or name_val.startswith("三、")) and not unit_val and not spec_val:
                    filtering_log["大纲章节标题行"] += 1
                    continue
                if len(name_val) > 100 and not unit_val and not spec_val:
                    filtering_log["长文本章节说明行"] += 1
                    continue

                raw_records.append({
                    "file": fname, "sheet": sheet_name, "row": r + 1,
                    "code": code_val, "name": name_val, "spec": spec_val, "unit": unit_val
                })
    except Exception as e:
        print(f"Error {fname}: {e}")

df = pd.DataFrame(raw_records)

def classify_record(row):
    combined = row['file'] + " " + row['sheet']
    if any(k in combined for k in ["PVC", "橡胶", "自动门", "打胶"]): return "专项材料发包"
    elif "医气" in combined or "动力气体" in combined or "气体" in combined: return "医用气体/动力"
    elif "弱电" in combined or "自控" in combined: return "弱电/自控"
    elif "强电" in combined or "电气" in combined: return "强电/电气"
    elif "暖通" in combined or "蒸汽" in combined or "锅炉" in combined: return "暖通空调"
    elif "给排水" in combined or "水专" in combined: return "给排水"
    elif "净化" in combined or "手术室" in combined or "ICU" in combined: return "净化工程"
    elif "装饰" in combined or "土建" in combined: return "装饰与土建"
    else: return "综合/其他"

df['discipline'] = df.apply(classify_record, axis=1)

print(f"Verified clean item count: {len(df)}")
print("Filtering audit breakdown:", dict(filtering_log))
print("\nDiscipline Breakdown:\n", df['discipline'].value_counts())
