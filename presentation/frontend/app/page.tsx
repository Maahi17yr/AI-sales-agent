
'use client';

import { useEffect, useRef, useState } from 'react';

import { useAuth } from './auth-provider';

import {
  ChefHat,
  Send,
  ShoppingBag,
  Wifi,
  WifiOff,
  LoaderCircle,
  Trash2,
  UtensilsCrossed,
  ArrowLeft,
  ArrowRight,
  Check,
  MapPin,
  ShieldCheck,
  Sparkles,
  X,
  MessageCircle,
  RefreshCw,
  LogOut,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react';

// ------------------------------------------
// TYPES
// ------------------------------------------

type Msg = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
};

type WireMsg = Msg;

type SavedChat = { id: string; title: string; messages: Msg[]; updatedAt: number };
const MAX_SAVED_CHATS = 30;
const historyKey = (customerId: number) => `naija-bites-chat-history-v1-${customerId}`;
const chatTitle = (items: Msg[]) => items.find(m => m.role === 'user')?.content.slice(0, 52).trim() || 'New chat';
const upsertChat = (items: SavedChat[], id: string, messages: Msg[]): SavedChat[] => {
  if (!messages.some(m => m.role === 'user')) return items;
  const existing = items.find(c => c.id === id);
  const updated: SavedChat = { id, title: chatTitle(messages), messages, updatedAt: Date.now() };
  return [updated, ...items.filter(c => c.id !== id)].slice(0, MAX_SAVED_CHATS);
};

type CheckoutItem = {
  product_id: number;
  name: string;
  variant: string;
  quantity: number;
  unit_price: number;
  line_total: number;
  modifications?: {
    action: string;
    name: string;
    quantity: number;
  }[];
};

type CheckoutSummary = {
  customer_name: string;
  phone: string;
  delivery_address: string;
  delivery_area: string;
  items: CheckoutItem[];
  subtotal: number;
  discount: number;
  delivery_fee: number;
  total: number;
  currency: string;
};

type PendingCheckout = {
  summary: CheckoutSummary;
  fingerprint: string;
};

type CheckoutResponse = {
  success: boolean;
  pending: boolean;
  summary: CheckoutSummary | null;
  fingerprint: string | null;
};

type ConfirmationResponse = {
  success: boolean;
  order_id: number;
  status: string;
  total: number;
  currency: string;
  message: string;
};

// ------------------------------------------
// CONFIGURATION
// ------------------------------------------

// Use the same-origin Next.js proxy for health checks and chat.
const API = '/api/backend';

const uid = () => crypto.randomUUID();

const welcome: Msg = {
  id: 'welcome',
  role: 'assistant',
  content:
    'Welcome to Naija Bites! 🍛 What would you like to eat today? Ask about our menu or tell me what you are craving.',
};

function formatMoney(amount: number) {
  return new Intl.NumberFormat('en-NG', {
    style: 'currency',
    currency: 'NGN',
    maximumFractionDigits: 0,
  }).format(amount);
}

// ------------------------------------------
// MAIN COMPONENT
// ------------------------------------------

