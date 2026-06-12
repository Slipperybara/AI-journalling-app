import { apiFetch } from './api';

export type DashboardData = {
  today_bucket: string;
  summary: string | null;
  journaling_week: { day: string; journaled: boolean }[];
  emotional: { day: string; valence: number; arousal: number }[];
  health: { day: string; sleep_quality: string | null; exercise_type: string | null; diet_quality: string | null }[];
  productivity: { day: string; deep_work_hours: number | null }[];
  goals: { active: { name: string }[]; fulfilled: { name: string }[] };
};

export async function getDashboard(): Promise<DashboardData | null> {
  const r = await apiFetch('/api/dashboard');
  if (!r.ok) return null;
  return (await r.json()) as DashboardData;
}
