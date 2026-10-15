-- Read-only check of the official operational contract.
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
    AND NOT EXISTS (
        SELECT 1
        FROM (VALUES
            ('product', 'company_id'),
            ('app_user', 'firebase_uid'),
            ('app_user', 'employee_id'),
            ('app_user', 'token_version'),
            ('employee', 'store_id'),
            ('employee', 'role_id'),
            ('inventory', 'version'),
            ('retail_store', 'company_id')
        ) AS required(table_name, column_name)
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns c
            WHERE c.table_schema = 'mottainai'
              AND c.table_name = required.table_name
              AND c.column_name = required.column_name
        )
    )
    AND to_regprocedure('mottainai.fn_bootstrap_staff_context(text,text)') IS NOT NULL
    AND to_regprocedure('mottainai.fn_get_current_user_id()') IS NOT NULL
    AND to_regprocedure('mottainai.fn_get_current_store_id()') IS NOT NULL
    AND to_regprocedure('mottainai.fn_can_access_store(integer)') IS NOT NULL
    AND to_regprocedure('mottainai.fn_atomic_update_inventory(integer,numeric,mottainai.movement_type,integer,text,integer)') IS NOT NULL
    AS schema_ready;
