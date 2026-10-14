"""Official operational writes and re-exported analytical consultations.

Business reads are delegated to analytics_tools. The two write operations at the bottom (discard_batch,
receive_inventory) are deliberately narrow: they act on an EXISTING
batch/inventory row rather than creating new products or batches from
scratch, and they lean on the schema's own fn_atomic_update_inventory
stored procedure for the row-locked, version-checked balance update and
its audit trail (mottainai.inventory_movement) instead of reimplementing
that logic in Python.

Note: SQL-computed status strings ('RUPTURA', 'ABAIXO_MINIMO', 'EXCESSO',
etc.) and the dict keys returned by get_shelf_inventory_crosscheck
("encontrados", "ausentes_esperados", "alertas_ativos") are a data contract
consumed by other code and by the agents' LLM prompts — they are kept in
Portuguese, not translated as part of this pass.
"""
import asyncio
import logging
from decimal import Decimal
from typing import Any, Awaitable, TypeVar

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from app.database.identity import initialize_pg_context
from app.database.postgres import get_pg_session
from app.tools.analytics_tools import (
    MAX_PRODUCT_NAME_CHARS,
    get_inventory_matches,
    get_inventory_status,
    get_stock_alerts,
)
from app.tools.analytics_tools import (
    get_daily_sales_series as get_daily_sales_series,
)
from app.tools.analytics_tools import (
    get_expiring_batches as get_expiring_batches,
)
from app.tools.analytics_tools import (
    get_inventory_match as get_inventory_match,
)
from app.tools.analytics_tools import (
    get_kpis as get_kpis,
)
from app.tools.analytics_tools import (
    get_kpis_by_store as get_kpis_by_store,
)
from app.tools.analytics_tools import (
    get_sales_summary as get_sales_summary,
)
from config.settings import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

T = TypeVar("T")

# Bounds how many of THIS app's own calls into these functions can be
# waiting on a connection at once. Agent nodes now fan several of these
# out together via asyncio.gather (app/agents/runtime.gather_or_raise) —
# without a limit, a burst of concurrent /chat requests could ask the
# pool (config/settings.py's postgres_pool_size + postgres_max_overflow)
# for more connections than it can ever hand out, which is just
# NullPool's old per-call wait again, moved from "opening a connection"
# to "waiting on pool_timeout". Sized to the pool's total real capacity,
# not to any one node's call count.
pg_fanout_semaphore = asyncio.Semaphore(settings.postgres_pool_size + settings.postgres_max_overflow)


async def guarded(call: Awaitable[T]) -> T:
    """Wrap a Postgres call with this (before passing it to timed_tool_call)
    in any code that fans multiple such calls out concurrently."""
    async with pg_fanout_semaphore:
        return await call


