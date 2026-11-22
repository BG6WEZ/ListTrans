# -*- coding: utf-8 -*-
"""
V0.1.5 M5 Excel File Boundary, Streaming Upload Quota, and Safe Parsing Budgets (F18 + F24).
"""

from __future__ import annotations

import os
import re
import sys
import time
import uuid
import zipfile
import threading
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

import openpyxl
import pandas as pd


DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB
DEFAULT_STREAM_CHUNK_BYTES = 1024 * 1024  # 1 MiB
DEFAULT_MULTIPART_HEADER_OVERHEAD = 65536  # 64 KiB multipart form framing allowance
DEFAULT_MAX_ZIP_ENTRIES = 4096
DEFAULT_MAX_ZIP_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MiB
DEFAULT_MAX_SHEETS = 32
DEFAULT_MAX_ROWS_PER_SHEET = 100_000
DEFAULT_MAX_COLS_PER_SHEET = 256
DEFAULT_MAX_TOTAL_CELLS = 5_000_000
DEFAULT_PARSE_TIMEOUT_SECONDS = 60.0

ZIP_PK_LOCAL_MAGIC = b"PK\x03\x04"
ZIP_PK_EMPTY_MAGIC = b"PK\x05\x06"
OLE2_CFB_MAGIC = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"


@dataclass(frozen=True)
class ExcelParseBudget:
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    stream_chunk_bytes: int = DEFAULT_STREAM_CHUNK_BYTES
    max_zip_entries: int = DEFAULT_MAX_ZIP_ENTRIES
    max_zip_uncompressed_bytes: int = DEFAULT_MAX_ZIP_UNCOMPRESSED_BYTES
    max_sheets: int = DEFAULT_MAX_SHEETS
    max_rows_per_sheet: int = DEFAULT_MAX_ROWS_PER_SHEET
    max_cols_per_sheet: int = DEFAULT_MAX_COLS_PER_SHEET
    max_total_cells: int = DEFAULT_MAX_TOTAL_CELLS
    parse_timeout_seconds: float = DEFAULT_PARSE_TIMEOUT_SECONDS


def get_default_budget() -> ExcelParseBudget:
    """Return active Excel upload and parsing budgets (override-friendly for tests)."""
    return ExcelParseBudget()


class ExcelBoundaryError(ValueError):
    """Explicit validation / security boundary failure with HTTP status code."""

    def __init__(self, detail: str, status_code: int = 400, code: str = "excel_boundary_error"):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


class UploadTooLargeError(ExcelBoundaryError):
    """Raised when streaming upload exceeds the configured byte cap."""

    def __init__(self, detail: str = "上传文件大小超出限制（最大 50MB）"):
        super().__init__(detail=detail, status_code=413, code="upload_too_large")


class ParseTimeoutError(ExcelBoundaryError):
    """Raised when workbook scanning exceeds the monotonic parsing budget."""

    def __init__(self, detail: str = "Excel 解析超时，已中止处理"):
        super().__init__(detail=detail, status_code=400, code="parse_timeout")


class ParseCancelledError(ExcelBoundaryError):
    """Raised when cooperative cancellation is triggered during parsing/import."""

    def __init__(self, detail: str = "任务已取消或超时中止"):
        super().__init__(detail=detail, status_code=499, code="parse_cancelled")


class DbImportLockManager:
    """
    Single-release ownership wrapper around the process-wide `_db_import_lock`.
    Preserves compatibility with callers referencing `_db_import_lock` directly while
    ensuring timeout cleanup and worker finally-blocks release at most once per owner token.
    """

    def __init__(self, lock: threading.Lock):
        self._lock = lock
        self._state_lock = threading.Lock()
        self._owner_token: Optional[str] = None

    def try_acquire(self) -> Optional[str]:
        if not self._lock.acquire(blocking=False):
            return None
        token = uuid.uuid4().hex
        with self._state_lock:
            self._owner_token = token
        return token

    def release_for_token(self, token: Optional[str]) -> bool:
        if not token:
            return False
        with self._state_lock:
            if self._owner_token != token:
                return False
            self._owner_token = None
            if self._lock.locked():
                try:
                    self._lock.release()
                    return True
                except RuntimeError:
                    return False
            return False


def normalize_excel_extension(filename: Optional[str]) -> str:
    """Normalize extension to lowercase with leading dot."""
    clean = (filename or "").strip()
    if not clean:
        return ""
    return os.path.splitext(clean)[1].lower()


