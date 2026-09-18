"""Generic Markdown pipe-table extraction.

Every hospital 1/3/4 contract states its rate schedules, premiums, discounts,
caps, bundles and exclusion windows as Markdown tables. Rather than
hand-transcribing numbers out of the .md files (error-prone, and impossible
to re-check against the source), we parse the tables mechanically and let
each hospital's contract-reader module assign meaning to the columns.

This keeps the "which section means what" judgment call in one visible
place per hospital, while the "read the numbers off the page correctly"
part is generic and testable once.
"""
from __future__ import annotations

import re
from pathlib import Path


def parse_markdown_tables(text: str) -> list[list[dict]]:
    """Return every pipe-table in `text` as a list of tables, each a list of
    row-dicts keyed by the table's header cells (stripped, as written).
    """
    lines = text.splitlines()
    tables: list[list[dict]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_table_row(line) and i + 1 < len(lines) and _is_separator_row(lines[i + 1]):
            header = _split_row(line)
            i += 2
            rows: list[dict] = []
            while i < len(lines) and _is_table_row(lines[i]):
                cells = _split_row(lines[i])
                if len(cells) == len(header):
                    rows.append(dict(zip(header, cells)))
                i += 1
            tables.append(rows)
        else:
            i += 1
    return tables


def _is_table_row(line: str) -> bool:
    return line.strip().startswith("|") and line.strip().endswith("|")


def _is_separator_row(line: str) -> bool:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return False
    inner = stripped.strip("|")
    return bool(re.fullmatch(r"[\s:\-|]+", inner))


def _split_row(line: str) -> list[str]:
    inner = line.strip().strip("|")
    return [cell.strip() for cell in inner.split("|")]


def load_tables(path: Path) -> list[list[dict]]:
    return parse_markdown_tables(Path(path).read_text(encoding="utf-8"))


def find_table(tables: list[list[dict]], *exact_headers: str) -> list[dict]:
    """Return the one table whose header set is *exactly* `exact_headers`.

    Exact matching (not "contains") matters here: several contracts reuse a
    generic column name like "Uplift" across more than one table (a
    threshold-premium table and a separate weekend-uplift table), so a
    subset match would silently grab the wrong one.
    """
    wanted = frozenset(exact_headers)
    matches = [t for t in tables if t and frozenset(t[0]) == wanted]
    if not matches:
        raise ValueError(f"no table found with exact headers {exact_headers}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous: {len(matches)} tables have headers {exact_headers}")
    return matches[0]
