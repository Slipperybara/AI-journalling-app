import { supabase } from './supabase';

// Defaults to the prod Render API so the app works from any device/simulator
// without LAN config. Override with EXPO_PUBLIC_API_URL in mobile/.env for
// local backend testing.
export const API_URL =
  process.env.EXPO_PUBLIC_API_URL ?? 'https://ai-journalling-app-api.onrender.com';

export async function getAccessToken(): Promise<string | null> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  return session?.access_token ?? null;
}

// Mirror of the web app's apiFetch — attaches the Supabase JWT as a Bearer
// token on every call. `path` is the API path (e.g. '/api/dashboard').
export async function apiFetch(path: string, opts: RequestInit = {}): Promise<Response> {
  const token = await getAccessToken();
  const headers: Record<string, string> = {
    ...((opts.headers as Record<string, string>) ?? {}),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(`${API_URL}${path}`, { ...opts, headers });
}
