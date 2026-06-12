import 'react-native-url-polyfill/auto';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { createClient } from '@supabase/supabase-js';

// Publishable values — safe to ship in the client bundle. Set them in
// mobile/.env (EXPO_PUBLIC_* are inlined at build time). See .env.example.
const SUPABASE_URL = process.env.EXPO_PUBLIC_SUPABASE_URL ?? '';
const SUPABASE_ANON_KEY = process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ?? '';

export const SUPABASE_CONFIGURED = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);

// PKCE + AsyncStorage session persistence is the documented Supabase RN setup.
// detectSessionInUrl is off because there is no browser URL on native — the
// OAuth code is exchanged manually via the deep-link handler (auth step).
export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: {
    storage: AsyncStorage,
    autoRefreshToken: true,
    persistSession: true,
    detectSessionInUrl: false,
    flowType: 'pkce',
  },
});