export default function Home() {
  const { customer, apiFetch, logout } = useAuth();
  const [messages, setMessages] = useState<Msg[]>([
    welcome,
  ]);

  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [online, setOnline] = useState<boolean | null>(null);
  const [error, setError] = useState('');
  const [activity, setActivity] = useState('');
  const [threadId, setThreadId] = useState('');
  const [savedChats, setSavedChats] = useState<SavedChat[]>([]);
  const [historyReady, setHistoryReady] = useState(false);

  const [checkout, setCheckout] =
    useState<PendingCheckout | null>(null);

  const [confirming, setConfirming] = useState(false);
  const [checkoutResult, setCheckoutResult] = useState('');
  const [checkoutError, setCheckoutError] = useState('');
  const [reviewOpen, setReviewOpen] = useState(true);
  const [paymentOrder, setPaymentOrder] = useState<number | null>(null);
  const [paymentEmail, setPaymentEmail] = useState('');
  const [paymentBusy, setPaymentBusy] = useState(false);
  const [paymentMessage, setPaymentMessage] = useState('');
  const [paymentToastSuccess, setPaymentToastSuccess] = useState(false);

  const conversationRef = useRef<HTMLDivElement>(null);
  const checkoutCardRef = useRef<HTMLElement>(null);
  const lastCheckoutFingerprint = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  // Escape closes the mobile drawer without resetting the food conversation.
  useEffect(() => {
    if (!mobileMenuOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileMenuOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [mobileMenuOpen]);

  // Stop an active agent stream when this authenticated view unmounts.
  useEffect(() => () => { abort.current?.abort(); }, []);

  // ----------------------------------------
  // INITIALIZATION
  // ----------------------------------------

  useEffect(() => {
    setPaymentEmail(customer.email);
  }, [customer.email]);


  // Restore server-owned chats and migrate any browser-only conversations.
  useEffect(() => {
    let cancelled = false;
    setHistoryReady(false);
    const load = async () => {
      let local: SavedChat[] = [];
      try {
        const raw = window.localStorage.getItem(historyKey(customer.id));
        const parsed: unknown = raw ? JSON.parse(raw) : [];
        if (Array.isArray(parsed)) local = parsed.filter((entry): entry is SavedChat =>
          entry !== null && typeof entry === 'object' &&
          typeof entry.id === 'string' && typeof entry.title === 'string' &&
          typeof entry.updatedAt === 'number' && Array.isArray(entry.messages) &&
          entry.messages.every((m: unknown) => m !== null && typeof m === 'object' &&
            'id' in m && typeof m.id === 'string' &&
            'role' in m && (m.role === 'user' || m.role === 'assistant') &&
            'content' in m && typeof m.content === 'string')
        ).slice(0, MAX_SAVED_CHATS);
      } catch { /* Local storage unavailable. */ }
      let remote: SavedChat[] = [];
      let remoteAvailable = false;
      try {
        const response = await apiFetch('/chats');
        if (!response.ok) throw new Error('Cloud history unavailable');
        const data: { chats: SavedChat[] } = await response.json();
        if (!Array.isArray(data.chats)) throw new Error('Invalid chat response');
        remote = data.chats;
        remoteAvailable = true;
      } catch {
        if (!cancelled) setError('Cloud history is unavailable. Local conversations remain on this browser.');
      }
      if (cancelled) return;
      const remoteById = new Map(remote.map(chat => [chat.id, chat]));
      // Preserve newer local edits if a tab closed before the last cloud save.
      const toUpload = local.filter(chat => {
        const saved = remoteById.get(chat.id);
        return !saved || chat.updatedAt > saved.updatedAt;
      });
      const pendingIds = new Set(toUpload.map(chat => chat.id));
      const restored = [...remote.filter(chat => !pendingIds.has(chat.id)), ...toUpload]
        .sort((left, right) => right.updatedAt - left.updatedAt)
        .slice(0, MAX_SAVED_CHATS);
      setSavedChats(restored);
      if (restored.length) {
        setThreadId(restored[0].id);
        setMessages(restored[0].messages);
      } else {
        setThreadId(uid());
        setMessages([welcome]);
      }
      setHistoryReady(true);
      if (remoteAvailable) {
        for (const chat of toUpload) {
          if (cancelled) return;
          try {
            await apiFetch('/chats/' + encodeURIComponent(chat.id), {
              method: 'PUT', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ title: chat.title, messages: chat.messages }),
            });
          } catch { /* Retain local copy for a later retry. */ }
        }
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [customer.id, apiFetch]);

  // Save the active conversation after each message, including streamed replies.
  useEffect(() => {
    if (!historyReady || !threadId) return;
    setSavedChats(prev => upsertChat(prev, threadId, messages));
  }, [historyReady, threadId, messages]);

  useEffect(() => {
    if (!historyReady) return;
    try { window.localStorage.setItem(historyKey(customer.id), JSON.stringify(savedChats)); }
    catch { /* Storage may be disabled or full; chat still works in this tab. */ }
  }, [customer.id, historyReady, savedChats]);


  // Persist the active conversation after the streaming response settles.
  useEffect(() => {
    if (!historyReady || !threadId) return;
    const active = savedChats.find(chat => chat.id === threadId);
    if (!active) return;
    const timer = window.setTimeout(() => {
      void apiFetch('/chats/' + encodeURIComponent(active.id), {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: active.title, messages: active.messages }),
      }).then(response => {
        if (!response.ok) throw new Error('Cloud save failed');
      }).catch(() => {
        setError('Unable to sync this conversation. Please check your connection.');
      });
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [historyReady, savedChats, threadId, apiFetch]);

  useEffect(() => {
    const controller = new AbortController();

    fetch(`${API}/ping`, {
      signal: controller.signal,
    })
      .then((response) => {
        setOnline(response.ok);
      })
      .catch((err) => {
        if (err.name !== 'AbortError') {
          setOnline(false);
        }
      });

    return () => {
      controller.abort();
    };
  }, []);

  // Scroll only the conversation, never the document or the message composer.
  useEffect(() => {
    if (messages.length === 1 && !activity) return;
    const panel = conversationRef.current;
    if (panel) panel.scrollTop = panel.scrollHeight;
  }, [messages, activity]);

  // Keep the review card in sync with the trusted backend state.
  // Polling also covers tool results that finish just after a chat stream.
  useEffect(() => {
    if (confirming) return;
    let disposed = false;
    let activeController: AbortController | null = null;

    async function refreshCheckout() {
      if (disposed) return;
      activeController?.abort();
      const controller = new AbortController();
      activeController = controller;

      try {
        const response = await apiFetch('/checkout', {
          cache: 'no-store',
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`Checkout returned HTTP ${response.status}`);

        const data: CheckoutResponse = await response.json();
        if (disposed || controller.signal.aborted) return;

        if (data.pending && data.summary && data.fingerprint) {
          if (lastCheckoutFingerprint.current !== data.fingerprint) {
            lastCheckoutFingerprint.current = data.fingerprint;
            setReviewOpen(true);
            window.setTimeout(() => {
              if (!disposed) checkoutCardRef.current?.scrollIntoView({
                behavior: 'smooth',
                block: 'nearest',
              });
            }, 150);
          }
          setCheckout({ summary: data.summary, fingerprint: data.fingerprint });
        } else {
          lastCheckoutFingerprint.current = null;
          setCheckout(null);
        }
        setCheckoutError('');
      } catch (err) {
        if (disposed || controller.signal.aborted) return;
        console.error('Checkout fetch failed:', err);
        setCheckoutError('Could not load the current checkout summary.');
      }
    }

    void refreshCheckout();
    const interval = window.setInterval(() => {
      if (!busy && !confirming) void refreshCheckout();
    }, 3000);

    return () => {
      disposed = true;
      window.clearInterval(interval);
      activeController?.abort();
    };
  }, [busy, confirming, apiFetch]);

  // ----------------------------------------
  // RESET CHAT
  // ----------------------------------------

  function reset() {
    if (busy || confirming) return;

    abort.current?.abort();
    if (threadId) setSavedChats(prev => upsertChat(prev, threadId, messages));

    setMessages([welcome]);
    setDraft('');
    setError('');
    setActivity('');
    setThreadId(uid());

    setCheckoutResult('');
    setCheckoutError('');

    // A new chat does not clear the server-side cart.
    // Any pending checkout remains visible until
    // it is changed, confirmed, or cancelled.
  }

  function openChat(chat: SavedChat) {
    if (busy || confirming || chat.id === threadId) return;
    if (threadId) setSavedChats(prev => upsertChat(prev, threadId, messages));
    setThreadId(chat.id);
    setMessages(chat.messages);
    setDraft('');
    setError('');
    setActivity('');
    setCheckoutError('');
    setCheckoutResult('');
    setMobileMenuOpen(false);
  }

  // ----------------------------------------
  // SEND MESSAGE TO AGENT
  // ----------------------------------------

  async function send(quick?: string) {
    const content = (quick ?? draft).trim();

    if (
      !content ||
      busy ||
      confirming ||
      !threadId
    ) {
      return;
    }

    setDraft('');
    setError('');
    setActivity('');
    setCheckoutError('');
    setBusy(true);

    const user: Msg = {
      id: uid(),
      role: 'user',
      content,
    };

    const next = [...messages, user];

    setMessages(next);

    const assistantId = uid();
    let assistantText = '';
    let gotText = false;

    const controller = new AbortController();
    abort.current = controller;

    try {
      const wire: WireMsg[] = next
        .filter((message) => message.id !== 'welcome')
        .map((message) => ({
          id: message.id,
          role: message.role,
          content: message.content,
        }));

      const response = await apiFetch('/agent', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify({
          threadId,
          runId: uid(),
          parentRunId: null,
          state: {},
          messages: wire,
          tools: [],
          context: [],
          forwardedProps: {},
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(
          `Backend returned HTTP ${response.status}.`
        );
      }

      if (!response.body) {
        throw new Error(
          'Backend returned no event stream.'
        );
      }

      setOnline(true);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      let buffer = '';

      function processFrame(frame: string) {
        const data = frame
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart())
          .join('\n');

        if (!data || data === '[DONE]') return;

        let evt: {
          type?: string;
          delta?: string;
          message?: string;
        };

        try {
          evt = JSON.parse(data);
        } catch {
          return;
        }

        if (
          evt.type === 'TEXT_MESSAGE_CONTENT' &&
          typeof evt.delta === 'string'
        ) {
          gotText = true;
          assistantText += evt.delta;

          const textSnapshot = assistantText;

          setMessages((current) => {
            const exists = current.some(
              (message) => message.id === assistantId
            );

            if (exists) {
              return current.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      content: textSnapshot,
                    }
                  : message
              );
            }

            return [
              ...current,
              {
                id: assistantId,
                role: 'assistant',
                content: textSnapshot,
              },
            ];
          });
        } else if (
          evt.type === 'TOOL_CALL_START'
        ) {
          setActivity(
            'Checking your order details…'
          );
        } else if (
          evt.type === 'TOOL_CALL_END'
        ) {
          setActivity('');
        } else if (evt.type === 'RUN_ERROR') {
          throw new Error(
            evt.message ||
              'The agent encountered an error.'
          );
        }
      }

      while (true) {
        const { done, value } = await reader.read();

        if (done) break;

        buffer += decoder
          .decode(value, { stream: true })
          .replace(/\r\n/g, '\n');

        let index: number;

        while (
          (index = buffer.indexOf('\n\n')) >= 0
        ) {
          const frame = buffer.slice(0, index);
          buffer = buffer.slice(index + 2);

          processFrame(frame);
        }
      }

      buffer += decoder.decode();

      if (buffer.trim()) {
        processFrame(buffer);
      }

      if (!gotText) {
        throw new Error(
          'No text response received. Check the backend logs.'
        );
      }
    } catch (err) {
      if (
        err instanceof Error &&
        err.name === 'AbortError'
      ) {
        return;
      }

      setError(
        err instanceof Error
          ? err.message
          : 'Unexpected request error.'
      );
    } finally {
      setBusy(false);
      setActivity('');
      abort.current = null;
    }
  }

  // ----------------------------------------
  // CONFIRM ORDER
  // ----------------------------------------

  async function confirmOrder() {
    if (!checkout || confirming || busy) return;

    const reviewedFingerprint = checkout.fingerprint;

    setConfirming(true);
    setCheckoutError('');
    setCheckoutResult('');

    try {
      const response = await apiFetch(
        '/checkout/confirm',
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            customer_approved: true,
            reviewed_fingerprint: reviewedFingerprint,
          }),
        }
      );

      const data = await response.json();

      if (!response.ok || !data.success) {
        throw new Error(
          typeof data.detail === 'string'
            ? data.detail
            : data.message ||
                'Order confirmation failed.'
        );
      }

      const result = data as ConfirmationResponse;

      setCheckout(null);
      setPaymentOrder(result.order_id);

      setCheckoutResult(
        `Order #${result.order_id} placed! Total: ${formatMoney(result.total)}. Payment is still pending.`
      );
    } catch (err) {
      setCheckoutError(
        err instanceof Error
          ? err.message
          : 'Could not confirm your order.'
      );
    } finally {
      setConfirming(false);
    }
  }

  async function payNow() {
    if (!paymentOrder || paymentBusy) return;
    setPaymentBusy(true);
    setCheckoutError('');
    try {
      // Flush this conversation before navigating away to Paystack.
      if (messages.some(message => message.role === 'user')) {
        const saved = await apiFetch('/chats/' + encodeURIComponent(threadId), {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: chatTitle(messages), messages }),
        });
        if (!saved.ok) throw new Error('Could not save your conversation. Payment was not started.');
      }
      const response = await apiFetch('/payments/initialize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order_id: paymentOrder, email: paymentEmail.trim(), frontend_origin: window.location.origin }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not start payment.');
      if (!String(data.authorization_url).startsWith('https://')) throw new Error('Invalid payment URL.');
      window.location.assign(data.authorization_url);
    } catch (err) {
      setCheckoutError(err instanceof Error ? err.message : 'Could not start payment.');
      setPaymentBusy(false);
    }
  }

  // A floating notification disappears three seconds after its message changes.
  // Clearing the timer also prevents updates after this view unmounts.
  useEffect(() => {
    if (!paymentMessage) return;
    const timeout = window.setTimeout(() => setPaymentMessage(''), 3000);
    return () => window.clearTimeout(timeout);
  }, [paymentMessage]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('payment_return') !== '1') return;
    const reference = params.get('reference') || params.get('trxref');
    if (!reference) {
      setPaymentToastSuccess(false);
      setPaymentMessage('Payment return received without a reference.');
      window.history.replaceState({}, '', window.location.pathname);
      return;
    }
    apiFetch('/payments/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reference }),
    }).then(async response => {
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Payment verification failed.');
      const paid = data.payment_status === 'paid';
      setPaymentToastSuccess(paid);
      setPaymentMessage(paid
        ? `Payment verified! Order #${data.order_id} is confirmed.`
        : 'Payment is not yet confirmed. You can try verifying again later.');
    }).catch(err => {
      setPaymentToastSuccess(false);
      setPaymentMessage(err instanceof Error ? err.message : 'Verification failed.');
    });
    window.history.replaceState({}, '', window.location.pathname);
  }, [apiFetch]);

  return (
    <main className={`site-shell ${process.env.NEXT_PUBLIC_DEMO_MODE === 'true' ? 'is-demo' : ''} ${sidebarCollapsed ? 'sidebar-collapsed' : ''} ${mobileMenuOpen ? 'mobile-nav-open' : ''}`}>
      {mobileMenuOpen && <button type="button" className="mobile-nav-backdrop" aria-label="Close navigation" onClick={() => setMobileMenuOpen(false)} />}
      <aside className="side-panel" id="main-navigation" aria-label="Main navigation">
        <div className="identity">
          <button type="button" className="sidebar-close-mobile" onClick={() => setMobileMenuOpen(false)} aria-label="Close navigation"><X size={20}/></button>
          <span className="identity-mark"><UtensilsCrossed size={20} strokeWidth={2.2} /></span>
          <div className="identity-copy"><strong>Naija Bites<span className="brand-dot">.</span></strong><small>Good food, good mood.</small></div>
        </div>
        <div className="side-divider" />
        <div className="side-caption">YOUR TABLE</div>
        <button className="side-link selected" title="Food chat" onClick={() => { setMobileMenuOpen(false); document.getElementById('message-input')?.focus(); }}><MessageCircle size={18}/><span className="side-link-text">Food chat</span><ArrowRight size={16} className="side-link-arrow"/></button>
        <button className="side-link" title="Explore menu" onClick={() => { setMobileMenuOpen(false); void send('Show me the menu'); }} disabled={busy || confirming || !threadId}><UtensilsCrossed size={18}/><span className="side-link-text">Explore menu</span><ArrowRight size={16} className="side-link-arrow"/></button>
        <button className="side-link" title="My cart" onClick={() => { setMobileMenuOpen(false); void send('Show me my cart'); }} disabled={busy || confirming || !threadId}><ShoppingBag size={18}/><span className="side-link-text">My cart</span><ArrowRight size={16} className="side-link-arrow"/></button>
        <div className="chat-history" aria-label="Recent conversations"><div className="chat-history-heading"><span className="side-caption">RECENT CHATS</span><span className="chat-history-count">{savedChats.length}</span></div>{savedChats.length ? <div className="chat-history-list">{savedChats.map(chat => <button key={chat.id} type="button" className={`chat-history-item ${threadId === chat.id ? 'active' : ''}`} title={chat.title} aria-current={threadId === chat.id ? 'page' : undefined} disabled={busy || confirming} onClick={() => openChat(chat)}><MessageCircle size={16}/><span>{chat.title}</span></button>)}</div> : <p className="chat-history-empty">Your conversations will appear here.</p>}</div>
        <div className="account-panel"><span className="account-label">SIGNED IN AS</span><strong>{customer.name}</strong><span className="account-email">{customer.email}</span><button className="account-logout" type="button" onClick={() => void logout()}><LogOut size={15}/> Sign out</button></div>
        <div className="side-help"><div className="side-help-icon"><Sparkles size={20}/></div><span className="side-help-eyebrow">YOUR PERSONAL FOOD GUIDE</span><strong>Good food, zero stress.</strong><p>Tell us what you’re craving. We’ll help you find the right bite.</p><button type="button" className="side-help-action" onClick={() => void send("Help me choose something delicious")} disabled={busy || confirming || !threadId}>Get inspired <ArrowRight size={15}/></button></div>
        <div className="sidebar-foot"><span className={`connect-dot ${online ? 'up' : ''}`}/><span>{online === null ? 'Connecting…' : online ? 'Ready to take your order' : 'Connection unavailable'}</span></div>
      </aside>
      <section className="chat-layout">
        {process.env.NEXT_PUBLIC_DEMO_MODE === 'true' && (
          <div className="demo-notice" role="note">Demo only · Paystack TEST payments · No real orders or deliveries. Use fictional details.</div>
        )}
        <header className="chat-header">
          <button type="button" className="sidebar-toggle desktop-sidebar-toggle" onClick={() => setSidebarCollapsed(value => !value)} aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'} aria-controls="main-navigation" aria-expanded={!sidebarCollapsed} title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}>{sidebarCollapsed ? <PanelLeftOpen size={20}/> : <PanelLeftClose size={20}/>}</button>
          <button type="button" className="sidebar-toggle mobile-sidebar-toggle" onClick={() => setMobileMenuOpen(true)} aria-label="Open navigation" aria-controls="main-navigation" aria-expanded={mobileMenuOpen} title="Open navigation"><Menu size={21}/></button>
          <div className="mobile-brand"><span className="identity-mark small"><UtensilsCrossed size={18}/></span></div>
          <div className="agent-avatar"><ChefHat size={24}/><span className="avatar-online"/></div>
          <div className="agent-title"><h1>Your food companion <span className="header-sparkle">✳</span></h1><p><span className="live-dot"/> Naija Bites AI · Here to help you order</p></div>
          <button className="mobile-logout" type="button" onClick={() => void logout()} aria-label="Sign out" title="Sign out"><LogOut size={17}/></button>
          <button className="new-chat" onClick={reset} disabled={busy || confirming} title="Start a fresh conversation"><RefreshCw size={16}/><span>New chat</span></button>
        </header>
        <div className="conversation" id="conversation" ref={conversationRef}>
          <div className="conversation-inner">
            {messages.length === 1 ? (
              <section className="welcome-hero" aria-label="Welcome to Naija Bites">
                <div className="hero-copy">
                  <span className="hero-eyebrow"><Sparkles size={15}/> THE NAIJA BITES EXPERIENCE</span>
                  <h2>Craving something <em>really good?</em></h2>
                  <p>From smoky jollof to your favourite comfort food, a delicious decision is just a conversation away.</p>
                  <button className="hero-cta" type="button" onClick={() => void send('Show me the menu')} disabled={busy || confirming || !threadId}>Explore the menu <ArrowRight size={18}/></button>
                  <div className="hero-footnote"><span className="hero-footnote-dot"/> Made for Nigerian cravings, powered by AI</div>
                </div>
                <div className="hero-visual" aria-hidden="true">
                  <div className="hero-orbit orbit-one"/><div className="hero-orbit orbit-two"/>
                  <div className="hero-food-plate"><span>🍛</span></div>
                  <div className="hero-sticker sticker-top">Fresh ideas <Sparkles size={14}/></div>
                  <div className="hero-sticker sticker-bottom">Good food. Good mood.</div>
                </div>
              </section>
            ) : <div className="date-divider"><span>Today</span></div>}
            {messages.filter(message => message.id !== 'welcome').map((message) => <div key={message.id} className={`message-row ${message.role}`}>
              {message.role === 'assistant' && <span className="message-avatar"><UtensilsCrossed size={15}/></span>}
              <div className="message-content"><div className="bubble"><MessageMarkup content={message.content}/></div><span className="message-meta">{message.role === 'assistant' ? 'Naija Bites' : 'You'}</span></div>
            </div>)}
            {messages.length === 1 && <section className="welcome-shortcuts" aria-label="Ways to get started">
              <div className="shortcuts-heading"><div><span className="shortcuts-eyebrow">NOT SURE WHERE TO START?</span><h3>What sounds good today?</h3></div><span>Pick a shortcut or type below</span></div>
              <div className="shortcut-grid">
                <button type="button" onClick={() => void send('Show me the menu')} disabled={busy || confirming || !threadId}><span className="shortcut-icon"><UtensilsCrossed size={21}/></span><strong>Browse the menu</strong><small>See what’s cooking</small><ArrowRight size={17} className="shortcut-arrow"/></button>
                <button type="button" onClick={() => void send('What can I get for ₦5,000?')} disabled={busy || confirming || !threadId}><span className="shortcut-icon"><ShoppingBag size={21}/></span><strong>Eat on a budget</strong><small>Great food, your price</small><ArrowRight size={17} className="shortcut-arrow"/></button>
                <button type="button" onClick={() => void send('Surprise me with a Nigerian food recommendation')} disabled={busy || confirming || !threadId}><span className="shortcut-icon"><Sparkles size={21}/></span><strong>Surprise me</strong><small>Find a new favourite</small><ArrowRight size={17} className="shortcut-arrow"/></button>
              </div>
            </section>}
            {busy && <div className="working-row"><span className="message-avatar"><UtensilsCrossed size={15}/></span><div className="working-bubble"><span className="typing-dots"><i/><i/><i/></span><span>{activity || 'Putting something together…'}</span></div></div>}
            {error && <div className="inline-alert" role="alert">{error}</div>}
            {checkout && reviewOpen && <section ref={checkoutCardRef} className="checkout-panel" aria-label="Review your order">
              <div className="checkout-top"><div><span className="checkout-kicker"><ShoppingBag size={15}/> YOUR ORDER</span><h2>Review your order</h2><p>Make sure everything looks right.</p></div><button className="icon-button" aria-label="Hide order review" title="Hide order review" onClick={() => setReviewOpen(false)}><X size={18}/></button></div>
              <div className="checkout-body">
                <div className="checkout-items">{checkout.summary.items.map((item, i) => <div className="checkout-item" key={`${item.product_id}-${i}`}><div className="food-icon"><UtensilsCrossed size={19}/></div><div className="food-details"><strong>{item.name}</strong><span>{item.variant} · Qty {item.quantity}</span>{item.modifications?.map((m,j) => <small key={j}>{m.action === 'add' ? '+' : '−'} {m.name} × {m.quantity}</small>)}</div><strong>{formatMoney(item.line_total)}</strong></div>)}</div>
                <div className="checkout-cost"><div><span>Subtotal</span><span>{formatMoney(checkout.summary.subtotal)}</span></div><div><span>Delivery</span><span>{formatMoney(checkout.summary.delivery_fee)}</span></div>{checkout.summary.discount > 0 && <div className="discount"><span>Discount</span><span>−{formatMoney(checkout.summary.discount)}</span></div>}<div className="checkout-total"><strong>Total</strong><strong>{formatMoney(checkout.summary.total)}</strong></div></div>
                <div className="delivery-box"><MapPin size={19}/><div><strong>Deliver to {checkout.summary.customer_name}</strong><p>{checkout.summary.delivery_address}</p><span>{checkout.summary.delivery_area} · {checkout.summary.phone}</span></div></div>
                <div className="checkout-actions"><button className="secondary-action" onClick={() => setReviewOpen(false)}>Back to chat</button><button className="primary-action" onClick={confirmOrder} disabled={confirming || busy}>{confirming ? <LoaderCircle size={17} className="spin"/> : <Check size={17}/>} {confirming ? 'Placing order…' : 'Confirm order'}</button></div>
                <p className="order-disclaimer"><ShieldCheck size={14}/> Payment is not collected here. This is a test order.</p>
              </div>
            </section>}
            {checkout && !reviewOpen && <button className="review-reopen" onClick={() => setReviewOpen(true)}><ShoppingBag size={17}/> Review your order · {formatMoney(checkout.summary.total)} <ArrowRight size={16}/></button>}
            {checkoutResult && <div className="checkout-success" role="status"><Check size={18}/><span>{checkoutResult}</span></div>}
            {paymentOrder && <section className="checkout-panel" aria-label="Paystack test payment">
              <div className="checkout-top"><div><span className="checkout-kicker">PAYSTACK TEST MODE</span><h2>Pay for order #{paymentOrder}</h2><p>Use Paystack's published test payment details. No real money will be charged.</p></div></div>
              <div className="checkout-body">
                <label htmlFor="payment-email">Email for payment receipt</label>
                <input id="payment-email" type="email" value={paymentEmail} onChange={e => setPaymentEmail(e.target.value)} placeholder="you@example.com" style={{width:'100%',padding:'12px',borderRadius:12,border:'1px solid #ddd',margin:'8px 0 14px'}}/>
                <button className="primary-action" onClick={() => void payNow()} disabled={paymentBusy || !paymentEmail.includes('@')}>{paymentBusy ? 'Opening Paystack…' : 'Pay Now (Test Mode)'}</button>
              </div>
            </section>}
             {checkoutError && <div className="inline-alert" role="alert">{checkoutError}</div>}

          </div>
        </div>
        <div className="composer-area">
          <div className="composer-intro"><Sparkles size={15}/> Ask me anything about your next meal</div>
          <form className="composer" onSubmit={(e) => {e.preventDefault();void send();}}><input id="message-input" aria-label="Message Naija Bites" placeholder="Tell me what you’re craving…" value={draft} onChange={(e) => setDraft(e.target.value)} disabled={busy || confirming}/><button type="submit" aria-label="Send message" disabled={!draft.trim() || busy || confirming || !threadId}>{busy ? <LoaderCircle size={19} className="spin"/> : <ArrowRight size={21}/>}</button></form>
          <div className="composer-caption">A little chat, a lot of flavour. <span>✳</span> Naija Bites</div>
        </div>
      </section>
      {paymentMessage && (
        <div
          className={`payment-toast ${paymentToastSuccess ? 'payment-toast--success' : 'payment-toast--warning'}`}
          role={paymentToastSuccess ? 'status' : 'alert'}
          aria-live={paymentToastSuccess ? 'polite' : 'assertive'}
          aria-atomic="true"
        >
          {paymentToastSuccess ? <Check size={20} aria-hidden="true" /> : <ShieldCheck size={20} aria-hidden="true" />}
          <span>{paymentMessage}</span>
        </div>
      )}
    </main>
  );
}

