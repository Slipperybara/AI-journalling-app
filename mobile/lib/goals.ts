import { apiFetch } from './api';

export type Goal = { name: string; source?: string };

export type GoalCommandResult =
  | { ok: true; listMessage?: string }
  | { ok: false; message: string };

async function errOf(res: Response, fallback: string): Promise<string> {
  const e = (await res.json().catch(() => ({}))) as { detail?: string };
  return e.detail || fallback;
}

// Mirror of the web app's /goal slash-commands (App.jsx tryGoalCommand).
// Returns null when `text` isn't a /goal command; otherwise the outcome:
//  - listMessage  → inject locally, don't call the bot
//  - ok (no list) → caller falls through so the bot acknowledges naturally
//  - !ok          → inject the error locally, don't call the bot
export async function tryGoalCommand(text: string): Promise<GoalCommandResult | null> {
  const match = text.match(/^\/goal\s+(\w+)\s*(.*)$/i);
  if (!match) return null;
  const verb = match[1].toLowerCase();
  const rest = (match[2] || '').trim();
  try {
    if (verb === 'add') {
      const res = await apiFetch('/api/goals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: rest }),
      });
      return res.ok ? { ok: true } : { ok: false, message: await errOf(res, 'failed to add goal') };
    }
    if (verb === 'fulfill') {
      const res = await apiFetch(`/api/goals/${encodeURIComponent(rest)}/fulfill`, { method: 'PATCH' });
      return res.ok ? { ok: true } : { ok: false, message: await errOf(res, 'failed to fulfill goal') };
    }
    if (verb === 'remove') {
      const res = await apiFetch(`/api/goals/${encodeURIComponent(rest)}`, { method: 'DELETE' });
      return res.ok ? { ok: true } : { ok: false, message: await errOf(res, 'failed to remove goal') };
    }
    if (verb === 'rename') {
      const parts = rest.match(/^"([^"]+)"\s+"([^"]+)"$/);
      if (!parts) return { ok: false, message: 'usage: /goal rename "Old Name" "New Name"' };
      const [, oldName, newName] = parts;
      const res = await apiFetch(`/api/goals/${encodeURIComponent(oldName)}/rename`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_name: newName }),
      });
      return res.ok ? { ok: true } : { ok: false, message: await errOf(res, 'failed to rename goal') };
    }
    if (verb === 'list') {
      const res = await apiFetch('/api/goals?status=active');
      if (!res.ok) return { ok: false, message: 'failed to list goals' };
      const rows = (await res.json()) as Goal[];
      const names = rows.map((r) => r.name);
      return { ok: true, listMessage: names.length ? `Active goals: ${names.join(', ')}` : 'No active goals.' };
    }
    return { ok: false, message: `unknown command /goal ${verb}` };
  } catch {
    return { ok: false, message: 'command failed' };
  }
}
