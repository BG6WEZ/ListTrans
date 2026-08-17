import openpyxl
import os
import copy
from typing import List, Dict, Any, Optional
from openpyxl.styles import Font, PatternFill
from app.db_manager import query_by_code

def build_historical_price_map(template_path: str) -> Dict[str, float]:
    """
    Build a map of material code -> price from the standard template sheet.
    This helps us preserve existing prices for matched materials.
    """
    price_map = {}
    if not template_path or not os.path.exists(template_path):
        return price_map
        
    try:
        wb = openpyxl.load_workbook(template_path, data_only=True)
        if "模板" in wb.sheetnames:
            sheet = wb["模板"]
        else:
            sheet = wb.active
        for r in range(3, sheet.max_row + 1):
            code = sheet.cell(r, 5).value  # *清单编码 (Column E)
            price = sheet.cell(r, 13).value  # 单价(无税) (Column M)
            if code and price is not None:
                try:
                    price_map[str(code).strip()] = float(price)
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        print(f"Error building historical price map: {e}")
        
    return price_map

def get_cell_style_dict(cell) -> Dict[str, Any]:
    """Extract openpyxl cell style properties into a dictionary."""
    return {
        "font": copy.copy(cell.font) if cell.font else None,
        "fill": copy.copy(cell.fill) if cell.fill else None,
        "border": copy.copy(cell.border) if cell.border else None,
        "alignment": copy.copy(cell.alignment) if cell.alignment else None,
        "number_format": cell.number_format,
        "protection": copy.copy(cell.protection) if cell.protection else None
    }

def apply_style_dict(dest_cell, style_dict: Dict[str, Any]):
    """Apply style properties from a dictionary to a cell."""
    if not style_dict:
        return
    if style_dict["font"]: dest_cell.font = style_dict["font"]
    if style_dict["fill"]: dest_cell.fill = style_dict["fill"]
    if style_dict["border"]: dest_cell.border = style_dict["border"]
    if style_dict["alignment"]: dest_cell.alignment = style_dict["alignment"]
    if style_dict["number_format"]: dest_cell.number_format = style_dict["number_format"]
    if style_dict["protection"]: dest_cell.protection = style_dict["protection"]

