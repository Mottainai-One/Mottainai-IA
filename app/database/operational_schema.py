"""Single source of truth for Mottainai operational schema readiness."""

OPERATIONAL_SCHEMA_READY_QUERY = """
SELECT
    to_regclass('mottainai.company') IS NOT NULL
    AND to_regclass('mottainai.retail_store') IS NOT NULL
    AND to_regclass('mottainai.inventory') IS NOT NULL
    AND to_regclass('mottainai.alert') IS NOT NULL
    AND to_regclass('mottainai.batch') IS NOT NULL
    AND to_regclass('mottainai.product') IS NOT NULL
    AND to_regclass('mottainai.sales_transaction') IS NOT NULL
    AND to_regclass('mottainai.sale_item') IS NOT NULL
    AND to_regclass('mottainai.sale_payment') IS NOT NULL
    AND to_regclass('mottainai.promotion') IS NOT NULL
    AND to_regclass('mottainai.disposal') IS NOT NULL
    AND to_regclass('mottainai.disposal_item') IS NOT NULL
    AND to_regclass('mottainai.schema_version') IS NOT NULL
    AND to_regprocedure('mottainai.fn_get_current_company_id()') IS NOT NULL
    AND EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'mottainai'
          AND table_name = 'sale_item'
          AND column_name = 'status'
    )
    AS schema_ready
"""
