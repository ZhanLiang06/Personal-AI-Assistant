"""
One-way import of a Money Manager `.xlsx` export into the finance database.

Money Manager is being retired, so this is a migration path, not a sync:
there is deliberately no export direction. All app-specific quirks of the
source file are isolated in this module so the internal schema stays clean.

Source columns (11, in order):
    Period, Accounts, Category, Subcategory, Note, MYR,
    Income/Expense, Description, Amount, Currency, Accounts(dup)

Two quirks worth knowing:

- The trailing `Accounts` column is mislabelled. It duplicates the `MYR`
  column exactly (verified across all 333 rows of the 2026-08-13 export),
  so it is read as a checksum and otherwise ignored.
- `Period` is an Excel serial datetime, not a string.

Money handling: the `MYR` column is treated as the source of truth for
`base_amount_minor`, and the FX rate is *derived* from it per row
(`MYR / Amount`). That keeps historical totals byte-identical to what
Money Manager reported, at the cost of many near-identical stored rates.

Run a dry run first; nothing is written without `--commit`:

    python -m src.finance.money_manager_import 2026-08-13.xlsx
    python -m src.finance.money_manager_import 2026-08-13.xlsx --commit
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

from src.db.finance_sqlite import FINANCE_DB_PATH, finance_db, init_finance_db


SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Excel's day zero. Excel treats 1900 as a leap year, so serial 1 is
# 1900-01-01 and the usable epoch is two days earlier than intuition
# suggests.
EXCEL_EPOCH = datetime(1899, 12, 30)

EXPECTED_HEADER = [
    "Period",
    "Accounts",
    "Category",
    "Subcategory",
    "Note",
    "MYR",
    "Income/Expense",
    "Description",
    "Amount",
    "Currency",
    "Accounts",
]

COLUMN_LETTERS = list("ABCDEFGHIJK")

BASE_CURRENCY = "MYR"

# Mirrors FX_RATE_SCALE in src/db/finance_sqlite.py.
FX_RATE_SCALE = 100_000_000

DIRECTION_BY_SOURCE_LABEL = {
    "Exp.": "expense",
    "Income": "income",
}

# --- Cleanup policy -------------------------------------------------
#
# Applied to the raw source labels during import. Everything not listed
# here passes through with whitespace trimmed and the leading emoji
# split into its own column.

# Categories folded into another category. The source export has one
# category whose name is an emoji with no text ("💝 "), which cannot be
# stored: `categories.name` carries CHECK (length(trim(name)) > 0).
# Its rows were jewellery, which belongs under Gift.
CATEGORY_MERGES = {
    "💝": "🎁 Gift",
}

# Typos and casing fixed on the way in.
CATEGORY_NAME_OVERRIDES = {
    "fitness": "Fitness",
}

SUBCATEGORY_NAME_OVERRIDES = {
    "sancks": "Snacks",
}

# Subcategories that merely restate their parent category carry no
# information, so the transaction is stored with no subcategory at all.
DROP_SUBCATEGORY_MATCHING_PARENT = True


@dataclass(frozen=True)
class SourceRow:
    """One raw spreadsheet row, before any interpretation."""

    row_number: int
    period: str
    account: str
    category: str
    subcategory: str | None
    note: str | None
    myr: str
    direction_label: str
    description: str | None
    amount: str
    currency: str
    myr_duplicate: str


@dataclass(frozen=True)
class PlannedTransaction:
    """A source row translated into finance-schema terms."""

    row_number: int
    occurred_at: str
    account: str
    category: str
    subcategory: str | None
    note: str | None
    description: str | None
    direction: str
    amount_minor: int
    currency: str
    fx_rate_scaled: int
    base_amount_minor: int


@dataclass
class ImportPlan:
    """Everything the import would write, plus what it noticed."""

    accounts: list[str] = field(default_factory=list)

    # category name -> emoji (None when the source had no emoji)
    categories: dict[str, str | None] = field(default_factory=dict)

    # category name -> sorted subcategory names
    subcategories: dict[str, list[str]] = field(default_factory=dict)

    transactions: list[PlannedTransaction] = field(default_factory=list)

    # Human-readable notes about anything changed or worth a second look.
    relabelled: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Totals carried straight from the source file, used to prove the
    # import did not lose or invent money.
    source_total_myr_minor: int = 0


# --- Workbook reading -----------------------------------------------


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []

    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))

    return [
        "".join(node.text or "" for node in item.iter(f"{SHEET_NS}t"))
        for item in root.findall(f"{SHEET_NS}si")
    ]


def read_workbook_rows(xlsx_path: str | Path) -> list[dict[str, str | None]]:
    """
    Return the first worksheet as a list of column-letter dicts.

    Reads the OOXML parts directly rather than depending on openpyxl.
    This import runs once against a retired app, so a permanent
    spreadsheet dependency is not worth carrying.
    """
    with zipfile.ZipFile(xlsx_path) as archive:
        shared_strings = _read_shared_strings(archive)
        sheet_xml = archive.read("xl/worksheets/sheet1.xml")

    rows: list[dict[str, str | None]] = []

    for row_node in ET.fromstring(sheet_xml).iter(f"{SHEET_NS}row"):
        cells: dict[str, str | None] = {}

        for cell in row_node.findall(f"{SHEET_NS}c"):
            reference = cell.get("r") or ""
            column_match = re.match(r"[A-Z]+", reference)
            if column_match is None:
                continue

            cell_type = cell.get("t")
            value_node = cell.find(f"{SHEET_NS}v")

            if cell_type == "s" and value_node is not None:
                value = shared_strings[int(value_node.text or "0")]
            elif cell_type == "inlineStr":
                value = "".join(
                    node.text or "" for node in cell.iter(f"{SHEET_NS}t")
                )
            else:
                value = value_node.text if value_node is not None else None

            cells[column_match.group()] = value

        rows.append(cells)

    return rows


def parse_source_rows(xlsx_path: str | Path) -> list[SourceRow]:
    """Read the workbook and validate its header before trusting it."""
    raw_rows = read_workbook_rows(xlsx_path)

    if not raw_rows:
        raise ValueError(f"{xlsx_path} contains no rows.")

    header = [(raw_rows[0].get(letter) or "").strip() for letter in COLUMN_LETTERS]
    if header != EXPECTED_HEADER:
        raise ValueError(
            "Unexpected Money Manager header.\n"
            f"  expected: {EXPECTED_HEADER}\n"
            f"  found:    {header}"
        )

    source_rows: list[SourceRow] = []

    for offset, raw_row in enumerate(raw_rows[1:], start=2):
        values = [raw_row.get(letter) for letter in COLUMN_LETTERS]

        # Money Manager writes fully blank spacer rows in some exports.
        if all(value is None or not str(value).strip() for value in values):
            continue

        source_rows.append(
            SourceRow(
                row_number=offset,
                period=str(values[0]),
                account=(values[1] or "").strip(),
                category=(values[2] or "").strip(),
                subcategory=_clean_optional(values[3]),
                note=_clean_optional(values[4]),
                myr=str(values[5]),
                direction_label=(values[6] or "").strip(),
                description=_clean_optional(values[7]),
                amount=str(values[8]),
                currency=(values[9] or "").strip().upper(),
                myr_duplicate=str(values[10]),
            )
        )

    return source_rows


def _clean_optional(value: str | None) -> str | None:
    """Collapse blanks and whitespace-only cells to None."""
    if value is None:
        return None

    cleaned = value.strip()

    return cleaned or None


# --- Value conversion -----------------------------------------------


def excel_serial_to_local_datetime(serial: str) -> datetime:
    """
    Convert an Excel serial datetime to naive local (UTC+8) time.

    Stored naive on purpose. SQLite's `date()` converts an
    offset-aware string to UTC first, so an 02:00+08:00 transaction
    would group into the previous day and quietly corrupt every daily
    total. All source data is Malaysia/China time, both UTC+8.
    """
    moment = EXCEL_EPOCH + timedelta(days=float(serial))

    # Serial values are floats, so times land a fraction of a second off
    # (12:05:41.999999 rather than 12:05:42).
    return (moment + timedelta(microseconds=500_000)).replace(microsecond=0)


def to_minor_units(value: str) -> int:
    """Convert a decimal money string to integer minor units (sen/fen)."""
    return int((Decimal(value) * 100).to_integral_value(rounding="ROUND_HALF_UP"))


def derive_fx_rate_scaled(amount: str, base_amount: str) -> int:
    """
    Derive the rate actually applied to this row, scaled to an integer.

    Uses the source `MYR` column as truth rather than any rate table,
    so imported totals reproduce Money Manager exactly.
    """
    amount_decimal = Decimal(amount)
    if amount_decimal == 0:
        raise ValueError("Cannot derive an FX rate from a zero amount.")

    rate = Decimal(base_amount) / amount_decimal

    return int((rate * FX_RATE_SCALE).to_integral_value(rounding="ROUND_HALF_UP"))


def split_emoji_prefix(label: str) -> tuple[str | None, str]:
    """
    Split a Money Manager label such as `🍜 Food` into emoji and name.

    The emoji is every leading character that is not an ASCII letter or
    digit, which correctly keeps multi-codepoint emoji together
    (`🅿️` is U+1F17F + U+FE0F, `🧘🏼` carries a skin-tone modifier).
    """
    split_index = 0
    while split_index < len(label):
        character = label[split_index]
        if character.isascii() and character.isalnum():
            break
        split_index += 1

    emoji = label[:split_index].strip()
    name = label[split_index:].strip()

    return (emoji or None), name


# --- Planning -------------------------------------------------------


def build_import_plan(source_rows: list[SourceRow]) -> ImportPlan:
    """Translate source rows into schema terms without touching the DB."""
    plan = ImportPlan()
    accounts: set[str] = set()
    subcategories: dict[str, set[str]] = {}
    relabelled: set[str] = set()

    for row in source_rows:
        # --- checksum the mislabelled trailing column
        if row.myr_duplicate != row.myr:
            plan.warnings.append(
                f"row {row.row_number}: trailing column {row.myr_duplicate!r} "
                f"does not match MYR {row.myr!r}; using the MYR column."
            )

        # --- direction
        direction = DIRECTION_BY_SOURCE_LABEL.get(row.direction_label)
        if direction is None:
            plan.warnings.append(
                f"row {row.row_number}: skipped, unknown "
                f"Income/Expense value {row.direction_label!r}."
            )
            continue

        # --- category, after merges and cleanup
        raw_category = row.category
        merge_key = raw_category.strip()
        merged_into = CATEGORY_MERGES.get(merge_key)
        if merged_into is not None:
            relabelled.add(f"category {raw_category!r} -> {merged_into!r} (merged)")
            raw_category = merged_into

        emoji, category_name = split_emoji_prefix(raw_category)

        override = CATEGORY_NAME_OVERRIDES.get(category_name)
        if override is not None:
            relabelled.add(f"category {category_name!r} -> {override!r}")
            category_name = override

        if not category_name:
            plan.warnings.append(
                f"row {row.row_number}: skipped, category {row.category!r} "
                "has no usable name."
            )
            continue

        # Splitting the emoji into its own column is structural, not a
        # relabel, so it is deliberately not reported here.
        existing_emoji = plan.categories.get(category_name)
        if existing_emoji is None:
            plan.categories[category_name] = emoji

        # --- subcategory, after cleanup and redundancy drop
        subcategory_name: str | None = None
        if row.subcategory:
            _, source_subcategory_name = split_emoji_prefix(row.subcategory)
            source_subcategory_name = source_subcategory_name.strip()
            subcategory_name = source_subcategory_name

            sub_override = SUBCATEGORY_NAME_OVERRIDES.get(subcategory_name)
            if sub_override is not None:
                relabelled.add(
                    f"subcategory {subcategory_name!r} -> {sub_override!r}"
                )
                subcategory_name = sub_override

            if not subcategory_name:
                subcategory_name = None
            elif (
                DROP_SUBCATEGORY_MATCHING_PARENT
                and subcategory_name.casefold() == category_name.casefold()
            ):
                relabelled.add(
                    f"subcategory {row.subcategory!r} dropped "
                    f"(restates category {category_name!r})"
                )
                subcategory_name = None
            elif subcategory_name != source_subcategory_name:
                # Report real renames only; the emoji split is structural.
                relabelled.add(
                    f"subcategory {row.subcategory!r} -> {subcategory_name!r}"
                )

        if subcategory_name is not None:
            subcategories.setdefault(category_name, set()).add(subcategory_name)

        # --- money
        amount_minor = to_minor_units(row.amount)
        base_amount_minor = to_minor_units(row.myr)
        plan.source_total_myr_minor += base_amount_minor

        if amount_minor <= 0 or base_amount_minor <= 0:
            plan.warnings.append(
                f"row {row.row_number}: skipped, non-positive amount "
                f"({row.amount} {row.currency} -> MYR {row.myr}); the schema "
                "requires amounts above zero."
            )
            plan.source_total_myr_minor -= base_amount_minor
            continue

        if row.currency == BASE_CURRENCY:
            fx_rate_scaled = FX_RATE_SCALE
        else:
            fx_rate_scaled = derive_fx_rate_scaled(row.amount, row.myr)

        accounts.add(row.account)

        plan.transactions.append(
            PlannedTransaction(
                row_number=row.row_number,
                occurred_at=excel_serial_to_local_datetime(row.period).isoformat(),
                account=row.account,
                category=category_name,
                subcategory=subcategory_name,
                note=row.note,
                description=row.description,
                direction=direction,
                amount_minor=amount_minor,
                currency=row.currency,
                fx_rate_scaled=fx_rate_scaled,
                base_amount_minor=base_amount_minor,
            )
        )

    plan.accounts = sorted(accounts)
    plan.subcategories = {
        category: sorted(names) for category, names in sorted(subcategories.items())
    }
    plan.relabelled = sorted(relabelled)

    return plan


# --- Reporting ------------------------------------------------------


def _format_minor(minor_units: int) -> str:
    return f"{Decimal(minor_units) / 100:,.2f}"


def render_plan_report(plan: ImportPlan) -> str:
    """Render a human-checkable summary of exactly what would be written."""
    lines: list[str] = []

    lines.append("=" * 68)
    lines.append("MONEY MANAGER IMPORT PLAN")
    lines.append("=" * 68)

    transactions = plan.transactions
    lines.append(f"\nTransactions to insert : {len(transactions)}")

    if transactions:
        occurred = sorted(item.occurred_at for item in transactions)
        lines.append(f"Date range             : {occurred[0][:10]} -> {occurred[-1][:10]}")

    expense_total = sum(
        item.base_amount_minor for item in transactions if item.direction == "expense"
    )
    income_total = sum(
        item.base_amount_minor for item in transactions if item.direction == "income"
    )

    lines.append(f"Total expense          : MYR {_format_minor(expense_total)}")
    lines.append(f"Total income           : MYR {_format_minor(income_total)}")

    currency_counts = Counter(item.currency for item in transactions)
    lines.append(
        "Currencies             : "
        + ", ".join(f"{code} x{count}" for code, count in currency_counts.most_common())
    )

    lines.append(f"\nAccounts ({len(plan.accounts)}):")
    for account in plan.accounts:
        lines.append(f"  - {account}")

    lines.append(f"\nCategories ({len(plan.categories)}):")
    spend_by_category = Counter()
    for item in transactions:
        spend_by_category[item.category] += item.base_amount_minor

    for category in sorted(plan.categories):
        emoji = plan.categories[category] or " "
        total = _format_minor(spend_by_category[category])
        children = plan.subcategories.get(category, [])
        lines.append(f"  {emoji:<3} {category:<16} MYR {total:>10}")
        for child in children:
            lines.append(f"        └─ {child}")

    if plan.relabelled:
        lines.append(f"\nRelabelled during cleanup ({len(plan.relabelled)}):")
        for entry in plan.relabelled:
            lines.append(f"  - {entry}")

    if plan.warnings:
        lines.append(f"\nWarnings ({len(plan.warnings)}):")
        for entry in plan.warnings:
            lines.append(f"  ! {entry}")
    else:
        lines.append("\nWarnings: none")

    planned_total = sum(item.base_amount_minor for item in transactions)
    lines.append("\nChecksum:")
    lines.append(f"  source MYR column total : {_format_minor(plan.source_total_myr_minor)}")
    lines.append(f"  planned insert total    : {_format_minor(planned_total)}")
    lines.append(
        "  match                   : "
        + ("yes" if planned_total == plan.source_total_myr_minor else "NO — investigate")
    )

    return "\n".join(lines)


# --- Committing -----------------------------------------------------


def commit_import_plan(
    plan: ImportPlan,
    db_path: str | Path = FINANCE_DB_PATH,
) -> None:
    """
    Write the plan to the finance database in a single transaction.

    Refuses to run against a database that already holds transactions,
    so a re-run cannot silently double every historical total.
    """
    init_finance_db(db_path)

    with finance_db(db_path) as connection:
        existing = connection.execute(
            "SELECT count(*) FROM transactions"
        ).fetchone()[0]

        if existing:
            raise RuntimeError(
                f"{db_path} already holds {existing} transaction(s). "
                "Refusing to import on top of existing data."
            )

        account_ids = {
            name: _insert_returning_id(
                connection,
                "INSERT INTO accounts (name) VALUES (?)",
                (name,),
            )
            for name in plan.accounts
        }

        category_ids: dict[str, int] = {}
        for name, emoji in sorted(plan.categories.items()):
            category_ids[name] = _insert_returning_id(
                connection,
                "INSERT INTO categories (name, emoji) VALUES (?, ?)",
                (name, emoji),
            )

        subcategory_ids: dict[tuple[str, str], int] = {}
        for category, children in plan.subcategories.items():
            for child in children:
                subcategory_ids[(category, child)] = _insert_returning_id(
                    connection,
                    "INSERT INTO subcategories (category_id, name) VALUES (?, ?)",
                    (category_ids[category], child),
                )

        connection.executemany(
            """
            INSERT INTO transactions (
                occurred_at, account_id, category_id, subcategory_id,
                note, description, direction, amount_minor, currency,
                fx_rate_scaled, base_amount_minor, base_currency, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'import')
            """,
            [
                (
                    item.occurred_at,
                    account_ids[item.account],
                    category_ids[item.category],
                    subcategory_ids.get((item.category, item.subcategory))
                    if item.subcategory
                    else None,
                    item.note,
                    item.description,
                    item.direction,
                    item.amount_minor,
                    item.currency,
                    item.fx_rate_scaled,
                    item.base_amount_minor,
                    BASE_CURRENCY,
                )
                for item in plan.transactions
            ],
        )


def _insert_returning_id(
    connection: sqlite3.Connection,
    statement: str,
    parameters: tuple[object, ...],
) -> int:
    return int(connection.execute(statement, parameters).lastrowid)


# --- Entry point ----------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a Money Manager .xlsx export into the finance database.",
    )
    parser.add_argument("xlsx_path", help="Path to the Money Manager export.")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write to the database. Without this flag the import is a dry run.",
    )
    parser.add_argument(
        "--db-path",
        default=str(FINANCE_DB_PATH),
        help="Finance database path.",
    )

    arguments = parser.parse_args()

    plan = build_import_plan(parse_source_rows(arguments.xlsx_path))
    print(render_plan_report(plan))

    if not arguments.commit:
        print("\nDry run. Re-run with --commit to write these rows.")
        return

    commit_import_plan(plan, arguments.db_path)

    print(
        f"\nImported {len(plan.transactions)} transaction(s) "
        f"into {arguments.db_path}."
    )


if __name__ == "__main__":
    main()
