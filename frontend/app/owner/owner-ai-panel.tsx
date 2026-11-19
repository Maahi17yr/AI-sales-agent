'use client';

import { FormEvent, useRef, useState } from 'react';
import { Bot, Send, ShieldCheck, Square, Sparkles, UserRound } from 'lucide-react';

type Turn = { role: 'user' | 'assistant'; content: string };
type Props = { apiFetch: (path: string, options?: RequestInit) => Promise<Response> };

const starters = [
  "Summarise my verified sales today.",
  "Which meals are running low on stock?",
  "What orders still need to be prepared?",
];

export default function OwnerAIPanel({ apiFetch }: Props) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const controllerRef = useRef<AbortController | null>(null);

  async function ask(question: string) {
    const text = question.trim();
    if (!text || busy || text.length > 600) return;
    const prior = turns.slice(-8);
    setBusy(true);
    setError('');
    setDraft('');
    setTurns(current => [...current, { role: 'user', content: text }]);
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const response = await apiFetch('/owner/assistant', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, history: prior }),
        signal: controller.signal,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not reach Owner AI.');
      if (typeof data.reply !== 'string' || !data.reply.trim()) throw new Error('Owner AI returned an empty response.');
      if (!controller.signal.aborted) setTurns(current => [...current, { role: 'assistant', content: data.reply }]);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Owner AI is temporarily unavailable.');
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      setBusy(false);
    }
  }

  function cancel() {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setBusy(false);
    setError('Request stopped. No business records were changed.');
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void ask(draft);
  }

  return <section className="owner-ai-page" aria-label="Owner AI assistant">
    <div className="owner-heading">
      <div><span className="owner-eyebrow">YOUR BUSINESS COPILOT</span><h1>Owner AI</h1>
        <p>Ask about real sales, orders, menu performance and stock.</p></div>
      <span className="owner-live"><ShieldCheck size={16}/> Read-only</span>
    </div>
    <div className="owner-ai-panel owner-panel">
      <div className="owner-ai-head"><span className="owner-ai-avatar"><Bot size={22}/></span>
        <div><strong>Naija Bites Business Assistant</strong><small>Powered by AWS Bedrock · Verified business data</small></div></div>
      <div className="owner-ai-conversation" role="log" aria-label="Owner AI conversation" aria-live="polite">
        {turns.length === 0 && <div className="owner-ai-welcome">
          <Sparkles size={28}/><h2>What would you like to know?</h2>
          <p>I can help you understand sales and stock using live recorded business data. I cannot change orders, prices or inventory.</p>
          <div className="owner-ai-starters">{starters.map(question =>
            <button type="button" key={question} disabled={busy} onClick={() => void ask(question)}>{question}</button>
          )}</div>
        </div>}
        {turns.map((turn, i) => <article key={i} className={'owner-ai-turn ' + turn.role}>
          <span aria-hidden="true">{turn.role === 'user' ? <UserRound size={16}/> : <Bot size={17}/>}</span>
          <p>{turn.content}</p>
        </article>)}
        {busy && <p className="owner-ai-busy" role="status">Checking business records and preparing an answer…</p>}
      </div>
      {error && <p className="owner-message error" role="alert">{error}</p>}
      <form className="owner-ai-composer" onSubmit={submit}>
        <label className="sr-only" htmlFor="owner-ai-question">Ask Owner AI</label>
        <textarea id="owner-ai-question" value={draft} maxLength={600} rows={2} disabled={busy}
          placeholder="Ask about today’s orders or inventory…" onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!busy) void ask(draft); } }}/>
        {busy ? <button type="button" aria-label="Stop Owner AI response" onClick={cancel}><Square size={18}/> Stop</button>
              : <button type="submit" aria-label="Send question to Owner AI" disabled={!draft.trim()}><Send size={18}/> Send</button>}
      </form>
      <p className="owner-ai-disclaimer">Answers depend on the latest recorded data. Confirm important decisions in the relevant Owner Mode section. This assistant cannot make business changes.</p>
    </div>
  </section>;
}
