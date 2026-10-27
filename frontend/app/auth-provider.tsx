'use client';

import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { ChefHat, LoaderCircle, ShieldCheck, UtensilsCrossed } from 'lucide-react';

const API = process.env.NEXT_PUBLIC_AGUI_URL || '/api/backend';

export type Customer = { id: number; name: string; email: string; phone?: string | null };

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
    const body: { detail?: string; message?: string } = await response.json();
    return body.detail || body.message || 'Request failed. Please try again.';
  } catch {
    return 'Request failed. Please try again.';
  }
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

  const apiFetch = useCallback(async (path: string, options: RequestInit = {}) => {
    const response = await fetch(`${API}${path}`, {
      ...options,
      credentials: 'include',
      cache: options.cache ?? 'no-store',
    });
    if (response.status === 401) {
      setCustomer(null);
      setNotice('Your session has expired. Please sign in again.');
    }
    return response;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API}/auth/me`, { credentials: 'include', cache: 'no-store', signal: controller.signal })
      .then(async response => {
        if (response.ok) {
          const data: { customer: Customer } = await response.json();
          if (!controller.signal.aborted) setCustomer(data.customer);
        } else if (response.status !== 401 && !controller.signal.aborted) {
          setNotice('Unable to verify your session. Please sign in.');
        }
      })
      .catch(error => {
        if (!controller.signal.aborted) setNotice(error instanceof Error ? error.message : 'Could not connect to the server.');
      })
      .finally(() => { if (!controller.signal.aborted) setChecking(false); });
    return () => controller.abort();
  }, []);

  const logout = useCallback(async () => {
    try {
      const response = await fetch(`${API}/auth/logout`, {
        method: 'POST', credentials: 'include', cache: 'no-store',
      });
      if (!response.ok) throw new Error(await detail(response));
      setCustomer(null);
      setNotice('You have signed out.');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Could not sign out.');
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
      setCustomer(data.customer);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Could not sign in.');
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
        <div className="auth-brand"><span className="identity-mark"><UtensilsCrossed size={23}/></span><div><strong>Naija Bites<span className="brand-dot">.</span></strong><small>Good food, good mood.</small></div></div>
        <div className="auth-icon"><ChefHat size={26}/></div>
        <h1>{mode === 'login' ? 'Welcome back' : 'Create your account'}</h1>
        <p className="auth-subtitle">{mode === 'login' ? 'Sign in to continue your food chat.' : 'Join Naija Bites and start ordering your favourites.'}</p>
        <form className="auth-form" onSubmit={submit}>
          {mode === 'register' && <label>Full name<input name="name" autoComplete="name" value={name} onChange={e => setName(e.target.value)} minLength={2} maxLength={120} required placeholder="Your name"/></label>}
          <label>Email address<input name="email" type="email" autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} required placeholder="you@example.com"/></label>
          <label>Password<input name="password" type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChange={e => setPassword(e.target.value)} required minLength={mode === 'register' ? 8 : undefined} placeholder="Enter your password"/></label>
          {notice && <p className="auth-notice" role="alert">{notice}</p>}
          <button className="auth-submit" type="submit" disabled={submitting}>{submitting ? <LoaderCircle className="spin" size={18}/> : <ShieldCheck size={18}/>} {submitting ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}</button>
        </form>
        <p className="auth-switch">{mode === 'login' ? 'New to Naija Bites?' : 'Already have an account?'} <button type="button" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setNotice(''); setPassword(''); }} disabled={submitting}>{mode === 'login' ? 'Create an account' : 'Sign in'}</button></p>
      </section>
    </main>
  );

  return <AuthContext.Provider value={{ customer, apiFetch, logout }}>{children}</AuthContext.Provider>;
}
