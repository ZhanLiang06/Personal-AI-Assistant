"""
Human-readable design codes for finance records.

Every finance table exposes a stable, readable identity such as
`TXN-000038` or `CAT-007` alongside its integer primary key. The code is
what the agent, the dashboard, and any exported report refer to, so a
record can be named unambiguously in conversation without leaking or
depending on raw row ids.

Two properties make the code more useful than the row id:

- It is stable across list refreshes, so an agent tool can address a
  transaction by code instead of the fragile index + expected_text
  pattern the todo tools use.
- It is obviously typed. `TXN-000038` cannot be confused for a category
  the way the bare integer 38 can.

The format is a flat zero-padded sequence with no date segment. A year
segment would read well, but it could not be assigned before
`occurred_at` was known, and editing a transaction's date across a year
boundary would either break the code or make it lie.

Codes are allocated from a persistent `code_sequences` counter rather
than from `max(existing code) + 1`. The difference matters: a hard
DELETE lowers the maximum, and a max-based allocator would then hand the
freed code to a different record, silently repointing any report or chat
message that named it. Most finance tables soft-delete, but `budgets`
and `goals` have no soft-delete column, so the hazard is real.

The counter is read and advanced inside the caller's write transaction.
SQLite serializes writers, so no two concurrent inserts can claim the
same sequence.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class CodeSpec:
    """How one table's design codes are shaped."""

    table: str
    prefix: str
    width: int

    def format(self, sequence: int) -> str:
        if sequence < 1:
            raise ValueError(f"Code sequence must be positive, got {sequence}.")

        return f"{self.prefix}-{sequence:0{self.width}d}"


# Transactions get six digits because they are the only table expected to
# reach five figures. The rest stay at three, which reads better and is
# still ample for reference data.
CODE_SPECS: dict[str, CodeSpec] = {
    spec.table: spec
    for spec in (
        CodeSpec(table="accounts", prefix="ACC", width=3),
        CodeSpec(table="categories", prefix="CAT", width=3),
        CodeSpec(table="subcategories", prefix="SUB", width=3),
        CodeSpec(table="transactions", prefix="TXN", width=6),
        CodeSpec(table="budgets", prefix="BGT", width=3),
        CodeSpec(table="goals", prefix="GOL", width=3),
    )
}

PREFIX_TO_TABLE: dict[str, str] = {
    spec.prefix: spec.table for spec in CODE_SPECS.values()
}

_CODE_PATTERN = re.compile(r"^([A-Z]{3})-(\d+)$")


class InvalidCodeError(ValueError):
    """The supplied string is not a well-formed design code."""


def spec_for(table: str) -> CodeSpec:
    """Return the code spec for a table, or raise for an unknown table."""
    try:
        return CODE_SPECS[table]
    except KeyError:
        raise InvalidCodeError(
            f"{table!r} has no design code. Known tables: "
            f"{', '.join(sorted(CODE_SPECS))}."
        ) from None


def normalize_code(code: str) -> str:
    """
    Clean up a user- or model-supplied code.

    Accepts lowercase and stray whitespace, because a code typed in chat
    will not always arrive perfectly formed. Does not accept a bare
    number: that would reintroduce exactly the ambiguity the code exists
    to remove.
    """
    candidate = code.strip().upper().replace(" ", "")

    if not _CODE_PATTERN.match(candidate):
        raise InvalidCodeError(
            f"{code!r} is not a design code. Expected a form like TXN-000038 "
            f"or CAT-007."
        )

    return candidate


def parse_code(code: str) -> tuple[str, int]:
    """Split a code into its table name and sequence number."""
    candidate = normalize_code(code)
    match = _CODE_PATTERN.match(candidate)
    assert match is not None  # normalize_code already enforced the shape

    prefix, digits = match.group(1), match.group(2)

    if prefix not in PREFIX_TO_TABLE:
        raise InvalidCodeError(
            f"{code!r} uses unknown prefix {prefix!r}. Known prefixes: "
            f"{', '.join(sorted(PREFIX_TO_TABLE))}."
        )

    return PREFIX_TO_TABLE[prefix], int(digits)


