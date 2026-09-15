from __future__ import annotations

from collections import Counter
from typing import Any


def profile_records(records: list[dict[str, str]]) -> dict[str, Any]:
    columns = list(records[0]) if records else []
    summary: dict[str, Any] = {"rows_read": len(records), "columns": columns, "fields": {}}
    for column in columns:
        values = [(row.get(column) or "").strip() for row in records]
        present = [value for value in values if value]
        summary["fields"][column] = {
            "nonempty": len(present),
            "empty": len(values) - len(present),
            "unique": len(set(present)),
            "top_values": [{"value": value, "count": count} for value, count in Counter(present).most_common(20)],
        }
    return summary
