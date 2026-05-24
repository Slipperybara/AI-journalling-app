import React, { useState, useEffect } from 'react';

export default function App() {
  const [view, setView] = useState('write'); // 'write' or 'dashboard'
  const [content, setContent] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [statusMessage, setStatusMessage] = useState('');
  
  // Dashboard states
  const [dbData, setDbData] = useState({ entries: [], todos: [], ideas: [] });

  const fetchDashboardData = async () => {
    try {
      const response = await fetch('http://127.0.0.1:8000/api/dashboard');
      if (response.ok) {
        const data = await response.json();
        setDbData(data);
      }
    } catch (error) {
      console.error("Error fetching dashboard data:", error);
    }
  };

  useEffect(() => {
    if (view === 'dashboard') {
      fetchDashboardData();
    }
  }, [view]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!content.trim()) return;

    setIsSubmitting(true);
    setStatusMessage('');

    try {
      const response = await fetch('http://127.0.0.1:8000/api/journal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      });

      if (response.ok) {
        setStatusMessage('✓ Entry captured. Background AI analysis running...');
        setContent('');
      } else {
        setStatusMessage('Error saving entry.');
      }
    } catch (error) {
      setStatusMessage('Error connecting to backend server.');
    } finally {
      setIsSubmitting(false);
    }
  };

  // Helper function to color code quadrants
  const getQuadrantBadgeColor = (quad) => {
    switch (quad) {
      case 'Peak Performance': return 'bg-emerald-100 text-emerald-800 border-emerald-200';
      case 'High-Stress': return 'bg-rose-100 text-rose-800 border-rose-200';
      case 'Low-Energy': return 'bg-amber-100 text-amber-800 border-amber-200';
      case 'Recovery & Clarity': return 'bg-sky-100 text-sky-800 border-sky-200';
      default: return 'bg-slate-100 text-slate-800 border-slate-200';
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-800 flex flex-col items-center py-8 px-4 font-sans">
      <div className="w-full max-w-5xl space-y-8">
        
        {/* Modern Header with Right-Aligned Toggle Switch */}
        <header className="flex items-center justify-between border-b border-slate-200 pb-5">
          <div className="space-y-1">
            <h1 className="text-3xl font-bold tracking-tight text-slate-900">MindForge AI</h1>
            <p className="text-slate-500 text-sm">Turning thoughts into raw data, analytics, and execution.</p>
          </div>
          
          {/* View Switcher Toggle */}
          <div className="flex bg-slate-200 p-1 rounded-xl shadow-inner">
            <button
              onClick={() => setView('write')}
              className={`px-4 py-2 text-sm font-medium rounded-lg transition-all ${view === 'write' ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-600 hover:text-slate-900'}`}
            >
              Brain Dump
            </button>
            <button
              onClick={() => setView('dashboard')}
              className={`px-4 py-2 text-sm font-medium rounded-lg transition-all ${view === 'dashboard' ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-600 hover:text-slate-900'}`}
            >
              Dashboard
            </button>
          </div>
        </header>

        {/* VIEW 1: BRAIN DUMP INTERFACE */}
        {view === 'write' && (
          <div className="max-w-3xl mx-auto w-full space-y-4">
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden focus-within:ring-2 focus-within:ring-indigo-500 focus-within:border-transparent transition-all">
                <textarea
                  className="w-full min-h-[320px] p-6 text-base text-slate-700 bg-transparent resize-none focus:outline-none placeholder-slate-400 leading-relaxed"
                  placeholder="Stream of consciousness go here... write your tasks, your mood, wild feature ideas, or physical notes like sleep/meals."
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  disabled={isSubmitting}
                />
                <div className="bg-slate-50 px-6 py-3 flex items-center justify-between border-t border-slate-100">
                  <span className="text-xs text-slate-400">
                    {content.split(/\s+/).filter(Boolean).length} words
                  </span>
                  <button
                    type="submit"
                    disabled={isSubmitting || !content.trim()}
                    className="px-5 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-300 text-white font-medium text-sm rounded-lg transition-colors shadow-sm"
                  >
                    {isSubmitting ? 'Syncing...' : 'Process Entry'}
                  </button>
                </div>
              </div>
            </form>
            {statusMessage && (
              <div className={`p-4 rounded-lg text-sm font-medium ${statusMessage.startsWith('✓') ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>
                {statusMessage}
              </div>
            )}
          </div>
        )}

        {/* VIEW 2: ANALYTICS DASHBOARD */}
        {view === 'dashboard' && (
          <div className="space-y-8 animate-fadeIn">
            
            {/* ROW 1: Horizontal Journal Cards Row */}
            <section className="space-y-3">
              <h2 className="text-lg font-semibold text-slate-900">Recent Stream Logs</h2>
              <div className="flex space-x-4 overflow-x-auto pb-3 scrollbar-thin">
                {dbData.entries.length === 0 ? (
                  <div className="bg-white p-6 rounded-xl border border-slate-200 min-w-[300px] text-center text-slate-400 text-sm">
                    No entries processed yet.
                  </div>
                ) : (
                  dbData.entries.map((entry) => (
                    <div key={entry.id} className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm min-w-[320px] max-w-[320px] flex flex-col justify-between space-y-4">
                      <div>
                        <span className="text-xs text-slate-400 block mb-1">
                          {new Date(entry.created_at).toLocaleDateString(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute:'2-digit'})}
                        </span>
                        <p className="text-sm text-slate-600 line-clamp-3 italic">"{entry.content}"</p>
                      </div>
                      
                      {entry.primary_quadrant && (
                        <div className="flex flex-wrap gap-1.5 pt-2 border-t border-slate-100">
                          <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium border ${getQuadrantBadgeColor(entry.primary_quadrant)}`}>
                            {entry.primary_quadrant}
                          </span>
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            </section>

            {/* ROW 2: Sentiment Scores & Primary Affect Grid */}
            <section className="grid grid-cols-1 md:grid-cols-3 gap-6">
              
              {/* Sentiment Matrix / Affect Log */}
              <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm md:col-span-2 space-y-4">
                <h3 className="text-sm font-semibold tracking-wide uppercase text-slate-400">Affective Space Matrix (Past Records)</h3>
                <div className="space-y-3">
                  {dbData.entries.slice(0, 4).map((entry, idx) => (
                    <div key={idx} className="flex items-center justify-between text-sm p-2 rounded-lg bg-slate-50 border border-slate-100">
                      <span className="font-medium text-slate-500 text-xs">
                        {new Date(entry.created_at).toLocaleDateString(undefined, {weekday: 'short', day: 'numeric'})}
                      </span>
                      <div className="flex items-center space-x-4 w-2/3">
                        <div className="w-1/2 space-y-1">
                          <div className="flex justify-between text-[10px] text-slate-400"><span>Valence</span><span>{entry.valence ?? 0}</span></div>
                          <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
                            <div className="bg-indigo-500 h-full rounded-full" style={{ width: `${((entry.valence ?? 0) + 1) * 50}%` }}></div>
                          </div>
                        </div>
                        <div className="w-1/2 space-y-1">
                          <div className="flex justify-between text-[10px] text-slate-400"><span>Arousal</span><span>{entry.arousal ?? 0}</span></div>
                          <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
                            <div className="bg-pink-500 h-full rounded-full" style={{ width: `${((entry.arousal ?? 0) + 1) * 50}%` }}></div>
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                  {dbData.entries.length === 0 && <p className="text-xs text-slate-400 italic">Submit data to see matrix metrics.</p>}
                </div>
              </div>

              {/* Aggregated Cognitive State Trends */}
              <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm space-y-4">
                <h3 className="text-sm font-semibold tracking-wide uppercase text-slate-400">Cognitive Focus Labels</h3>
                <div className="flex flex-wrap gap-2">
                  {dbData.entries.flatMap(e => e.cognitive_labels || []).slice(0, 10).map((label, idx) => (
                    <span key={idx} className="px-2.5 py-1 text-xs font-medium bg-slate-100 text-slate-700 border border-slate-200 rounded-md">
                      #{label}
                    </span>
                  ))}
                  {dbData.entries.length === 0 && <p className="text-xs text-slate-400 italic">Waiting for analytical tags...</p>}
                </div>
              </div>
            </section>

            {/* ROW 3: Split Rows for Extracted Actions & Creative Ideas */}
            <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
              
              {/* Extracted To-Dos Box */}
              <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm space-y-4">
                <div className="flex justify-between items-center border-b border-slate-100 pb-2">
                  <h3 className="font-bold text-slate-900 text-base flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full bg-indigo-500"></span>
                    Extracted Action Items
                  </h3>
                  <span className="text-xs bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded font-medium">
                    {dbData.todos.filter(t => !t.is_completed).length} Pending
                  </span>
                </div>
                <div className="space-y-2 max-h-[300px] overflow-y-auto">
                  {dbData.todos.map((todo) => (
                    <div key={todo.id} className="flex items-start space-x-3 p-2.5 hover:bg-slate-50 rounded-lg transition-colors group">
                      <input 
                        type="checkbox" 
                        checked={Boolean(todo.is_completed)} 
                        readOnly
                        className="mt-1 h-4 w-4 rounded text-indigo-600 focus:ring-indigo-500 border-slate-300"
                      />
                      <div className="space-y-0.5">
                        <p className={`text-sm text-slate-700 ${todo.is_completed ? 'line-through text-slate-400' : ''}`}>
                          {todo.task_description}
                        </p>
                        {todo.due_date && <span className="text-[10px] text-rose-500 font-medium">Due: {todo.due_date}</span>}
                      </div>
                    </div>
                  ))}
                  {dbData.todos.length === 0 && <p className="text-sm text-slate-400 italic p-2">No tasks extracted yet.</p>}
                </div>
              </div>

              {/* Extracted Creative Ideas Box */}
              <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm space-y-4">
                <div className="flex justify-between items-center border-b border-slate-100 pb-2">
                  <h3 className="font-bold text-slate-900 text-base flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full bg-emerald-500"></span>
                    Captured Insights & Ideas
                  </h3>
                </div>
                <div className="space-y-3 max-h-[300px] overflow-y-auto">
                  {dbData.ideas.map((idea) => (
                    <div key={idea.id} className="p-3 bg-slate-50 border border-slate-100 rounded-lg space-y-1">
                      <h4 className="text-sm font-semibold text-slate-800">{idea.title}</h4>
                      <p className="text-xs text-slate-600 leading-relaxed">{idea.description}</p>
                      {idea.tags && (
                        <div className="flex gap-1 pt-1">
                          {idea.tags.split(',').map((tag, i) => (
                            <span key={i} className="text-[9px] bg-emerald-50 text-emerald-700 px-1.5 py-0.5 rounded font-mono">
                              {tag.trim()}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                  {dbData.ideas.length === 0 && <p className="text-sm text-slate-400 italic p-2">No concepts captured yet.</p>}
                </div>
              </div>

            </section>
          </div>
        )}
      </div>
    </div>
  );
}