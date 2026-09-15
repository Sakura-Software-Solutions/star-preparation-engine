from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .profiler import profile_records
from .reader import coerce_excel_date, read_records

TARGET_FIELDS = ("source_id", "component", "severity", "arrival_date", "closure_date")


def load_profile(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as source:
        return json.load(source)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normal(value: str, blanks: set[str]) -> str | None:
    stripped = (value or "").strip()
    return None if not stripped or stripped.casefold() in blanks else stripped


def prepare(input_path: str | Path, profile: dict[str, Any]) -> dict[str, Any]:
    source = Path(input_path)
    records = read_records(source)
    mapping = profile["source_columns"]
    missing_columns = sorted(set(mapping.values()) - set(records[0] if records else []))
    if missing_columns:
        raise ValueError(f"Source file is missing mapped column(s): {', '.join(missing_columns)}")

    blanks = {value.casefold() for value in profile.get("normalization", {}).get("blank_values", [""])}
    allowed_statuses = {value.casefold() for value in profile.get("inclusion_rules", {}).get("statuses", [])}
    version_policy = profile.get("inclusion_rules", {}).get("version_policy", {"mode": "ignore"})
    aliases = {
        key.casefold(): value
        for key, value in version_policy.get("aliases", {}).items()
    }
    decisions: list[dict[str, Any]] = []
    output_rows: list[dict[str, str]] = []
    counts: Counter[str] = Counter()

    for number, record in enumerate(records, start=2):
        source_id = _normal(record.get(mapping["source_id"], ""), blanks)
        status = _normal(record.get(mapping.get("status", ""), ""), blanks)
        version = _normal(record.get(mapping.get("found_in_version", ""), ""), blanks)
        normalized_version = aliases.get(version.casefold(), version) if version else None
        row: dict[str, str | None] = {}
        for target in TARGET_FIELDS:
            value = _normal(record.get(mapping.get(target, ""), ""), blanks)
            row[target] = coerce_excel_date(value) if target.endswith("_date") and value else value

        decision, reason, rule_id = "included", "Mapped and validated", "MAP-VALID"
        if not source_id:
            decision, reason, rule_id = "excluded", "Missing source issue ID", "SOURCE-ID-REQUIRED"
        elif not row["arrival_date"]:
            decision, reason, rule_id = "excluded", "Missing arrival date", "ARRIVAL-DATE-REQUIRED"
        elif row["closure_date"] and row["closure_date"] < row["arrival_date"]:
            decision, reason, rule_id = "excluded", "Closure date precedes arrival date", "DATE-CHRONOLOGY"
        elif allowed_statuses and (not status or status.casefold() not in allowed_statuses):
            decision, reason, rule_id = "excluded", f"Status not in approved set: {status or '(blank)'}", "STATUS-NOT-IN-SCOPE"
        elif version_policy.get("mode") == "review_required":
            decision, reason, rule_id = "review_required", "Version cohort has not been approved", "VERSION-REVIEW-REQUIRED"
        elif version_policy.get("mode") == "allow_list":
            allowed_versions = {item.casefold() for item in version_policy.get("accepted_values", [])}
            if not normalized_version or normalized_version.casefold() not in allowed_versions:
                decision, reason, rule_id = "excluded", f"Version not in approved set: {normalized_version or '(blank)'}", "VERSION-NOT-IN-SCOPE"

        counts[decision] += 1
        decision_row = {
            "source_row": number,
            "source_id": source_id or "",
            "status": status or "",
            "found_in_version": version or "",
            "normalized_version": normalized_version or "",
            "decision": decision,
            "reason": reason,
            "rule_id": rule_id,
            "output_row": "",
        }
        if decision in {"included", "review_required"}:
            output_rows.append({key: value or "" for key, value in row.items()})
            decision_row["output_row"] = len(output_rows) + 1
        decisions.append(decision_row)

    approval_required = counts["review_required"] > 0
    return {
        "profile": profile,
        "profile_report": profile_records(records),
        "output_rows": output_rows,
        "decisions": decisions,
        "validation_report": {
            "rows_read": len(records),
            "rows_included": counts["included"],
            "rows_review_required": counts["review_required"],
            "rows_excluded": counts["excluded"],
            "approval_required": approval_required,
            "approved": not approval_required,
        },
        "metadata": {
            "input_file": source.name,
            "input_sha256": _hash_file(source),
            "profile_id": profile.get("profile_id"),
            "profile_sha256": hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }
