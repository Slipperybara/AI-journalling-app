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

  // Boot: load conversations, select most recent (or create new)
  useEffect(() => {
    (async () => {
      const list = await fetchConversations();
      if (list.length > 0) {
        setActiveConvId(list[0].id);
      } else {
        await createConversation();
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
    <div className="h-screen flex bg-slate-50 text-slate-800 font-sans">
      {/* Sidebar */}
      <aside className="w-72 border-r border-slate-200 bg-white flex flex-col">
        <div className="p-4 border-b border-slate-200">
          <h1 className="text-lg font-bold tracking-tight text-slate-900">MindForge AI</h1>
          <p className="text-xs text-slate-500 mt-0.5">Talk it through. We'll track the rest.</p>
        </div>

        <div className="p-3 border-b border-slate-200 space-y-2">
          <button
            onClick={createConversation}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors shadow-sm"
          >
            <span className="text-base leading-none">+</span> New Chat
          </button>
          <div className="flex bg-slate-100 p-1 rounded-lg">
            <button
              onClick={() => setView('chat')}
              className={`flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-all ${view === 'chat' ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-600'}`}
            >
              Chat
            </button>
            <button
              onClick={() => setView('dashboard')}
              className={`flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-all ${view === 'dashboard' ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-600'}`}
            >
              Dashboard
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-4">
          {Object.keys(grouped).length === 0 && (
            <p className="text-xs text-slate-400 italic px-2">No conversations yet.</p>
          )}
          {Object.entries(grouped).map(([label, convs]) => (
            <div key={label} className="space-y-1">
              <div className="text-[10px] uppercase tracking-wider text-slate-400 font-semibold px-2">{label}</div>
              {convs.map(c => (
                <button
                  key={c.id}
                  onClick={() => { setActiveConvId(c.id); setView('chat'); }}
                  className={`w-full text-left px-2.5 py-2 rounded-lg text-sm transition-colors ${
                    activeConvId === c.id && view === 'chat'
                      ? 'bg-indigo-50 text-indigo-900 border border-indigo-100'
                      : 'text-slate-700 hover:bg-slate-100 border border-transparent'
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
  return (
    <>
      <div className="flex-1 overflow-y-auto px-6 py-8">
        <div className="max-w-3xl mx-auto space-y-4">
          {messages.length === 0 && (
            <div className="text-center text-slate-400 text-sm italic mt-20">
              Start by saying anything — how you're feeling, what you got done, an idea you're sitting on.
            </div>
          )}
          {messages.map(m => (
            <MessageBubble key={m.id} message={m} />
          ))}
          {isWaiting && (
            <div className="flex justify-start">
              <div className="bg-white border border-slate-200 rounded-2xl rounded-bl-sm px-4 py-3 shadow-sm">
                <TypingDots />
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      <div className="border-t border-slate-200 bg-white px-6 py-4">
        <div className="max-w-3xl mx-auto">
          <div className="flex items-end gap-3 bg-slate-50 rounded-2xl border border-slate-200 p-2 focus-within:ring-2 focus-within:ring-indigo-500 focus-within:border-transparent transition-all">
            <textarea
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="What's on your mind?"
              className="flex-1 bg-transparent resize-none outline-none px-3 py-2 text-sm text-slate-700 placeholder-slate-400 max-h-40"
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
        className={`max-w-[75%] px-4 py-2.5 rounded-2xl whitespace-pre-wrap text-sm leading-relaxed shadow-sm ${
          isUser
            ? 'bg-indigo-600 text-white rounded-br-sm'
            : 'bg-white border border-slate-200 text-slate-800 rounded-bl-sm'
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
    <div className="flex-1 overflow-y-auto px-8 py-8">
      <div className="max-w-6xl mx-auto space-y-6">
        <header className="pb-4 border-b border-slate-200">
          <h2 className="text-2xl font-bold tracking-tight text-slate-900">Dashboard</h2>
          <p className="text-sm text-slate-500 mt-1">
            Last 7 days across the four tracked domains. Today's chat populates after the overnight parse.
          </p>
        </header>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
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
    <section className="bg-white rounded-xl border border-slate-200 shadow-sm p-6 space-y-4">
      <div className="flex items-center justify-between border-b border-slate-100 pb-3">
        <h3 className="font-semibold text-slate-900 text-base flex items-center gap-2">
          <span className={`w-2.5 h-2.5 rounded-full ${accent}`}></span>
          {title}
        </h3>
        {count != null && (
          <span className="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded-full font-medium">
            {count}
          </span>
        )}
      </div>
      <div className="space-y-3 max-h-[360px] overflow-y-auto">
        {children}
      </div>
    </section>
  );
}

function EmptyMsg({ children }) {
  return <p className="text-xs text-slate-400 italic p-2">{children}</p>;
}

function EmotionalPanel({ rows }) {
  return (
    <PanelShell title="Emotional" accent="bg-indigo-500" count={rows.length}>
      {rows.length === 0 && <EmptyMsg>No emotional data yet — chat to start tracking.</EmptyMsg>}
      {rows.slice(0, 8).map((r) => (
        <div key={r.day} className="p-3 bg-slate-50 border border-slate-100 rounded-lg space-y-2">
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
        <div key={r.day} className="p-3 bg-slate-50 border border-slate-100 rounded-lg space-y-2">
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
        <div className="grid grid-cols-2 gap-2">
          <div className="bg-emerald-50 border border-emerald-100 rounded-lg p-3">
            <div className="text-[10px] uppercase tracking-wider text-emerald-700">Deep (7d)</div>
            <div className="text-xl font-semibold text-emerald-900">{totals.deep.toFixed(1)}h</div>
          </div>
          <div className="bg-slate-50 border border-slate-100 rounded-lg p-3">
            <div className="text-[10px] uppercase tracking-wider text-slate-600">Shallow (7d)</div>
            <div className="text-xl font-semibold text-slate-800">{totals.shallow.toFixed(1)}h</div>
          </div>
        </div>
      )}
      {rows.slice(0, 6).map((r) => (
        <div key={r.day} className="p-3 bg-slate-50 border border-slate-100 rounded-lg space-y-2">
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
        <div key={e.id} className="p-3 bg-slate-50 border border-slate-100 rounded-lg space-y-1">
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
    <section className="bg-white rounded-xl border border-slate-200 shadow-sm p-6 space-y-3">
      <div className="flex items-center justify-between border-b border-slate-100 pb-2">
        <h3 className="font-semibold text-slate-900 text-base flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-sky-500"></span>
          Action Items
        </h3>
        <span className="text-xs bg-sky-50 text-sky-700 px-2 py-0.5 rounded font-medium">
          {pending.length} pending
        </span>
      </div>
      <div className="space-y-1.5 max-h-[200px] overflow-y-auto">
        {rows.length === 0 && <EmptyMsg>No todos extracted yet.</EmptyMsg>}
        {rows.map((t) => (
          <div key={t.id} className="flex items-start gap-3 p-2 hover:bg-slate-50 rounded-lg transition-colors">
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
              {t.due_date && <span className="text-[10px] text-rose-500 font-medium">Due: {t.due_date}</span>}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
