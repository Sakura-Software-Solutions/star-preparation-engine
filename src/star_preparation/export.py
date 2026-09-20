"""STAR-compatible exports plus complete preparation evidence."""
from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from .engine import TARGET_FIELDS

STAR_FIELDS = ("defect_id", "component", "severity", "arrival_date", "closure_date")


def _safe_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv(path: Path, rows: list[dict], fields, *, protect: bool = True) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _safe_cell(row.get(k, "")) if protect else row.get(k, "") for k in fields})


def star_rows(rows: list[dict]) -> list[dict]:
    return [{"defect_id": r["source_id"], **{k: r[k] for k in TARGET_FIELDS if k != "source_id"}} for r in rows]


def write_run(result: dict[str, Any], output: str | Path) -> None:
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    # Never leave a previously approved artifact behind when overwriting a CLI run.
    if any(p.name not in {"source.csv", "source.xlsx"} for p in destination.iterdir()):
        raise ValueError("Output already contains a run; choose a new output directory")
    approved = result["validation_report"]["approved"]
    name = "star_defects.csv" if approved else "proposed_star_defects.csv"
    write_csv(destination / name, star_rows(result["output_rows"]), STAR_FIELDS)
    decisions = result["decisions"]
    write_csv(destination / "decision-log.csv", decisions,
              list(decisions[0]) if decisions else ["source_row", "decision", "reason"])
    rows = result["prepared_rows"]
    write_csv(destination / "prepared-defects.csv", rows,
              ["source_row", *TARGET_FIELDS, "status", "found_in_version", "release"])
    write_csv(destination / "release-timeline.csv", result["series"],
              ["release", "date", "new_defects", "cumulative_defects"])
    write_csv(destination / "release-totals.csv", result["release_totals"], ["release", "count", "release_date"])
    manifest = []
    if approved:
        by_release = defaultdict(list)
        for row in rows:
            by_release[row["release"]].append(row)
        for index, (release, records) in enumerate(sorted(by_release.items()), 1):
            filename = f"release-{index:03d}-star.csv"
            write_csv(destination / filename, star_rows(records), STAR_FIELDS)
            manifest.append({"release": release, "file": filename, "rows": len(records)})
    for key in ("profile_report", "validation_report", "metadata", "profile", "resolutions"):
        (destination / f"{key.replace('_', '-')}.json").write_text(
            json.dumps(result[key], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (destination / "release-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (destination / "result.json").write_text(json.dumps(result), encoding="utf-8")
    artifacts = sorted(p for p in destination.iterdir() if p.is_file() and p.name != "result.json")
    checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts}
    (destination / "checksums.json").write_text(json.dumps(checksums, indent=2), encoding="utf-8")
    with zipfile.ZipFile(destination / "preparation-bundle.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in [*artifacts, destination / "checksums.json"]:
            bundle.write(path, path.name)