def validate_extension(
    filename: Optional[str],
    allowed_exts: FrozenSet[str],
    context_label: str = "Excel 导入",
) -> str:
    """Validate normalized extension before streaming/parsing."""
    ext = normalize_excel_extension(filename)
    if ext not in allowed_exts:
        if allowed_exts == frozenset({".xlsx"}):
            raise ExcelBoundaryError(
                f"{context_label}仅支持 .xlsx 格式文件；旧版 .xls 或其他格式请先另存为 .xlsx 后再上传（收到：{ext or '无扩展名'}）",
                status_code=400,
                code="unsupported_extension",
            )
        allowed_desc = ", ".join(sorted(allowed_exts))
        raise ExcelBoundaryError(
            f"{context_label}不支持的文件格式（仅允许 {allowed_desc}，收到：{ext or '无扩展名'}）",
            status_code=400,
            code="unsupported_extension",
        )
    return ext


def remove_file_quietly(path: Optional[str]) -> None:
    """Best-effort temporary file cleanup without raising."""
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


async def stream_save_upload_file(
    request: Any,
    upload_file: Any,
    dest_path: str,
    max_bytes: Optional[int] = None,
    chunk_bytes: Optional[int] = None,
) -> int:
    """
    Save an incoming UploadFile in bounded chunks without trusting Content-Length.
    Rejects invalid Content-Length (400) or oversized uploads (413) and cleans dest_path on failure.
    """
    from starlette.concurrency import run_in_threadpool

    budget = get_default_budget()
    limit = int(max_bytes if max_bytes is not None else budget.max_upload_bytes)
    read_size = max(1024, min(int(chunk_bytes or budget.stream_chunk_bytes), DEFAULT_STREAM_CHUNK_BYTES))

    headers = getattr(request, "headers", None) or {}
    raw_cl = headers.get("content-length") if hasattr(headers, "get") else None
    if raw_cl is not None:
        cl_text = str(raw_cl).strip()
        if not re.fullmatch(r"\d+", cl_text):
            raise ExcelBoundaryError(
                "请求头 Content-Length 无效",
                status_code=400,
                code="invalid_content_length",
            )
        declared_len = int(cl_text)
        content_type = str(headers.get("content-type", "")).lower()
        overhead = DEFAULT_MULTIPART_HEADER_OVERHEAD if "multipart/form-data" in content_type else 0
        if declared_len > limit + overhead:
            raise UploadTooLargeError(
                f"上传请求体超出大小限制（最大 {round(limit / (1024 * 1024), 2)} MB）"
            )

    total_bytes = 0
    dest_dir = os.path.dirname(os.path.abspath(dest_path))
    def open_output():
        if dest_dir:
            os.makedirs(dest_dir, exist_ok=True)
        return open(dest_path, "wb")

    try:
        out_fp = await run_in_threadpool(open_output)
        try:
            while True:
                chunk = await upload_file.read(read_size)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > limit:
                    raise UploadTooLargeError(
                        f"上传文件超出大小限制（最大 {round(limit / (1024 * 1024), 2)} MB）"
                    )
                await run_in_threadpool(out_fp.write, chunk)
        finally:
            await run_in_threadpool(out_fp.close)
    except BaseException:
        await run_in_threadpool(remove_file_quietly, dest_path)
        raise
    finally:
        try:
            await upload_file.close()
        except Exception:
            pass

    if total_bytes == 0:
        await run_in_threadpool(remove_file_quietly, dest_path)
        raise ExcelBoundaryError("上传文件为空（0 字节）", status_code=400, code="empty_upload")

    return total_bytes


def is_mock_xlrd_module(xlrd_mod: Any) -> bool:
    """
    Detect test-only SimpleNamespace xlrd stubs (M4 lifecycle tests) vs real xlrd package.
    Real xlrd always carries package `__spec__` and `XLRDError`.
    """
    if xlrd_mod is None:
        return False
    return getattr(xlrd_mod, "__spec__", None) is None and not hasattr(xlrd_mod, "XLRDError")


def _read_magic_bytes(file_path: str, size: int = 8) -> bytes:
    if not os.path.exists(file_path):
        raise ExcelBoundaryError(f"文件不存在: {file_path}", status_code=400, code="file_not_found")
    if os.path.getsize(file_path) == 0:
        raise ExcelBoundaryError("Excel 文件为空（0 字节）", status_code=400, code="empty_file")
    with open(file_path, "rb") as f:
        return f.read(size)


