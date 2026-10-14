"""Mobile/IA consultations from published analytical facts, never live stock."""
import json
from decimal import Decimal

from sqlalchemy import text

from app.database.analytics import get_analytics_session
from app.database.identity import initialize_pg_context
from app.database.postgres import get_pg_session


class AnalyticsPublicationUnavailable(RuntimeError):
    pass


async def _read(sql, *, empresa_id, params=None, store_id=None):
    if isinstance(empresa_id, bool) or not isinstance(empresa_id, int) or empresa_id < 1:
        raise ValueError("empresa_id must be a positive integer")
    if store_id is not None and (isinstance(store_id, bool) or not isinstance(store_id, int) or store_id < 1):
        raise ValueError("store_id must be a positive integer")
    # Operational access checks do not read business facts for the consultation.
    async with get_pg_session() as operational:
        actor = await initialize_pg_context(operational, empresa_id)
        admin = (await operational.execute(text(
            "SELECT mottainai.fn_current_user_is_company_admin()"
        ))).scalar_one()
        if store_id is not None:
            allowed = (await operational.execute(text(
                "SELECT mottainai.fn_can_access_store(:store_id)"
            ), {"store_id": store_id})).scalar_one()
            if not allowed:
                raise PermissionError("Store is outside the authenticated scope")
    scope = None if admin else actor["store_id"]
    async with get_analytics_session() as session:
        publication = (await session.execute(text(
            """SELECT source_cutoff_at, snapshot_date
               FROM mottainai_analytics.vw_published_batch
               WHERE EXISTS (
                   SELECT 1 FROM mottainai_analytics.dim_store s
                   JOIN mottainai_analytics.dim_company c ON c.company_id=s.company_id
                   WHERE s.company_id=:empresa_id AND s.active AND c.active
                     AND (CAST(:scope_store_id AS bigint) IS NULL OR s.store_id=CAST(:scope_store_id AS bigint))
                     AND (CAST(:requested_store_id AS bigint) IS NULL OR s.store_id=CAST(:requested_store_id AS bigint))
               )"""
        ), {"empresa_id": empresa_id, "scope_store_id": scope,
             "requested_store_id": store_id})).mappings().first()
        if publication is None:
            raise AnalyticsPublicationUnavailable("No published analytical data for the authorized scope")
        query = """
            WITH allowed_stores AS (
                SELECT s.store_id, s.name
                FROM mottainai_analytics.dim_store s
                JOIN mottainai_analytics.dim_company c ON c.company_id = s.company_id
                WHERE s.company_id = :empresa_id AND s.active AND c.active
                  AND (CAST(:scope_store_id AS bigint) IS NULL OR s.store_id = CAST(:scope_store_id AS bigint))
                  AND (CAST(:requested_store_id AS bigint) IS NULL OR s.store_id = CAST(:requested_store_id AS bigint))
            )
        """ + sql
        result = await session.execute(text(query), {
            **(params or {}), "empresa_id": empresa_id,
            "scope_store_id": scope, "requested_store_id": store_id,
            "snapshot_date": publication["snapshot_date"],
        })
        return [{**dict(row), "data_source": "analytics",
                 "snapshot_date": publication["snapshot_date"],
                 "source_cutoff_at": publication["source_cutoff_at"]}
                for row in result.mappings().all()]


async def get_stock_alerts(empresa_id, limit=10, store_id=None):
    return await _read("""
        SELECT a.alert_id AS id, a.status, s.name AS store_name,
               NULL::text AS type, NULL::text AS priority,
               NULL::text AS title, NULL::text AS description
        FROM mottainai_analytics.fact_alert_snapshot a
        JOIN allowed_stores s ON s.store_id=a.store_id
        WHERE a.snapshot_date=:snapshot_date AND a.status='ACTIVE'
        ORDER BY a.alert_id DESC LIMIT :limit
    """, empresa_id=empresa_id, store_id=store_id, params={"limit": limit})


