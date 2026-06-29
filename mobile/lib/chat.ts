import { fetch as expoFetch } from 'expo/fetch';

import { API_URL, apiFetch, getAccessToken } from './api';

export type Message = {
  id: number | string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
};

export type Conversation = {
  id: number;
  started_at: string;
  title: string | null;
  message_count: number;
  last_message_at: string | null;
  first_user_message: string | null;
};

export async function listConversations(): Promise<Conversation[]> {
  const r = await apiFetch('/api/conversations');
  if (!r.ok) return [];
  return (await r.json()) as Conversation[];
}

export async function createConversation(): Promise<number> {
  const r = await apiFetch('/api/conversations', { method: 'POST' });
  const c = (await r.json()) as { id: number };
  return c.id;
}

// Seed (or fetch) a brand-new user's first conversation with a personalized
// greeting. Idempotent server-side: returns the existing conversation if the user
// already has one. Returns null on failure so boot can fall back to an empty chat.
export async function getOrCreateWelcome(): Promise<number | null> {
  try {
    const r = await apiFetch('/api/conversations/welcome', { method: 'POST' });
    if (!r.ok) return null;
    const c = (await r.json()) as { id: number };
    return c.id ?? null;
  } catch {
    return null;
  }
}

export async function getMessages(convId: number): Promise<Message[]> {
  const r = await apiFetch(`/api/conversations/${convId}/messages`);
  if (!r.ok) return [];
  return (await r.json()) as Message[];
}

export async function renameConversation(convId: number, title: string): Promise<void> {
  await apiFetch(`/api/conversations/${convId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
}

// Soft-delete (archive): hides the conversation from the list; messages stay
// in the database so the nightly parser / knowledge graph keep referencing them.
export async function deleteConversation(convId: number): Promise<void> {
  await apiFetch(`/api/conversations/${convId}`, { method: 'DELETE' });
}

export type StreamHandlers = {
  onRetrieval?: (phase: 'start' | 'end') => void;
  onDelta: (text: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
};

// Parse one SSE frame ("event: x\ndata: {...}") and dispatch to the handlers.
function dispatchFrame(frame: string, h: StreamHandlers, finish: (fn: () => void) => void): void {
  let event = '';
  let data = '';
  for (const raw of frame.split('\n')) {
    const line = raw.replace(/\r$/, '');
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trim();
  }
  if (event === 'retrieval') {
    try {
      h.onRetrieval?.(JSON.parse(data).phase);
    } catch {}
  } else if (event === 'delta') {
    try {
      h.onDelta(JSON.parse(data).text);
    } catch {}
  } else if (event === 'done') {
    finish(h.onDone);
  } else if (event === 'error') {
    let msg = 'Connection error';
    try {
      msg = JSON.parse(data).message ?? msg;
    } catch {}
    finish(() => h.onError(msg));
  }
}

// Streams the assistant reply over the native plain-SSE endpoint
// (app/routers/stream.py). Uses `expo/fetch` (a real streaming fetch with a
// ReadableStream body) rather than react-native-sse's XHR transport — the XHR
// path coalesces small chunks on iOS so tokens arrived in one burst; the
// fetch-stream delivers them as the server flushes each, matching the web app's
// @ag-ui fetch streaming. Returns a cancel function.
export async function streamReply(
  convId: number,
  content: string,
  h: StreamHandlers,
): Promise<() => void> {
  const token = await getAccessToken();
  const controller = new AbortController();
  let finished = false;
  const finish = (fn: () => void) => {
    if (finished) return;
    finished = true;
    controller.abort();
    fn();
  };

  (async () => {
    try {
      const resp = await expoFetch(`${API_URL}/api/conversations/${convId}/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ content }),
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) {
        finish(() => h.onError(`Connection error (${resp.status})`));
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sep: number;
        // Frames are separated by a blank line.
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          dispatchFrame(buffer.slice(0, sep), h, finish);
          buffer = buffer.slice(sep + 2);
        }
      }
      if (buffer.trim()) dispatchFrame(buffer, h, finish);
      // Safety net if the stream closed without an explicit `done` event.
      finish(h.onDone);
    } catch (err: any) {
      if (controller.signal.aborted) return; // cancelled by the caller
      finish(() => h.onError(err?.message ?? 'Connection error'));
    }
  })();

  return () => finish(() => {});
}
