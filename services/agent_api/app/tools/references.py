"""Canonical cell identities shared by the graph and evidence validator."""

import json


def sql_cell_ref(query_id: str, row_index: int, column: str) -> str:
    return "sql-cell:" + json.dumps([query_id, row_index, column], ensure_ascii=False, separators=(",", ":"))
