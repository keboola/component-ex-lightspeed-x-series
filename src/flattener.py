"""Flattens raw Lightspeed X-Series records into parent + child table rows.

Strips the response envelope (`data`/`version`) — the caller only ever passes
in the `data` array — and any per-record internal keys such as `_metadata`
that are not part of a stream's declared column schema; the `version` cursor
is persisted to Keboola state, never written as output data (design spec
§4.4). Any column value that is still a `dict`/`list` after extraction (a
nested object that is not modelled as a child table) is serialised to a
single JSON string column rather than exploded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from streams import ChildTableSpec, ColumnSchema, StreamDefinition

# Keys the API attaches to every record that are never part of an output column
# schema; skipped defensively even though `_extract_row` only reads declared columns.
_INTERNAL_KEYS = {"_metadata", "version"}


@dataclass
class FlattenResult:
    """Flattened rows for one stream: one parent row-set + one per child table."""

    parent_rows: list[dict[str, Any]] = field(default_factory=list)
    child_rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @classmethod
    def empty(cls, stream: StreamDefinition) -> FlattenResult:
        return cls(parent_rows=[], child_rows={child.table_name: [] for child in stream.child_tables})

    def extend(self, other: FlattenResult) -> None:
        self.parent_rows.extend(other.parent_rows)
        for table_name, rows in other.child_rows.items():
            self.child_rows.setdefault(table_name, []).extend(rows)


def flatten_records(stream: StreamDefinition, records: list[dict]) -> FlattenResult:
    """Flatten one page of raw records for `stream` into parent + child rows."""
    result = FlattenResult.empty(stream)
    parent_id_field = stream.parent_primary_key[0] if stream.parent_primary_key else None
    for record in records:
        parent_id = record.get(parent_id_field, "") if parent_id_field else ""
        result.parent_rows.append(_extract_row(record, stream.columns))
        for child in stream.child_tables:
            result.child_rows[child.table_name].extend(_flatten_child(child, record, parent_id))
    return result


def _flatten_child(child: ChildTableSpec, parent_record: dict, parent_id: Any) -> list[dict[str, Any]]:
    items = parent_record.get(child.source_field) or []
    rows = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        row = _extract_row(item, child.columns)
        row[child.parent_fk_column] = parent_id
        # Synthesise a stable composite key when the nested line has no own id
        # (e.g. tax/adjustment lines, design spec §2.1 / §9 risk #6).
        if not row.get(child.id_field):
            row[child.id_field] = str(index)
        rows.append(row)
    return rows


def _extract_row(record: dict, columns: ColumnSchema) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for name in columns:
        if name in _INTERNAL_KEYS:
            continue
        value = record.get(name)
        if isinstance(value, dict | list):
            value = json.dumps(value, default=str)
        elif value is None:
            value = ""
        row[name] = value
    return row
