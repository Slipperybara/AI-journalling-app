import { apiFetch } from './api';

// Preset catalog entry (bubbles) and a saved tracked field. Mirrors the backend
// `app/routers/tracking.py` shapes.
export type CatalogItem = { key: string; label: string };
export type TrackedField = {
  field_key: string;
  name: string;
  kind: 'preset' | 'custom';
  status: string;
};

export async function getCatalog(): Promise<CatalogItem[]> {
  const res = await apiFetch('/api/tracking/catalog');
  if (!res.ok) return [];
  return (await res.json()) as CatalogItem[];
}

export async function getTracking(): Promise<TrackedField[]> {
  const res = await apiFetch('/api/tracking');
  if (!res.ok) return [];
  return (await res.json()) as TrackedField[];
}

// Bulk-replace the user's active selection. Returns the new active set, or null
// on failure.
export async function saveTracking(
  presetKeys: string[],
  customNames: string[],
): Promise<TrackedField[] | null> {
  const res = await apiFetch('/api/tracking', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ preset_keys: presetKeys, custom_names: customNames }),
  });
  if (!res.ok) return null;
  return (await res.json()) as TrackedField[];
}
