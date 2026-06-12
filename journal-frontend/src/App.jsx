import { useState, useEffect, useRef, useCallback, Component } from 'react';
import { supabase } from './supabase';
import { HttpAgent } from '@ag-ui/client';
import { motion } from 'motion/react';
import logo from './assets/logo.png';

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

// Editorial design tokens (from the reference "Design main interface").
// The background carries a gentle ambient tint: warm (orange) during normal
// conversation, cool (blue) while the bot is searching the knowledge graph
// (the retrieval phase). The two share the same structure and crossfade.
const BG_COOL = [
  'radial-gradient(ellipse at 2% 98%, rgba(243, 242, 238, 0.85) 0%, transparent 52%)',
  'radial-gradient(ellipse at 100% 0%, rgba(190, 204, 220, 0.45) 0%, transparent 56%)',
  'radial-gradient(ellipse at 78% 58%, rgba(202, 212, 223, 0.22) 0%, transparent 60%)',
  'linear-gradient(110deg, #E6E7E4 0%, #DBDFE2 40%, #CFD6DC 70%, #C7D0D9 100%)',
].join(', ');
const BG_WARM = [
  'radial-gradient(ellipse at 2% 98%, rgba(245, 242, 237, 0.85) 0%, transparent 52%)',
  'radial-gradient(ellipse at 100% 0%, rgba(232, 200, 172, 0.36) 0%, transparent 56%)',
  'radial-gradient(ellipse at 78% 58%, rgba(231, 212, 193, 0.18) 0%, transparent 60%)',
  'linear-gradient(110deg, #ECE9E4 0%, #E5DED3 40%, #DFD6C7 70%, #DAD1C0 100%)',
].join(', ');
const SERIF = "'Lora', Georgia, serif";
const SANS = "'DM Sans', system-ui, sans-serif";

const SUPABASE_CONFIGURED =
  !!import.meta.env.VITE_SUPABASE_URL && !!import.meta.env.VITE_SUPABASE_ANON_KEY;

// Wraps the global fetch with a Supabase access token (when configured).
// Aliased so the global stays callable inside this helper even after we
// rename every consumer's `apiFetch(` → `apiFetch(` below.
const _rawFetch = globalThis.fetch.bind(globalThis);
async function apiFetch(url, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (SUPABASE_CONFIGURED) {
    const { data: { session } } = await supabase.auth.getSession();
    if (session?.access_token) {
      headers.Authorization = `Bearer ${session.access_token}`;
    }
  }
  return _rawFetch(url, { ...opts, headers });
}

async function getAccessToken() {
  if (!SUPABASE_CONFIGURED) return null;
  const { data: { session } } = await supabase.auth.getSession();
  return session?.access_token ?? null;
}

function LoginScreen() {
  const handleSignIn = async () => {
    await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: window.location.origin },
    });
  };
  return (
    <div className="flex h-screen w-screen items-center justify-center bg-slate-50">
      <div className="rounded-2xl bg-white p-10 shadow-xl max-w-md w-full text-center">
        <h1 className="text-3xl font-semibold mb-3 text-slate-900">MindForge AI</h1>
        <p className="text-slate-600 mb-8 leading-relaxed">
          A warm journaling companion that helps you notice the patterns in how
          you feel, work, and live.
        </p>
        <button
          onClick={handleSignIn}
          className="w-full rounded-xl bg-slate-900 px-4 py-3 text-white font-medium hover:bg-slate-700 transition"
        >
          Sign in with Google
        </button>
      </div>
    </div>
  );
}