async def get_expiring_batches(empresa_id, days_ahead=7, store_id=None):
    return await _read("""
        SELECT i.batch_id, NULL::text AS batch_code, i.expiration_date,
               i.expiration_date - CAST(:snapshot_date AS date) AS days_to_expire,
               sum(i.quantity) AS total_quantity, p.name AS product_name, p.barcode,
               s.store_id, s.name AS store_name
        FROM mottainai_analytics.fact_inventory_snapshot i
        JOIN allowed_stores s ON s.store_id=i.store_id
        JOIN mottainai_analytics.dim_product p ON p.product_id=i.product_id AND p.active
        WHERE i.company_id=:empresa_id AND i.snapshot_date=:snapshot_date
          AND i.quantity>0 AND i.expiration_date BETWEEN :snapshot_date
              AND CAST(:snapshot_date AS date)+CAST(:days_ahead AS integer)
        GROUP BY i.batch_id,i.expiration_date,p.name,p.barcode,s.store_id,s.name
        ORDER BY i.expiration_date LIMIT 20
    """, empresa_id=empresa_id, store_id=store_id, params={"days_ahead": days_ahead})


async def get_sales_summary(empresa_id, days_back=30, store_id=None):
    return await _read("""
        SELECT p.product_id,p.name AS product_name,p.barcode,
               sum(i.quantity) AS total_sold,count(DISTINCT (f.sale_id,f.sale_date)) AS transactions,
               avg(i.unit_price) AS avg_price
        FROM mottainai_analytics.fact_sale_item i
        JOIN mottainai_analytics.fact_sales f ON f.sale_id=i.sale_id AND f.sale_date=i.sale_date AND f.store_id=i.store_id
        JOIN allowed_stores s ON s.store_id=f.store_id
        JOIN mottainai_analytics.dim_product p ON p.product_id=i.product_id AND p.active
        WHERE f.company_id=:empresa_id AND f.status='COMPLETED'
          AND f.sale_date BETWEEN CAST(:snapshot_date AS date)-CAST(:days_back AS integer)+1 AND :snapshot_date
        GROUP BY p.product_id,p.name,p.barcode ORDER BY total_sold DESC LIMIT 20
    """, empresa_id=empresa_id, store_id=store_id, params={"days_back": days_back})


async def get_daily_sales_series(empresa_id, product_ids, days_back=28, store_id=None):
    if not product_ids:
        return []
    return await _read("""
        SELECT i.product_id,i.sale_date,sum(i.quantity) AS quantity_sold
        FROM mottainai_analytics.fact_sale_item i
        JOIN mottainai_analytics.fact_sales f ON f.sale_id=i.sale_id AND f.sale_date=i.sale_date AND f.store_id=i.store_id
        JOIN allowed_stores s ON s.store_id=f.store_id
        WHERE f.company_id=:empresa_id AND f.status='COMPLETED'
          AND f.sale_date BETWEEN CAST(:snapshot_date AS date)-CAST(:days_back AS integer)+1 AND :snapshot_date
          AND i.product_id IN (SELECT value::bigint FROM jsonb_array_elements_text(CAST(:product_ids AS jsonb)))
        GROUP BY i.product_id,i.sale_date ORDER BY i.product_id,i.sale_date
    """, empresa_id=empresa_id, store_id=store_id,
        params={"days_back": days_back,"product_ids": json.dumps(product_ids)})


async def get_kpis_by_store(empresa_id, days_back=30):
    return await _read("""
        SELECT s.store_id,s.name AS store_name,
            coalesce((SELECT sum(f.net_amount) FROM mottainai_analytics.fact_sales f
                WHERE f.store_id=s.store_id AND f.company_id=:empresa_id AND f.status='COMPLETED'
                AND f.sale_date BETWEEN CAST(:snapshot_date AS date)-CAST(:days_back AS integer)+1 AND :snapshot_date),0) AS revenue,
            (SELECT count(*) FROM mottainai_analytics.fact_sales f WHERE f.store_id=s.store_id
                AND f.company_id=:empresa_id AND f.status='COMPLETED'
                AND f.sale_date BETWEEN CAST(:snapshot_date AS date)-CAST(:days_back AS integer)+1 AND :snapshot_date) AS transactions,
            coalesce((SELECT sum(l.estimated_value) FROM mottainai_analytics.fact_loss_destination l
                WHERE l.store_id=s.store_id AND l.company_id=:empresa_id AND l.destination_type='DISPOSAL'
                AND l.loss_date BETWEEN CAST(:snapshot_date AS date)-CAST(:days_back AS integer)+1 AND :snapshot_date),0) AS disposal_cost,
            (SELECT count(*) FROM mottainai_analytics.fact_alert_snapshot a WHERE a.store_id=s.store_id
                AND a.snapshot_date=:snapshot_date AND a.status='ACTIVE') AS active_alerts
        FROM allowed_stores s ORDER BY revenue DESC
    """, empresa_id=empresa_id, params={"days_back": days_back})


