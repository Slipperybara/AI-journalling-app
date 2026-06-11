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

const getQuadrantBadgeColor = (q) => {
  switch (q) {
    case 'Peak Performance': return 'bg-emerald-100 text-emerald-800 border-emerald-200';
    case 'High-Stress': return 'bg-rose-100 text-rose-800 border-rose-200';
    case 'Low-Energy': return 'bg-amber-100 text-amber-800 border-amber-200';
    case 'Recovery & Clarity': return 'bg-sky-100 text-sky-800 border-sky-200';
    default: return 'bg-slate-100 text-slate-800 border-slate-200';
  }
};

const eventTypeColor = (t) => {
  switch (t) {
    case 'idea': return 'bg-emerald-50 text-emerald-700 border-emerald-200';
    case 'milestone': return 'bg-amber-50 text-amber-700 border-amber-200';
    case 'location': return 'bg-sky-50 text-sky-700 border-sky-200';
    case 'media': return 'bg-purple-50 text-purple-700 border-purple-200';
    default: return 'bg-slate-50 text-slate-700 border-slate-200';
  }
};

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
  const [input, setInput] = useState('');
  const [isWaiting, setIsWaiting] = useState(false);
  const [morningBrief, setMorningBrief] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [bgMode, setBgMode] = useState('warm'); // 'warm' (conversing) | 'cool' (retrieving)
  const [dashboard, setDashboard] = useState({ emotional: [], health: [], productivity: [], events: [], goals: { active: [], fulfilled: [], candidate: [] } });
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

  // Whenever active conv changes, load its messages
  useEffect(() => {
    if (activeConvId == null) return;
    (async () => { await loadMessages(activeConvId); })();
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

  const navItems = [['chat', 'Chat'], ['dashboard', 'Dashboard'], ['inspect', 'Inspect']];

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

          <button
            onClick={() => { createConversation(); setSidebarOpen(false); }}
            className="text-left"
            style={{ padding: '8px 0', marginTop: '10px', fontSize: '13px', color: '#C0BDB6', fontFamily: SANS, background: 'transparent', cursor: 'pointer', transition: 'color 0.2s' }}
            onMouseEnter={(e) => (e.currentTarget.style.color = '#7A7870')}
            onMouseLeave={(e) => (e.currentTarget.style.color = '#C0BDB6')}
          >
            + New entry
          </button>
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
          {view === 'inspect' && <InspectView />}
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

function ChatView({ messages, activeConv, morningBrief, input, setInput, isWaiting, handleKeyDown, messagesEndRef }) {
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
          {messages.length === 0 && !thinking && (
            <p style={{ fontFamily: SERIF, fontSize: '19px', lineHeight: 1.65, color: '#6E6B64', whiteSpace: 'pre-wrap' }}>
              {morningBrief || "What's on your mind?"}
            </p>
          )}

          {messages.map(m => (
            <JournalMessage key={m.id} message={m} />
          ))}

          {thinking && (
            <div className="mb-3" style={{ height: '20px' }}>
              <motion.span
                style={{ display: 'inline-block', width: '6px', height: '6px', borderRadius: '9999px', background: '#8E8B83' }}
                animate={{ opacity: [0.25, 1, 0.25] }}
                transition={{ duration: 1.4, repeat: Infinity, ease: 'easeInOut' }}
              />
            </div>
          )}

          {/* Inline serif input under the last AI message */}
          {showInput && (
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
          <p style={{ fontFamily: SERIF, fontSize: '19px', lineHeight: 1.65, color: '#6E6B64', whiteSpace: 'pre-wrap' }}>
            {message.content}
          </p>
        </div>
      )}
    </motion.div>
  );
}

function DashboardView({ data }) {
  const { emotional, health, productivity, events, goals: initialGoals } = data;
  return (
    <div className="flex-1 overflow-y-auto px-8 py-7 space-y-8">
      <div className="max-w-5xl mx-auto space-y-8">
        <GoalsStrip initialGoals={initialGoals} />
        <WeeklySummary emotional={emotional} health={health} productivity={productivity} events={events} />
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



const SLEEP_MAP = { 'Poor': 0, 'Fair': 0.33, 'Good': 0.67, 'Excellent': 1 };
const DIET_MAP = { 'Junk/Heavy': 0, 'Carbs Centered': 0.25, 'Meat and Vegetable centered': 0.6, 'Clean': 1 };

function SparkPolyline({ days, byDay, W = 100, H = 32, color = '#f97316' }) {
  const pts = days.map((d, i) => {
    const v = byDay[d];
    if (v == null) return null;
    const x = (i / Math.max(days.length - 1, 1)) * W;
    const y = H - ((v + 1) / 2) * H;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).filter(Boolean);
  return (
    <svg width={W} height={H} className="overflow-visible">
      <line x1="0" y1={H / 2} x2={W} y2={H / 2} stroke="#f1f5f9" strokeWidth="1" />
      {pts.length >= 2 && (
        <polyline points={pts.join(' ')} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
      )}
      {pts.length < 2 && pts.map((p, i) => {
        const [cx, cy] = p.split(',');
        return <circle key={i} cx={cx} cy={cy} r="2" fill={color} />;
      })}
    </svg>
  );
}

function SparkBars({ days, byDay, W = 100, H = 32, color = '#f97316' }) {
  const vals = days.map(d => byDay[d] ?? null);
  const maxVal = Math.max(...vals.filter(v => v != null), 1);
  const bw = Math.max(1, W / days.length - 2);
  return (
    <svg width={W} height={H}>
      {vals.map((v, i) => {
        const barH = v != null ? Math.max(2, (v / maxVal) * H) : 0;
        return (
          <rect key={i} x={i * (W / days.length)} y={H - barH}
            width={bw} height={barH} fill={v != null ? color : '#e2e8f0'} rx="1" />
        );
      })}
    </svg>
  );
}

function SparkDots({ days, byDay, W = 100, H = 32, color = '#f97316' }) {
  return (
    <svg width={W} height={H}>
      {days.map((d, i) => {
        const v = byDay[d];
        const cx = (i + 0.5) * (W / days.length);
        const cy = H / 2;
        const r = v != null ? 3 + v * 4 : 2.5;
        return <circle key={d} cx={cx} cy={cy} r={r} fill={v != null ? color : '#e2e8f0'} />;
      })}
    </svg>
  );
}

function WeeklySummary({ emotional, health, productivity, events }) {
  const last7 = Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (6 - i));
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  });

  const emotByDay = Object.fromEntries(emotional.map(r => [r.day, r.valence]));
  const sleepByDay = Object.fromEntries(health.filter(r => r.sleep_quality).map(r => [r.day, SLEEP_MAP[r.sleep_quality] ?? null]));
  const exerciseByDay = Object.fromEntries(health.filter(r => r.exercise_type).map(r => [r.day, r.exercise_type !== 'None' ? 1 : 0]));
  const dietByDay = Object.fromEntries(health.filter(r => r.diet_quality).map(r => [r.day, DIET_MAP[r.diet_quality] ?? null]));
  const deepByDay = Object.fromEntries(productivity.filter(r => r.deep_work_hours != null).map(r => [r.day, r.deep_work_hours]));
  const eventCountByDay = {};
  events.forEach(e => { eventCountByDay[e.day] = (eventCountByDay[e.day] || 0) + 1; });

  const avgValence = emotional.length ? (emotional.reduce((s, r) => s + r.valence, 0) / emotional.length).toFixed(2) : '—';
  const sleepDays = health.filter(r => r.sleep_quality).length;
  const exerciseDays = health.filter(r => r.exercise_type && r.exercise_type !== 'None').length;
  const dietDays = health.filter(r => r.diet_quality).length;
  const totalDeep = productivity.reduce((s, r) => s + (r.deep_work_hours || 0), 0).toFixed(1);
  const totalEvents = events.length;

  const cards = [
    { title: 'Emotional', color: '#f97316', stat: `avg ${avgValence > 0 ? '+' : ''}${avgValence}`, sparkline: <SparkPolyline days={last7} byDay={emotByDay} color="#f97316" /> },
    { title: 'Sleep', color: '#f43f5e', stat: `${sleepDays}/7 days`, sparkline: <SparkDots days={last7} byDay={sleepByDay} color="#f43f5e" /> },
    { title: 'Exercise', color: '#10b981', stat: `${exerciseDays}/7 days`, sparkline: <SparkBars days={last7} byDay={exerciseByDay} color="#10b981" /> },
    { title: 'Diet', color: '#f59e0b', stat: `${dietDays}/7 days`, sparkline: <SparkDots days={last7} byDay={dietByDay} color="#f59e0b" /> },
    { title: 'Deep Work', color: '#3b82f6', stat: `${totalDeep}h total`, sparkline: <SparkBars days={last7} byDay={deepByDay} color="#3b82f6" /> },
    { title: 'Events', color: '#8b5cf6', stat: `${totalEvents} total`, sparkline: <SparkBars days={last7} byDay={eventCountByDay} color="#8b5cf6" /> },
  ];

  return (
    <section className="space-y-3">
      <h2 style={{ fontSize: '10px', letterSpacing: '0.14em', color: '#8E8B84', textTransform: 'uppercase', fontFamily: SANS }}>Past 7 Days</h2>
      <div className="grid grid-cols-3 gap-3">
        {cards.map(({ title, color, stat, sparkline }) => (
          <div key={title} className="bg-white/45 border border-white/50 rounded-xl p-4 space-y-2 backdrop-blur-sm">
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />
              <span className="text-xs font-medium" style={{ color: '#4A4842' }}>{title}</span>
            </div>
            <div className="flex justify-center py-1">{sparkline}</div>
            <p className="text-[11px] text-center" style={{ color: '#7A776F' }}>{stat}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function EmptyMsg({ children }) {
  return <p className="text-xs text-slate-400 py-1">{children}</p>;
}

function InspectView() {
  const [days, setDays] = useState([]);
  const [selectedDay, setSelectedDay] = useState('');
  const [data, setData] = useState(null);
  const [reparsing, setReparsing] = useState(false);
  const [evalResult, setEvalResult] = useState(null);
  const [evalLoading, setEvalLoading] = useState(false);

  const loading = !!selectedDay && (!data || data.day !== selectedDay);

  const loadDay = useCallback(async (day) => {
    if (!day) return;
    let result = null;
    try {
      const res = await apiFetch(`${API}/api/admin/inspect/${day}`);
      if (res.ok) result = await res.json();
    } catch {
      // swallow — `result` stays null and UI shows empty state
    }
    setData(result);
    setEvalResult(null);
  }, []);

  useEffect(() => {
    (async () => {
      const res = await apiFetch(`${API}/api/admin/inspect/days`);
      if (!res.ok) return;
      const list = await res.json();
      setDays(list);
      if (list.length > 0) {
        setSelectedDay((prev) => prev || list[0].day);
      }
    })();
  }, []);

  useEffect(() => {
    if (!selectedDay) return;
    (async () => { await loadDay(selectedDay); })();
  }, [selectedDay, loadDay]);

  const reparseDay = async () => {
    if (!selectedDay || reparsing) return;
    setReparsing(true);
    try {
      await apiFetch(`${API}/api/admin/parse-day/${selectedDay}`, { method: 'POST' });
      await loadDay(selectedDay);
    } finally {
      setReparsing(false);
    }
  };

  const runEval = async () => {
    if (!selectedDay || evalLoading) return;
    setEvalLoading(true);
    try {
      const res = await apiFetch(`${API}/api/admin/eval/${selectedDay}`, { method: 'POST' });
      setEvalResult(await res.json());
    } catch {
      setEvalResult({ error: 'eval call failed' });
    } finally {
      setEvalLoading(false);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto px-8 py-7">
      <div className="max-w-7xl mx-auto space-y-5">
        <header className="flex items-end justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold text-slate-900">Inspect</h2>
            <p className="text-sm text-slate-500 mt-1">
              Raw chat next to parsed extractions, for verifying parse quality day by day.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={selectedDay}
              onChange={(e) => setSelectedDay(e.target.value)}
              className="text-sm bg-white border border-slate-200 rounded-md px-2 py-1.5"
            >
              {days.length === 0 && <option value="">No days with messages</option>}
              {days.map((d) => (
                <option key={d.day} value={d.day}>
                  {d.day} ({d.message_count} msg{d.message_count === 1 ? '' : 's'})
                </option>
              ))}
            </select>
            <button
              onClick={reparseDay}
              disabled={!selectedDay || reparsing}
              className="text-xs px-3 py-1.5 bg-orange-500 hover:bg-orange-600 disabled:bg-orange-200 text-white rounded-md transition-colors"
            >
              {reparsing ? 'Reparsing…' : 'Re-parse this day'}
            </button>
          </div>
        </header>

        {loading && <p className="text-sm text-slate-400">Loading…</p>}

        {!loading && data && (
          <>
            <ParseLogBadge log={data.parse_log} window={data.bucket_window} />
            <div className="grid grid-cols-2 gap-6">
              <TranscriptColumn messages={data.messages} />
              <ExtractionsColumn
                extractions={data.extractions}
                onEval={runEval}
                evalLoading={evalLoading}
                evalResult={evalResult}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ParseLogBadge({ log, window }) {
  const status = log?.status ?? 'none';
  const tone = {
    succeeded: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    empty: 'bg-slate-50 text-slate-600 border-slate-200',
    failed: 'bg-rose-50 text-rose-700 border-rose-200',
    none: 'bg-amber-50 text-amber-700 border-amber-200',
  }[status];
  return (
    <div className="flex items-center justify-between text-xs">
      <div className="flex items-center gap-3">
        <span className={`px-2 py-0.5 rounded border font-medium ${tone}`}>
          parse_log: {status}
        </span>
        {log?.parsed_at && (
          <span className="text-slate-400">parsed at {new Date(log.parsed_at).toLocaleString()}</span>
        )}
        {log?.error && (
          <span className="text-rose-600 truncate max-w-xl">error: {log.error}</span>
        )}
      </div>
      {window && (
        <span className="text-slate-400">
          window: {new Date(window.start).toLocaleString()} → {new Date(window.end).toLocaleString()}
        </span>
      )}
    </div>
  );
}

function TranscriptColumn({ messages }) {
  return (
    <section className="space-y-2">
      <h3 className="font-medium text-slate-900 text-sm">Raw transcript ({messages.length})</h3>
      <div className="space-y-2 max-h-[calc(100vh-220px)] overflow-y-auto pr-2">
        {messages.length === 0 && <EmptyMsg>No messages in this day-bucket.</EmptyMsg>}
        {messages.map((m, i) => {
          const prev = messages[i - 1];
          const convBreak = prev && prev.conversation_id !== m.conversation_id;
          return (
            <div key={m.id}>
              {convBreak && (
                <div className="flex items-center gap-2 py-2">
                  <div className="flex-1 h-px bg-slate-200" />
                  <span className="text-[10px] text-slate-400">new conversation</span>
                  <div className="flex-1 h-px bg-slate-200" />
                </div>
              )}
              <TranscriptMessage m={m} />
            </div>
          );
        })}
      </div>
    </section>
  );
}

function TranscriptMessage({ m }) {
  const isUser = m.role === 'user';
  const roleClass = isUser
    ? 'bg-slate-100 text-slate-700 border-slate-200'
    : 'bg-orange-50 text-orange-700 border-orange-200';
  return (
    <div className="border border-slate-200 bg-white rounded-md p-3 space-y-1.5">
      <div className="flex items-center justify-between">
        <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${roleClass}`}>
          {m.role}
        </span>
        <span className="text-[10px] text-slate-400 font-mono">
          {new Date(m.created_at).toLocaleString()} · #{m.id}
        </span>
      </div>
      <p className="text-sm text-slate-800 whitespace-pre-wrap leading-relaxed">{m.content}</p>
    </div>
  );
}

function ExtractionsColumn({ extractions, onEval, evalLoading, evalResult }) {
  const { emotional, health, productivity, events = [] } = extractions || {};
  return (
    <section className="space-y-3">
      <h3 className="font-medium text-slate-900 text-sm">Extractions</h3>
      <div className="space-y-3 max-h-[calc(100vh-220px)] overflow-y-auto pr-2">
        <ExtractionCard title="Emotional">
          {emotional ? <EmotionalDetail r={emotional} /> : <NotExtracted />}
        </ExtractionCard>
        <ExtractionCard title="Health">
          {health ? <HealthDetail r={health} /> : <NotExtracted />}
        </ExtractionCard>
        <ExtractionCard title="Productivity">
          {productivity ? <ProductivityDetail r={productivity} /> : <NotExtracted />}
        </ExtractionCard>
        <ExtractionCard title={`Events (${events.length})`}>
          {events.length === 0 ? <NotExtracted /> : (
            <div className="space-y-2">
              {events.map((e) => (
                <div key={e.id} className="border-t border-slate-100 first:border-0 pt-2 first:pt-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-slate-800">{e.title}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${eventTypeColor(e.event_type)}`}>
                      {e.event_type}
                    </span>
                  </div>
                  <p className="text-xs text-slate-600 mt-1">{e.description}</p>
                  {e.tags && <p className="text-[10px] text-slate-400 font-mono mt-1">tags: {e.tags}</p>}
                </div>
              ))}
            </div>
          )}
        </ExtractionCard>
        <ExtractionCard title="Automated evaluation (preview)">
          <div className="space-y-2">
            <p className="text-xs text-slate-500">
              Run a higher-tier model over the transcript + extractions and grade each field. Scaffold only — wires up next round.
            </p>
            <button
              onClick={onEval}
              disabled={evalLoading}
              className="text-xs px-3 py-1.5 bg-slate-800 hover:bg-slate-900 disabled:bg-slate-400 text-white rounded-md transition-colors"
            >
              {evalLoading ? 'Calling…' : 'Run automated evaluation'}
            </button>
            {evalResult && (
              <pre className="text-[11px] bg-slate-50 border border-slate-200 rounded p-2 overflow-x-auto">
                {JSON.stringify(evalResult, null, 2)}
              </pre>
            )}
          </div>
        </ExtractionCard>
      </div>
    </section>
  );
}

function ExtractionCard({ title, children }) {
  return (
    <div className="border border-slate-200 bg-white rounded-md p-3 space-y-2">
      <h4 className="text-xs font-semibold text-slate-700 uppercase tracking-wide">{title}</h4>
      {children}
    </div>
  );
}

function NotExtracted() {
  return <p className="text-xs text-slate-400 italic">Not extracted for this day.</p>;
}

function EmotionalDetail({ r }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium border ${getQuadrantBadgeColor(r.primary_quadrant)}`}>
          {r.primary_quadrant || '—'}
        </span>
        <span className="text-[11px] text-slate-500">
          valence {r.valence?.toFixed(2)} · arousal {r.arousal?.toFixed(2)}
        </span>
      </div>
      <FieldList
        items={[
          ['cognitive_labels', r.cognitive_labels],
          ['cognitive_triggers', r.cognitive_triggers],
          ['social_interactions', r.social_interactions],
        ]}
      />
    </div>
  );
}

function HealthDetail({ r }) {
  return (
    <FieldList
      items={[
        ['sleep_quality', r.sleep_quality],
        ['exercise_type', r.exercise_type],
        ['diet_quality', r.diet_quality],
        ['physical_performance', r.physical_performance],
        ['somatic_sensations', r.somatic_sensations],
        ['supplements', r.supplements],
      ]}
    />
  );
}

function ProductivityDetail({ r }) {
  return (
    <FieldList
      items={[
        ['deep_work_hours', r.deep_work_hours],
        ['shallow_work_hours', r.shallow_work_hours],
        ['time_block_adherence', r.time_block_adherence],
        ['cognitive_load', r.cognitive_load],
        ['friction_points', r.friction_points],
      ]}
    />
  );
}

function FieldList({ items }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-slate-400 font-mono">{k}</dt>
          <dd className="text-slate-700">{formatValue(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function formatValue(v) {
  if (v == null || v === '') return <span className="text-slate-300 italic">null</span>;
  if (Array.isArray(v)) {
    if (v.length === 0) return <span className="text-slate-300 italic">[]</span>;
    return (
      <div className="flex flex-wrap gap-1">
        {v.map((item, i) => (
          <span key={i} className="bg-slate-100 text-slate-700 px-1.5 py-0.5 rounded text-[11px]">{item}</span>
        ))}
      </div>
    );
  }
  if (typeof v === 'number') return v.toString();
  return v;
}