const dayLabel = (iso) => {
  // Bare YYYY-MM-DD is anchored to local noon so timezone offsets can't push it into the prior day.
  const input = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T12:00:00` : iso;
  const d = new Date(input);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const that = new Date(d); that.setHours(0, 0, 0, 0);
  const diffDays = Math.round((today - that) / 86400000);
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
};

// Mirror of the backend's `bucket_for` (`app/time_buckets.py`): subtract 6h from a
// local timestamp, take its calendar date. Returned as YYYY-MM-DD for stable comparison.
const BUCKET_OFFSET_MS = 6 * 60 * 60 * 1000;
const bucketKey = (t) => {
  const d = t instanceof Date ? t : new Date(t);
  const shifted = new Date(d.getTime() - BUCKET_OFFSET_MS);
  const y = shifted.getFullYear();
  const m = String(shifted.getMonth() + 1).padStart(2, '0');
  const day = String(shifted.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

const conversationPreview = (c) => {
  if (c.first_user_message) {
    const s = c.first_user_message.trim().replace(/\s+/g, ' ');
    return s.length > 48 ? s.slice(0, 48) + '…' : s;
  }
  return 'New conversation';
};


// Keeps one crashing view (e.g. a bad data shape) from white-screening the
// whole app — the sidebar/nav stay usable and the error clears on view change.
class ViewErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidUpdate(prevProps) {
    if (prevProps.viewKey !== this.props.viewKey && this.state.error) {
      this.setState({ error: null });
    }
  }
  render() {
    if (this.state.error) {
      return (
        <div className="flex-1 flex items-center justify-center p-10 text-center">
          <p style={{ fontFamily: SERIF, fontSize: '18px', color: '#9A9790' }}>
            Something went wrong showing this view. Try another tab, or reload the page.
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  const [view, setView] = useState('chat');
  const [conversations, setConversations] = useState([]);
  const [activeConvId, setActiveConvId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [input, setInput] = useState('');
  const [isWaiting, setIsWaiting] = useState(false);
  const [morningBrief, setMorningBrief] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [bgMode, setBgMode] = useState('warm'); // 'warm' (conversing) | 'cool' (retrieving)
  const [dashboard, setDashboard] = useState({ emotional: [], health: [], productivity: [], events: [], goals: { active: [], fulfilled: [], candidate: [] }, summary: '', journaling_week: [] });
  const messagesEndRef = useRef(null);

  // Auth state. When Supabase isn't configured, treat the app as ready
  // and skip the login gate (the backend dev shim resolves user_id).
  const [session, setSession] = useState(null);
  const [authReady, setAuthReady] = useState(!SUPABASE_CONFIGURED);

  useEffect(() => {
    if (!SUPABASE_CONFIGURED) return;
    let unsub;
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setAuthReady(true);
    });
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, newSession) => {
      setSession(newSession);
    });
    unsub = subscription;
    return () => { unsub?.unsubscribe(); };
  }, []);

  const signOut = useCallback(async () => {
    if (SUPABASE_CONFIGURED) {
      await supabase.auth.signOut();
    }
  }, []);

  const fetchConversations = useCallback(async () => {
    const res = await apiFetch(`${API}/api/conversations`);
    if (!res.ok) return [];
    const data = await res.json();
    setConversations(data);
    return data;
  }, []);

  const loadMessages = useCallback(async (convId) => {
    const res = await apiFetch(`${API}/api/conversations/${convId}/messages`);
    if (!res.ok) return [];
    const data = await res.json();
    setMessages(data);
    return data;
  }, []);

  const createConversation = useCallback(async () => {
    const res = await apiFetch(`${API}/api/conversations`, { method: 'POST' });
    const conv = await res.json();
    await fetchConversations();
    setActiveConvId(conv.id);
    setMessages([]);
    return conv.id;
  }, [fetchConversations]);

  // Boot: open the latest conversation in today's bucket if one exists
  // (e.g. the morning brief, or a chat the user already started today).
  // Otherwise leave activeConvId null — a new conversation is created lazily
  // on the first send, so the sidebar doesn't show empty 'New Conversation'
  // entries the user never used.
  //
  // Also capture today's morning brief — a conversation that opens with an
  // assistant message (normal chats open with the user). Its text becomes the
  // blank-slate greeting in the chat instead of the generic prompt.
  useEffect(() => {
    (async () => {
      const list = await fetchConversations();
      const todayBucket = bucketKey(new Date());

      const briefConv = list.find(
        c => bucketKey(c.started_at) === todayBucket && c.first_user_message == null && c.message_count > 0
      );
      if (briefConv) {
        try {
          const res = await apiFetch(`${API}/api/conversations/${briefConv.id}/messages`);
          if (res.ok) {
            const msgs = await res.json();
            const firstAi = msgs.find(m => m.role === 'assistant');
            if (firstAi) setMorningBrief(firstAi.content);
          }
        } catch { /* non-fatal — fall back to the generic greeting */ }
      }

      const newest = list[0];
      if (newest && bucketKey(newest.started_at) === todayBucket) {
        setActiveConvId(newest.id);
      }
    })();
  }, [fetchConversations]);

  // Whenever active conv changes, load its messages. Flag the in-flight fetch
  // so the chat panel can show a loading state; `cancelled` guards against a
  // fast switch resolving out of order. The skeleton itself is suppressed
  // during a send (isWaiting) so the optimistic bubble isn't hidden.
  useEffect(() => {
    if (activeConvId == null) return;
    let cancelled = false;
    (async () => {
      setMessagesLoading(true);
      await loadMessages(activeConvId);
      if (!cancelled) setMessagesLoading(false);
    })();
    return () => { cancelled = true; };
  }, [activeConvId, loadMessages]);

  // Auto-scroll on new message
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isWaiting]);

  // Dashboard fetch when switching to dashboard view
  useEffect(() => {
    if (view === 'dashboard') {
      apiFetch(`${API}/api/dashboard`).then(r => r.json()).then(setDashboard).catch(() => {});
    }
  }, [view]);

  const tryGoalCommand = async (text) => {
    const match = text.match(/^\/goal\s+(\w+)\s*(.*)$/i);
    if (!match) return null;
    const verb = match[1].toLowerCase();
    const rest = (match[2] || '').trim();
    const errOf = async (res, fallback) => {
      const e = await res.json().catch(() => ({}));
      return e.detail || fallback;
    };
    try {
      if (verb === 'add') {
        const res = await apiFetch(`${API}/api/goals`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: rest }),
        });
        return res.ok ? { ok: true } : { ok: false, message: await errOf(res, 'failed to add goal') };
      }
      if (verb === 'fulfill') {
        const res = await apiFetch(`${API}/api/goals/${encodeURIComponent(rest)}/fulfill`, { method: 'PATCH' });
        return res.ok ? { ok: true } : { ok: false, message: await errOf(res, 'failed to fulfill goal') };
      }
      if (verb === 'remove') {
        const res = await apiFetch(`${API}/api/goals/${encodeURIComponent(rest)}`, { method: 'DELETE' });
        return res.ok ? { ok: true } : { ok: false, message: await errOf(res, 'failed to remove goal') };
      }
      if (verb === 'rename') {
        const parts = rest.match(/^"([^"]+)"\s+"([^"]+)"$/);
        if (!parts) return { ok: false, message: 'usage: /goal rename "Old Name" "New Name"' };
        const [, oldName, newName] = parts;
        const res = await apiFetch(`${API}/api/goals/${encodeURIComponent(oldName)}/rename`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ new_name: newName }),
        });
        return res.ok ? { ok: true } : { ok: false, message: await errOf(res, 'failed to rename goal') };
      }
      if (verb === 'list') {
        const res = await apiFetch(`${API}/api/goals?status=active`);
        if (!res.ok) return { ok: false, message: 'failed to list goals' };
        const rows = await res.json();
        const names = rows.map(r => r.name);
        return { ok: true, listMessage: names.length ? `Active goals: ${names.join(', ')}` : 'No active goals.' };
      }
      return { ok: false, message: `unknown command /goal ${verb}` };
    } catch {
      return { ok: false, message: 'command failed' };
    }
  };

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || isWaiting) return;

    setInput('');

    // Slash-command interception runs before we touch the conversation —
    // /goal list and failed mutations just inject a local-only message and
    // never need a backing conversation row.
    const goalResult = await tryGoalCommand(text);
    if (goalResult) {
      if (goalResult.ok) {
        apiFetch(`${API}/api/dashboard`).then(r => r.json()).then(setDashboard).catch(() => {});
      }
      if (goalResult.listMessage) {
        setMessages(prev => [
          ...prev,
          { id: `local-${Date.now()}`, role: 'assistant', content: goalResult.listMessage, created_at: new Date().toISOString() },
        ]);
        return;
      }
      if (!goalResult.ok) {
        setMessages(prev => [
          ...prev,
          { id: `local-${Date.now()}`, role: 'assistant', content: `(${goalResult.message})`, created_at: new Date().toISOString() },
        ]);
        // Mutation failed — don't run the bot. User saw the error inline.
        return;
      }
      // Mutation succeeded — fall through, post the user message so the bot
      // can acknowledge naturally.
    }

    // Lazily create a conversation on the first real send. Keeps empty
    // 'New Conversation' rows out of the sidebar.
    let convId = activeConvId;
    if (!convId) {
      convId = await createConversation();
    }

    setIsWaiting(true);

    // Optimistic user message
    const optimistic = {
      id: `tmp-${Date.now()}`,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, optimistic]);

    try {
      // Stream the assistant reply over AG-UI. The endpoint persists both the
      // user message and the assistant reply, so we don't POST /messages here;
      // the optimistic user bubble is reconciled by loadMessages on complete.
      const token = await getAccessToken();
      const agent = new HttpAgent({
        url: `${API}/api/conversations/${convId}/agui`,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        // The SDK references global fetch unbound from window; pass a bound
        // copy or it throws "Illegal invocation" in the browser.
        fetch: globalThis.fetch.bind(globalThis),
      });
      agent.messages = [{ id: `u-${Date.now()}`, role: 'user', content: text }];

      // runAgent(params, subscriber) returns a Promise; the subscriber's
      // callbacks fire as events arrive. textMessageBuffer is the running
      // accumulated assistant text, so we render it directly.
      const streamId = `stream-${Date.now()}`;
      let started = false;
      const upsertStream = (content) => {
        if (!started) {
          started = true; // first token: the growing serif text replaces the thinking pulse
          setMessages(prev => [
            ...prev,
            { id: streamId, role: 'assistant', content, created_at: new Date().toISOString() },
          ]);
        } else {
          setMessages(prev => prev.map(m => m.id === streamId ? { ...m, content } : m));
        }
      };

      await agent.runAgent({}, {
        // Ambient tint: cool while the bot searches the graph, warm otherwise.
        onStepStartedEvent: ({ event }) => { if (event.stepName === 'retrieval') setBgMode('cool'); },
        onStepFinishedEvent: ({ event }) => { if (event.stepName === 'retrieval') setBgMode('warm'); },
        onTextMessageContentEvent: ({ textMessageBuffer }) => upsertStream(textMessageBuffer),
        onRunErrorEvent: () => upsertStream('(Something went wrong streaming the reply.)'),
      });

      // Reconcile optimistic/streamed bubbles with persisted rows.
      setBgMode('warm');
      setIsWaiting(false);
      loadMessages(convId);
      fetchConversations();
    } catch {
      setMessages(prev => prev.filter(m => m.id !== optimistic.id));
      setBgMode('warm');
      setIsWaiting(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const openConversation = (id) => { setActiveConvId(id); setView('chat'); setSidebarOpen(false); };

  const renameConversation = async (id, title) => {
    await apiFetch(`${API}/api/conversations/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });
    fetchConversations();
  };

  const deleteConversation = async (id) => {
    const ok = window.confirm(
      'Remove this chat from your sidebar?\n\n' +
      'Your conversation history is kept — it still feeds your morning briefs, ' +
      'dashboard, and the companion’s memory. Only the sidebar entry is hidden.'
    );
    if (!ok) return;
    await apiFetch(`${API}/api/conversations/${id}`, { method: 'DELETE' });
    if (activeConvId === id) { setActiveConvId(null); setMessages([]); }
    fetchConversations();
  };

  if (!authReady) return null;
  if (SUPABASE_CONFIGURED && !session) return <LoginScreen />;

  const navItems = [['chat', 'Chat'], ['dashboard', 'Dashboard']];

  return (
    <div className="relative h-screen flex overflow-hidden" style={{ background: BG_WARM, fontFamily: SANS }}>
      {/* Cool (retrieval) tint — crossfades in while the bot searches the graph */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{ background: BG_COOL, opacity: bgMode === 'cool' ? 1 : 0, transition: 'opacity 1.1s ease' }}
      />

      {/* Mobile backdrop when the drawer is open */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-20 md:hidden" style={{ background: 'rgba(40,40,38,0.18)' }} onClick={() => setSidebarOpen(false)} />
      )}

      {/* Left sidebar — minimal, editorial. Static on desktop; a slide-in
          drawer on mobile (collapsed unless opened). */}
      <aside
        className={
          'z-30 flex flex-col shrink-0 pt-10 pb-8 pl-8 pr-5 ' +
          'fixed inset-y-0 left-0 md:static ' +
          'bg-white/85 backdrop-blur-md md:bg-transparent md:backdrop-blur-none ' +
          'transition-transform duration-300 ease-out md:translate-x-0 ' +
          (sidebarOpen ? 'translate-x-0' : '-translate-x-full')
        }
        style={{ width: '224px' }}
      >
        <div className="flex items-center gap-2" style={{ marginBottom: '22px' }}>
          <img src={logo} alt="MindForge logo" style={{ height: '26px', width: 'auto', objectFit: 'contain', flexShrink: 0 }} />
          <span style={{ fontSize: '10px', letterSpacing: '0.14em', color: '#8E8B84', textTransform: 'uppercase' }}>
            MindForge
          </span>
        </div>

        {/* Page toggles */}
        <div className="flex flex-col gap-2.5 mb-8">
          {navItems.map(([v, label]) => (
            <button
              key={v}
              onClick={() => { setView(v); setSidebarOpen(false); }}
              className="text-left"
              style={{
                fontFamily: SANS, fontSize: '13px',
                fontWeight: view === v ? 500 : 400,
                color: view === v ? '#2A2825' : '#9A9790',
                background: 'transparent', cursor: 'pointer', transition: 'color 0.2s',
              }}
            >
              {label}
            </button>
          ))}
        </div>

        {/* New chat — primary action, sits above the entries history. */}
        <button
          onClick={() => { createConversation(); setView('chat'); setSidebarOpen(false); }}
          className="text-left flex items-center gap-2 rounded-md"
          style={{
            marginLeft: '-9px', marginBottom: '20px', padding: '8px 10px',
            fontFamily: SANS, fontSize: '13px', fontWeight: 500, color: '#5E5B54',
            background: 'transparent', cursor: 'pointer', transition: 'background 0.2s, color 0.2s',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(0,0,0,0.045)'; e.currentTarget.style.color = '#2A2825'; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = '#5E5B54'; }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round">
            <path d="M12 5v14M5 12h14" />
          </svg>
          New chat
        </button>

        <span style={{ fontSize: '10px', letterSpacing: '0.14em', color: '#B0AEA8', textTransform: 'uppercase', marginBottom: '12px' }}>
          Entries
        </span>
        <div className="flex flex-col gap-0.5 flex-1 overflow-y-auto no-scrollbar -mr-3 pr-3">
          {conversations.length === 0 && (
            <span style={{ fontSize: '12px', color: '#BDB9B2' }}>Nothing yet.</span>
          )}
          {conversations.map(c => (
            <SidebarEntry
              key={c.id}
              conv={c}
              isActive={activeConvId === c.id && view === 'chat'}
              onOpen={openConversation}
              onRename={renameConversation}
              onDelete={deleteConversation}
            />
          ))}
        </div>

        {SUPABASE_CONFIGURED && session && (
          <div className="mt-4 pt-3" style={{ borderTop: '1px solid rgba(0,0,0,0.06)' }}>
            <button onClick={signOut} className="hover:underline" style={{ fontSize: '11px', color: '#9A9790', background: 'transparent', cursor: 'pointer' }}>
              Sign out
            </button>
          </div>
        )}
      </aside>

      {/* Main */}
      <main className="relative z-10 flex-1 flex flex-col overflow-hidden">
        {/* Mobile menu button — opens the drawer */}
        <button
          onClick={() => setSidebarOpen(true)}
          className="md:hidden absolute top-5 left-4 z-10 p-2 rounded-md"
          style={{ background: 'rgba(255,255,255,0.5)', color: '#6E6B64', lineHeight: 0 }}
          aria-label="Open menu"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>

        <ViewErrorBoundary viewKey={view}>
          {view === 'chat' && (
            <ChatView
              messages={messages}
              messagesLoading={messagesLoading}
              activeConv={conversations.find(c => c.id === activeConvId)}
              morningBrief={morningBrief}
              input={input}
              setInput={setInput}
              isWaiting={isWaiting}
              handleKeyDown={handleKeyDown}
              messagesEndRef={messagesEndRef}
            />
          )}
          {view === 'dashboard' && <DashboardView data={dashboard} />}
        </ViewErrorBoundary>
      </main>

      {/* Right spacer — narrower than the sidebar so the journal column sits
          slightly right of centre. Hidden on mobile. */}
      {view === 'chat' && <div className="hidden md:block shrink-0" style={{ width: '120px' }} />}
    </div>
  );
}