// Minimal Markdown handling for safe model text: supports bold, italic and inline code.
// Rendering React nodes (not HTML) prevents model text from injecting markup.
function InlineMarkup({ value }: { value: string }) {
  const tokens = value.split(/(\*\*[^*\n]+\*\*|\*[^*\n]+\*|`[^`\n]+`)/g);
  return <>{tokens.map((t, i) => {
    if (t.startsWith('**') && t.endsWith('**')) return <strong key={i}>{t.slice(2,-2)}</strong>;
    if (t.startsWith('*') && t.endsWith('*')) return <em key={i}>{t.slice(1,-1)}</em>;
    if (t.startsWith('`') && t.endsWith('`')) return <code key={i}>{t.slice(1,-1)}</code>;
    return <span key={i}>{t}</span>;
  })}</>;
}
function MessageMarkup({ content }: { content: string }) {
  return <div className="message-markup">{content.split('\n').map((line, i) => {
    const bullet = line.match(/^\s*[-•]\s+(.+)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (!line.trim()) return <div key={i} className="message-break" />;
    if (bullet || numbered) return <div className="message-list-line" key={i}><span className="list-marker">{numbered ? '•' : '•'}</span><span><InlineMarkup value={(bullet || numbered)![1]}/></span></div>;
    return <div className="message-line" key={i}><InlineMarkup value={line.replace(/^#{1,4}\s+/, '')}/></div>;
  })}</div>;
}