def _enforce_file_byte_budget(
    file_path: str,
    budget: ExcelParseBudget,
    context_label: str,
) -> None:
    """R02 A-M5-03: every shared-reader entry path must honor the unified file-size budget."""
    if not os.path.isfile(file_path):
        raise ExcelBoundaryError(f"文件不存在: {file_path}", status_code=400, code="file_not_found")
    file_size = os.path.getsize(file_path)
    if file_size == 0:
        raise ExcelBoundaryError("Excel 文件为空（0 字节）", status_code=400, code="empty_file")
    if file_size > budget.max_upload_bytes:
        raise UploadTooLargeError(
            f"{context_label}文件大小 ({file_size} 字节) 超出安全预算上限 "
            f"({budget.max_upload_bytes} 字节)，已拒绝解析"
        )


def _validate_xlsx_container(
    file_path: str,
    budget: ExcelParseBudget,
    cancel_event: Optional[threading.Event] = None,
    started_at: Optional[float] = None,
) -> None:
    t0 = started_at if started_at is not None else time.monotonic()
    magic = _read_magic_bytes(file_path, 8)
    if magic.startswith(OLE2_CFB_MAGIC):
        raise ExcelBoundaryError(
            "检测到旧版 .xls 二进制格式或受密码保护的加密 Excel 文件，清单导入仅支持未加密的 .xlsx 文件",
            status_code=400,
            code="encrypted_or_xls_disguised",
        )
    if not magic.startswith(ZIP_PK_LOCAL_MAGIC):
        raise ExcelBoundaryError(
            "文件头签名与标准 .xlsx (OOXML ZIP) 格式不符，请确认文件未损坏且非其他格式改名",
            status_code=400,
            code="invalid_xlsx_signature",
        )

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            infos = zf.infolist()
            if not infos:
                raise ExcelBoundaryError("损坏的 .xlsx 压缩包（无有效条目）", status_code=400, code="empty_zip")
            if len(infos) > budget.max_zip_entries:
                raise ExcelBoundaryError(
                    f".xlsx 包内条目数 ({len(infos)}) 超过安全上限 ({budget.max_zip_entries})",
                    status_code=400,
                    code="zip_entries_exceeded",
                )

            declared_uncompressed = sum(max(0, info.file_size) for info in infos)
            if declared_uncompressed > budget.max_zip_uncompressed_bytes:
                raise ExcelBoundaryError(
                    ".xlsx 解压声明大小超过安全上限（拒绝潜在 ZIP 炸弹文件）",
                    status_code=400,
                    code="zip_declared_size_exceeded",
                )

            names = {info.filename for info in infos}
            if "EncryptionInfo" in names or "EncryptedPackage" in names:
                raise ExcelBoundaryError(
                    "检测到加密 Excel 文件，请先取消密码保护后再上传",
                    status_code=400,
                    code="encrypted_xlsx",
                )
            if "xl/workbook.xml" not in names or "[Content_Types].xml" not in names:
                raise ExcelBoundaryError(
                    "文件内容不是有效的 .xlsx 工作簿（缺少 xl/workbook.xml 或 [Content_Types].xml）",
                    status_code=400,
                    code="not_ooxml_workbook",
                )

            # Stream-verify actual decompressed bytes to defeat forged zip headers.
            # R02 A-M5-03: the extraction loop itself must observe cancellation and the monotonic deadline.
            actual_uncompressed = 0
            for info in infos:
                if info.is_dir():
                    continue
                with zf.open(info, "r") as member:
                    while True:
                        block = member.read(65536)
                        if not block:
                            break
                        actual_uncompressed += len(block)
                        if actual_uncompressed > budget.max_zip_uncompressed_bytes:
                            raise ExcelBoundaryError(
                                ".xlsx 实际解压体积超出安全预算上限，已拒绝潜在 ZIP 炸弹文件",
                                status_code=400,
                                code="zip_actual_size_exceeded",
                            )
                        _check_budget_and_cancel(t0, budget, cancel_event)
    except ExcelBoundaryError:
        raise
    except (zipfile.BadZipFile, RuntimeError, ValueError, OSError) as exc:
        raise ExcelBoundaryError(
            f"损坏或不可读取的 .xlsx 文件: {exc}",
            status_code=400,
            code="corrupted_xlsx",
        ) from exc


