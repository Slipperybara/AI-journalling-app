import AsyncStorage from '@react-native-async-storage/async-storage';

import { apiFetch } from './api';
import { loadAnswers } from './onboardingProfile';

const SYNCED_KEY = 'jai_profile_synced';

// Push the locally-captured onboarding answers to the backend once, after login,
// so the bot's earliest replies already know the user (name, how they're feeling,
// what's weighing on them). Best-effort and idempotent: a flag prevents re-PUTs,
// and any failure leaves the flag unset so the next launch retries.
export async function syncOnboardingProfile(): Promise<void> {
  try {
    if (await AsyncStorage.getItem(SYNCED_KEY)) return;

    const a = await loadAnswers();
    const hasAnything = a.name || a.emotional || a.occupation || (a.issues?.length ?? 0) > 0;
    if (!hasAnything) return; // nothing worth syncing (e.g. legacy install)

    const res = await apiFetch('/api/profile', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: a.name,
        age: a.age,
        gender: a.gender,
        occupation: a.occupation,
        emotional: a.emotional,
        familiarity: a.familiarity,
        issues: a.issues ?? [],
      }),
    });
    if (res.ok) await AsyncStorage.setItem(SYNCED_KEY, '1');
  } catch {
    // best-effort — retried next launch
  }
}
