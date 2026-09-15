"""Small dependency-free readers for the first CSV/XLSX prototype."""

from __future__ import annotations

import csv
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS = {"x": _SHEET_NS}


def _column_index(reference: str) -> int:
    letters = "".join(char for char in reference if char.isalpha())
    number = 0
    for letter in letters:
        number = number * 26 + ord(letter.upper()) - 64
    return number - 1


def _excel_date(value: str) -> str:
    try:
        serial = float(value)
    except ValueError:
        return value
    # Excel's 1900 date system, including its historical leap-year bug.
    return (date(1899, 12, 30) + timedelta(days=serial)).isoformat()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return [dict(row) for row in csv.DictReader(source)]


def read_xlsx(path: Path) -> list[dict[str, str]]:
    """Read the first worksheet of a simple tabular XLSX export.

    Formula evaluation, macros, merged cells, and multi-sheet selection are
    intentionally outside this first local prototype.
    """
    with zipfile.ZipFile(path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [
                "".join(node.text or "" for node in item.iter(f"{{{_SHEET_NS}}}t"))
                for item in root.findall("x:si", _NS)
            ]

        worksheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows: list[list[str]] = []
        for row in worksheet.findall(".//x:sheetData/x:row", _NS):
            cells: dict[int, str] = {}
            for cell in row.findall("x:c", _NS):
                index = _column_index(cell.attrib["r"])
                value_node = cell.find("x:v", _NS)
                value = "" if value_node is None else value_node.text or ""
                cell_type = cell.attrib.get("t")
                if cell_type == "s" and value:
                    value = shared_strings[int(value)]
                elif cell_type == "inlineStr":
                    value = "".join(
                        node.text or "" for node in cell.iter(f"{{{_SHEET_NS}}}t")
                    )
                cells[index] = value
            width = max(cells, default=-1) + 1
            rows.append([cells.get(index, "") for index in range(width)])

    if not rows:
        return []
    headers = rows[0]
    records: list[dict[str, str]] = []
    for raw_row in rows[1:]:
        padded = raw_row + [""] * (len(headers) - len(raw_row))
        records.append(dict(zip(headers, padded)))
    return records


def read_records(path: str | Path) -> list[dict[str, str]]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        return read_csv(source)
    if suffix == ".xlsx":
        return read_xlsx(source)
    raise ValueError(f"Unsupported input format: {source.suffix}; expected .csv or .xlsx")


def coerce_excel_date(value: str) -> str:
    """Convert numeric Excel cells only when a mapped source field is a date."""
    return _excel_date(value.strip()) if re.fullmatch(r"\d+(?:\.\d+)?", value.strip()) else value.strip()
