'use client';

import { createContext, useCallback, useContext, useEffect, useRef, useState, Fragment, type FormEvent, type ReactNode } from 'react';
import { ChefHat, Eye, EyeOff, LoaderCircle, ShieldCheck, UtensilsCrossed } from 'lucide-react';
import { usePathname } from 'next/navigation';

// Always use the same-origin Next.js proxy; FastAPI owns authentication.
import { classifyRequest, recordRequest } from './request-metrics';

const API = '/api/backend';

// Direct authentication requests are background traffic, independent of
// chat/checkout/payment journeys. Never retain URLs, cookies or response data.
async function backgroundFetch(path: string, options: RequestInit): Promise<Response> {
  try {
    const response = await fetch(`${API}${path}`, options);
    recordRequest(classifyRequest(path), !response.ok);
    return response;
  } catch (error) {
    recordRequest(classifyRequest(path), true);
    throw error;
  }
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
  apiFetch: (path: string, options?: RequestInit, journeyId?: string) => Promise<Response>;
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
  useEffect(() => { customerRef.current = customer; }, [customer]);

  const refreshSession = useCallback(async () => {
    try {
      const response = await backgroundFetch('/auth/me', { credentials: 'include', cache: 'no-store' });
      if (response.ok) {
        const data: { customer: Customer } = await response.json();
        customerRef.current = data.customer;
        setCustomer(data.customer);
      } else if (response.status === 401) {
        customerRef.current = null;
        setCustomer(null);
      }
    } catch {
      /* Keep current state on transient network failure. */
    }
  }, []);

  // Tabs on the same origin share the session cookie. Recheck when another tab
  // logs in/out, or when a tab regains focus after an external payment redirect.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === 'naija-bites-auth-change') void refreshSession();
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

  const apiFetch = useCallback(async (path: string, options: RequestInit = {}, journeyId?: string) => {
    const expectedCustomerId = customerRef.current?.id;
    const headers = new Headers(options.headers);
    if (expectedCustomerId !== undefined) {
      headers.set('X-Naija-Bites-Expected-Customer', String(expectedCustomerId));
    }
    // Do not send account-changing requests from a stale tab under another user's cookie.
    if (options.method && !['GET', 'HEAD'].includes(options.method.toUpperCase())) {
      let session: Response;
      try {
        session = await fetch(`${API}/auth/me`, { credentials: 'include', cache: 'no-store' });
        recordRequest('auth', !session.ok, journeyId);
      } catch (error) {
        recordRequest('auth', true, journeyId);
        throw error;
      }
      if (session.status === 401) {
        customerRef.current = null;
        setCustomer(null);
        throw new Error('Your session has changed. Sign in again before continuing.');
      }
      if (!session.ok) throw new Error('Could not verify your session. Please try again.');
      const data: { customer: Customer } = await session.json();
      if (customerRef.current && data.customer.id !== customerRef.current.id) {
        customerRef.current = data.customer;
        setCustomer(data.customer);
        throw new Error('The signed-in account changed in another tab. Please review your account before continuing.');
      }
    }
    let response: Response;
    try {
      response = await fetch(`${API}${path}`, {
        ...options,
        headers,
        credentials: 'include',
        cache: options.cache ?? 'no-store',
      });
      recordRequest(classifyRequest(path), !response.ok, journeyId);
    } catch (error) {
      recordRequest(classifyRequest(path), true, journeyId);
      throw error;
    }
    if (response.status === 401) {
      customerRef.current = null;
      setCustomer(null);
      setNotice('Your session has expired. Please sign in again.');
    }
    return response;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    backgroundFetch('/auth/me', { credentials: 'include', cache: 'no-store', signal: controller.signal })
      .then(async response => {
        if (response.ok) {
          const data: { customer: Customer } = await response.json();
          if (!controller.signal.aborted) setCustomer(data.customer);
        } else if (response.status !== 401 && !controller.signal.aborted) {
          setNotice(await detail(response));
        }
      })
      .catch(error => {
        if (!controller.signal.aborted) setNotice(connectionMessage(error));
      })
      .finally(() => { if (!controller.signal.aborted) setChecking(false); });
    return () => controller.abort();
  }, []);

  const logout = useCallback(async () => {
    try {
      const response = await backgroundFetch('/auth/logout', {
        method: 'POST', credentials: 'include', cache: 'no-store',
      });
      if (!response.ok) throw new Error(await detail(response));
      customerRef.current = null;
      setCustomer(null);
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
        const register = await backgroundFetch('/auth/register', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name.trim(), email: normalizedEmail, password }),
        });
        if (!register.ok) throw new Error(await detail(register));
      }
      const response = await backgroundFetch('/auth/login', {
        method: 'POST', credentials: 'include', cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: normalizedEmail, password }),
      });
      if (!response.ok) throw new Error(await detail(response));
      const data: { customer: Customer } = await response.json();
      setPassword('');
      setShowPassword(false);
      customerRef.current = data.customer;
      setCustomer(data.customer);
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
