"""Financial core HTTP layer."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Header, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache, idempotency
from app.core.clock import utc_today
from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.core.errors import UnprocessableError, ValidationError
from app.core.pagination import MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.modules.finance import importer as csv_importer
from app.modules.finance.models import TransactionSource
from app.modules.finance.schemas import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    BudgetCreate,
    BudgetOut,
    BudgetUpdate,
    BulkResponse,
    BulkResult,
    CategoryCreate,
    CategoryOut,
    ColumnMapping,
    GoalContribution,
    GoalCreate,
    GoalOut,
    GoalUpdate,
    ImportAnalysis,
    RecurringCreate,
    RecurringOut,
    RecurringUpdate,
    TransactionCreate,
    TransactionFilters,
    TransactionOut,
    TransactionUpdate,
)
from app.modules.finance.seeder import DemoSeeder
from app.modules.finance.service import FinanceService, normalize_merchant

router = APIRouter(tags=["finance"])

Pace = Literal["on_track", "ahead", "over"]

MAX_BULK_ROWS = 500
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> FinanceService:
    return FinanceService(db)


ServiceDep = Annotated[FinanceService, Depends(get_service)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


def _txn_out(txn: object) -> TransactionOut:
    out = TransactionOut.model_validate(txn)
    return out


# --- accounts --------------------------------------------------------------


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(
    current: CurrentUserDep, service: ServiceDep, include_archived: bool = False
) -> list[AccountOut]:
    rows = await service.accounts.list_all(current.id, include_archived=include_archived)
    return [AccountOut.model_validate(a) for a in rows]


@router.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(
    data: AccountCreate, current: CurrentUserDep, service: ServiceDep
) -> AccountOut:
    return AccountOut.model_validate(await service.create_account(current.id, data))


@router.get("/accounts/{account_id}", response_model=AccountOut)
async def get_account(
    account_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep
) -> AccountOut:
    return AccountOut.model_validate(await service.accounts.get_or_404(current.id, account_id))


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: uuid.UUID, data: AccountUpdate, current: CurrentUserDep, service: ServiceDep
) -> AccountOut:
    return AccountOut.model_validate(await service.update_account(current.id, account_id, data))


@router.post("/accounts/{account_id}/archive", response_model=AccountOut)
async def archive_account(
    account_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep
) -> AccountOut:
    return AccountOut.model_validate(await service.archive_account(current.id, account_id))


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep, force: bool = False
) -> None:
    await service.delete_account(current.id, account_id, force=force)


# --- categories ------------------------------------------------------------


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(current: CurrentUserDep, service: ServiceDep) -> list[CategoryOut]:
    rows = await service.list_categories(current.id)
    return [CategoryOut.model_validate(c) for c in rows]


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    data: CategoryCreate, current: CurrentUserDep, service: ServiceDep
) -> CategoryOut:
    return CategoryOut.model_validate(await service.create_category(current.id, data))


# --- transactions ----------------------------------------------------------


@router.get("/transactions", response_model=Page[TransactionOut])
async def list_transactions(
    current: CurrentUserDep,
    service: ServiceDep,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    from_date: date | None = None,
    to_date: date | None = None,
    account_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    kind: str | None = None,
    q: str | None = None,
    uncategorized_only: bool = False,
    needs_review: bool = False,
) -> Page[TransactionOut]:
    filters = TransactionFilters(
        from_date=from_date,
        to_date=to_date,
        account_id=account_id,
        category_id=category_id,
        kind=kind,
        q=q,
        uncategorized_only=uncategorized_only,
        needs_review=needs_review,
    )
    rows, has_more = await service.list_transactions(
        current.id, cursor=Cursor.decode(cursor) if cursor else None, limit=limit, filters=filters
    )

    next_cursor = (
        Cursor(occurred_on=rows[-1].occurred_on, entity_id=rows[-1].id).encode()
        if rows and has_more
        else None
    )
    return Page[TransactionOut](
        data=[_txn_out(t) for t in rows],
        pagination=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit),
    )


@router.post("/transactions", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    data: TransactionCreate,
    current: CurrentUserDep,
    service: ServiceDep,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> TransactionOut:
    """Create a transaction.

    With an `Idempotency-Key`, a retry returns the original response instead of
    creating a second row -- which is what makes a timed-out request safe to
    repeat (ADR-007).
    """
    endpoint = "transactions.create"
    fingerprint = idempotency.fingerprint(data.model_dump(mode="json"))

    if idempotency_key:
        stored = await idempotency.lookup(str(current.id), endpoint, idempotency_key, fingerprint)
        if stored is not None:
            # Return the *original* response verbatim, status included: the
            # point of a replay is that the client cannot tell it apart from
            # the first call succeeding, apart from the marker header.
            response.status_code = stored.status_code
            response.headers["Idempotency-Replayed"] = "true"
            return TransactionOut.model_validate(stored.body)

    outcome = await service.create_transaction(current.id, data)
    if outcome.duplicate or outcome.transaction is None:
        raise UnprocessableError(
            "An identical transaction already exists. "
            "Set allow_duplicate=true if this is a separate purchase."
        )

    body = _txn_out(outcome.transaction)
    if idempotency_key:
        await idempotency.remember(
            str(current.id),
            endpoint,
            idempotency_key,
            fingerprint,
            201,
            body.model_dump(mode="json"),
        )
    return body


@router.post("/transactions/bulk", response_model=BulkResponse)
async def bulk_create(
    rows: list[TransactionCreate],
    current: CurrentUserDep,
    service: ServiceDep,
    response: Response,
) -> BulkResponse:
    """Create many transactions, reporting each row's outcome.

    Returns **207 Multi-Status** when any row fails: a 500-row import must not
    be all-or-nothing, and the user needs to know exactly which rows were
    rejected and why (FR-2.5).
    """
    if len(rows) > MAX_BULK_ROWS:
        raise ValidationError(f"At most {MAX_BULK_ROWS} rows per request")

    results: list[BulkResult] = []
    created = duplicates = errors = 0

    for index, row in enumerate(rows):
        try:
            outcome = await service.create_transaction(
                current.id, row, source=TransactionSource.CSV_IMPORT
            )
        except Exception as exc:
            errors += 1
            results.append(BulkResult(index=index, status="error", error=_reason(exc)))
            continue

        if outcome.duplicate or outcome.transaction is None:
            duplicates += 1
            results.append(BulkResult(index=index, status="duplicate"))
        else:
            created += 1
            results.append(BulkResult(index=index, status="created", id=outcome.transaction.id))

    if errors:
        response.status_code = status.HTTP_207_MULTI_STATUS

    return BulkResponse(created=created, duplicates=duplicates, errors=errors, results=results)


def _reason(exc: Exception) -> str:
    message = getattr(exc, "message", None)
    return str(message or exc)[:200]


@router.get("/transactions/uncategorized", response_model=Page[TransactionOut])
async def uncategorized(
    current: CurrentUserDep,
    service: ServiceDep,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
) -> Page[TransactionOut]:
    rows, has_more = await service.list_transactions(
        current.id,
        cursor=None,
        limit=limit,
        filters=TransactionFilters(uncategorized_only=True),
    )
    return Page[TransactionOut](
        data=[_txn_out(t) for t in rows],
        pagination=PageMeta(has_more=has_more, limit=limit),
    )


@router.get("/transactions/{txn_id}", response_model=TransactionOut)
async def get_transaction(
    txn_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep
) -> TransactionOut:
    return _txn_out(await service.transactions.get_or_404(current.id, txn_id))


@router.patch("/transactions/{txn_id}", response_model=TransactionOut)
async def update_transaction(
    txn_id: uuid.UUID, data: TransactionUpdate, current: CurrentUserDep, service: ServiceDep
) -> TransactionOut:
    return _txn_out(await service.update_transaction(current.id, txn_id, data))


@router.delete("/transactions/{txn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(
    txn_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep
) -> None:
    await service.delete_transaction(current.id, txn_id)


# --- import ----------------------------------------------------------------


@router.post("/imports/csv/analyze", response_model=ImportAnalysis)
async def analyze_csv(
    current: CurrentUserDep,
    service: ServiceDep,
    file: Annotated[UploadFile, File()],
    account_id: uuid.UUID | None = None,
) -> ImportAnalysis:
    """Inspect a CSV: detect the column mapping, preview rows, count duplicates.

    Nothing is written. The duplicate count is the point -- telling the user
    that 12 of their rows already exist before they commit is what makes the
    importer trustworthy.
    """
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValidationError("The file is larger than 5 MB")

    parsed = csv_importer.parse_csv(content)
    detected, confidence = (
        csv_importer.detect_mapping(parsed.columns) if parsed.columns else (None, Decimal("0"))
    )

    duplicate_hashes: set[str] = set()
    duplicate_estimate = 0

    if account_id and parsed.rows:
        await service.accounts.get_or_404(current.id, account_id)
        hashes = [
            csv_importer.content_hash_for(current.id, account_id, r, normalize_merchant)
            for r in parsed.rows
            if r.ok
        ]
        duplicate_hashes = await service.transactions.existing_hashes(current.id, hashes)
        duplicate_estimate = sum(1 for h in hashes if h in duplicate_hashes)

    preview = csv_importer.to_preview(
        parsed.rows,
        duplicate_hashes,
        lambda r: (
            csv_importer.content_hash_for(
                current.id, account_id or uuid.uuid4(), r, normalize_merchant
            )
            if account_id
            else ""
        ),
    )

    return ImportAnalysis(
        import_id=uuid.uuid4(),
        columns=parsed.columns,
        detected_mapping=detected,
        confidence=confidence,
        row_count=len(parsed.rows),
        preview=preview,
        warnings=parsed.warnings,
        duplicate_estimate=duplicate_estimate,
    )


@router.post("/imports/csv/commit", response_model=BulkResponse)
async def commit_csv(
    current: CurrentUserDep,
    service: ServiceDep,
    response: Response,
    file: Annotated[UploadFile, File()],
    account_id: Annotated[uuid.UUID, Query()],
    mapping_date: Annotated[str, Query(alias="mapping.date")],
    mapping_amount: Annotated[str | None, Query(alias="mapping.amount")] = None,
    mapping_debit: Annotated[str | None, Query(alias="mapping.debit")] = None,
    mapping_credit: Annotated[str | None, Query(alias="mapping.credit")] = None,
    mapping_merchant: Annotated[str | None, Query(alias="mapping.merchant")] = None,
    currency: str = "INR",
    date_format: str | None = None,
) -> BulkResponse:
    """Apply a mapping and import.

    Re-importing the same file is safe: each row's content hash collides with
    the existing one and is reported as a duplicate rather than inserted
    (FR-2.6). The unique index is the authority, not this code path.
    """
    await service.accounts.get_or_404(current.id, account_id)

    mapping = ColumnMapping(
        date=mapping_date,
        amount=mapping_amount,
        debit=mapping_debit,
        credit=mapping_credit,
        merchant=mapping_merchant,
    )
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValidationError("The file is larger than 5 MB")

    parsed = csv_importer.parse_csv(content, mapping, date_format)

    results: list[BulkResult] = []
    created = duplicates = errors = 0

    for row in parsed.rows:
        if not row.ok:
            errors += 1
            results.append(BulkResult(index=row.index, status="error", error=row.error))
            continue

        assert row.amount is not None and row.occurred_on is not None and row.kind is not None
        payload = TransactionCreate(
            account_id=account_id,
            kind=row.kind,
            amount=row.amount,
            occurred_on=row.occurred_on,
            currency=currency.upper(),
            merchant_raw=row.merchant,
            description=row.description,
        )
        try:
            outcome = await service.create_transaction(
                current.id, payload, source=TransactionSource.CSV_IMPORT
            )
        except Exception as exc:
            errors += 1
            results.append(BulkResult(index=row.index, status="error", error=_reason(exc)))
            continue

        if outcome.duplicate or outcome.transaction is None:
            duplicates += 1
            results.append(BulkResult(index=row.index, status="duplicate"))
        else:
            created += 1
            results.append(BulkResult(index=row.index, status="created", id=outcome.transaction.id))

    if errors:
        response.status_code = status.HTTP_207_MULTI_STATUS

    return BulkResponse(created=created, duplicates=duplicates, errors=errors, results=results)


@router.post("/imports/demo-seed", status_code=status.HTTP_201_CREATED)
async def demo_seed(current: CurrentUserDep, service: ServiceDep) -> dict[str, object]:
    """Populate a year of realistic demo data (FR-2.10).

    A first-class endpoint, not a fixture script: it is the answer to cold
    start, and the fastest path from a new account to a product that has
    something to say.
    """
    existing = await service.transactions.count_all(current.id)
    if existing:
        raise UnprocessableError(
            "This account already has transactions. Demo data is only for an empty account."
        )

    summary = await DemoSeeder(service.session, current.id).run()
    await service.session.flush()
    # One invalidation for the whole seed rather than per row.
    await cache.bump_version(current.id)
    return {"status": "seeded", **summary}


# --- budgets ---------------------------------------------------------------


@router.get("/budgets", response_model=list[BudgetOut])
async def list_budgets(
    current: CurrentUserDep, service: ServiceDep, period_start: date | None = None
) -> list[BudgetOut]:
    period = (period_start or utc_today()).replace(day=1)
    pairs = await service.budgets_with_spend(current.id, period)

    out: list[BudgetOut] = []
    for budget, spent in pairs:
        model = BudgetOut.model_validate(budget)
        model.spent = spent
        model.remaining = Decimal(budget.amount_limit) - spent
        model.pace = _pace(spent, Decimal(budget.amount_limit), period)
        out.append(model)
    return out


def _pace(spent: Decimal, limit: Decimal, period_start: date) -> Pace:
    """On track / ahead / over, judged against elapsed time in the period.

    Comparing spend to the limit alone would call every budget "on track" on the
    2nd of the month and "over" only once it is too late to act.
    """
    if spent > limit:
        return "over"

    from calendar import monthrange

    days_in_month = monthrange(period_start.year, period_start.month)[1]
    today = utc_today()
    elapsed = (
        days_in_month
        if (today.year, today.month) > (period_start.year, period_start.month)
        else min(today.day, days_in_month)
        if (today.year, today.month) == (period_start.year, period_start.month)
        else 0
    )
    if elapsed == 0:
        return "on_track"

    expected = limit * Decimal(elapsed) / Decimal(days_in_month)
    return "ahead" if spent <= expected else "on_track"


@router.post("/budgets", response_model=BudgetOut, status_code=status.HTTP_201_CREATED)
async def create_budget(
    data: BudgetCreate, current: CurrentUserDep, service: ServiceDep
) -> BudgetOut:
    return BudgetOut.model_validate(await service.create_budget(current.id, data))


@router.patch("/budgets/{budget_id}", response_model=BudgetOut)
async def update_budget(
    budget_id: uuid.UUID, data: BudgetUpdate, current: CurrentUserDep, service: ServiceDep
) -> BudgetOut:
    return BudgetOut.model_validate(await service.update_budget(current.id, budget_id, data))


@router.delete("/budgets/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(budget_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep) -> None:
    await service.budgets.soft_delete(current.id, budget_id)


@router.post("/budgets/copy-from-previous", response_model=list[BudgetOut])
async def copy_budgets(
    current: CurrentUserDep, service: ServiceDep, period_start: date | None = None
) -> list[BudgetOut]:
    period = (period_start or utc_today()).replace(day=1)
    created = await service.copy_budgets_forward(current.id, period)
    return [BudgetOut.model_validate(b) for b in created]


# --- goals -----------------------------------------------------------------


@router.get("/goals", response_model=list[GoalOut])
async def list_goals(
    current: CurrentUserDep, service: ServiceDep, status_filter: str | None = None
) -> list[GoalOut]:
    rows = await service.goals.list_all(current.id, status_filter)
    out = []
    for goal in rows:
        model = GoalOut.model_validate(goal)
        model.progress_pct = (
            (Decimal(goal.current_amount) / Decimal(goal.target_amount) * 100).quantize(
                Decimal("0.01")
            )
            if goal.target_amount
            else Decimal("0")
        )
        out.append(model)
    return out


@router.post("/goals", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
async def create_goal(data: GoalCreate, current: CurrentUserDep, service: ServiceDep) -> GoalOut:
    return GoalOut.model_validate(await service.create_goal(current.id, data))


@router.patch("/goals/{goal_id}", response_model=GoalOut)
async def update_goal(
    goal_id: uuid.UUID, data: GoalUpdate, current: CurrentUserDep, service: ServiceDep
) -> GoalOut:
    return GoalOut.model_validate(await service.update_goal(current.id, goal_id, data))


@router.post("/goals/{goal_id}/contribute", response_model=GoalOut)
async def contribute(
    goal_id: uuid.UUID,
    data: GoalContribution,
    current: CurrentUserDep,
    service: ServiceDep,
) -> GoalOut:
    return GoalOut.model_validate(
        await service.contribute_to_goal(current.id, goal_id, data.amount)
    )


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(goal_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep) -> None:
    await service.goals.soft_delete(current.id, goal_id)


# --- recurring -------------------------------------------------------------


@router.get("/recurring", response_model=list[RecurringOut])
async def list_recurring(
    current: CurrentUserDep, service: ServiceDep, active_only: bool = True
) -> list[RecurringOut]:
    rows = await service.recurring.list_all(current.id, active_only=active_only)
    return [RecurringOut.model_validate(r) for r in rows]


@router.post("/recurring", response_model=RecurringOut, status_code=status.HTTP_201_CREATED)
async def create_recurring(
    data: RecurringCreate, current: CurrentUserDep, service: ServiceDep
) -> RecurringOut:
    return RecurringOut.model_validate(await service.create_recurring(current.id, data))


@router.patch("/recurring/{item_id}", response_model=RecurringOut)
async def update_recurring(
    item_id: uuid.UUID, data: RecurringUpdate, current: CurrentUserDep, service: ServiceDep
) -> RecurringOut:
    return RecurringOut.model_validate(await service.update_recurring(current.id, item_id, data))


@router.get("/recurring/upcoming", response_model=list[RecurringOut])
async def upcoming_recurring(
    current: CurrentUserDep, service: ServiceDep, days: int = Query(default=30, ge=1, le=365)
) -> list[RecurringOut]:
    from datetime import timedelta

    rows = await service.recurring.upcoming(current.id, utc_today() + timedelta(days=days))
    return [RecurringOut.model_validate(r) for r in rows]


# --- reconciliation --------------------------------------------------------


@router.post("/accounts/reconcile")
async def reconcile(current: CurrentUserDep, service: ServiceDep) -> dict[str, object]:
    """Verify materialised balances against the ledger.

    Reports drift rather than silently correcting it: a silent fix would hide
    the write path that caused it.
    """
    drifts = await service.reconcile_balances(current.id)
    return {"checked_at": utc_today().isoformat(), "drift_count": len(drifts), "drifts": drifts}