def _validate_xls_container(file_path: str) -> None:
    magic = _read_magic_bytes(file_path, 8)
    if magic.startswith(ZIP_PK_LOCAL_MAGIC) or magic.startswith(ZIP_PK_EMPTY_MAGIC):
        raise ExcelBoundaryError(
            "文件扩展名为 .xls，但内部实际为 .xlsx/ZIP 格式，请使用正确扩展名",
            status_code=400,
            code="xlsx_disguised_as_xls",
        )
    if not magic.startswith(OLE2_CFB_MAGIC):
        raise ExcelBoundaryError(
            "文件头签名与标准 BIFF .xls (OLE2 CFB) 格式不符，请确认文件未损坏且非其他格式改名",
            status_code=400,
            code="invalid_xls_signature",
        )
    try:
        import xlrd  # noqa: F401
    except ImportError as exc:
        raise ExcelBoundaryError(
            "系统缺少 .xls 解析引擎 xlrd，无法读取 .xls 文件，请另存为 .xlsx 或安装 xlrd>=2.0.1",
            status_code=400,
            code="missing_xlrd_engine",
        ) from exc


def validate_excel_container(
    file_path: str,
    allowed_exts: FrozenSet[str],
    original_filename: Optional[str] = None,
    budget: Optional[ExcelParseBudget] = None,
    context_label: str = "Excel 导入",
    cancel_event: Optional[threading.Event] = None,
    started_at: Optional[float] = None,
) -> str:
    """Validate filename/path extension and binary magic/container without loading entire workbook."""
    active_budget = budget or get_default_budget()
    ext = validate_extension(original_filename or file_path, allowed_exts, context_label=context_label)
    if ext == ".xlsx":
        _validate_xlsx_container(
            file_path,
            active_budget,
            cancel_event=cancel_event,
            started_at=started_at,
        )
    elif ext == ".xls":
        _validate_xls_container(file_path)
    return ext


def _check_budget_and_cancel(
    started_at: float,
    budget: ExcelParseBudget,
    cancel_event: Optional[threading.Event],
) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ParseCancelledError("Excel 解析任务已取消或超时中止")
    if (time.monotonic() - started_at) > budget.parse_timeout_seconds:
        raise ParseTimeoutError(
            f"Excel 解析超过最大耗时预算 ({budget.parse_timeout_seconds:.1f}s)"
        )


def _load_xlsx_sheets(
    file_path: str,
    budget: ExcelParseBudget,
    max_sheets_to_read: Optional[int] = None,
    cancel_event: Optional[threading.Event] = None,
    started_at: Optional[float] = None,
) -> List[Tuple[str, List[Tuple[Any, ...]]]]:
    t0 = started_at if started_at is not None else time.monotonic()
    _validate_xlsx_container(file_path, budget, cancel_event=cancel_event, started_at=t0)
    wb = None
    sheets_out: List[Tuple[str, List[Tuple[Any, ...]]]] = []
    total_cells = 0
    fast_poll = budget.parse_timeout_seconds <= 5.0

    try:
        _check_budget_and_cancel(t0, budget, cancel_event)
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        # R02 A-M5-03: re-check after the blocking workbook open before any row is published.
        _check_budget_and_cancel(t0, budget, cancel_event)
        sheetnames = list(wb.sheetnames or [])
        if not sheetnames:
            raise ExcelBoundaryError("Excel 工作簿不包含任何工作表", status_code=400, code="no_sheets")
        if len(sheetnames) > budget.max_sheets:
            raise ExcelBoundaryError(
                f"工作表数量 ({len(sheetnames)}) 超过最大允许值 ({budget.max_sheets})",
                status_code=400,
                code="sheet_limit_exceeded",
            )

        target_names = sheetnames[:max_sheets_to_read] if max_sheets_to_read else sheetnames
        for name in target_names:
            _check_budget_and_cancel(t0, budget, cancel_event)
            ws = wb[name]
            # Critical: bypass forged/stale <dimension> XML so sparse/oversized columns are enforced.
            ws.reset_dimensions()

            sheet_rows: List[Tuple[Any, ...]] = []
            for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
                if fast_poll or (row_idx & 63) == 0:
                    _check_budget_and_cancel(t0, budget, cancel_event)
                if row_idx > budget.max_rows_per_sheet:
                    raise ExcelBoundaryError(
                        f"工作表 '{name}' 行数超过最大允许值 ({budget.max_rows_per_sheet})",
                        status_code=400,
                        code="row_limit_exceeded",
                    )
                col_count = len(row)
                if col_count > budget.max_cols_per_sheet:
                    raise ExcelBoundaryError(
                        f"工作表 '{name}' 列数 ({col_count}) 超过最大允许值 ({budget.max_cols_per_sheet})",
                        status_code=400,
                        code="col_limit_exceeded",
                    )
                total_cells += col_count
                if total_cells > budget.max_total_cells:
                    raise ExcelBoundaryError(
                        f"Excel 扫描单元格总数超过最大允许值 ({budget.max_total_cells})",
                        status_code=400,
                        code="cell_limit_exceeded",
                    )
                sheet_rows.append(tuple(row))
            sheets_out.append((name, sheet_rows))
    except ExcelBoundaryError:
        raise
    except Exception as exc:
        raise ExcelBoundaryError(
            f"读取 .xlsx 工作簿失败（文件可能损坏或含不受支持的加密/XML结构）: {exc}",
            status_code=400,
            code="xlsx_parse_failed",
        ) from exc
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass

    # R02 A-M5-03: final re-check before returning parsed rows to callers.
    _check_budget_and_cancel(t0, budget, cancel_event)
    return sheets_out