def code_for_table(code: str, expected_table: str) -> str:
    """
    Normalize a code and assert it belongs to the expected table.

    Guards against an agent passing `CAT-003` where a transaction was
    wanted, which would otherwise silently match nothing.
    """
    table, _ = parse_code(code)

    if table != expected_table:
        expected_prefix = spec_for(expected_table).prefix
        raise InvalidCodeError(
            f"{code!r} identifies a {table} record, but a {expected_table} "
            f"record was expected. {expected_table.capitalize()} codes start "
            f"with {expected_prefix}-."
        )

    return normalize_code(code)


def highest_assigned(connection: sqlite3.Connection, spec: CodeSpec) -> int:
    """
    The largest sequence currently present in a table's data.

    Taken over the numeric suffix rather than the string, so `TXN-000009`
    correctly precedes `TXN-000010` instead of sorting after it.
    """
    offset = len(spec.prefix) + 2  # skip the prefix and the hyphen

    row = connection.execute(
        f"""
        SELECT COALESCE(MAX(CAST(substr(code, {offset}) AS INTEGER)), 0) AS highest
        FROM {spec.table}
        WHERE code IS NOT NULL
        """
    ).fetchone()

    return int(row["highest"])


def _peek_sequence(connection: sqlite3.Connection, spec: CodeSpec) -> int:
    """
    The next sequence value for a table, without consuming it.

    Never returns a value at or below what the data already contains.
    That floor is a safety net: if `code_sequences` were lost, cleared,
    or restored from an older backup, the allocator still cannot hand out
    a code that is already in use.
    """
    row = connection.execute(
        "SELECT next_value FROM code_sequences WHERE table_name = ?",
        (spec.table,),
    ).fetchone()

    stored = int(row["next_value"]) if row is not None else 0

    return max(stored, highest_assigned(connection, spec) + 1)


def _store_sequence(
    connection: sqlite3.Connection,
    spec: CodeSpec,
    next_value: int,
) -> None:
    connection.execute(
        """
        INSERT INTO code_sequences (table_name, next_value)
        VALUES (?, ?)
        ON CONFLICT (table_name) DO UPDATE SET
            next_value = max(next_value, excluded.next_value)
        """,
        (spec.table, next_value),
    )


def next_code(connection: sqlite3.Connection, table: str) -> str:
    """
    Allocate the next unused code for a table and consume it.

    Must be called inside the same write transaction as the insert it is
    for, so the counter read and the insert that consumes it cannot be
    interleaved with another writer.

    The counter only ever moves forward. Deleting a record — softly or
    permanently — does not free its code, so an old report never points
    at a different record than it did when it was written.
    """
    spec = spec_for(table)
    sequence = _peek_sequence(connection, spec)

    _store_sequence(connection, spec, sequence + 1)

    return spec.format(sequence)


def backfill_missing_codes(connection: sqlite3.Connection) -> dict[str, int]:
    """
    Assign a code to every row that lacks one, continuing each sequence.

    Used by the v1 -> v2 migration and by the Money Manager import, which
    writes rows in bulk without going through the service layer. Rows
    that already have a code are left untouched, so this is safe to run
    repeatedly and can never renumber history.

    Transactions are numbered in `occurred_at` order so TXN-000001 is the
    oldest. Every other table keeps insertion order. Returns how many
    codes were assigned per table.
    """
    assigned: dict[str, int] = {}

    for spec in CODE_SPECS.values():
        # Chronological for transactions, because the Money Manager
        # import inserted newest-first and row id order is therefore the
        # exact reverse of the order a person would expect.
        order = "occurred_at, id" if spec.table == "transactions" else "id"

        rows = connection.execute(
            f"SELECT id FROM {spec.table} WHERE code IS NULL ORDER BY {order}"
        ).fetchall()

        if not rows:
            # Still advance the counter past anything already present,
            # so a table that was backfilled by an earlier run cannot
            # hand out a duplicate later.
            _store_sequence(connection, spec, _peek_sequence(connection, spec))
            continue

        start = _peek_sequence(connection, spec)

        connection.executemany(
            f"UPDATE {spec.table} SET code = ? WHERE id = ?",
            [
                (spec.format(start + offset), row["id"])
                for offset, row in enumerate(rows)
            ],
        )

        _store_sequence(connection, spec, start + len(rows))
        assigned[spec.table] = len(rows)

    return assigned
