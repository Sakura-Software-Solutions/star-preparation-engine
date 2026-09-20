"""Deterministic preparation with explicit rules and a record-level decision trail."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .profiler import profile_records
from .reader import coerce_excel_date, read_table

TARGET_FIELDS = ("source_id", "component", "severity", "arrival_date", "closure_date")
MODEL_FIELDS = (*TARGET_FIELDS, "status", "found_in_version", "release")
RULE_CONFIRMATIONS = ("date", "statuses", "versions", "duplicates")
DUPLICATE_MODES = {"review", "issue_release", "issue", "keep_all"}


def load_profile(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validate_profile(profile: dict) -> None:
    if not isinstance(profile, dict) or not isinstance(profile.get("source_columns"), dict):
        raise ValueError("A profile needs source column mappings")
    if profile.get("schema_version", 1) not in {1, 2}:
        raise ValueError("Unsupported profile schema version")
    mapping = profile["source_columns"]
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()):
        raise ValueError("Column mappings must be text")
    rules = profile.get("inclusion_rules", {})
    version = rules.get("version_policy", {})
    if version.get("mode", "review_required") not in {"review_required", "allow_list", "mapped", "ignore"}:
        raise ValueError("Unknown version policy")
    if rules.get("duplicate_policy", "review") not in DUPLICATE_MODES:
        raise ValueError("Unknown duplicate policy")
    if profile.get("date_format", "iso") not in {"iso", "dmy", "mdy"}:
        raise ValueError("Unknown date format")
    for key in ("statuses",):
        if not isinstance(rules.get(key, []), list) or any(not isinstance(v, str) for v in rules.get(key, [])):
            raise ValueError(f"{key} must be a list of text values")
    if not isinstance(version.get("aliases", {}), dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in version.get("aliases", {}).items()):
        raise ValueError("Version aliases must map text to text")
    if not isinstance(version.get("accepted_values", []), list) or any(
            not isinstance(v, str) for v in version.get("accepted_values", [])):
        raise ValueError("Accepted releases must be a list of text values")
    if not isinstance(version.get("excluded_values", []), list) or any(
            not isinstance(v, str) for v in version.get("excluded_values", [])):
        raise ValueError("Excluded source versions must be a list of text values")
    blanks = profile.get("normalization", {}).get("blank_values", [""])
    if not isinstance(blanks, list) or any(not isinstance(v, str) for v in blanks):
        raise ValueError("Missing-value markers must be a list of text values")
    options = profile.get("source_options", {})
    if not isinstance(options, dict) or set(options) - {"sheet", "header_row", "encoding", "delimiter"}:
        raise ValueError("Unsupported source options")
    for release, day in profile.get("release_dates", {}).items():
        if coerce_excel_date(day) != day:
            raise ValueError(f"Release date for {release} must be YYYY-MM-DD")
    if profile.get("schema_version", 1) >= 2:
        confirmations = profile.get("confirmations", {})
        if any(not isinstance(confirmations.get(k, False), bool) for k in RULE_CONFIRMATIONS):
            raise ValueError("Rule confirmations must be true or false")


def suggest_profile(columns: list[str]) -> dict:
    synonyms = {
        "source_id": ["issueid", "sourceid", "defectid", "id", "issuekey"],
        "arrival_date": ["created", "createddate", "reporteddate", "arrivaldate", "issue"],
        "closure_date": ["resolved", "resolveddate", "closuredate", "closeddate"],
        "component": ["components", "component", "module"],
        "severity": ["priority", "severity"],
        "status": ["issuestatus", "status"],
        "found_in_version": ["versionnamefoundin", "affectsversionfoundin", "affectsversionsfoundin",
                             "foundinversion", "version", "release"],
    }
    normal = {re.sub(r"[^a-z0-9]", "", c.lower()): c for c in columns}
    return {"schema_version": 2, "profile_id": "customer", "source_columns": {
        key: next((normal[n] for n in names if n in normal), "") for key, names in synonyms.items()},
        "date_format": "iso", "normalization": {"blank_values": ["", "none", "NO_FW_Version"]},
        "inclusion_rules": {"statuses": [], "duplicate_policy": "review",
                            "version_policy": {"mode": "mapped", "aliases": {}, "accepted_values": []}},
        "confirmations": {k: False for k in RULE_CONFIRMATIONS}, "release_dates": {}}


def inspect_source(path: str | Path, options: dict | None = None) -> dict:
    table = read_table(path, **(options or {}))
    records = table.pop("records")
    table.pop("row_numbers")
    report = profile_records(records)
    report["columns"] = table["columns"]
    for column in table["columns"]:
        values = Counter(r.get(column, "") for r in records)
        report["fields"].setdefault(column, {})
        report["fields"][column]["values"] = [
            {"value": value, "count": count} for value, count in values.most_common(1000)
        ]
        report["fields"][column]["values_truncated"] = len(values) > 1000
    return {**table, "report": report, "suggested_profile": suggest_profile(table["columns"])}


def prepare(input_path: str | Path, profile: dict[str, Any], *,
            resolutions: dict | None = None) -> dict[str, Any]:
    validate_profile(profile)
    source = Path(input_path)
    table = read_table(source, **profile.get("source_options", {}))
    if not table["valid_headers"]:
        raise ValueError("Choose a header row containing unique, nonempty column names")
    mapping = profile["source_columns"]
    for required in ("source_id", "arrival_date"):
        if not mapping.get(required):
            raise ValueError(f"Map the required field: {required}")
    missing = sorted({v for v in mapping.values() if v} - set(table["columns"]))
    if missing:
        raise ValueError("Source file is missing mapped column(s): " + ", ".join(missing))
    records = table["records"]
    blanks = {v.casefold() for v in profile.get("normalization", {}).get("blank_values", [""])}
    rules = profile.get("inclusion_rules", {})
    statuses = {v.casefold() for v in rules.get("statuses", [])}
    vp = rules.get("version_policy", {})
    mode = vp.get("mode", "review_required")
    aliases = {k.casefold(): v.strip() for k, v in vp.get("aliases", {}).items()}
    accepted = {v.casefold() for v in vp.get("accepted_values", [])}
    excluded_versions = {v.casefold() for v in vp.get("excluded_values", [])}
    duplicate_mode = rules.get("duplicate_policy", "review")
    resolutions = resolutions or {}
    if not isinstance(resolutions, dict) or set(resolutions) - {str(n) for n in table["row_numbers"]}:
        raise ValueError("Corrections must refer to existing source row numbers")
    pending = []
    if profile.get("schema_version", 1) >= 2:
        pending = [k for k in RULE_CONFIRMATIONS if not profile.get("confirmations", {}).get(k, False)]
    if mode == "review_required":
        pending.append("release cohort")
    normalized, decisions = [], []

    def clean(value):
        text = str(value or "").strip()
        return "" if text.casefold() in blanks else text

    for number, record in zip(table["row_numbers"], records):
        row = {key: clean(record.get(mapping.get(key, ""), "")) for key in MODEL_FIELDS if key != "release"}
        row["found_in_version"] = str(record.get(mapping.get("found_in_version", ""), "")).strip()
        row["source_row"] = number
        row["release"] = aliases.get(row["found_in_version"].casefold(), "")
        if mode == "allow_list" and not row["release"]:
            row["release"] = row["found_in_version"]
        if mode == "ignore":
            row["release"] = "All defects"
        resolution = resolutions.get(str(number), {})
        if resolution:
            if not isinstance(resolution, dict):
                raise ValueError(f"Row {number}: invalid correction")
            if not resolution.get("reason", "").strip():
                raise ValueError(f"Row {number}: a correction/exclusion needs a reason")
            if resolution.get("action") not in {"edit", "exclude"}:
                raise ValueError(f"Row {number}: invalid resolution action")
            changes = resolution.get("values", {})
            if not isinstance(changes, dict) or any(not isinstance(v, str) for v in changes.values()):
                raise ValueError("Corrections must contain text values")
            if set(changes) - set(MODEL_FIELDS):
                raise ValueError("Unknown correction field")
            row.update({k: clean(v) for k, v in changes.items()})
        errors = []
        for key in ("arrival_date", "closure_date"):
            try:
                row[key] = coerce_excel_date(row[key], profile.get("date_format", "iso"), table["date_system"])
            except ValueError:
                errors.append(f"Invalid {key}: {row[key]}")
        if not row["source_id"]:
            errors.append("Missing issue ID")
        if not row["arrival_date"]:
            errors.append("Missing arrival date")
        if not errors and row["closure_date"] and row["closure_date"] < row["arrival_date"]:
            errors.append("Closure date precedes arrival date")
        if any(str(row[k]).lstrip().startswith(("=", "+", "-", "@", "#FORMULA!", "#CELL_ERROR!"))
               for k in MODEL_FIELDS):
            errors.append("Formula, cell error, or unsafe spreadsheet value; review and correct")
        if any("\n" in row[k] or "\r" in row[k] for k in TARGET_FIELDS):
            errors.append("Multiline STAR field; correct or exclude before import")
        decision, reason, rule_id = "included", "Mapped and validated", "MAP-VALID"
        if resolution.get("action") == "exclude":
            decision, reason, rule_id = "excluded", resolution["reason"], "MANUAL-EXCLUSION"
        elif row["found_in_version"].casefold() in excluded_versions:
            decision, reason, rule_id = "excluded", "Source version explicitly excluded", "VERSION-EXCLUDED"
        elif statuses and row["status"].casefold() not in statuses:
            decision, reason, rule_id = "excluded", "Status outside selected cohort", "STATUS-NOT-IN-SCOPE"
        elif mode == "allow_list" and row["release"].casefold() not in accepted:
            decision, reason, rule_id = "excluded", "Release outside selected cohort", "VERSION-NOT-IN-SCOPE"
        else:
            if mode != "ignore" and not row["release"]:
                errors.append("Unmapped release")
            if errors:
                decision, reason, rule_id = "review_required", "; ".join(errors), "ROW-REVIEW"
            elif pending:
                decision, reason, rule_id = "review_required", "Confirm rules: " + ", ".join(pending), "RULE-REVIEW"
        normalized.append(row)
        decisions.append({**row, "decision": decision, "reason": reason, "rule_id": rule_id,
                          "correction_reason": resolution.get("reason", ""), "output_row": ""})

    groups = defaultdict(list)
    for i, row in enumerate(normalized):
        if decisions[i]["decision"] != "excluded" and row["source_id"]:
            key = (row["source_id"], row["release"]) if duplicate_mode != "issue" else (row["source_id"],)
            groups[key].append(i)
    duplicate_rows = sum(len(indices) - 1 for indices in groups.values())
    for indices in groups.values():
        if len(indices) < 2 or duplicate_mode == "keep_all":
            continue
        if duplicate_mode == "review" or any(decisions[i]["rule_id"] == "ROW-REVIEW" for i in indices):
            for i in indices:
                decisions[i].update(decision="review_required", rule_id="DUPLICATE-REVIEW",
                                    reason=decisions[i]["reason"] + "; repeated issue ID requires review")
        else:
            winner = min(indices, key=lambda i: (normalized[i]["arrival_date"], normalized[i]["source_row"]))
            for i in indices:
                if i != winner:
                    decisions[i].update(decision="excluded", rule_id="DUPLICATE-COLLAPSED",
                                        reason=f"Earliest reported record retained at source row {normalized[winner]['source_row']}")
    counts = Counter(d["decision"] for d in decisions)
    candidate_rows, output_rows = [], []
    for row, decision in zip(normalized, decisions):
        if decision["decision"] != "excluded":
            candidate_rows.append(row)
            output_rows.append({k: row[k] for k in TARGET_FIELDS})
            decision["output_row"] = len(output_rows) + 1
    # The chart uses validated included rows only; unresolved records never inflate totals.
    daily = defaultdict(Counter)
    for row, decision in zip(normalized, decisions):
        if decision["decision"] == "included":
            daily[row["release"]][row["arrival_date"]] += 1
    series = []
    for release in sorted(daily):
        cumulative = 0
        for day, count in sorted(daily[release].items()):
            cumulative += count
            series.append({"release": release, "date": day, "new_defects": count, "cumulative_defects": cumulative})
    totals = [{"release": release, "count": sum(values.values()),
               "release_date": profile.get("release_dates", {}).get(release, "")}
              for release, values in sorted(daily.items())]
    approved = bool(counts["included"]) and not counts["review_required"] and not pending
    return {"profile": profile, "profile_report": profile_records(records),
            "output_rows": output_rows, "prepared_rows": candidate_rows, "decisions": decisions,
            "series": series, "release_totals": totals, "resolutions": resolutions,
            "validation_report": {"rows_read": len(records), "rows_included": counts["included"],
                "rows_review_required": counts["review_required"], "rows_excluded": counts["excluded"],
                "duplicate_rows": duplicate_rows, "pending_rules": pending,
                "approval_required": bool(counts["review_required"] or pending), "approved": approved,
                "warnings": (["No included records; no approved export generated"] if not counts["included"] else [])},
            "metadata": {"input_file": source.name, "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "profile_id": profile.get("profile_id"), "profile_sha256": fingerprint(profile),
                "resolutions_sha256": fingerprint(resolutions),
                "created_at": datetime.now(timezone.utc).isoformat()}}
