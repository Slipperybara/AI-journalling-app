-- Self-service account deletion RPC.
--
-- SECURITY DEFINER so an authenticated user can delete their OWN auth identity
-- without the backend ever holding the service-role key. The backend wipes all
-- app data (Postgres rows + Neo4j subgraph) first; the client then calls this
-- to remove the auth.users row, then signs out.
--
-- Apply to BOTH Supabase projects:
--   staging: eqwijryhjubgnyetufpa
--   prod:    iazqotzjesomoyqfelgt

create or replace function public.delete_account()
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if auth.uid() is null then
    raise exception 'not authenticated';
  end if;
  delete from auth.users where id = auth.uid();
end;
$$;

revoke all on function public.delete_account() from public, anon;
grant execute on function public.delete_account() to authenticated;