async def get_kpis(empresa_id):
    rows = await get_kpis_by_store(empresa_id)
    return {"revenue_30d": sum((r["revenue"] for r in rows), Decimal(0)),
            "disposal_cost_30d": sum((r["disposal_cost"] for r in rows), Decimal(0)),
            "active_alerts": sum(r["active_alerts"] for r in rows),
            **({k: rows[0][k] for k in ("snapshot_date","source_cutoff_at","data_source")} if rows else {})}


async def get_inventory_status(empresa_id, store_id=None):
    return await _read("""
        SELECT p.name AS product_name,p.barcode,i.quantity AS quantity,
               i.minimum_quantity AS min_quantity,NULL::numeric AS max_quantity,
               CASE WHEN i.quantity<=0 THEN 'RUPTURA'
                    WHEN i.minimum_quantity IS NULL THEN 'MINIMO_INDISPONIVEL'
                    WHEN i.quantity<i.minimum_quantity THEN 'ABAIXO_MINIMO' ELSE 'NORMAL' END AS stock_status,
               s.name AS store_name
        FROM mottainai_analytics.fact_inventory_snapshot i
        JOIN allowed_stores s ON s.store_id=i.store_id
        JOIN mottainai_analytics.dim_product p ON p.product_id=i.product_id AND p.active
        WHERE i.company_id=:empresa_id AND i.snapshot_date=:snapshot_date
        ORDER BY i.quantity LIMIT 30
    """, empresa_id=empresa_id, store_id=store_id)


MAX_PRODUCT_NAME_CHARS=80


async def get_inventory_matches(empresa_id, product_names, store_id=None):
    names=list(dict.fromkeys(n.strip()[:MAX_PRODUCT_NAME_CHARS].lower() for n in product_names if n and n.strip()))
    if not names:
        return {}
    rows=await _read("""
        SELECT w.value AS wanted_name,m.*
        FROM jsonb_array_elements_text(CAST(:names AS jsonb)) w
        CROSS JOIN LATERAL (
            SELECT p.product_id AS id,p.name,p.barcode,sum(i.quantity) AS quantity,
                   sum(i.minimum_quantity) AS min_quantity,
                   CASE WHEN sum(i.quantity)<=0 THEN 'RUPTURA'
                        WHEN sum(i.minimum_quantity) IS NULL THEN 'MINIMO_INDISPONIVEL'
                        WHEN sum(i.quantity)<sum(i.minimum_quantity) THEN 'ABAIXO_MINIMO' ELSE 'OK' END AS status
            FROM mottainai_analytics.fact_inventory_snapshot i
            JOIN allowed_stores s ON s.store_id=i.store_id
            JOIN mottainai_analytics.dim_product p ON p.product_id=i.product_id AND p.active
            WHERE i.company_id=:empresa_id AND i.snapshot_date=:snapshot_date
              AND lower(p.name) LIKE '%'||w.value||'%'
            GROUP BY p.product_id,p.name,p.barcode
            ORDER BY CASE WHEN lower(p.name)=w.value THEN 0 ELSE 1 END,p.name LIMIT 1
        ) m
    """, empresa_id=empresa_id, store_id=store_id, params={"names":json.dumps(names)})
    return {r["wanted_name"]:{k:v for k,v in r.items() if k!="wanted_name"} for r in rows}


async def get_inventory_match(empresa_id, product_name, store_id=None):
    return next(iter((await get_inventory_matches(empresa_id,[product_name],store_id)).values()),None)
