# -*- coding: utf-8 -*-
"""ASSET-M1-01：可复现脱敏标准格式模板生成器。

`doc/sample/标准格式.xlsx` 为用户业务资产（.gitignore 全局忽略 *.xlsx，
R04 裁决禁止广泛解除），本机可能缺失，历史导出用例长期 skip。本模块用
openpyxl 确定性生成结构等价的脱敏模板：第 1 行 15 列表头、第 2 行样式
缓存行、可选历史价格行（列 E 编码 / 列 M 单价，供 build_historical_price_map
读取），保证 generate_standard_report 的真实导出链路可被持续验证。

生成内容与真实业务模板同构：与 report_generator.generate_standard_report
的"模板" sheet 读取约定（表头行 1、样式缓存行 2/3、E 列编码、M 列单价）一致，
不含任何个人或业务敏感信息。
"""
import os
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

# 与 report_generator 内置 fallback 表头一致（真实业务模板同款 15 列契约）
STANDARD_HEADERS = [
    '*序号', '*费用类型', '*清单类型', '*材料类型', '*清单编码',
    '*清单名称', '*特征/规格型号', '单位', '*工程量', '材料损耗',
    '品牌', '竞聘系数', '单价(无税)', '备注', '专业'
]


def generate_standard_template(path: str, with_history_rows: bool = True) -> str:
    """生成脱敏标准格式模板并返回路径。

    Args:
        path: 输出 XLSX 路径（调用方负责放入隔离目录）。
        with_history_rows: 是否写入历史价格行（列 E 编码 / 列 M 单价），
            用于验证 build_historical_price_map 的价格继承契约。
    """
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "模板"

    # 第 1 行：表头
    header_font = Font(name="微软雅黑", size=11, bold=True)
    header_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    for col_idx, header in enumerate(STANDARD_HEADERS, start=1):
        cell = sheet.cell(1, col_idx)
        cell.value = header
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")

    # 第 2 行：样式缓存行（generate_standard_report 从行 3 或行 2 取样式模板）
    body_font = Font(name="微软雅黑", size=10)
    for col_idx in range(1, 17):
        cell = sheet.cell(2, col_idx)
        cell.value = None
        cell.font = body_font

    row = 3
    if with_history_rows:
        # 历史价格行：与导出列位一致（E=编码, I=工程量, M=单价, O=专业）
        history_rows = [
            ("1.1", "材料费", "手动录入", "轻钢龙骨", "HIST_001", "竖龙骨",
             "75*50", "米", 1.0, 1.0, "华康", None, 12.5, "标段一", "装饰"),
            ("1.2", "材料费", "手动录入", "轻钢龙骨", "HIST_002", "穿心龙骨",
             "38*12", "米", 1.0, 1.0, "华康", None, 8.0, "标段一", "装饰"),
        ]
        for row_data in history_rows:
            for col_idx, value in enumerate(row_data, start=1):
                cell = sheet.cell(row, col_idx)
                cell.value = value
                cell.font = body_font
            row += 1

    wb.save(path)
    wb.close()
    return path
