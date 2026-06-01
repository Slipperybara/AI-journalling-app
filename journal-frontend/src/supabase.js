import { createClient } from '@supabase/supabase-js';

const url = import.meta.env.VITE_SUPABASE_URL;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!url || !anonKey) {
  console.warn(
    '[supabase] VITE_SUPABASE_URL and/or VITE_SUPABASE_ANON_KEY not set — login will not work. ' +
    'Set them in journal-frontend/.env and restart `npm run dev`.'
  );
}

export const supabase = createClient(
  url || 'http://placeholder',
  anonKey || 'placeholder-anon-key'
);
