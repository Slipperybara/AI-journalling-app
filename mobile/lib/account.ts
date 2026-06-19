import { apiFetch } from './api';
import { supabase } from './supabase';

// Permanently delete the account. Two steps, in order:
//   1. Backend wipes all app data (Postgres rows + the Neo4j subgraph).
//   2. A SECURITY DEFINER Supabase RPC removes the auth identity itself — the
//      backend never holds the service-role key, so this self-delete lives in
//      Postgres and runs as the authenticated user (auth.uid()).
// Returns true once the auth user is gone; the caller then signs out.
export async function deleteAccount(): Promise<boolean> {
  try {
    const res = await apiFetch('/api/account', { method: 'DELETE' });
    if (!res.ok) return false;
    const { error } = await supabase.rpc('delete_account');
    return !error;
  } catch {
    return false;
  }
}
