"""Bounded, offline CSV/XLSX inspection with source row tracking."""
from __future__ import annotations

import csv
import io
import posixpath
import re
import zipfile
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MAX_BYTES = 25 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 300


def _column_index(reference: str) -> int:
    n = 0
    for letter in re.sub(r"[^A-Za-z]", "", reference):
        n = n * 26 + ord(letter.upper()) - 64
    if not 0 < n <= MAX_COLUMNS:
        raise ValueError(f"Worksheet exceeds {MAX_COLUMNS} supported columns")
    return n - 1


def _xlsx(path: Path, sheet: str | None):
    with zipfile.ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_BYTES * 8:
            raise ValueError("Expanded workbook is too large")
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target", "") for r in rels}
        sheets = {s.get("name"): targets.get(s.get(f"{{{REL}}}id"), "")
                  for s in book.findall("x:sheets/x:sheet", NS)}
        selected = sheet or next(iter(sheets), None)
        if selected not in sheets:
            raise ValueError("Choose a worksheet from the workbook")
        target = sheets[selected]
        member = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            strings = ["".join(n.text or "" for n in s.findall(".//x:t", NS))
                       for s in root.findall("x:si", NS)]
        root = ET.fromstring(archive.read(member))
        rows = []
        for row in root.findall("x:sheetData/x:row", NS):
            values = {}
            for cell in row.findall("x:c", NS):
                idx = _column_index(cell.get("r", ""))
                value = cell.findtext("x:v", "", NS)
                if cell.find("x:f", NS) is not None:
                    value = "#FORMULA!"  # Cached results may be stale; never execute formulas.
                elif cell.get("t") == "s":
                    value = strings[int(value)] if value else ""
                elif cell.get("t") == "inlineStr":
                    value = "".join(n.text or "" for n in cell.findall(".//x:t", NS))
                elif cell.get("t") == "e":
                    value = "#CELL_ERROR!"
                values[idx] = value
            rows.append((int(row.get("r", len(rows) + 1)),
                         [values.get(i, "") for i in range(max(values, default=-1) + 1)]))
            if len(rows) > MAX_ROWS:
                raise ValueError(f"Maximum {MAX_ROWS} rows supported")
        props = book.find("x:workbookPr", NS)
        epoch = "1904" if props is not None and props.get("date1904") in {"1", "true"} else "1900"
        return rows, list(sheets), selected, epoch


def read_table(path: str | Path, *, sheet: str | None = None, header_row: int = 1,
               encoding: str = "utf-8-sig", delimiter: str = "auto") -> dict:
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Upload exceeds 25 MiB")
    if not isinstance(header_row, int) or not 1 <= header_row <= MAX_ROWS:
        raise ValueError("Header row must be a positive row number")
    sheets, selected, epoch = [], None, "1900"
    if path.suffix.lower() == ".xlsx":
        try:
            rows, sheets, selected, epoch = _xlsx(path, sheet)
        except (zipfile.BadZipFile, KeyError, ET.ParseError, IndexError) as error:
            raise ValueError("Invalid or unsupported XLSX workbook") from error
    elif path.suffix.lower() == ".csv":
        if encoding not in {"utf-8-sig", "cp1252"}:
            raise ValueError("Choose UTF-8 or Windows-1252 encoding")
        try:
            source = path.read_text(encoding=encoding)
        except UnicodeDecodeError as error:
            raise ValueError("Cannot decode CSV; try Windows-1252 encoding") from error
        if delimiter == "auto":
            try:
                delimiter = csv.Sniffer().sniff(source[:8192], delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = ","
        if delimiter not in {",", ";", "\t", "|"}:
            raise ValueError("Unsupported CSV delimiter")
        rows = []
        try:
            for values in csv.reader(io.StringIO(source), delimiter=delimiter):
                rows.append((len(rows) + 1, values))
                if len(rows) > MAX_ROWS or len(values) > MAX_COLUMNS:
                    raise ValueError("CSV exceeds supported row or column limit")
        except csv.Error as error:
            raise ValueError(f"Invalid CSV: {error}") from error
    else:
        raise ValueError("Only CSV and XLSX files are supported")
    header = list(next((values for number, values in rows if number == header_row), []))
    while header and not header[-1].strip():
        header.pop()
    headers = [v.strip() for v in header]
    duplicates = {value for value, count in Counter(headers).items() if count > 1 and value}
    original_headers = list(headers)
    headers = [f"{value} [{i}]" if value in duplicates else value or f"Column [{i}]"
               for i, value in enumerate(headers, 1)]
    # Generated positional names must also be unique when source labels contain suffixes.
    seen = set()
    for i, value in enumerate(headers):
        while value in seen:
            value += f" [{i + 1}]"
        headers[i] = value
        seen.add(value)
    valid_headers = bool(headers) and any(original_headers)
    records, numbers = [], []
    if valid_headers:
        for number, values in rows:
            if number <= header_row or not any(v.strip() for v in values):
                continue
            if any(v.strip() for v in values[len(headers):]):
                valid_headers = False
                records, numbers = [], []
                break
            records.append(dict(zip(headers, values + [""] * (len(headers) - len(values)))))
            numbers.append(number)
    return {"records": records, "row_numbers": numbers, "columns": headers,
            "sheets": sheets, "sheet": selected, "date_system": epoch,
            "header_row": header_row, "valid_headers": valid_headers,
            "original_headers": original_headers, "duplicate_headers": sorted(duplicates),
            "preview": [{"row": n, "values": v} for n, v in rows[:20]]}


def read_records(path: str | Path) -> list[dict[str, str]]:
    table = read_table(path)
    if not table["valid_headers"]:
        raise ValueError("Select a header row with unique, nonempty column names")
    return table["records"]


def coerce_excel_date(value: str, date_format: str = "iso", date_system: str = "1900") -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        if re.fullmatch(r"\d+(?:\.\d+)?", value):
            base = date(1904, 1, 1) if date_system == "1904" else date(1899, 12, 30)
            return (base + timedelta(days=float(value))).isoformat()
        if re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[T ])", value):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        formats = {"dmy": "%d/%m/%Y", "mdy": "%m/%d/%Y"}
        if date_format in formats:
            return datetime.strptime(value, formats[date_format]).date().isoformat()
    except (ValueError, OverflowError):
        pass
    raise ValueError("Invalid date; select ISO, day/month/year, or month/day/year")