function SidebarEntry({ conv, isActive, onOpen, onRename, onDelete }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const label = conv.title || conversationPreview(conv);

  const startRename = () => { setDraft(conv.title || ''); setEditing(true); setMenuOpen(false); };
  const commitRename = () => {
    setEditing(false);
    const t = draft.trim();
    if (t && t !== (conv.title || '')) onRename(conv.id, t);
  };

  if (editing) {
    return (
      <input
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') setEditing(false); }}
        onBlur={commitRename}
        placeholder={conversationPreview(conv)}
        className="my-1.5 w-full outline-none"
        style={{ fontFamily: SANS, fontSize: '13px', color: '#2A2825', background: 'rgba(255,255,255,0.7)', borderRadius: '4px', padding: '4px 6px' }}
      />
    );
  }

  return (
    <div className="group relative flex items-start">
      <button
        onClick={() => onOpen(conv.id)}
        className="text-left flex-1 min-w-0 flex flex-col gap-0.5 py-2"
        style={{ background: 'transparent', cursor: 'pointer' }}
      >
        <span className="truncate" style={{ fontSize: '13px', fontWeight: isActive ? 500 : 400, color: isActive ? '#2A2825' : '#9A9790', transition: 'color 0.2s' }}>
          {label}
        </span>
        <span style={{ fontSize: '11px', color: isActive ? '#A8A59E' : '#BDB9B2' }}>
          {dayLabel(conv.started_at)}
        </span>
      </button>
      <button
        onClick={() => setMenuOpen((o) => !o)}
        className="opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity shrink-0 px-1.5 py-2"
        style={{ color: '#A8A59E', background: 'transparent', cursor: 'pointer', lineHeight: 1, fontSize: '15px' }}
        aria-label="Chat options"
      >
        ⋯
      </button>
      {menuOpen && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
          <div className="absolute right-0 top-8 z-20 rounded-md py-1" style={{ background: 'rgba(255,255,255,0.97)', border: '1px solid rgba(0,0,0,0.07)', boxShadow: '0 4px 14px rgba(0,0,0,0.08)', minWidth: '116px' }}>
            <button onClick={startRename} className="block w-full text-left px-3 py-1.5 hover:bg-black/5" style={{ fontSize: '12px', color: '#4A4842', background: 'transparent', cursor: 'pointer' }}>
              Rename
            </button>
            <button onClick={() => { setMenuOpen(false); onDelete(conv.id); }} className="block w-full text-left px-3 py-1.5 hover:bg-black/5" style={{ fontSize: '12px', color: '#B0524A', background: 'transparent', cursor: 'pointer' }}>
              Delete
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// Restrained shimmer panel shown while a conversation's messages load from the
// backend. Mimics a couple of message blocks so the layout doesn't jump.
const SKELETON_LINES = [
  { w: '34%', mb: '12px' },
  { w: '90%', mb: '12px' },
  { w: '78%', mb: '22px' },
  { w: '28%', mb: '12px' },
  { w: '86%', mb: '12px' },
  { w: '62%', mb: '12px' },
];

function MessagesSkeleton() {
  return (
    <div role="status" aria-busy="true" aria-label="Loading conversation" style={{ paddingTop: '4px' }}>
      {SKELETON_LINES.map((l, i) => (
        <div
          key={i}
          className="animate-pulse"
          style={{ height: '13px', width: l.w, borderRadius: '6px', background: 'rgba(120,116,108,0.12)', marginBottom: l.mb }}
        />
      ))}
    </div>
  );
}

function ChatView({ messages, messagesLoading, activeConv, morningBrief, input, setInput, isWaiting, handleKeyDown, messagesEndRef }) {
  const textareaRef = useRef(null);

  // Auto-grow vertically with content.
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${ta.scrollHeight}px`;
  }, [input]);

  const last = messages[messages.length - 1];
  // Input shows under the last AI message (or an empty entry). Hidden while a
  // reply is in flight — the streamed serif text takes its place.
  const showInput = !isWaiting && (!last || last.role === 'assistant');
  const thinking = isWaiting && (!last || last.role === 'user');
  // Show the loading panel only for genuine conversation loads — never during a
  // send (isWaiting), where the optimistic bubble should stay visible.
  const loading = messagesLoading && !isWaiting;

  useEffect(() => {
    if (showInput) textareaRef.current?.focus();
  }, [showInput, activeConv?.id]);

  const today = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
  const COL = { maxWidth: '620px', margin: '0 auto', width: '100%' };

  return (
    <>
      {/* Date header (extra left padding on mobile clears the menu button) */}
      <div className="pt-10 flex items-baseline gap-3 pl-16 pr-6 md:px-6 min-w-0" style={COL}>
        <span className="shrink-0" style={{ fontSize: '11px', letterSpacing: '0.12em', color: '#9C998F', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>
          {today}
        </span>
        {activeConv && (
          <span className="truncate" style={{ fontSize: '11px', letterSpacing: '0.06em', color: '#B4B1A9', textTransform: 'uppercase' }}>
            · {activeConv.title || conversationPreview(activeConv)}
          </span>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto no-scrollbar pt-10 pb-12 pl-16 pr-6 md:px-6" style={{ width: '100%' }}>
        <div style={COL}>
          {loading && <MessagesSkeleton />}

          {!loading && messages.length === 0 && !thinking && (
            <p style={{ fontFamily: SERIF, fontSize: '19px', lineHeight: 1.65, color: '#6E6B64', whiteSpace: 'pre-wrap' }}>
              {morningBrief || "What's on your mind?"}
            </p>
          )}

          {!loading && messages.map(m => (
            <JournalMessage key={m.id} message={m} />
          ))}

          {!loading && thinking && (
            <div className="mb-3" style={{ height: '20px' }}>
              <motion.span
                style={{ display: 'inline-block', width: '6px', height: '6px', borderRadius: '9999px', background: '#8E8B83' }}
                animate={{ opacity: [0.25, 1, 0.25] }}
                transition={{ duration: 1.4, repeat: Infinity, ease: 'easeInOut' }}
              />
            </div>
          )}

          {/* Inline serif input under the last AI message */}
          {!loading && showInput && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4, delay: 0.1 }} className="mt-3">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={1}
                placeholder="Write here…"
                className="w-full resize-none outline-none no-scrollbar"
                style={{ background: 'transparent', border: 'none', fontFamily: SERIF, fontSize: '20px', lineHeight: 1.65, color: '#38342F', caretColor: '#A8A49C', maxHeight: '45vh' }}
              />
              {input === '' && (
                <p style={{ fontSize: '11px', letterSpacing: '0.06em', color: '#B4B1A9', marginTop: '6px' }}>
                  Return to send · Shift + return for new line
                </p>
              )}
            </motion.div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>
    </>
  );
}

// Lightweight markdown for the companion's voice — mirrors mobile/components/
// Markdown.tsx: **bold**, *italics*, ==highlight==, and "- " bullets.
const MD_INLINE = /(\*\*[^*]+\*\*|==[^=]+==|\*[^*\n]+\*|_[^_\n]+_)/g;
function mdInline(text, keyBase) {
  const out = [];
  let last = 0, i = 0, m;
  MD_INLINE.lastIndex = 0;
  while ((m = MD_INLINE.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0], key = `${keyBase}-${i++}`;
    if (tok.startsWith('**')) out.push(<strong key={key} style={{ fontWeight: 600 }}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith('==')) out.push(<span key={key} style={{ background: 'rgba(224,137,79,0.20)', borderRadius: '2px', padding: '0 2px' }}>{tok.slice(2, -2)}</span>);
    else out.push(<em key={key}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function MarkdownBody({ content, style }) {
  const blocks = [];
  let para = [], bullets = null, k = 0;
  const flushPara = () => {
    if (!para.length) return;
    blocks.push(<p key={`p-${k++}`} style={{ ...style, whiteSpace: 'pre-wrap', margin: blocks.length ? '10px 0 0' : 0 }}>{mdInline(para.join('\n'), `p-${k}`)}</p>);
    para = [];
  };
  const flushBullets = () => {
    if (!bullets) return;
    blocks.push(<ul key={`u-${k++}`} style={{ ...style, margin: blocks.length ? '6px 0 0' : 0, paddingLeft: '20px' }}>{bullets}</ul>);
    bullets = null;
  };
  content.split('\n').forEach((line, idx) => {
    const b = /^\s*[-*]\s+(.*)$/.exec(line);
    if (b) { flushPara(); if (!bullets) bullets = []; bullets.push(<li key={`li-${idx}`}>{mdInline(b[1], `li-${idx}`)}</li>); }
    else if (line.trim() === '') { flushPara(); flushBullets(); }
    else { flushBullets(); para.push(line); }
  });
  flushPara(); flushBullets();
  return <>{blocks}</>;
}

function JournalMessage({ message }) {
  const isUser = message.role === 'user';
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}>
      {isUser ? (
        <div className="flex justify-end mb-8 mt-1">
          <p style={{ fontFamily: SERIF, fontSize: '18px', fontStyle: 'italic', lineHeight: 1.75, color: '#5C5850', textAlign: 'right', whiteSpace: 'pre-wrap' }}>
            {message.content}
          </p>
        </div>
      ) : (
        <div className="mb-6">
          <MarkdownBody content={message.content} style={{ fontFamily: SERIF, fontSize: '19px', lineHeight: 1.65, color: '#6E6B64' }} />
        </div>
      )}
    </motion.div>
  );
}

function DashboardView({ data }) {
  const {
    emotional = [], health = [], productivity = [],
    goals: initialGoals, summary, journaling_week = [],
  } = data;

  // The journaling tracker defines the canonical 7-day axis (proper 6am
  // buckets from the backend); the bars look up scores against the same days.
  const days = journaling_week.length ? journaling_week.map(w => w.day) : last7Days();

  const emoByDay = {};
  emotional.forEach(r => { const s = emotionalScore(r.valence, r.arousal); if (s != null) emoByDay[r.day] = s; });
  const physByDay = {};
  health.forEach(r => { const s = physicalScore(r); if (s != null) physByDay[r.day] = s; });
  const focusByDay = {}, focusHoursByDay = {};
  productivity.forEach(r => {
    if (r.deep_work_hours != null) {
      focusByDay[r.day] = Math.min(r.deep_work_hours / FOCUS_TARGET_HOURS, 1) * 100;
      focusHoursByDay[r.day] = r.deep_work_hours;
    }
  });

  const emoAvg = avg(days.map(d => emoByDay[d]));
  const physAvg = avg(days.map(d => physByDay[d]));
  const focusAvg = avg(days.map(d => focusByDay[d]));
  const focusHoursAvg = avg(days.map(d => focusHoursByDay[d]));

  return (
    <div className="flex-1 overflow-y-auto px-8 py-7">
      <div className="max-w-4xl mx-auto space-y-7">
        <GoalsStrip initialGoals={initialGoals} />
        <SummarySentence text={summary} />
        <JournalingTracker week={journaling_week} days={days} />
        <DimensionBars title="Emotional health" color="#E0894F" days={days} scoreByDay={emoByDay} headline={fmtScore(emoAvg)} />
        <DimensionBars title="Physical health" color="#6E9B7A" days={days} scoreByDay={physByDay} headline={fmtScore(physAvg)} />
        <DimensionBars
          title="Focus" color="#6E86C4" days={days} scoreByDay={focusByDay}
          headline={fmtScore(focusAvg)}
          subtitle={focusHoursAvg != null ? `${focusHoursAvg.toFixed(1)}h/day avg` : null}
        />
      </div>
    </div>
  );
}

function GoalsStrip({ initialGoals }) {
  const empty = { active: [], fulfilled: [] };
  const [goals] = useState({ ...empty, ...(initialGoals || {}) });

  if (!goals.active || goals.active.length === 0) return null;

  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-slate-400 uppercase tracking-wider mr-1">Goals</span>
      {goals.active.map(g => (
        <span
          key={g.name}
          className="px-2 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200"
          title={g.source === 'agent' ? 'discovered from chat' : 'added by you'}
        >
          {g.name}
        </span>
      ))}
      <span className="ml-auto text-slate-400">{goals.active.length} of 3 active</span>
    </div>
  );
}

// ── Dashboard scoring (mirrors app/dashboard_summary.py) ─────────────────────
// Everything is scored out of 100 for consistency.
const SLEEP_MAP = { 'Poor': 0, 'Fair': 0.33, 'Good': 0.67, 'Excellent': 1 };
const DIET_MAP = { 'Junk/Heavy': 0, 'Carbs Centered': 0.25, 'Meat and Vegetable centered': 0.6, 'Clean': 1 };
const EXERCISE_MAP = { 'None': 0, 'Light Cardio': 0.5, 'Light Strength': 0.5, 'Heavy Cardio': 1, 'Heavy Strength': 1 };
// A focused, sustainable deep-work day. 4h maps to a full focus score of 100.
const FOCUS_TARGET_HOURS = 4;

// Emotional 0-100: (valence+arousal)/2 mapped from [-1,1] → [0,100]. Neutral = 50.
function emotionalScore(valence, arousal) {
  if (valence == null && arousal == null) return null;
  const v = valence ?? 0, a = arousal ?? 0;
  return ((v + a) / 2 + 1) / 2 * 100;
}

// Physical 0-100: mean of whichever of sleep / exercise-intensity / diet are present.
function physicalScore(r) {
  const parts = [];
  if (r.sleep_quality in SLEEP_MAP) parts.push(SLEEP_MAP[r.sleep_quality]);
  if (r.exercise_type in EXERCISE_MAP) parts.push(EXERCISE_MAP[r.exercise_type]);
  if (r.diet_quality in DIET_MAP) parts.push(DIET_MAP[r.diet_quality]);
  if (!parts.length) return null;
  return parts.reduce((s, v) => s + v, 0) / parts.length * 100;
}

function avg(vals) {
  const nums = vals.filter(v => v != null);
  return nums.length ? nums.reduce((s, v) => s + v, 0) / nums.length : null;
}

function fmtScore(v) { return v == null ? '—' : Math.round(v).toString(); }

function last7Days() {
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
// can't slip across a timezone boundary). Distinct from the verbose `dayLabel`.
function weekdayShort(iso) {
  const input = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T12:00:00` : iso;
  return new Date(input).toLocaleDateString('en-US', { weekday: 'short' });
}

// ── Dashboard presentation ───────────────────────────────────────────────────
function SummarySentence({ text }) {
  if (!text) return null;
  return (
    <p style={{ fontFamily: SERIF, fontSize: '17px', lineHeight: 1.6, color: '#56534B' }}>
      {text}
    </p>
  );
}

function JournalingTracker({ week, days }) {
  const list = week && week.length ? week : days.map(d => ({ day: d, journaled: false }));
  const count = list.filter(w => w.journaled).length;
  return (
    <section className="space-y-2">
      <div className="flex items-baseline justify-between">
        <span style={{ fontSize: '11px', letterSpacing: '0.12em', color: '#8E8B84', textTransform: 'uppercase', fontFamily: SANS }}>
          Journaling streak
        </span>
        <span style={{ fontFamily: SANS, fontSize: '13px', color: '#4A4842' }}>
          <span style={{ fontWeight: 600 }}>{count}</span>
          <span style={{ color: '#A8A49C' }}>/7 days</span>
        </span>
      </div>
      <div className="flex gap-1.5">
        {list.map(w => (
          <div key={w.day} className="flex-1 flex flex-col items-center gap-1">
            <div
              title={`${w.day}${w.journaled ? ' · journaled' : ''}`}
              style={{ width: '100%', height: '30px', borderRadius: '6px', background: w.journaled ? '#6E9B7A' : '#ECEAE5', transition: 'background 0.3s' }}
            />
            <span style={{ fontSize: '9px', color: '#B7B4AD', fontFamily: SANS }}>{weekdayShort(w.day)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

// Full-width per-day bar chart for one dimension. One bar per day, scored 0-100.
function DimensionBars({ title, color, days, scoreByDay, headline, subtitle }) {
  return (
    <section className="space-y-2">
      <div className="flex items-baseline justify-between">
        <span style={{ fontSize: '11px', letterSpacing: '0.12em', color: '#8E8B84', textTransform: 'uppercase', fontFamily: SANS }}>
          {title}
        </span>
        <span style={{ fontFamily: SANS, fontSize: '13px', color: '#4A4842' }}>
          <span style={{ fontWeight: 600 }}>{headline}</span>
          <span style={{ color: '#A8A49C' }}>/100</span>
          {subtitle && <span style={{ color: '#A8A49C', marginLeft: '8px' }}>· {subtitle}</span>}
        </span>
      </div>
      <div className="flex items-end gap-1.5 w-full" style={{ height: '88px' }}>
        {days.map(d => {
          const v = scoreByDay[d];
          const has = v != null;
          return (
            <div key={d} className="flex-1 flex items-end justify-center h-full"
                 title={has ? `${weekdayShort(d)}: ${Math.round(v)}/100` : `${d}: no data`}>
              <div style={{ width: '100%', height: has ? `${Math.max(4, v)}%` : '4%', background: has ? color : '#ECEAE5', borderRadius: '5px 5px 2px 2px', transition: 'height 0.4s ease' }} />
            </div>
          );
        })}
      </div>
      <div className="flex gap-1.5">
        {days.map(d => (
          <span key={d} className="flex-1 text-center" style={{ fontSize: '9px', color: '#B7B4AD', fontFamily: SANS }}>
            {weekdayShort(d)}
          </span>
        ))}
      </div>
    </section>
  );
}
