// Dashboard scoring — ported verbatim from journal-frontend/src/App.jsx.
// Everything is scored out of 100, mirroring app/dashboard_summary.py.

export const SLEEP_MAP: Record<string, number> = { Poor: 0, Fair: 0.33, Good: 0.67, Excellent: 1 };
export const DIET_MAP: Record<string, number> = {
  'Junk/Heavy': 0,
  'Carbs Centered': 0.25,
  'Meat and Vegetable centered': 0.6,
  Clean: 1,
};
export const EXERCISE_MAP: Record<string, number> = {
  None: 0,
  'Light Cardio': 0.5,
  'Light Strength': 0.5,
  'Heavy Cardio': 1,
  'Heavy Strength': 1,
};
// A focused, sustainable deep-work day. 4h maps to a full focus score of 100.
export const FOCUS_TARGET_HOURS = 4;

// Emotional 0-100: (valence+arousal)/2 mapped from [-1,1] → [0,100]. Neutral = 50.
export function emotionalScore(valence: number | null, arousal: number | null): number | null {
  if (valence == null && arousal == null) return null;
  const v = valence ?? 0;
  const a = arousal ?? 0;
  return (((v + a) / 2 + 1) / 2) * 100;
}

export type HealthRow = {
  sleep_quality?: string | null;
  exercise_type?: string | null;
  diet_quality?: string | null;
};

// Physical 0-100: mean of whichever of sleep / exercise-intensity / diet are present.
export function physicalScore(r: HealthRow): number | null {
  const parts: number[] = [];
  if (r.sleep_quality && r.sleep_quality in SLEEP_MAP) parts.push(SLEEP_MAP[r.sleep_quality]);
  if (r.exercise_type && r.exercise_type in EXERCISE_MAP) parts.push(EXERCISE_MAP[r.exercise_type]);
  if (r.diet_quality && r.diet_quality in DIET_MAP) parts.push(DIET_MAP[r.diet_quality]);
  if (!parts.length) return null;
  return (parts.reduce((s, v) => s + v, 0) / parts.length) * 100;
}

export function avg(vals: (number | null | undefined)[]): number | null {
  const nums = vals.filter((v): v is number => v != null);
  return nums.length ? nums.reduce((s, v) => s + v, 0) / nums.length : null;
}

export function fmtScore(v: number | null): string {
  return v == null ? '—' : Math.round(v).toString();
}

export function last7Days(): string[] {
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (6 - i));
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  });
}

// Compact weekday label for the 7-day axis (anchored to local noon so the date
// can't slip across a timezone boundary).
export function weekdayShort(iso: string): string {
  const input = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T12:00:00` : iso;
  return new Date(input).toLocaleDateString('en-US', { weekday: 'short' });
}

// Mirror of the backend's bucket_for (app/time_buckets.py): subtract 6h from a
// local timestamp, take its calendar date as YYYY-MM-DD.
const BUCKET_OFFSET_MS = 6 * 60 * 60 * 1000;
export function bucketKey(t: Date | string | number): string {
  const d = t instanceof Date ? t : new Date(t);
  const shifted = new Date(d.getTime() - BUCKET_OFFSET_MS);
  const y = shifted.getFullYear();
  const m = String(shifted.getMonth() + 1).padStart(2, '0');
  const day = String(shifted.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}
