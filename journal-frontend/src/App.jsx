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
  const [dashboard, setDashboard] = useState({ emotional: [], health: [], productivity: [], events: [], todos: [] });
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

  // Boot: open a chat in today's 6-AM bucket. Auto-create a fresh one if the newest
  // conversation is from a previous bucket (or none exists yet). The new chat still
  // inherits the day's accumulated context via the backend's day-wide TODAY_TRANSCRIPT.
  useEffect(() => {
    (async () => {
      const list = await fetchConversations();
      const newest = list[0];
      const todayBucket = bucketKey(new Date());
      if (!newest || bucketKey(newest.started_at) !== todayBucket) {
        await createConversation();
      } else {
        setActiveConvId(newest.id);
      }
    })();
  }, [fetchConversations, createConversation]);

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

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || !activeConvId || isWaiting) return;

    setInput('');
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
      const res = await fetch(`${API}/api/conversations/${activeConvId}/messages`, {
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
          const fresh = await loadMessages(activeConvId);
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
    <div className="h-screen flex text-slate-800 font-sans bg-[radial-gradient(circle_at_top_right,_#bae6fd_0%,_#ffffff_55%)]">
      {/* Sidebar */}
      <aside className="w-72 bg-slate-50 flex flex-col">
        <div className="p-5">
          <h1 className="text-base font-semibold text-slate-900">MindForge</h1>
          <p className="text-xs text-slate-500 mt-0.5">Talk it through. We'll track the rest.</p>
        </div>

        <div className="px-3 space-y-3">
          <button
            onClick={createConversation}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors"
          >
            <span className="text-base leading-none">+</span> New Chat
          </button>
          <div className="flex gap-4 px-1 pt-1">
            <button
              onClick={() => setView('chat')}
              className={`text-xs font-medium pb-1.5 transition-colors ${
                view === 'chat'
                  ? 'text-slate-900 border-b border-slate-900'
                  : 'text-slate-500 hover:text-slate-700 border-b border-transparent'
              }`}
            >
              Chat
            </button>
            <button
              onClick={() => setView('dashboard')}
              className={`text-xs font-medium pb-1.5 transition-colors ${
                view === 'dashboard'
                  ? 'text-slate-900 border-b border-slate-900'
                  : 'text-slate-500 hover:text-slate-700 border-b border-transparent'
              }`}
            >
              Dashboard
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
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-300 text-white text-sm font-medium rounded-xl transition-colors"
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
            ? 'bg-slate-800 text-white'
            : 'bg-slate-50 text-slate-800'
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
  const { emotional, health, productivity, events, todos } = data;
  return (
    <div className="flex-1 overflow-y-auto px-8 py-7">
      <div className="max-w-6xl mx-auto space-y-7">
        <header>
          <h2 className="text-xl font-semibold text-slate-900">Dashboard</h2>
          <p className="text-sm text-slate-500 mt-1">
            Last 7 days across the four tracked domains. Today's chat populates after the overnight parse.
          </p>
        </header>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-10 gap-y-8">
          <EmotionalPanel rows={emotional} />
          <HealthPanel rows={health} />
          <ProductivityPanel rows={productivity} />
          <EventsPanel rows={events} />
        </div>

        <TodosStrip rows={todos} />
      </div>
    </div>
  );
}

function PanelShell({ title, accent, count, children }) {
  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h3 className="font-medium text-slate-900 text-sm flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${accent}`}></span>
          {title}
        </h3>
        {count != null && (
          <span className="text-xs text-slate-400">{count}</span>
        )}
      </div>
      <div className="space-y-3 max-h-[360px] overflow-y-auto pr-1">
        {children}
      </div>
    </section>
  );
}

function EmptyMsg({ children }) {
  return <p className="text-xs text-slate-400 py-1">{children}</p>;
}

