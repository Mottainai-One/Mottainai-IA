-- Retired: the official database repository owns every table and migration.
-- Use Mottainai-Banco-Operacional develop/database/operational/install.sql.
DO $$ BEGIN RAISE EXCEPTION 'Legacy IA schema disabled; use the official operational installer'; END $$;
