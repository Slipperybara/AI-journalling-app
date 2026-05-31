import { useState, useEffect, useRef, useCallback } from 'react';

const API = 'http://127.0.0.1:8000';

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

const groupByDate = (conversations) => {
  const groups = {};
  for (const c of conversations) {
    const key = dayLabel(c.started_at);
    if (!groups[key]) groups[key] = [];
    groups[key].push(c);
  }
  return groups;
};

export default function App() {
  const [view, setView] = useState('chat');
  const [conversations, setConversations] = useState([]);
  const [activeConvId, setActiveConvId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isWaiting, setIsWaiting] = useState(false);
  const [dashboard, setDashboard] = useState({ emotional: [], health: [], productivity: [], events: [], goals: { active: [], fulfilled: [], candidate: [] } });
  const messagesEndRef = useRef(null);
  const pollRef = useRef(null);

  const fetchConversations = useCallback(async () => {
    const res = await fetch(`${API}/api/conversations`);
    if (!res.ok) return [];
    const data = await res.json();
    setConversations(data);
    return data;
  }, []);

  const loadMessages = useCallback(async (convId) => {
    const res = await fetch(`${API}/api/conversations/${convId}/messages`);
    if (!res.ok) return [];
    const data = await res.json();
    setMessages(data);
    return data;
  }, []);

  const createConversation = useCallback(async () => {
    const res = await fetch(`${API}/api/conversations`, { method: 'POST' });
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
  useEffect(() => {
    (async () => {
      const list = await fetchConversations();
      const newest = list[0];
      const todayBucket = bucketKey(new Date());
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
      fetch(`${API}/api/dashboard`).then(r => r.json()).then(setDashboard).catch(() => {});
    }
  }, [view]);

  // Cleanup polling on unmount / conv change
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

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
        const res = await fetch(`${API}/api/goals`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: rest }),
        });
        return res.ok ? { ok: true } : { ok: false, message: await errOf(res, 'failed to add goal') };
      }
      if (verb === 'fulfill') {
        const res = await fetch(`${API}/api/goals/${encodeURIComponent(rest)}/fulfill`, { method: 'PATCH' });
        return res.ok ? { ok: true } : { ok: false, message: await errOf(res, 'failed to fulfill goal') };
      }
      if (verb === 'remove') {
        const res = await fetch(`${API}/api/goals/${encodeURIComponent(rest)}`, { method: 'DELETE' });
        return res.ok ? { ok: true } : { ok: false, message: await errOf(res, 'failed to remove goal') };
      }
      if (verb === 'rename') {
        const parts = rest.match(/^"([^"]+)"\s+"([^"]+)"$/);
        if (!parts) return { ok: false, message: 'usage: /goal rename "Old Name" "New Name"' };
        const [, oldName, newName] = parts;
        const res = await fetch(`${API}/api/goals/${encodeURIComponent(oldName)}/rename`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ new_name: newName }),
        });
        return res.ok ? { ok: true } : { ok: false, message: await errOf(res, 'failed to rename goal') };
      }
      if (verb === 'list') {
        const res = await fetch(`${API}/api/goals?status=active`);
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
        fetch(`${API}/api/dashboard`).then(r => r.json()).then(setDashboard).catch(() => {});
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
      const res = await fetch(`${API}/api/conversations/${convId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text }),
      });
      if (!res.ok) throw new Error('send failed');
      const stored = await res.json();

      // Replace optimistic with persisted version
      setMessages(prev => prev.map(m => m.id === optimistic.id ? stored : m));

      // Poll for assistant reply
      const baselineId = stored.id;
      const start = Date.now();
      pollRef.current = setInterval(async () => {
        try {
          const fresh = await loadMessages(convId);
          const hasNewAssistant = fresh.some(m => m.role === 'assistant' && m.id > baselineId);
          if (hasNewAssistant) {
            clearInterval(pollRef.current);
            pollRef.current = null;
            setIsWaiting(false);
            fetchConversations();
          } else if (Date.now() - start > 45000) {
            clearInterval(pollRef.current);
            pollRef.current = null;
            setIsWaiting(false);
          }
        } catch {
          // ignore transient errors during polling
        }
      }, 1500);
    } catch {
      setMessages(prev => prev.filter(m => m.id !== optimistic.id));
      setIsWaiting(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const grouped = groupByDate(conversations);

  return (
    <div className="h-screen flex text-slate-800 font-sans bg-[radial-gradient(circle_at_top_right,_#fed7aa_0%,_#ffffff_60%)]">
      {/* Sidebar */}
      <aside className="w-72 bg-slate-50 flex flex-col">
        <div className="p-5">
          <h1 className="text-base font-semibold text-slate-900">MindForge</h1>
          <p className="text-xs text-slate-500 mt-0.5">Talk it through. We'll track the rest.</p>
        </div>

        <div className="px-3 space-y-3">
          <button
            onClick={createConversation}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-orange-500 hover:bg-orange-600 text-white text-sm font-medium rounded-lg transition-colors"
          >
            <span className="text-base leading-none">+</span> New Chat
          </button>
          <div className="flex gap-4 px-1 pt-1">
            <button
              onClick={() => setView('chat')}
              className={`text-xs font-medium pb-1.5 transition-colors ${
                view === 'chat'
                  ? 'text-orange-600 border-b border-orange-500'
                  : 'text-slate-500 hover:text-slate-700 border-b border-transparent'
              }`}
            >
              Chat
            </button>
            <button
              onClick={() => setView('dashboard')}
              className={`text-xs font-medium pb-1.5 transition-colors ${
                view === 'dashboard'
                  ? 'text-orange-600 border-b border-orange-500'
                  : 'text-slate-500 hover:text-slate-700 border-b border-transparent'
              }`}
            >
              Dashboard
            </button>
            <button
              onClick={() => setView('inspect')}
              className={`text-xs font-medium pb-1.5 transition-colors ${
                view === 'inspect'
                  ? 'text-orange-600 border-b border-orange-500'
                  : 'text-slate-500 hover:text-slate-700 border-b border-transparent'
              }`}
            >
              Inspect
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-3 pt-4 pb-3 space-y-4">
          {Object.keys(grouped).length === 0 && (
            <p className="text-xs text-slate-400 px-2">No conversations yet.</p>
          )}
          {Object.entries(grouped).map(([label, convs]) => (
            <div key={label} className="space-y-1">
              <div className="text-[10px] text-slate-400 font-medium px-2">{label}</div>
              {convs.map(c => (
                <button
                  key={c.id}
                  onClick={() => { setActiveConvId(c.id); setView('chat'); }}
                  className={`w-full text-left px-2.5 py-1.5 rounded-md text-sm transition-colors ${
                    activeConvId === c.id && view === 'chat'
                      ? 'bg-white text-slate-900'
                      : 'text-slate-600 hover:bg-white/60'
                  }`}
                >
                  <div className="truncate">{conversationPreview(c)}</div>
                  <div className="text-[10px] text-slate-400 mt-0.5">
                    {c.message_count} msg{c.message_count === 1 ? '' : 's'}
                  </div>
                </button>
              ))}
            </div>
          ))}
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {view === 'chat' && (
          <ChatView
            messages={messages}
            input={input}
            setInput={setInput}
            isWaiting={isWaiting}
            sendMessage={sendMessage}
            handleKeyDown={handleKeyDown}
            messagesEndRef={messagesEndRef}
          />
        )}
        {view === 'dashboard' && <DashboardView data={dashboard} />}
        {view === 'inspect' && <InspectView />}
      </main>
    </div>
  );
}

function ChatView({ messages, input, setInput, isWaiting, sendMessage, handleKeyDown, messagesEndRef }) {
  const textareaRef = useRef(null);

  // Auto-grow vertically with content; clamp via CSS max-height.
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${ta.scrollHeight}px`;
  }, [input]);

  return (
    <>
      <div className="flex-1 overflow-y-auto px-6 py-7">
        <div className="max-w-3xl mx-auto space-y-3">
          {messages.length === 0 && (
            <div className="text-center text-slate-400 text-sm mt-24">
              Say anything — how you slept, what you ate, an idea you're sitting on.
            </div>
          )}
          {messages.map(m => (
            <MessageBubble key={m.id} message={m} />
          ))}
          {isWaiting && (
            <div className="flex justify-start">
              <div className="bg-slate-50 rounded-2xl px-4 py-3">
                <TypingDots />
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      <div className="bg-white px-6 py-4">
        <div className="max-w-3xl mx-auto">
          <div className="flex items-end gap-2 bg-slate-50 rounded-2xl p-2 focus-within:bg-slate-100 transition-colors">
            <textarea
              ref={textareaRef}
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="What's on your mind?"
              className="flex-1 bg-transparent resize-none outline-none px-3 py-2 text-sm text-slate-700 placeholder-slate-400 max-h-48 overflow-y-auto"
              disabled={isWaiting}
            />
            <button
              onClick={sendMessage}
              disabled={!input.trim() || isWaiting}
              className="px-4 py-2 bg-orange-500 hover:bg-orange-600 disabled:bg-orange-200 text-white text-sm font-medium rounded-xl transition-colors"
            >
              Send
            </button>
          </div>
          <p className="text-[10px] text-slate-400 mt-2 text-center">
            Enter to send · Shift+Enter for newline
          </p>
        </div>
      </div>
    </>
  );
}

function MessageBubble({ message }) {
  const isUser = message.role === 'user';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[75%] px-4 py-2.5 rounded-2xl whitespace-pre-wrap text-sm leading-relaxed ${
          isUser
            ? 'bg-orange-500 text-white'
            : 'bg-white text-slate-800 border border-slate-100'
        }`}
      >
        {message.content}
      </div>
    </div>
  );
}

function TypingDots() {
  return (
    <div className="flex gap-1 items-center">
      <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
      <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
      <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
    </div>
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
      <h2 className="text-sm font-semibold text-slate-900">Past 7 Days</h2>
      <div className="grid grid-cols-3 gap-3">
        {cards.map(({ title, color, stat, sparkline }) => (
          <div key={title} className="bg-white border border-slate-200 rounded-xl p-4 space-y-2">
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />
              <span className="text-xs font-medium text-slate-700">{title}</span>
            </div>
            <div className="flex justify-center py-1">{sparkline}</div>
            <p className="text-[11px] text-slate-500 text-center">{stat}</p>
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
      const res = await fetch(`${API}/api/admin/inspect/${day}`);
      if (res.ok) result = await res.json();
    } catch {
      // swallow — `result` stays null and UI shows empty state
    }
    setData(result);
    setEvalResult(null);
  }, []);

  useEffect(() => {
    (async () => {
      const res = await fetch(`${API}/api/admin/inspect/days`);
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
      await fetch(`${API}/api/admin/parse-day/${selectedDay}`, { method: 'POST' });
      await loadDay(selectedDay);
    } finally {
      setReparsing(false);
    }
  };

  const runEval = async () => {
    if (!selectedDay || evalLoading) return;
    setEvalLoading(true);
    try {
      const res = await fetch(`${API}/api/admin/eval/${selectedDay}`, { method: 'POST' });
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
  const { emotional, health, productivity, events, todos } = extractions;
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
        <ExtractionCard title={`Todos (${todos.length})`}>
          {todos.length === 0 ? <NotExtracted /> : (
            <ul className="space-y-1">
              {todos.map((t) => (
                <li key={t.id} className="text-sm text-slate-700 flex gap-2">
                  <span className="text-slate-400">•</span>
                  <span className="flex-1">{t.task_description}</span>
                  {t.due_date && <span className="text-[10px] text-rose-500">due {t.due_date}</span>}
                </li>
              ))}
            </ul>
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