def _normalize_xlrd_cell(cell: Any, datemode: int, xlrd_mod: Any) -> Any:
    ctype = getattr(cell, "ctype", None)
    val = getattr(cell, "value", None)
    if val is None:
        return None
    empty_type = getattr(xlrd_mod, "XL_CELL_EMPTY", 0)
    blank_type = getattr(xlrd_mod, "XL_CELL_BLANK", 6)
    num_type = getattr(xlrd_mod, "XL_CELL_NUMBER", 2)
    bool_type = getattr(xlrd_mod, "XL_CELL_BOOLEAN", 4)
    if ctype in (empty_type, blank_type):
        return None
    if ctype == bool_type:
        return bool(val)
    if ctype == num_type and isinstance(val, float) and val.is_integer():
        return int(val)
    return val


def _load_xls_sheets(
    file_path: str,
    budget: ExcelParseBudget,
    max_sheets_to_read: Optional[int] = None,
    cancel_event: Optional[threading.Event] = None,
    started_at: Optional[float] = None,
    allow_mock_xlrd: bool = False,
) -> List[Tuple[str, List[Tuple[Any, ...]]]]:
    t0 = started_at if started_at is not None else time.monotonic()
    xlrd_mod = sys.modules.get("xlrd")
    if xlrd_mod is None:
        try:
            import xlrd as xlrd_mod  # type: ignore[no-redef]
        except ImportError as exc:
            raise ExcelBoundaryError(
                "系统缺少 .xls 解析引擎 xlrd，无法读取 .xls 文件",
                status_code=400,
                code="missing_xlrd_engine",
            ) from exc

    mock_mode = allow_mock_xlrd and is_mock_xlrd_module(xlrd_mod)
    if not mock_mode:
        _validate_xls_container(file_path)

    book = None
    sheets_out: List[Tuple[str, List[Tuple[Any, ...]]]] = []
    total_cells = 0
    fast_poll = budget.parse_timeout_seconds <= 5.0

    try:
        if mock_mode:
            _check_budget_and_cancel(t0, budget, cancel_event)
            book = xlrd_mod.open_workbook(file_path)
            _check_budget_and_cancel(t0, budget, cancel_event)
            sheet = book.sheet_by_index(0)
            nrows = int(getattr(sheet, "nrows", 0))
            if nrows > budget.max_rows_per_sheet:
                raise ExcelBoundaryError(
                    f".xls 行数 ({nrows}) 超过最大允许值 ({budget.max_rows_per_sheet})",
                    status_code=400,
                    code="row_limit_exceeded",
                )
            rows: List[Tuple[Any, ...]] = []
            for r in range(nrows):
                _check_budget_and_cancel(t0, budget, cancel_event)
                if hasattr(sheet, "row_values"):
                    vals = tuple(sheet.row_values(r))
                else:
                    ncols = int(getattr(sheet, "ncols", 0))
                    vals = tuple(sheet.cell_value(r, c) for c in range(ncols))
                if len(vals) > budget.max_cols_per_sheet:
                    raise ExcelBoundaryError(
                        f".xls 列数 ({len(vals)}) 超过最大允许值 ({budget.max_cols_per_sheet})",
                        status_code=400,
                        code="col_limit_exceeded",
                    )
                total_cells += len(vals)
                if total_cells > budget.max_total_cells:
                    raise ExcelBoundaryError(
                        f"Excel 扫描单元格总数超过最大允许值 ({budget.max_total_cells})",
                        status_code=400,
                        code="cell_limit_exceeded",
                    )
                rows.append(vals)
            sheets_out.append((getattr(sheet, "name", "Sheet1"), rows))
            _check_budget_and_cancel(t0, budget, cancel_event)
            return sheets_out

        _check_budget_and_cancel(t0, budget, cancel_event)
        book = xlrd_mod.open_workbook(file_path, on_demand=True)
        # R02 A-M5-03: re-check after the blocking workbook open before any sheet is published.
        _check_budget_and_cancel(t0, budget, cancel_event)
        nsheets = int(getattr(book, "nsheets", 0))
        if nsheets <= 0:
            raise ExcelBoundaryError("Excel 工作簿不包含任何工作表", status_code=400, code="no_sheets")
        if nsheets > budget.max_sheets:
            raise ExcelBoundaryError(
                f"工作表数量 ({nsheets}) 超过最大允许值 ({budget.max_sheets})",
                status_code=400,
                code="sheet_limit_exceeded",
            )

        count = min(nsheets, max_sheets_to_read) if max_sheets_to_read else nsheets
        datemode = int(getattr(book, "datemode", 0))
        for s_idx in range(count):
            _check_budget_and_cancel(t0, budget, cancel_event)
            sheet = book.sheet_by_index(s_idx)
            try:
                nrows = int(sheet.nrows)
                ncols = int(sheet.ncols)
                if nrows > budget.max_rows_per_sheet:
                    raise ExcelBoundaryError(
                        f"工作表 '{sheet.name}' 行数 ({nrows}) 超过最大允许值 ({budget.max_rows_per_sheet})",
                        status_code=400,
                        code="row_limit_exceeded",
                    )
                if ncols > budget.max_cols_per_sheet:
                    raise ExcelBoundaryError(
                        f"工作表 '{sheet.name}' 列数 ({ncols}) 超过最大允许值 ({budget.max_cols_per_sheet})",
                        status_code=400,
                        code="col_limit_exceeded",
                    )
                sheet_rows: List[Tuple[Any, ...]] = []
                for r in range(nrows):
                    if fast_poll or (r & 63) == 0:
                        _check_budget_and_cancel(t0, budget, cancel_event)
                    total_cells += ncols
                    if total_cells > budget.max_total_cells:
                        raise ExcelBoundaryError(
                            f"Excel 扫描单元格总数超过最大允许值 ({budget.max_total_cells})",
                            status_code=400,
                            code="cell_limit_exceeded",
                        )
                    row_cells = sheet.row_slice(r) if hasattr(sheet, "row_slice") else None
                    if row_cells is not None:
                        vals = tuple(_normalize_xlrd_cell(c, datemode, xlrd_mod) for c in row_cells)
                    else:
                        vals = tuple(sheet.row_values(r))
                    sheet_rows.append(vals)
                sheets_out.append((getattr(sheet, "name", f"Sheet{s_idx + 1}"), sheet_rows))
            finally:
                if hasattr(book, "unload_sheet"):
                    try:
                        book.unload_sheet(s_idx)
                    except Exception:
                        pass
    except ExcelBoundaryError:
        raise
    except Exception as exc:
        if mock_mode:
            raise
        raise ExcelBoundaryError(
            f"读取 .xls 工作簿失败（文件可能损坏、加密或非标准 BIFF 格式）: {exc}",
            status_code=400,
            code="xls_parse_failed",
        ) from exc
    finally:
        if book is not None and hasattr(book, "release_resources"):
            try:
                book.release_resources()
            except Exception:
                pass

    # R02 A-M5-03: final re-check before returning parsed rows to callers.
    _check_budget_and_cancel(t0, budget, cancel_event)
    return sheets_out


