'use client';

import { createContext, useCallback, useContext, useEffect, useRef, useState, Fragment, type FormEvent, type ReactNode } from 'react';
import { ChefHat, Eye, EyeOff, LoaderCircle, ShieldCheck, UtensilsCrossed } from 'lucide-react';
import { usePathname } from 'next/navigation';

// Always use the same-origin Next.js proxy; FastAPI owns authentication.
const API = '/api/backend';
const SESSION_CHECK_COOLDOWN_MS = 10_000;
const DEFAULT_429_COOLDOWN_MS = 30_000;
const MAX_429_COOLDOWN_MS = 5 * 60_000;

function retryDelay(response: Response): number {
  const retryAfter = response.headers.get('retry-after')?.trim();
  if (retryAfter) {
    const seconds = Number(retryAfter);
    const delay = Number.isFinite(seconds) && seconds >= 0
      ? seconds * 1000 : Date.parse(retryAfter) - Date.now();
    if (Number.isFinite(delay) && delay >= 0) {
      return Math.min(MAX_429_COOLDOWN_MS, Math.max(1000, delay));
    }
  }
  return DEFAULT_429_COOLDOWN_MS;
}


function connectionMessage(error: unknown): string {
  if (error instanceof TypeError) {
    return 'Cannot reach Naija Bites right now. Check that the website and API are running, then try again.';
  }
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.';
}

export type Customer = { id: number; name: string; email: string; role: 'customer' | 'owner'; phone?: string | null };

type AuthContextValue = {
  customer: Customer;
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('Authentication context is missing.');
  return value;
}

async function detail(response: Response): Promise<string> {
  try {
    const body: { detail?: unknown; message?: unknown } = await response.json();
    if (typeof body.detail === 'string') return body.detail;
    if (typeof body.message === 'string') return body.message;
  } catch {
    // Proxy and gateway failures may return HTML rather than JSON.
  }
  if (response.status === 429) {
    const seconds = Math.ceil(retryDelay(response) / 1000);
    return `Too many requests. Please try again in about ${seconds} seconds.`;
  }
  if ([502, 503, 504].includes(response.status)) {
    return 'The Naija Bites API is unavailable. Please try again shortly.';
  }
  return `Request failed (HTTP ${response.status}). Please try again.`;
}