@retry(
    stop=stop_after_attempt(settings.postgres_max_retries),
    wait=wait_exponential_jitter(),
    retry=retry_if_exception_type(OperationalError),
    reraise=True,
    before_sleep=lambda retry_state: logger.warning(
        "Postgres query failed (attempt %s/%s), retrying: %s",
        retry_state.attempt_number, settings.postgres_max_retries, retry_state.outcome.exception(),
    ),
)
async def _exec(
    sql: str,
    *,
    empresa_id: int,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Executes a query scoped to the authenticated tenant.

    Retries on OperationalError (SQLAlchemy's category for a transient
    connection failure — dropped connection, connection refused, timeout)
    with exponential backoff + jitter, same pattern already used for LLM
    provider calls (app/agents/runtime.get_llm). Every query here is a
    read, so retrying is always safe — nothing here can double-apply a
    side effect. Does NOT retry on query errors (bad SQL, a constraint
    violation surfaced through a stored function) — those aren't transient.
    """
    if isinstance(empresa_id, bool) or empresa_id < 1:
        raise ValueError("empresa_id must be a positive integer.")

    query_params = {**(params or {}), "empresa_id": empresa_id}
    async with get_pg_session() as session:
        await initialize_pg_context(session, empresa_id)
        result = await session.execute(text(sql), query_params)
        cols = list(result.keys())
        return [dict(zip(cols, row)) for row in result.fetchall()]


async def get_shelf_inventory_crosscheck(
    empresa_id: int,
    store_id: int | None,
    detected_products: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Cross-checks detected products against v6 schema stock and alerts."""
    # One query for every detected product instead of one per product. The
    # loop still walks `detected_products` in order so `found` keeps the order
    # the products were detected in; only the lookup moved out of it.
    # `seen_names` stays keyed on the untruncated name, because the
    # missing-product check below does substring matching against it.
    matches = await get_inventory_matches(empresa_id, detected_products, store_id)
    found: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for product_name in detected_products:
        normalized_name = product_name.strip()
        if not normalized_name or normalized_name.lower() in seen_names:
            continue
        seen_names.add(normalized_name.lower())
        match = matches.get(normalized_name[:MAX_PRODUCT_NAME_CHARS].lower())
        if match:
            found.append(match)

    inventory = await get_inventory_status(empresa_id, store_id)
    detected_names = {name.lower() for name in seen_names}
    missing: list[dict[str, Any]] = []
    seen_inventory: set[tuple[str, str]] = set()
    for item in inventory:
        if item["stock_status"] not in {"RUPTURA", "ABAIXO_MINIMO"}:
            continue
        product_name = item["product_name"]
        key = (product_name.lower(), item["store_name"])
        if key in seen_inventory or any(
            product_name.lower() in name or name in product_name.lower()
            for name in detected_names
        ):
            continue
        seen_inventory.add(key)
        missing.append(item)

    return {
        "encontrados": found,
        "ausentes_esperados": missing,
        "alertas_ativos": await get_stock_alerts(empresa_id, limit=10, store_id=store_id),
    }


def _validate_positive_int(value: Any, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer.")


async def _find_inventory(
    session,
    *,
    empresa_id: int,
    store_id: int,
    batch_id: int,
):
    """Resolves (store_id, batch_id) to its inventory row (inventory_id,
    current_quantity), scoped to the tenant. current_quantity is the
    balance BEFORE any write this call is part of — discard_batch needs
    it to check the disposal ceiling against what's on hand right now,
    not the function's own post-decrement balance."""
    row = (
        await session.execute(
            text("""
                SELECT i.inventory_id, i.current_quantity
                FROM mottainai.inventory i
                JOIN mottainai.retail_store rs ON rs.store_id = i.store_id
                JOIN mottainai.company c ON c.company_id = rs.company_id
                WHERE i.batch_id = :batch_id
                  AND i.store_id = :store_id
                  AND c.company_id = :empresa_id
                  AND i.deleted_at IS NULL
                  AND rs.active = TRUE
                  AND rs.deleted_at IS NULL
                  AND c.active = TRUE
                  AND c.deleted_at IS NULL
                FOR UPDATE OF i
            """),
            {"batch_id": batch_id, "store_id": store_id, "empresa_id": empresa_id},
        )
    ).mappings().first()

    if row is None:
        raise ValueError(
            f"No inventory record for batch_id={batch_id} at store_id={store_id} in this company."
        )
    return row


class DisposalCeilingExceeded(Exception):
    """A single disposal would remove more than settings.disposal_ceiling_percent
    of the batch's current quantity, and the acting role isn't authorized
    to do that alone (only GERENTE/DONO are)."""

    def __init__(self, *, requested: Decimal, current_quantity: Decimal, ceiling_percent: float):
        self.requested = requested
        self.current_quantity = current_quantity
        self.ceiling_percent = ceiling_percent
        super().__init__(
            f"Disposal of {requested} exceeds {ceiling_percent}% of the current "
            f"balance ({current_quantity}); requires GERENTE or DONO approval."
        )


# Only these roles may discard more than settings.disposal_ceiling_percent of
# a batch's current quantity in a single call — an authorization boundary,
# so (like ROLE_TO_INTENT_AGENTS in app/agents/supervisor.py) it stays
# hardcoded here rather than becoming app-configurable.
_DISPOSAL_CEILING_EXEMPT_ROLES = {"GERENTE", "DONO"}


async def discard_batch(
    empresa_id: int,
    store_id: int,
    batch_id: int,
    user_id: int,
    quantity: Decimal,
    reason: str,
    role: str,
    observation: str | None = None,
) -> dict:
    """
    Registers a batch disposal (descarte): creates the disposal + disposal_item
    audit rows and atomically decrements the matching inventory row via the
    schema's fn_atomic_update_inventory (row-locked, optimistic-version-checked,
    writes its own inventory_movement row). Used by the Employee Agent.

    Raises DisposalCeilingExceeded, before writing anything, if `quantity` is
    more than settings.disposal_ceiling_percent of the batch's current
    quantity and `role` isn't GERENTE/DONO — an ESTOQUISTA cannot single-
    handedly discard most or all of a batch.
    """
    _validate_positive_int(store_id, "store_id")
    _validate_positive_int(batch_id, "batch_id")
    _validate_positive_int(user_id, "user_id")
    if not reason or not reason.strip():
        raise ValueError("reason is required.")
    quantity = Decimal(str(quantity))
    if quantity <= 0:
        raise ValueError("quantity must be greater than zero.")

    async with get_pg_session() as session:
        actor = await initialize_pg_context(session, empresa_id)
        if actor["user_id"] != user_id:
            raise PermissionError("Acting user does not match the authenticated account")
        employee_id = actor["employee_id"]

        inventory = await _find_inventory(
            session, empresa_id=empresa_id, store_id=store_id, batch_id=batch_id
        )
        inventory_id = inventory["inventory_id"]
        current_quantity = inventory["current_quantity"]

        if (
            role.upper() not in _DISPOSAL_CEILING_EXEMPT_ROLES
            and current_quantity > 0
            and (quantity / current_quantity) * 100 > Decimal(str(settings.disposal_ceiling_percent))
        ):
            raise DisposalCeilingExceeded(
                requested=quantity,
                current_quantity=current_quantity,
                ceiling_percent=settings.disposal_ceiling_percent,
            )

        disposal_row = (
            await session.execute(
                text("""
                    INSERT INTO mottainai.disposal (store_id, employee_id, reason, observation)
                    VALUES (:store_id, :employee_id, :reason, :observation)
                    RETURNING disposal_id
                """),
                {
                    "store_id": store_id,
                    "employee_id": employee_id,
                    "reason": reason.strip(),
                    "observation": observation,
                },
            )
        ).mappings().first()
        disposal_id = disposal_row["disposal_id"]

        await session.execute(
            text("""
                INSERT INTO mottainai.disposal_item (disposal_id, batch_id, disposed_quantity)
                VALUES (:disposal_id, :batch_id, :quantity)
            """),
            {"disposal_id": disposal_id, "batch_id": batch_id, "quantity": quantity},
        )

        balance_row = (
            await session.execute(
                text("""
                    SELECT mottainai.fn_atomic_update_inventory(
                        :inventory_id, :delta, 'DISPOSAL', :employee_id, :observation
                    ) AS new_balance
                """),
                {
                    "inventory_id": inventory_id,
                    "delta": -quantity,
                    "employee_id": employee_id,
                    "observation": observation or reason.strip(),
                },
            )
        ).mappings().first()

    return {
        "disposal_id": disposal_id,
        "batch_id": batch_id,
        "store_id": store_id,
        "disposed_quantity": quantity,
        "new_inventory_balance": balance_row["new_balance"],
    }


async def receive_inventory(
    empresa_id: int,
    store_id: int,
    batch_id: int,
    user_id: int,
    quantity: Decimal,
    observation: str | None = None,
) -> dict:
    """
    Registers receipt of goods for an EXISTING batch already tracked in
    inventory at that store (e.g. confirming a restock). Does not create new
    products or batches — that's a separate, bigger feature (purchase-order
    management) intentionally left out of scope here. Used by the Employee
    Agent.
    """
    _validate_positive_int(store_id, "store_id")
    _validate_positive_int(batch_id, "batch_id")
    _validate_positive_int(user_id, "user_id")
    quantity = Decimal(str(quantity))
    if quantity <= 0:
        raise ValueError("quantity must be greater than zero.")

    async with get_pg_session() as session:
        actor = await initialize_pg_context(session, empresa_id)
        if actor["user_id"] != user_id:
            raise PermissionError("Acting user does not match the authenticated account")
        employee_id = actor["employee_id"]

        inventory = await _find_inventory(
            session, empresa_id=empresa_id, store_id=store_id, batch_id=batch_id
        )
        inventory_id = inventory["inventory_id"]

        balance_row = (
            await session.execute(
                text("""
                    SELECT mottainai.fn_atomic_update_inventory(
                        :inventory_id, :delta, 'IN', :employee_id, :observation
                    ) AS new_balance
                """),
                {
                    "inventory_id": inventory_id,
                    "delta": quantity,
                    "employee_id": employee_id,
                    "observation": observation,
                },
            )
        ).mappings().first()

    return {
        "batch_id": batch_id,
        "store_id": store_id,
        "received_quantity": quantity,
        "new_inventory_balance": balance_row["new_balance"],
    }
