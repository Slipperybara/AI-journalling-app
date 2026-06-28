import AsyncStorage from '@react-native-async-storage/async-storage';

import { apiFetch } from './api';

export type NotificationPrefs = {
  enabled: boolean;
  hour: number;
  minute: number;
  tz: string;
};

// Earliest selectable time, in the user's LOCAL clock — mirrors the backend
// floor (app/notifications_prefs.py). Yesterday's recap only exists after the
// 06:00 local day boundary, and the rolling batch generates it at the top of
// that hour; 06:30 leaves the generation tick a margin before delivery.
export const MIN_HOUR = 6;
export const MIN_MINUTE = 30;

export function deviceTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

// Clamp a chosen (hour, minute) up to the 06:15 floor.
export function clampToFloor(hour: number, minute: number): { hour: number; minute: number } {
  if (hour < MIN_HOUR || (hour === MIN_HOUR && minute < MIN_MINUTE)) {
    return { hour: MIN_HOUR, minute: MIN_MINUTE };
  }
  return { hour, minute };
}

export async function getNotificationPrefs(): Promise<NotificationPrefs | null> {
  try {
    const res = await apiFetch('/api/notifications');
    if (!res.ok) return null;
    const json = await res.json();
    return (json?.prefs as NotificationPrefs) ?? null;
  } catch {
    return null;
  }
}

export async function saveNotificationPrefs(p: {
  enabled: boolean;
  hour: number;
  minute: number;
}): Promise<boolean> {
  const { hour, minute } = clampToFloor(p.hour, p.minute);
  try {
    const res = await apiFetch('/api/notifications', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: p.enabled, hour, minute, tz: deviceTimezone() }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

// Pretty 12-hour label, e.g. (8, 0) -> "8:00 AM", (6, 15) -> "6:15 AM".
export function formatTime(hour: number, minute: number): string {
  const ampm = hour < 12 ? 'AM' : 'PM';
  const h12 = hour % 12 === 0 ? 12 : hour % 12;
  return `${h12}:${String(minute).padStart(2, '0')} ${ampm}`;
}

// Onboarding runs before login, so the chosen time can't be PUT yet — it's
// stashed locally and synced to the backend on the first authed app boot.
const LOCAL_KEY = 'jai_notify_choice';

export type LocalNotifyChoice = { enabled: boolean; hour: number; minute: number };

export async function saveLocalNotifyChoice(c: LocalNotifyChoice): Promise<void> {
  try {
    await AsyncStorage.setItem(LOCAL_KEY, JSON.stringify(c));
  } catch {
    // best-effort
  }
}

export async function loadLocalNotifyChoice(): Promise<LocalNotifyChoice | null> {
  try {
    const v = await AsyncStorage.getItem(LOCAL_KEY);
    return v ? (JSON.parse(v) as LocalNotifyChoice) : null;
  } catch {
    return null;
  }
}

// Post-login: push the locally-chosen time to the backend. Idempotent (the
// backend upserts), and the drawer setting keeps the local copy in step, so
// re-running on every boot is harmless.
export async function syncNotificationPrefs(): Promise<void> {
  const local = await loadLocalNotifyChoice();
  if (!local) return; // nothing chosen during onboarding (e.g. legacy install)
  await saveNotificationPrefs(local);
}
