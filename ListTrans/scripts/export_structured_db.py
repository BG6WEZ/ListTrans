# -*- coding: utf-8 -*-
"""
Export master material database (materials.db) after structuring to Excel in doc/ directory.
"""
import os
import sqlite3
import json
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

def export_materials_to_excel():
    db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "materials.db")
    output_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "doc", "全局标准物料明细库_结构化结果.xlsx")
    
    if not os.path.exists(db_path):
        print(f"Database file not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    rows = cursor.execute("SELECT code, category, name, specification, unit, brand, parsed_params FROM materials").fetchall()
    conn.close()
    
    data = []
    total_count = len(rows)
    model_count = 0
    attr_count = 0
    full_struct_count = 0
    
    for r in rows:
        code, cat, name, spec, unit, brand, p_json = r
        model = ""
        attrs_str = ""
        status = "未提取型号"
        
        if p_json:
            try:
                p = json.loads(p_json)
                model = p.get("model", "")
                attrs = p.get("attrs", {})
                if isinstance(attrs, dict) and attrs:
                    attrs_str = "; ".join([f"{k}: {v}" for k, v in attrs.items() if v])
                
                if model:
                    model_count += 1
                if attrs_str:
                    attr_count += 1
                    
                if model and attrs_str:
                    status = "完整结构化"
                    full_struct_count += 1
                elif model or attrs_str:
                    status = "部分结构化"
            except Exception:
                pass
                
        data.append({
            "物料编码": code or "",
            "专业分类": cat or "",
            "标准物料名称": name or "",
            "原始规格描述": spec or "",
            "提取标准型号": model or "",
            "提取技术属性": attrs_str or "",
            "计量单位": unit or "",
            "品牌/厂商": brand or "",
            "结构化完成度": status
        })
        
    df_data = pd.DataFrame(data)
    
    # Summary Data Frame
    summary_data = [
        {"统计维度": "物料库总行数", "度量数值": total_count, "占比说明": "100.00%"},
        {"统计维度": "原始规格列非空数", "度量数值": sum(1 for d in data if d["原始规格描述"]), "占比说明": f"{sum(1 for d in data if d['原始规格描述'])/total_count*100:.2f}%"},
        {"统计维度": "提取标准型号数 (Model)", "度量数值": model_count, "占比说明": f"{model_count/total_count*100:.2f}%"},
        {"统计维度": "提取技术属性数 (Attrs)", "度量数值": attr_count, "占比说明": f"{attr_count/total_count*100:.2f}%"},
        {"统计维度": "完整结构化行数 (型号+属性)", "度量数值": full_struct_count, "占比说明": f"{full_struct_count/total_count*100:.2f}%"},
        {"统计维度": "尚需大模型治理行数 (缺失型号)", "度量数值": total_count - model_count, "占比说明": f"{(total_count - model_count)/total_count*100:.2f}%"}
    ]
    df_summary = pd.DataFrame(summary_data)
    
    # Write Excel with openpyxl styling
    wb = openpyxl.Workbook()
    
    # Sheet 1: Details
    ws_data = wb.active
    ws_data.title = "全局物料明细结构化数据"
    
    # Write Headers
    headers = list(df_data.columns)
    ws_data.append(headers)
    
    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(name="Microsoft YaHei", size=11, bold=True, color="FFFFFF")
    align_center = Alignment(horizontal="center", vertical="center")
    
    for col_idx in range(1, len(headers) + 1):
        cell = ws_data.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = align_center
        
    # Write Data Rows
    for r_idx, row_dict in enumerate(data, start=2):
        row_vals = [row_dict[h] for h in headers]
        ws_data.append(row_vals)
        
        # Color status column
        status_val = row_dict["结构化完成度"]
        status_cell = ws_data.cell(row=r_idx, column=len(headers))
        if status_val == "完整结构化":
            status_cell.font = Font(color="15803D", bold=True)
        elif status_val == "部分结构化":
            status_cell.font = Font(color="2563EB")
        else:
            status_cell.font = Font(color="DC2626")
            
    # Auto-adjust Column Widths
    col_widths = {
        "物料编码": 16,
        "专业分类": 16,
        "标准物料名称": 24,
        "原始规格描述": 45,
        "提取标准型号": 20,
        "提取技术属性": 45,
        "计量单位": 10,
        "品牌/厂商": 16,
        "结构化完成度": 14
    }
    for col_idx, col_name in enumerate(headers, start=1):
        col_letter = get_column_letter(col_idx)
        ws_data.column_dimensions[col_letter].width = col_widths.get(col_name, 18)

    # Sheet 2: Summary
    ws_sum = wb.create_sheet(title="结构化统计分析汇总")
    ws_sum.append(["统计维度", "度量数值", "占比说明"])
    for cell in ws_sum[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = align_center
        
    for s_dict in summary_data:
        ws_sum.append([s_dict["统计维度"], s_dict["度量数值"], s_dict["占比说明"]])
        
    for row in ws_sum.iter_rows(min_row=2, max_col=3):
        for cell in row:
            cell.alignment = align_center
            cell.font = Font(name="Microsoft YaHei", size=10)
            
    ws_sum.column_dimensions["A"].width = 35
    ws_sum.column_dimensions["B"].width = 18
    ws_sum.column_dimensions["C"].width = 18

    wb.save(output_path)
    print(f"Successfully saved styled Excel workbook with {total_count} rows to {output_path}")

if __name__ == "__main__":
    export_materials_to_excel()
