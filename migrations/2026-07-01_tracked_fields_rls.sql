-- Enable Row Level Security on the two new tracking tables.
--
-- Every other app table already has RLS enabled. These tables are created
-- automatically by `init_db()` on deploy, but ENABLE ROW LEVEL SECURITY is a
-- manual step (init_db is portable across dev/prod and doesn't manage RLS).
--
-- Both tables are only ever read/written by the FastAPI backend, which connects
-- as the table owner (bypasses RLS) and scopes every query by user_id. Enabling
-- RLS with no policies denies the `anon` / `authenticated` PostgREST roles all
-- direct access — the intended defense-in-depth, matching the rest of the schema.
--
-- Apply to BOTH Supabase projects:
--   staging: eqwijryhjubgnyetufpa
--   prod:    iazqotzjesomoyqfelgt

alter table public.tracked_fields        enable row level security;
alter table public.tracked_field_values  enable row level security;
