-- Retired: legacy dataLoad does not match the protected operational schema.
DO $$ BEGIN RAISE EXCEPTION 'Legacy IA data load disabled; use fixtures approved by the operational database repository'; END $$;