def generate_standard_report(matched_items: List[Dict[str, Any]], template_path: str, output_path: str):
    """
    Standard report generator with grouped professional categories,
    parent-child rows styling, and multi-row flat export.
    """
    # Build price map if template path is valid
    price_map = {}
    if template_path and os.path.exists(template_path):
        price_map = build_historical_price_map(template_path)

    wb = None
    if template_path and os.path.exists(template_path):
        try:
            wb = openpyxl.load_workbook(template_path, data_only=False)
            if "模板" in wb.sheetnames:
                sheet = wb["模板"]
            else:
                sheet = wb.active
                sheet.title = "模板"
        except Exception as e:
            print(f"Error loading template file: {e}. Falling back to system built-in template.")
            wb = None

    if wb is None:
        # Build built-in template dynamically
        wb = openpyxl.Workbook()
        sheet = wb.active
        sheet.title = "模板"
        
        headers = [
            '*序号', '*费用类型', '*清单类型', '*材料类型', '*清单编码', 
            '*清单名称', '*特征/规格型号', '单位', '*工程量', '材料损耗', 
            '品牌', '竞聘系数', '单价(无税)', '备注', '专业'
        ]
        # Set headers styling
        for col_idx, header in enumerate(headers, start=1):
            cell = sheet.cell(1, col_idx)
            cell.value = header
            cell.font = Font(name="微软雅黑", size=11, bold=True)
            cell.fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
            
        # Add dummy row 2 for styling template cache
        for col_idx in range(1, 17):
            cell = sheet.cell(2, col_idx)
            cell.value = None
            cell.font = Font(name="微软雅黑", size=10)
            
    # Cache style template from row 3 if exists, or row 2
    style_templates = []
    if sheet.max_row >= 3:
        style_templates = [get_cell_style_dict(sheet.cell(3, c)) for c in range(1, 17)]
    elif sheet.max_row >= 2:
        style_templates = [get_cell_style_dict(sheet.cell(2, c)) for c in range(1, 17)]
        
    # Clear all rows from Row 3 onwards
    max_r = sheet.max_row
    if max_r >= 3:
        sheet.delete_rows(3, max_r - 2)
        
    # Group items by professional category, preserving order
    prof_groups = {}
    prof_order = []
    for item in matched_items:
        prof = item.get("professional") or "暖通"
        if prof not in prof_groups:
            prof_groups[prof] = []
            prof_order.append(prof)
        prof_groups[prof].append(item)
        
    # Set fills
    orange_fill = PatternFill(start_color="FFE8D6", end_color="FFE8D6", fill_type="solid")
    gray_fill = PatternFill(start_color="F2F4F7", end_color="F2F4F7", fill_type="solid")
    no_fill = PatternFill(fill_type=None)
    
    current_row = 3
    target_prof = "暖通"
    
    # Write professional groupings and their items
    for prof_idx, prof in enumerate(prof_order, start=1):
        # 1. Write Professional Grouping Row
        for c in range(1, 17):
            cell = sheet.cell(current_row, c)
            if c == 1:
                cell.value = prof_idx
            elif c == 6:
                cell.value = prof
            elif c == 15:
                cell.value = prof
            else:
                cell.value = None
                
            if style_templates:
                apply_style_dict(cell, style_templates[c - 1])
                
            # Styling grouping row
            cell.font = Font(name=cell.font.name if cell.font else "Arial", size=cell.font.size if cell.font else 11, bold=True)
            cell.fill = gray_fill
        current_row += 1
        
        # 2. Write Items
        for item_idx, item in enumerate(prof_groups[prof], start=1):
            sec_idx = f"{prof_idx}.{item_idx}"
            
            # A. Write Original List Row (Orange)
            original_row_data = {
                1: sec_idx,
                2: "材料费",
                3: "手动录入",
                4: item.get("original_category", ""),
                5: item.get("original_code", ""),
                6: item.get("original_name", ""),
                7: item.get("original_spec", ""),
                8: item.get("original_unit", ""),
                9: item.get("original_qty", 0),
                10: item.get("original_loss", 1.0),
                11: item.get("original_brand", ""),
                12: None,
                13: None,  # No price on original row
                14: "标段一",
                15: prof,
                16: None
            }
            for c in range(1, 17):
                cell = sheet.cell(current_row, c)
                cell.value = original_row_data.get(c)
                if style_templates:
                    apply_style_dict(cell, style_templates[c - 1])
                cell.fill = orange_fill
            current_row += 1
            
            # B. Write Matched Material Row(s)
            bindings = item.get("bindings")
            if bindings:
                for b in bindings:
                    code = b.get("code", "")
                    price = price_map.get(str(code).strip(), None)
                    match_row_data = {
                        1: None, # Column A is empty/None
                        2: "材料费",
                        3: "手动录入",
                        4: b.get("category", ""),
                        5: code,
                        6: b.get("name", ""),
                        7: b.get("specification", ""),
                        8: b.get("unit", item.get("original_unit", "")),
                        9: b.get("qty", item.get("original_qty", 0)),
                        10: item.get("original_loss", 1.0),
                        11: b.get("brand", ""),
                        12: None,
                        13: price,
                        14: "标段一",
                        15: prof,
                        16: None
                    }
                    for c in range(1, 17):
                        cell = sheet.cell(current_row, c)
                        cell.value = match_row_data.get(c)
                        if style_templates:
                            apply_style_dict(cell, style_templates[c - 1])
                        cell.fill = no_fill
                    current_row += 1
            else:
                # Fallback to single item match if any code is present
                code = item.get("matched_code", "")
                if code:
                    price = price_map.get(str(code).strip(), None)
                    match_row_data = {
                        1: None,
                        2: "材料费",
                        3: "手动录入",
                        4: item.get("matched_category", ""),
                        5: code,
                        6: item.get("matched_name", ""),
                        7: item.get("matched_spec", ""),
                        8: item.get("matched_unit", item.get("original_unit", "")),
                        9: item.get("original_qty", 0),
                        10: item.get("original_loss", 1.0),
                        11: item.get("matched_brand", ""),
                        12: None,
                        13: price,
                        14: "标段一",
                        15: prof,
                        16: None
                    }
                    for c in range(1, 17):
                        cell = sheet.cell(current_row, c)
                        cell.value = match_row_data.get(c)
                        if style_templates:
                            apply_style_dict(cell, style_templates[c - 1])
                        cell.fill = no_fill
                    current_row += 1
                    
    # Save the output workbook
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    wb.save(output_path)
    wb.close()
    
    print(f"Generated standard format Excel with {len(matched_items)} items written.")
