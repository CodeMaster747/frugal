"""CSV statement import.

Two phases by design: **analyse** returns a detected column mapping, a preview,
and a count of rows that already exist; **commit** applies it. Telling the user
that 12 of their rows are duplicates *before* they commit is the difference
between trusting the importer and fearing it (FR-2.5).
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.modules.finance.models import Transaction, TransactionKind
from app.modules.finance.schemas import ColumnMapping, ImportPreviewRow

MAX_PREVIEW_ROWS = 20
MAX_ROWS = 10_000

# Ordered by specificity: unambiguous ISO first, then day-first (the Indian and
# European convention), then month-first. A bare 03/08/2026 is genuinely
# ambiguous, so the order is a documented choice rather than a guess -- and the
# caller can override it with an explicit date_format.
DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d/%m/%y",
    "%m/%d/%y",
)

_HEADER_HINTS: dict[str, tuple[str, ...]] = {
    "date": ("date", "txn date", "transaction date", "value date", "posted"),
    "amount": ("amount", "value", "transaction amount"),
    "debit": ("debit", "withdrawal", "withdrawal amt", "paid out", "dr"),
    "credit": ("credit", "deposit", "deposit amt", "paid in", "cr"),
    "merchant": ("merchant", "payee", "description", "narration", "particulars", "details"),
    "description": ("remarks", "notes", "memo"),
}


@dataclass(slots=True)
class ParsedRow:
    index: int
    occurred_on: date | None = None
    amount: Decimal | None = None
    kind: TransactionKind | None = None
    merchant: str | None = None
    description: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(slots=True)
class ParsedFile:
    columns: list[str]
    rows: list[ParsedRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_amount(raw: str) -> Decimal | None:
    """Parse a monetary cell.

    Handles thousands separators, currency symbols, and the accounting
    convention of parenthesising negatives. Returns None for a blank cell --
    which is meaningful in debit/credit layouts, where every row leaves one
    side empty.
    """
    text = (raw or "").strip()
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[^\d.\-]", "", text.strip("()"))
    if not text or text in {"-", "."}:
        return None

    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_date(raw: str, explicit_format: str | None = None) -> date | None:
    text = (raw or "").strip()
    if not text:
        return None

    formats = (explicit_format,) if explicit_format else DATE_FORMATS
    for fmt in formats:
        if not fmt:
            continue
        try:
            # A statement date carries no time or zone; a naive calendar
            # date is exactly the right result here.
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def detect_mapping(columns: list[str]) -> tuple[ColumnMapping | None, Decimal]:
    """Guess which columns mean what, with a confidence score.

    Confidence is reported rather than hidden so the wizard can pre-fill a
    strong guess and prompt on a weak one, instead of silently importing a
    misread file.
    """
    lowered = {c.strip().lower(): c for c in columns}
    found: dict[str, str] = {}

    for field_name, hints in _HEADER_HINTS.items():
        for hint in hints:
            for lower, original in lowered.items():
                if lower == hint or hint in lower:
                    found.setdefault(field_name, original)
                    break
            if field_name in found:
                break

    if "date" not in found or not (
        found.get("amount") or found.get("debit") or found.get("credit")
    ):
        return None, Decimal("0")

    # A date column plus an amount source is the minimum; a merchant column is
    # what makes the result useful rather than merely valid.
    score = Decimal("0.5")
    if found.get("merchant"):
        score += Decimal("0.3")
    if found.get("amount") or (found.get("debit") and found.get("credit")):
        score += Decimal("0.2")

    return ColumnMapping(**found), min(score, Decimal("1.0"))


def parse_csv(
    content: bytes, mapping: ColumnMapping | None = None, date_format: str | None = None
) -> ParsedFile:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Bank exports are frequently latin-1; failing the whole import over one
        # accented merchant name would be needless.
        text = content.decode("latin-1")

    sample = text[:4096]
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    columns = [c.strip() for c in (reader.fieldnames or [])]
    parsed = ParsedFile(columns=columns)

    if not columns:
        parsed.warnings.append("The file has no header row")
        return parsed

    active = mapping or detect_mapping(columns)[0]
    if active is None:
        parsed.warnings.append("Could not identify a date column and an amount column")
        return parsed

    for index, raw_row in enumerate(reader):
        if index >= MAX_ROWS:
            parsed.warnings.append(f"Only the first {MAX_ROWS} rows were read")
            break
        parsed.rows.append(
            _parse_row(index, {k.strip(): v for k, v in raw_row.items() if k}, active, date_format)
        )

    unparsed_dates = sum(1 for r in parsed.rows if r.error and "date" in r.error.lower())
    if unparsed_dates:
        parsed.warnings.append(f"{unparsed_dates} rows have an unreadable date")

    return parsed


def _parse_row(
    index: int, row: dict[str, str], mapping: ColumnMapping, date_format: str | None
) -> ParsedRow:
    out = ParsedRow(index=index)

    out.occurred_on = parse_date(row.get(mapping.date, ""), date_format)
    if out.occurred_on is None:
        out.error = f"Unreadable date: {row.get(mapping.date, '')!r}"
        return out

    if mapping.amount:
        value = parse_amount(row.get(mapping.amount, ""))
        if value is None or value == 0:
            out.error = "Missing or zero amount"
            return out
        # A signed single-amount column: negative is money out.
        out.kind = TransactionKind.EXPENSE if value < 0 else TransactionKind.INCOME
        out.amount = abs(value)
    else:
        debit = parse_amount(row.get(mapping.debit or "", "")) or Decimal("0")
        credit = parse_amount(row.get(mapping.credit or "", "")) or Decimal("0")
        if debit and credit:
            out.error = "Row has both a debit and a credit"
            return out
        if not debit and not credit:
            out.error = "Row has neither a debit nor a credit"
            return out
        out.kind = TransactionKind.EXPENSE if debit else TransactionKind.INCOME
        out.amount = abs(debit or credit)

    if mapping.merchant:
        out.merchant = (row.get(mapping.merchant) or "").strip() or None
    if mapping.description:
        out.description = (row.get(mapping.description) or "").strip() or None

    return out


def to_preview(
    rows: list[ParsedRow],
    duplicate_hashes: set[str],
    hash_for: Callable[[ParsedRow], str],
) -> list[ImportPreviewRow]:
    return [
        ImportPreviewRow(
            index=r.index,
            occurred_on=r.occurred_on,
            amount=r.amount,
            kind=r.kind.value if r.kind else None,
            merchant=r.merchant,
            is_duplicate=(r.ok and hash_for(r) in duplicate_hashes),
            error=r.error,
        )
        for r in rows[:MAX_PREVIEW_ROWS]
    ]


def content_hash_for(
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    row: ParsedRow,
    normalizer: Callable[[str | None], str | None],
) -> str:
    assert row.occurred_on is not None and row.amount is not None
    return Transaction.compute_hash(
        user_id, row.occurred_on, row.amount, normalizer(row.merchant), account_id
    )