def load_validated_excel_sheets(
    file_path: str,
    allowed_exts: FrozenSet[str] = frozenset({".xlsx"}),
    original_filename: Optional[str] = None,
    budget: Optional[ExcelParseBudget] = None,
    max_sheets_to_read: Optional[int] = None,
    cancel_event: Optional[threading.Event] = None,
    context_label: str = "Excel 导入",
    allow_mock_xlrd: bool = False,
) -> List[Tuple[str, List[Tuple[Any, ...]]]]:
    """
    Validate extension, binary signature/container, and read rows under strict budgets.
    """
    active_budget = budget or get_default_budget()
    started_at = time.monotonic()
    ext = validate_extension(original_filename or file_path, allowed_exts, context_label=context_label)
    # R02 A-M5-03: unified file-size budget at the shared entry so every caller
    # (DB upload, pre-check/prepare pipeline, personnel import, dataframe adapter) is covered.
    _enforce_file_byte_budget(file_path, active_budget, context_label)

    if ext == ".xlsx":
        return _load_xlsx_sheets(
            file_path=file_path,
            budget=active_budget,
            max_sheets_to_read=max_sheets_to_read,
            cancel_event=cancel_event,
            started_at=started_at,
        )
    if ext == ".xls":
        return _load_xls_sheets(
            file_path=file_path,
            budget=active_budget,
            max_sheets_to_read=max_sheets_to_read,
            cancel_event=cancel_event,
            started_at=started_at,
            allow_mock_xlrd=allow_mock_xlrd,
        )
    raise ExcelBoundaryError(f"不支持的扩展名: {ext}", status_code=400, code="unsupported_extension")


