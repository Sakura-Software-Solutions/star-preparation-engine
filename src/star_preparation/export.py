from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .engine import TARGET_FIELDS


def write_run(result: dict[str, Any], output: str | Path) -> None:
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    approved = result["validation_report"]["approved"]
    csv_name = "star_defects.csv" if approved else "proposed_star_defects.csv"
    with (destination / csv_name).open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=TARGET_FIELDS)
        writer.writeheader()
        writer.writerows(result["output_rows"])
    with (destination / "decision-log.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=result["decisions"][0].keys() if result["decisions"] else [])
        if result["decisions"]:
            writer.writeheader()
            writer.writerows(result["decisions"])
    for name in ("profile_report", "validation_report", "metadata"):
        (destination / f"{name.replace('_', '-')}.json").write_text(
            json.dumps(result[name], indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
