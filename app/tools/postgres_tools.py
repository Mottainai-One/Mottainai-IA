"""Read-only queries against the Mottainai v6 operational schema.

Note: SQL-computed status strings ('RUPTURA', 'ABAIXO_MINIMO', 'EXCESSO',
etc.) and the dict keys returned by get_shelf_inventory_crosscheck
("encontrados", "ausentes_esperados", "alertas_ativos") are a data contract
consumed by other code and by the agents' LLM prompts — they are kept in
Portuguese, not translated as part of this pass.
"""
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.database.postgres import get_pg_session


async def _exec(
    sql: str,
    *,
    empresa_id: int,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Executes a query scoped to the authenticated tenant."""
    if isinstance(empresa_id, bool) or empresa_id < 1:
        raise ValueError("empresa_id must be a positive integer.")

    query_params = {**(params or {}), "empresa_id": empresa_id}
    async with get_pg_session() as session:
        # The v6 operational schema applies RLS on company, retail_store,
        # inventory and sales_transaction. The context is local to the
        # transaction to avoid leaking the tenant across connections.
        await session.execute(
            text("SELECT set_config('app.current_company_id', CAST(:empresa_id AS TEXT), true)"),
            {"empresa_id": str(empresa_id)},
        )
        result = await session.execute(text(sql), query_params)
        cols = list(result.keys())
        return [dict(zip(cols, row)) for row in result.fetchall()]


async def get_stock_alerts(
    empresa_id: int,
    limit: int = 10,
    store_id: int | None = None,
) -> list[dict]:
    """
    Returns active stock alerts for a company.
    Used by the Employee Agent and the Predictive Engine.
    """
    if store_id is not None and (isinstance(store_id, bool) or store_id < 1):
        raise ValueError("store_id must be a positive integer.")

    store_filter = "AND a.store_id = :store_id" if store_id is not None else ""
    sql = f"""
        SELECT
            a.alert_id        AS id,
            a.alert_type      AS type,
            a.priority,
            a.status,
            a.title,
            a.description,
            a.created_at,
            rs.name           AS store_name
        FROM mottainai.alert a
        JOIN mottainai.retail_store rs ON rs.store_id = a.store_id
        JOIN mottainai.company c ON c.company_id = rs.company_id
        WHERE c.company_id = :empresa_id
          AND c.active = TRUE
          AND c.deleted_at IS NULL
          AND rs.active = TRUE
          AND rs.deleted_at IS NULL
          AND a.status = 'ACTIVE'
          {store_filter}
        ORDER BY
            CASE a.priority
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH'     THEN 2
                WHEN 'MEDIUM'   THEN 3
                ELSE 4
            END,
            a.created_at DESC
        LIMIT :limit
    """
    params: dict[str, Any] = {"limit": limit}
    if store_id is not None:
        params["store_id"] = store_id
    return await _exec(sql, empresa_id=empresa_id, params=params)


async def get_expiring_batches(empresa_id: int, days_ahead: int = 7) -> list[dict]:
    """
    Returns batches with an upcoming expiration date.
    Used by the Predictive Engine for loss risk detection.
    """
    sql = """
        SELECT
            b.batch_id,
            b.batch_code,
            b.expiration_date,
            (b.expiration_date - CURRENT_DATE) AS days_to_expire,
            COALESCE(SUM(i.current_quantity), 0) AS total_quantity,
            p.name    AS product_name,
            p.barcode AS barcode,
            rs.store_id,
            rs.name   AS store_name
        FROM mottainai.batch b
        JOIN mottainai.product p ON p.product_id = b.product_id
        JOIN mottainai.inventory i ON i.batch_id = b.batch_id
        JOIN mottainai.retail_store rs ON rs.store_id = i.store_id
        JOIN mottainai.company c ON c.company_id = rs.company_id
        WHERE c.company_id = :empresa_id
          AND b.expiration_date BETWEEN CURRENT_DATE AND (CURRENT_DATE + CAST(:days_ahead AS INTEGER))
          AND i.current_quantity > 0
          AND b.active = TRUE
          AND b.deleted_at IS NULL
          AND i.deleted_at IS NULL
          AND p.active = TRUE
          AND p.deleted_at IS NULL
          AND rs.active = TRUE
          AND rs.deleted_at IS NULL
          AND c.active = TRUE
          AND c.deleted_at IS NULL
        GROUP BY
            b.batch_id,
            b.batch_code,
            b.expiration_date,
            p.name,
            p.barcode,
            rs.store_id,
            rs.name
        ORDER BY b.expiration_date ASC
        LIMIT 20
    """
    return await _exec(
        sql,
        empresa_id=empresa_id,
        params={"days_ahead": days_ahead},
    )


async def get_sales_summary(empresa_id: int, days_back: int = 30) -> list[dict]:
    """
    Returns a per-product sales summary for the last `days_back` days.
    Used by the Predictive Engine for demand forecasting.
    """
    sql = """
        SELECT
            p.product_id,
            p.name        AS product_name,
            p.barcode     AS barcode,
            SUM(si.quantity_sold)          AS total_sold,
            COUNT(DISTINCT st.sale_id)     AS transactions,
            AVG(si.unit_price)             AS avg_price
        FROM mottainai.sales_transaction st
        JOIN mottainai.sale_item si ON si.sale_id = st.sale_id AND si.sale_date = st.sale_date
        JOIN mottainai.product p ON p.product_id = si.product_id
        JOIN mottainai.retail_store rs ON rs.store_id = st.store_id
        JOIN mottainai.company c ON c.company_id = rs.company_id
        WHERE c.company_id = :empresa_id
          AND st.sale_date >= (CURRENT_DATE - CAST(:days_back AS INTEGER))
          AND st.status = 'COMPLETED'
          AND st.deleted_at IS NULL
          AND p.active = TRUE
          AND p.deleted_at IS NULL
          AND rs.active = TRUE
          AND rs.deleted_at IS NULL
          AND c.active = TRUE
          AND c.deleted_at IS NULL
        GROUP BY p.product_id, p.name, p.barcode
        ORDER BY total_sold DESC
        LIMIT 20
    """
    return await _exec(sql, empresa_id=empresa_id, params={"days_back": days_back})


async def get_daily_sales_series(
    empresa_id: int,
    product_ids: list[int],
    days_back: int = 28,
) -> list[dict]:
    """
    Per-product, per-day sold quantity for the given products over the last
    `days_back` days. Used by the Predictive Engine to compute a real
    moving-average/trend demand forecast (as opposed to guessing from a raw
    aggregate dump).
    """
    if not product_ids:
        return []

    sql = """
        SELECT
            p.product_id,
            st.sale_date,
            SUM(si.quantity_sold) AS quantity_sold
        FROM mottainai.sales_transaction st
        JOIN mottainai.sale_item si ON si.sale_id = st.sale_id AND si.sale_date = st.sale_date
        JOIN mottainai.product p ON p.product_id = si.product_id
        JOIN mottainai.retail_store rs ON rs.store_id = st.store_id
        JOIN mottainai.company c ON c.company_id = rs.company_id
        WHERE c.company_id = :empresa_id
          AND st.sale_date >= (CURRENT_DATE - CAST(:days_back AS INTEGER))
          AND st.status = 'COMPLETED'
          AND st.deleted_at IS NULL
          AND p.product_id = ANY(:product_ids)
          AND p.active = TRUE
          AND p.deleted_at IS NULL
          AND rs.active = TRUE
          AND rs.deleted_at IS NULL
          AND c.active = TRUE
          AND c.deleted_at IS NULL
        GROUP BY p.product_id, st.sale_date
        ORDER BY p.product_id, st.sale_date
    """
    return await _exec(
        sql,
        empresa_id=empresa_id,
        params={"days_back": days_back, "product_ids": product_ids},
    )


async def get_kpis(empresa_id: int) -> dict:
    """
    Consolidated management KPIs.
    Used by the Owner Agent.
    """
    sql_revenue = """
        SELECT COALESCE(SUM(si.subtotal), 0) AS revenue_30d
        FROM mottainai.sales_transaction st
        JOIN mottainai.sale_item si ON si.sale_id = st.sale_id AND si.sale_date = st.sale_date
        JOIN mottainai.retail_store rs ON rs.store_id = st.store_id
        JOIN mottainai.company c ON c.company_id = rs.company_id
        WHERE c.company_id = :empresa_id
          AND st.sale_date >= (CURRENT_DATE - INTERVAL '30 days')
          AND st.status = 'COMPLETED'
          AND si.status = 'SOLD'
          AND st.deleted_at IS NULL
          AND rs.active = TRUE
          AND rs.deleted_at IS NULL
          AND c.active = TRUE
          AND c.deleted_at IS NULL
    """
    sql_losses = """
        SELECT
            COALESCE(SUM(di.disposed_quantity * b.unit_cost), 0) AS disposal_cost_30d
        FROM mottainai.disposal d
        JOIN mottainai.disposal_item di ON di.disposal_id = d.disposal_id
        JOIN mottainai.batch b ON b.batch_id = di.batch_id
        JOIN mottainai.retail_store rs ON rs.store_id = d.store_id
        JOIN mottainai.company c ON c.company_id = rs.company_id
        WHERE c.company_id = :empresa_id
          AND d.created_at >= (CURRENT_DATE - CAST(30 AS INTEGER))
          AND b.active = TRUE
          AND b.deleted_at IS NULL
          AND rs.active = TRUE
          AND rs.deleted_at IS NULL
          AND c.active = TRUE
          AND c.deleted_at IS NULL
    """
    sql_alerts = """
        SELECT COUNT(*) AS active_alerts
        FROM mottainai.alert a
        JOIN mottainai.retail_store rs ON rs.store_id = a.store_id
        JOIN mottainai.company c ON c.company_id = rs.company_id
        WHERE c.company_id = :empresa_id
          AND c.active = TRUE
          AND c.deleted_at IS NULL
          AND rs.active = TRUE
          AND rs.deleted_at IS NULL
          AND a.status = 'ACTIVE'
    """

    revenue = await _exec(sql_revenue, empresa_id=empresa_id)
    losses = await _exec(sql_losses, empresa_id=empresa_id)
    alerts = await _exec(sql_alerts, empresa_id=empresa_id)

    return {
        "revenue_30d": revenue[0]["revenue_30d"] if revenue else Decimal("0"),
        "disposal_cost_30d": losses[0]["disposal_cost_30d"] if losses else Decimal("0"),
        "active_alerts": int(alerts[0]["active_alerts"]) if alerts else 0,
    }


async def get_kpis_by_store(empresa_id: int, days_back: int = 30) -> list[dict]:
    """
    Per-store KPIs (revenue, disposal cost, active alerts) for benchmarking
    stores within the same company. Used by the Owner Agent.
    """
    sql_revenue = """
        SELECT
            rs.store_id,
            rs.name AS store_name,
            COALESCE(SUM(si.subtotal), 0) AS revenue,
            COUNT(DISTINCT st.sale_id) AS transactions
        FROM mottainai.retail_store rs
        JOIN mottainai.company c ON c.company_id = rs.company_id
        LEFT JOIN mottainai.sales_transaction st
            ON st.store_id = rs.store_id
           AND st.sale_date >= (CURRENT_DATE - CAST(:days_back AS INTEGER))
           AND st.status = 'COMPLETED'
           AND st.deleted_at IS NULL
        LEFT JOIN mottainai.sale_item si
            ON si.sale_id = st.sale_id
           AND si.sale_date = st.sale_date
           AND si.status = 'SOLD'
        WHERE c.company_id = :empresa_id
          AND c.active = TRUE
          AND c.deleted_at IS NULL
          AND rs.active = TRUE
          AND rs.deleted_at IS NULL
        GROUP BY rs.store_id, rs.name
        ORDER BY revenue DESC
    """
    sql_losses = """
        SELECT
            rs.store_id,
            COALESCE(SUM(di.disposed_quantity * b.unit_cost), 0) AS disposal_cost
        FROM mottainai.retail_store rs
        JOIN mottainai.company c ON c.company_id = rs.company_id
        LEFT JOIN mottainai.disposal d
            ON d.store_id = rs.store_id
           AND d.created_at >= (CURRENT_DATE - CAST(:days_back AS INTEGER))
        LEFT JOIN mottainai.disposal_item di ON di.disposal_id = d.disposal_id
        LEFT JOIN mottainai.batch b
            ON b.batch_id = di.batch_id
           AND b.active = TRUE
           AND b.deleted_at IS NULL
        WHERE c.company_id = :empresa_id
          AND c.active = TRUE
          AND c.deleted_at IS NULL
          AND rs.active = TRUE
          AND rs.deleted_at IS NULL
        GROUP BY rs.store_id
    """
    sql_alerts = """
        SELECT
            rs.store_id,
            COUNT(a.alert_id) AS active_alerts
        FROM mottainai.retail_store rs
        JOIN mottainai.company c ON c.company_id = rs.company_id
        LEFT JOIN mottainai.alert a
            ON a.store_id = rs.store_id
           AND a.status = 'ACTIVE'
        WHERE c.company_id = :empresa_id
          AND c.active = TRUE
          AND c.deleted_at IS NULL
          AND rs.active = TRUE
          AND rs.deleted_at IS NULL
        GROUP BY rs.store_id
    """

    revenue_rows = await _exec(sql_revenue, empresa_id=empresa_id, params={"days_back": days_back})
    loss_rows = await _exec(sql_losses, empresa_id=empresa_id, params={"days_back": days_back})
    alert_rows = await _exec(sql_alerts, empresa_id=empresa_id)

    losses_by_store = {row["store_id"]: row["disposal_cost"] for row in loss_rows}
    alerts_by_store = {row["store_id"]: row["active_alerts"] for row in alert_rows}

    return [
        {
            "store_id": row["store_id"],
            "store_name": row["store_name"],
            "revenue": row["revenue"],
            "transactions": row["transactions"],
            "disposal_cost": losses_by_store.get(row["store_id"], Decimal("0")),
            "active_alerts": int(alerts_by_store.get(row["store_id"], 0)),
        }
        for row in revenue_rows
    ]


async def get_inventory_status(empresa_id: int, store_id: int | None = None) -> list[dict]:
    """
    Current inventory status (stock quantity vs minimum).
    Used by the Employee Agent.
    """
    if store_id is not None and (isinstance(store_id, bool) or store_id < 1):
        raise ValueError("store_id must be a positive integer.")

    store_filter = "AND i.store_id = :store_id" if store_id is not None else ""
    sql = f"""
        SELECT
            p.name        AS product_name,
            p.barcode     AS barcode,
            i.current_quantity   AS quantity,
            i.minimum_quantity   AS min_quantity,
            i.maximum_quantity   AS max_quantity,
            CASE
                WHEN i.current_quantity <= 0                          THEN 'RUPTURA'
                WHEN i.current_quantity < i.minimum_quantity          THEN 'ABAIXO_MINIMO'
                WHEN i.maximum_quantity IS NOT NULL
                 AND i.current_quantity > i.maximum_quantity          THEN 'EXCESSO'
                ELSE 'NORMAL'
            END AS stock_status,
            rs.name AS store_name
        FROM mottainai.inventory i
        JOIN mottainai.batch b ON b.batch_id = i.batch_id
        JOIN mottainai.product p ON p.product_id = b.product_id
        JOIN mottainai.retail_store rs ON rs.store_id = i.store_id
        JOIN mottainai.company c ON c.company_id = rs.company_id
        WHERE c.company_id = :empresa_id
          AND c.active = TRUE
          AND c.deleted_at IS NULL
          AND rs.active = TRUE
          AND rs.deleted_at IS NULL
          AND i.deleted_at IS NULL
          AND b.active = TRUE
          AND b.deleted_at IS NULL
          AND p.active = TRUE
          AND p.deleted_at IS NULL
          {store_filter}
        ORDER BY
            CASE
                WHEN i.current_quantity <= 0                     THEN 1
                WHEN i.current_quantity < i.minimum_quantity     THEN 2
                ELSE 3
            END
        LIMIT 30
    """
    params: dict[str, Any] = {}
    if store_id is not None:
        params["store_id"] = store_id
    return await _exec(sql, empresa_id=empresa_id, params=params)


async def get_inventory_match(
    empresa_id: int,
    product_name: str,
    store_id: int | None = None,
) -> dict[str, Any] | None:
    """Locates a product seen on the shelf within the authenticated tenant."""
    if store_id is not None and (isinstance(store_id, bool) or store_id < 1):
        raise ValueError("store_id must be a positive integer.")

    store_filter = "AND i.store_id = :store_id" if store_id is not None else ""
    sql = f"""
        WITH company_inventory AS (
            SELECT
                i.batch_id,
                i.current_quantity,
                i.minimum_quantity
            FROM mottainai.inventory i
            JOIN mottainai.retail_store rs ON rs.store_id = i.store_id
            JOIN mottainai.company c ON c.company_id = rs.company_id
            WHERE c.company_id = :empresa_id
              AND c.active = TRUE
              AND c.deleted_at IS NULL
              AND rs.active = TRUE
              AND rs.deleted_at IS NULL
              AND i.deleted_at IS NULL
              {store_filter}
        )
        SELECT
            p.product_id AS id,
            p.name,
            p.barcode,
            COALESCE(SUM(ci.current_quantity), 0) AS quantity,
            COALESCE(SUM(ci.minimum_quantity), 0) AS min_quantity,
            CASE
                WHEN COUNT(ci.batch_id) = 0 THEN 'SEM_INVENTARIO'
                WHEN COALESCE(SUM(ci.current_quantity), 0) <= 0 THEN 'RUPTURA'
                WHEN COALESCE(SUM(ci.current_quantity), 0) < COALESCE(SUM(ci.minimum_quantity), 0)
                    THEN 'ABAIXO_MINIMO'
                ELSE 'OK'
            END AS status
        FROM mottainai.product p
        LEFT JOIN mottainai.batch b
          ON b.product_id = p.product_id
         AND b.active = TRUE
         AND b.deleted_at IS NULL
        LEFT JOIN company_inventory ci ON ci.batch_id = b.batch_id
        WHERE p.active = TRUE
          AND p.deleted_at IS NULL
          AND LOWER(p.name) ILIKE LOWER(:name_like)
        GROUP BY p.product_id, p.name, p.barcode
        ORDER BY
            CASE WHEN LOWER(p.name) = LOWER(:exact_name) THEN 0 ELSE 1 END,
            p.name
        LIMIT 1
    """
    params: dict[str, Any] = {
        "name_like": f"%{product_name[:80]}%",
        "exact_name": product_name[:80],
    }
    if store_id is not None:
        params["store_id"] = store_id
    rows = await _exec(sql, empresa_id=empresa_id, params=params)
    return rows[0] if rows else None


async def get_shelf_inventory_crosscheck(
    empresa_id: int,
    store_id: int | None,
    detected_products: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Cross-checks detected products against v6 schema stock and alerts."""
    found: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for product_name in detected_products:
        normalized_name = product_name.strip()
        if not normalized_name or normalized_name.lower() in seen_names:
            continue
        seen_names.add(normalized_name.lower())
        match = await get_inventory_match(empresa_id, normalized_name, store_id)
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