def count_non_empty_rows(rows: Sequence[Tuple[Any, ...]]) -> int:
    return sum(
        1
        for row in rows
        if any(cell is not None and str(cell).strip() != "" for cell in row)
    )


def ensure_workbook_non_empty(
    sheets: Sequence[Tuple[str, Sequence[Tuple[Any, ...]]]],
    context_label: str = "Excel 文件",
) -> None:
    if not sheets:
        raise ExcelBoundaryError(f"{context_label}不含可解析的工作表", status_code=400, code="no_sheets")
    for _, rows in sheets:
        if count_non_empty_rows(rows) > 0:
            return
    raise ExcelBoundaryError(
        f"{context_label}为空表，不包含有效数据行",
        status_code=400,
        code="empty_workbook",
    )


def load_validated_dataframe_from_excel(
    file_path: str,
    header_row_index: int = 1,
    allowed_exts: FrozenSet[str] = frozenset({".xlsx", ".xls"}),
    original_filename: Optional[str] = None,
    budget: Optional[ExcelParseBudget] = None,
    cancel_event: Optional[threading.Event] = None,
    context_label: str = "物料库导入",
) -> pd.DataFrame:
    """
    Read the first sheet under strict container and cell/time budgets and convert to a pandas DataFrame
    matching `pd.read_excel(..., header=header_row_index)` row/column semantics.
    """
    sheets = load_validated_excel_sheets(
        file_path=file_path,
        allowed_exts=allowed_exts,
        original_filename=original_filename,
        budget=budget,
        max_sheets_to_read=1,
        cancel_event=cancel_event,
        context_label=context_label,
    )
    ensure_workbook_non_empty(sheets, context_label=context_label)
    _, rows = sheets[0]
    if len(rows) <= header_row_index:
        raise ExcelBoundaryError(
            f"{context_label}缺少表头行（至少需要 {header_row_index + 1} 行，当前仅 {len(rows)} 行）",
            status_code=400,
            code="missing_header_row",
        )

    header_raw = list(rows[header_row_index])
    data_raw = [list(r) for r in rows[header_row_index + 1 :]]
    width = max(len(header_raw), max((len(r) for r in data_raw), default=0))

    columns: List[str] = []
    seen: Dict[str, int] = {}
    for idx in range(width):
        val = header_raw[idx] if idx < len(header_raw) else None
        col = str(val).strip() if val is not None and str(val).strip() != "" else f"Unnamed: {idx}"
        if col in seen:
            seen[col] += 1
            col_unique = f"{col}.{seen[col]}"
        else:
            seen[col] = 0
            col_unique = col
        columns.append(col_unique)

    padded_rows = [
        (r + [None] * (width - len(r)))[:width]
        for r in data_raw
    ]
    return pd.DataFrame(padded_rows, columns=columns, dtype=object)
