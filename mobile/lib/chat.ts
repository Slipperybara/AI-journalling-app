import EventSource from 'react-native-sse';

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

export async function getMessages(convId: number): Promise<Message[]> {
  const r = await apiFetch(`/api/conversations/${convId}/messages`);
  if (!r.ok) return [];
  return (await r.json()) as Message[];
}

export type StreamHandlers = {
  onRetrieval?: (phase: 'start' | 'end') => void;
  onDelta: (text: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
};

// Streams the assistant reply over the native plain-SSE endpoint
// (app/routers/stream.py). The endpoint persists both the user message and the
// reply, so the caller only sends the text. Returns a cancel function.
export async function streamReply(
  convId: number,
  content: string,
  h: StreamHandlers,
): Promise<() => void> {
  const token = await getAccessToken();
  const es = new EventSource<'retrieval' | 'delta' | 'done'>(
    `${API_URL}/api/conversations/${convId}/stream`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ content }),
      pollingInterval: 0,
    },
  );

  let finished = false;
  const finish = (fn: () => void) => {
    if (finished) return;
    finished = true;
    es.close();
    fn();
  };

  es.addEventListener('retrieval', (e: any) => {
    try {
      h.onRetrieval?.(JSON.parse(e.data).phase);
    } catch {}
  });
  es.addEventListener('delta', (e: any) => {
    try {
      h.onDelta(JSON.parse(e.data).text);
    } catch {}
  });
  es.addEventListener('done', () => finish(h.onDone));
  es.addEventListener('error', (e: any) => {
    // Distinguishes a backend `event: error` (has JSON data) from a transport
    // error (has a message). Both end the stream.
    let msg = 'Connection error';
    if (e?.data) {
      try {
        msg = JSON.parse(e.data).message ?? msg;
      } catch {}
    } else if (e?.message) {
      msg = e.message;
    }
    finish(() => h.onError(msg));
  });

  return () => finish(() => {});
}
