import openpyxl
import os
import copy
from typing import List, Dict, Any, Optional
from app.db_manager import query_by_code

def build_historical_price_map(template_path: str) -> Dict[str, float]:
    """
    Build a map of material code -> price from the standard template sheet.
    This helps us preserve existing prices for matched materials.
    """
    price_map = {}
    if not os.path.exists(template_path):
        return price_map
        
    try:
        wb = openpyxl.load_workbook(template_path, data_only=True)
        sheet = wb["模板"]
        for r in range(3, sheet.max_row + 1):
            code = sheet.cell(r, 5).value  # *清单编码 (Column E)
            price = sheet.cell(r, 13).value  # 单价(无税) (Column M)
            if code and price is not None:
                price_map[str(code).strip()] = float(price)
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
    Standard report generator.
    Only contains the content of the currently uploaded table.
    """
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template file not found at {template_path}")
        
    # Build price map from template
    price_map = build_historical_price_map(template_path)
    
    # Load workbook preserving formulas
    wb = openpyxl.load_workbook(template_path, data_only=False)
    sheet = wb["模板"]
    
    # 1. Determine target professional from matched_items
    target_prof = "暖通"
    if matched_items:
        # Determine professional from the first item
        target_prof = matched_items[0].get("professional") or "暖通"
        
    # 2. Extract style templates
    style_templates = []
    
    # Cache style template from row 3 if exists, or row 2
    if sheet.max_row >= 3:
        style_templates = [get_cell_style_dict(sheet.cell(3, c)) for c in range(1, 17)]
    elif sheet.max_row >= 2:
        style_templates = [get_cell_style_dict(sheet.cell(2, c)) for c in range(1, 17)]
        
    # 3. Clear all rows from Row 3 onwards
    max_r = sheet.max_row
    if max_r >= 3:
        sheet.delete_rows(3, max_r - 2)
        
    # 4. Write New matched rows next
    current_row = 3
    
    for item in matched_items:
        code = item.get("matched_code", "")
        # Look up price from cache
        price = price_map.get(str(code).strip(), None)
        
        row_data = {
            1: "",  # Re-numbered sequentially later
            2: "材料费",
            3: "手动录入",
            4: item.get("matched_category", ""),
            5: code,
            6: item.get("matched_name", ""),
            7: item.get("matched_spec", ""),
            8: item.get("matched_unit", item.get("original_unit", "")),
            9: item.get("original_qty", 0),
            10: item.get("original_loss", 1),
            11: item.get("matched_brand", ""),
            12: None,
            13: price,
            14: "标段一",
            15: item.get("professional") or target_prof,
            16: None
        }
        
        for c in range(1, 17):
            cell = sheet.cell(current_row, c)
            cell.value = row_data.get(c)
            # Apply template style
            if style_templates:
                apply_style_dict(cell, style_templates[c - 1])
        current_row += 1
        
    # 5. Re-number column A (*序号) sequentially starting from Row 3
    # Format: "1.1", "1.2", ...
    for r in range(3, current_row):
        sheet.cell(r, 1).value = f"1.{r - 2}"
        
    # Save the output workbook
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    wb.save(output_path)
    wb.close()
    
    print(f"Generated standard format Excel with {len(matched_items)} written rows.")