function EmotionalPanel({ rows }) {
  return (
    <PanelShell title="Emotional" accent="bg-indigo-500" count={rows.length}>
      {rows.length === 0 && <EmptyMsg>No emotional data yet — chat to start tracking.</EmptyMsg>}
      {rows.slice(0, 8).map((r) => (
        <div key={r.day} className="pb-3 border-b border-slate-100 last:border-0 space-y-2">
          <div className="flex items-center justify-between">
            <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium border ${getQuadrantBadgeColor(r.primary_quadrant)}`}>
              {r.primary_quadrant || '—'}
            </span>
            <span className="text-[10px] text-slate-400">{dayLabel(r.day)}</span>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Bar label="Valence" value={r.valence} color="bg-indigo-500" />
            <Bar label="Arousal" value={r.arousal} color="bg-pink-500" />
          </div>
          {r.cognitive_labels?.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {r.cognitive_labels.map((l, i) => (
                <span key={i} className="text-[10px] bg-white border border-slate-200 text-slate-700 px-1.5 py-0.5 rounded">#{l}</span>
              ))}
            </div>
          )}
          {r.cognitive_triggers?.length > 0 && (
            <div className="text-[11px] text-slate-600">
              <span className="text-slate-400">Triggers:</span> {r.cognitive_triggers.join(', ')}
            </div>
          )}
          {r.social_interactions?.length > 0 && (
            <div className="text-[11px] text-slate-600">
              <span className="text-slate-400">Social:</span> {r.social_interactions.join(', ')}
            </div>
          )}
        </div>
      ))}
    </PanelShell>
  );
}

function Bar({ label, value, color }) {
  const v = value ?? 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-[10px] text-slate-400">
        <span>{label}</span>
        <span>{v.toFixed(2)}</span>
      </div>
      <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
        <div className={`${color} h-full rounded-full`} style={{ width: `${((v + 1) * 50)}%` }}></div>
      </div>
    </div>
  );
}

function HealthPanel({ rows }) {
  return (
    <PanelShell title="Health" accent="bg-rose-500" count={rows.length}>
      {rows.length === 0 && <EmptyMsg>No health data yet.</EmptyMsg>}
      {rows.slice(0, 8).map((r) => (
        <div key={r.day} className="pb-3 border-b border-slate-100 last:border-0 space-y-2">
          <div className="text-[10px] text-slate-400">{dayLabel(r.day)}</div>
          <div className="flex flex-wrap gap-1.5">
            {r.sleep_quality && <KV k="Sleep" v={r.sleep_quality} />}
            {r.exercise_type && <KV k="Exercise" v={r.exercise_type} />}
            {r.diet_quality && <KV k="Diet" v={r.diet_quality} />}
            {r.physical_performance && <KV k="Perf" v={r.physical_performance} />}
          </div>
          {r.somatic_sensations?.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {r.somatic_sensations.map((s, i) => (
                <span key={i} className="text-[10px] bg-rose-50 text-rose-700 border border-rose-200 px-1.5 py-0.5 rounded">{s}</span>
              ))}
            </div>
          )}
          {r.supplements?.length > 0 && (
            <div className="text-[11px] text-slate-600">
              <span className="text-slate-400">Supplements:</span> {r.supplements.join(', ')}
            </div>
          )}
        </div>
      ))}
    </PanelShell>
  );
}

function KV({ k, v }) {
  return (
    <span className="text-[10px] bg-white border border-slate-200 px-2 py-0.5 rounded">
      <span className="text-slate-400">{k}:</span> <span className="text-slate-700 font-medium">{v}</span>
    </span>
  );
}

function ProductivityPanel({ rows }) {
  const totals = rows.reduce((acc, r) => {
    acc.deep += r.deep_work_hours || 0;
    acc.shallow += r.shallow_work_hours || 0;
    return acc;
  }, { deep: 0, shallow: 0 });

  return (
    <PanelShell title="Productivity" accent="bg-emerald-500" count={rows.length}>
      {rows.length === 0 && <EmptyMsg>No productivity data yet.</EmptyMsg>}
      {rows.length > 0 && (
        <div className="grid grid-cols-2 gap-x-6 pb-2">
          <div className="border-l-2 border-emerald-500 pl-3">
            <div className="text-[10px] text-emerald-700">Deep (7d)</div>
            <div className="text-xl font-semibold text-emerald-900">{totals.deep.toFixed(1)}h</div>
          </div>
          <div className="border-l-2 border-slate-300 pl-3">
            <div className="text-[10px] text-slate-500">Shallow (7d)</div>
            <div className="text-xl font-semibold text-slate-800">{totals.shallow.toFixed(1)}h</div>
          </div>
        </div>
      )}
      {rows.slice(0, 6).map((r) => (
        <div key={r.day} className="pb-3 border-b border-slate-100 last:border-0 space-y-2">
          <div className="text-[10px] text-slate-400">{dayLabel(r.day)}</div>
          <div className="flex flex-wrap gap-1.5">
            {r.deep_work_hours != null && <KV k="Deep" v={`${r.deep_work_hours}h`} />}
            {r.shallow_work_hours != null && <KV k="Shallow" v={`${r.shallow_work_hours}h`} />}
            {r.time_block_adherence && <KV k="Adherence" v={r.time_block_adherence} />}
            {r.cognitive_load && <KV k="Load" v={r.cognitive_load} />}
          </div>
          {r.friction_points?.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {r.friction_points.map((f, i) => (
                <span key={i} className="text-[10px] bg-amber-50 text-amber-700 border border-amber-200 px-1.5 py-0.5 rounded">{f}</span>
              ))}
            </div>
          )}
        </div>
      ))}
    </PanelShell>
  );
}

function EventsPanel({ rows }) {
  return (
    <PanelShell title="Events" accent="bg-amber-500" count={rows.length}>
      {rows.length === 0 && <EmptyMsg>No events captured yet.</EmptyMsg>}
      {rows.map((e) => (
        <div key={e.id} className="pb-3 border-b border-slate-100 last:border-0 space-y-1">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-sm font-semibold text-slate-800">{e.title}</h4>
            <span className={`text-[10px] px-2 py-0.5 rounded font-medium border ${eventTypeColor(e.event_type)}`}>
              {e.event_type}
            </span>
          </div>
          <p className="text-xs text-slate-600 leading-relaxed">{e.description}</p>
          {e.tags && (
            <div className="flex flex-wrap gap-1 pt-1">
              {e.tags.split(',').filter(t => t.trim()).map((tag, i) => (
                <span key={i} className="text-[9px] bg-white border border-slate-200 text-slate-600 px-1.5 py-0.5 rounded font-mono">
                  {tag.trim()}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </PanelShell>
  );
}

function TodosStrip({ rows }) {
  const pending = rows.filter(t => !t.is_completed);
  return (
    <section className="space-y-3 pt-2">
      <div className="flex items-baseline justify-between">
        <h3 className="font-medium text-slate-900 text-sm flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-sky-500"></span>
          Action Items
        </h3>
        <span className="text-xs text-slate-400">{pending.length} pending</span>
      </div>
      <div className="space-y-1 max-h-[200px] overflow-y-auto pr-1">
        {rows.length === 0 && <EmptyMsg>No todos extracted yet.</EmptyMsg>}
        {rows.map((t) => (
          <div key={t.id} className="flex items-start gap-3 py-1.5 px-1 hover:bg-slate-50 rounded-md transition-colors">
            <input
              type="checkbox"
              checked={Boolean(t.is_completed)}
              readOnly
              className="mt-1 h-4 w-4 rounded text-indigo-600 border-slate-300"
            />
            <div className="flex-1">
              <p className={`text-sm text-slate-700 ${t.is_completed ? 'line-through text-slate-400' : ''}`}>
                {t.task_description}
              </p>
              {t.due_date && <span className="text-[10px] text-rose-500">Due: {t.due_date}</span>}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