// The backend decides the role. This only chooses the matching UI;
// FastAPI must still enforce owner permissions on every owner endpoint.
function RoleGate({ customer, children }: { customer: Customer; children: ReactNode }) {
  // usePathname is consistent across server rendering and hydration.
  const pathname = usePathname();
  const ownerPath = pathname === '/owner' || pathname.startsWith('/owner/');
  const redirect = (customer.role === 'owner') !== ownerPath;
  useEffect(() => {
    if (redirect) window.location.replace(customer.role === 'owner' ? '/owner' : '/');
  }, [redirect, customer.role]);
  if (redirect) return <main className="auth-shell"><div className="auth-loading" role="status">Opening your account…</div></main>;
  return <Fragment key={customer.id}>{children}</Fragment>;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [checking, setChecking] = useState(true);
  const [notice, setNotice] = useState('');
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [submitting, setSubmitting] = useState(false);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const customerRef = useRef<Customer | null>(null);
  const sessionCheckInFlight = useRef<Promise<void> | null>(null);
  const lastSessionCheckAt = useRef(0);
  const sessionChecksBlockedUntil = useRef(0);
  const authEpoch = useRef(0);
  useEffect(() => { customerRef.current = customer; }, [customer]);

  const refreshSession = useCallback(async (force = false) => {
    const now = Date.now();
    // Account changes can bypass the 10-second debounce, never Retry-After.
    if (now < sessionChecksBlockedUntil.current) return;
    if (sessionCheckInFlight.current) return sessionCheckInFlight.current;
    if (!force && now - lastSessionCheckAt.current < SESSION_CHECK_COOLDOWN_MS) return;
    lastSessionCheckAt.current = now;
    const startedEpoch = authEpoch.current;
    const check = (async () => {
      try {
        const response = await fetch(`${API}/auth/me`, {
          credentials: 'include', cache: 'no-store',
        });
        if (startedEpoch !== authEpoch.current) return;
        if (response.status === 429) {
          sessionChecksBlockedUntil.current = Date.now() + retryDelay(response);
          setNotice(await detail(response));
          return;
        }
        if (response.ok) {
          const data: { customer: Customer } = await response.json();
          if (startedEpoch !== authEpoch.current) return;
          customerRef.current = data.customer;
          setCustomer(data.customer);
          setNotice('');
        } else if (response.status === 401) {
          customerRef.current = null;
          setCustomer(null);
        } else {
          setNotice(await detail(response));
        }
      } catch {
        // Transient proxy/network errors must not log out the customer.
      }
    })();
    sessionCheckInFlight.current = check;
    try {
      await check;
    } finally {
      if (sessionCheckInFlight.current === check) sessionCheckInFlight.current = null;
    }
  }, []);

  // Focus + visibility events commonly fire together; do not double-fetch.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === 'naija-bites-auth-change') void refreshSession(true);
    };
    const onFocus = () => { void refreshSession(); };
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refreshSession();
    };
    window.addEventListener('storage', onStorage);
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.removeEventListener('storage', onStorage);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [refreshSession]);

  const apiFetch = useCallback(async (path: string, options: RequestInit = {}) => {
    const expectedCustomerId = customerRef.current?.id;
    const headers = new Headers(options.headers);
    if (expectedCustomerId !== undefined) {
      headers.set('X-Naija-Bites-Expected-Customer', String(expectedCustomerId));
    }
    // FastAPI's middleware atomically checks the expected customer header
    // against the cookie BEFORE writes. The old preflight /auth/me was
    // redundant and still subject to a time-of-check/time-of-use race.
    const response = await fetch(`${API}${path}`, {
      ...options, headers, credentials: 'include',
      cache: options.cache ?? 'no-store',
    });
    if (response.status === 401) {
      authEpoch.current++;
      customerRef.current = null;
      setCustomer(null);
      setNotice('Your session has expired. Please sign in again.');
    } else if (response.status === 409 && expectedCustomerId !== undefined) {
      const body = await response.clone().json().catch(() => null);
      if (body?.detail === 'Account changed. Refresh before continuing.') {
        void refreshSession(true);
        throw new Error('The signed-in account changed in another tab. Please review your account before continuing.');
      }
    } else if (response.status === 429) {
      setNotice(await detail(response.clone()));
    }
    return response;
  }, [refreshSession]);

  useEffect(() => {
    void refreshSession(true).finally(() => setChecking(false));
  }, [refreshSession]);

  const logout = useCallback(async () => {
    try {
      const response = await fetch(`${API}/auth/logout`, {
        method: 'POST', credentials: 'include', cache: 'no-store',
      });
      if (!response.ok) throw new Error(await detail(response));
      authEpoch.current++;
      customerRef.current = null;
      setCustomer(null);
      lastSessionCheckAt.current = 0;
      window.localStorage.setItem('naija-bites-auth-change', String(Date.now()));
      setNotice('You have signed out.');
    } catch (error) {
      setNotice(connectionMessage(error));
    }
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setNotice('');
    try {
      const normalizedEmail = email.trim().toLowerCase();
      if (mode === 'register') {
        const register = await fetch(`${API}/auth/register`, {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name.trim(), email: normalizedEmail, password }),
        });
        if (!register.ok) throw new Error(await detail(register));
      }
      const response = await fetch(`${API}/auth/login`, {
        method: 'POST', credentials: 'include', cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: normalizedEmail, password }),
      });
      if (!response.ok) throw new Error(await detail(response));
      const data: { customer: Customer } = await response.json();
      setPassword('');
      setShowPassword(false);
      authEpoch.current++;
      customerRef.current = data.customer;
      setCustomer(data.customer);
      lastSessionCheckAt.current = Date.now();
      sessionChecksBlockedUntil.current = 0;
      window.localStorage.setItem('naija-bites-auth-change', String(Date.now()));
    } catch (error) {
      setNotice(connectionMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  if (checking) return (
    <main className="auth-shell"><div className="auth-loading" role="status"><LoaderCircle className="spin" size={26}/> Checking your session…</div></main>
  );

  if (!customer) return (
    <main className="auth-shell">
      <section className="auth-card" aria-label="Naija Bites account">
        {process.env.NEXT_PUBLIC_DEMO_MODE === 'true' && <p className="demo-notice" role="note">Demo only: no real orders or deliveries. Use a fictional identity and a unique test password.</p>}
        <div className="auth-brand"><span className="identity-mark"><UtensilsCrossed size={23}/></span><div><strong>Naija Bites<span className="brand-dot">.</span></strong><small>Good food, good mood.</small></div></div>
        <div className="auth-icon"><ChefHat size={26}/></div>
        <h1>{mode === 'login' ? 'Welcome back' : 'Create your account'}</h1>
        <p className="auth-subtitle">{mode === 'login' ? 'Sign in to continue your food chat.' : 'Join Naija Bites and start ordering your favourites.'}</p>
        <form className="auth-form" onSubmit={submit}>
          {mode === 'register' && <label>Full name<input name="name" autoComplete="name" value={name} onChange={e => setName(e.target.value)} minLength={2} maxLength={120} required placeholder="Your name"/></label>}
          <label>Email address<input name="email" type="email" autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} required placeholder="you@example.com"/></label>
          <label>Password<div className="auth-password-field"><input name="password" type={showPassword ? 'text' : 'password'} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChange={e => setPassword(e.target.value)} required minLength={mode === 'register' ? 12 : undefined} placeholder="Enter your password"/><button className="auth-password-toggle" type="button" onClick={() => setShowPassword(visible => !visible)} aria-label={showPassword ? 'Hide password' : 'Show password'} aria-pressed={showPassword} title={showPassword ? 'Hide password' : 'Show password'}>{showPassword ? <EyeOff size={19}/> : <Eye size={19}/>}</button></div></label>
          {notice && <p className="auth-notice" role="alert">{notice}</p>}
          <button className="auth-submit" type="submit" disabled={submitting}>{submitting ? <LoaderCircle className="spin" size={18}/> : <ShieldCheck size={18}/>} {submitting ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}</button>
        </form>
        <p className="auth-switch">{mode === 'login' ? 'New to Naija Bites?' : 'Already have an account?'} <button type="button" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setNotice(''); setPassword(''); setShowPassword(false); }} disabled={submitting}>{mode === 'login' ? 'Create an account' : 'Sign in'}</button></p>
      </section>
    </main>
  );

  return <AuthContext.Provider value={{ customer, apiFetch, logout }}><RoleGate customer={customer}>{children}</RoleGate></AuthContext.Provider>;
}
