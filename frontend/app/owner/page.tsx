'use client';

import { useEffect, useState } from 'react';
import { useAuth } from '../auth-provider';

export default function OwnerHome() {
  const { customer, apiFetch, logout } = useAuth();
  const [status, setStatus] = useState<'checking' | 'ready' | 'error'>('checking');
  useEffect(() => {
    let active = true;
    apiFetch('/owner/me').then(response => {
      if (active) setStatus(response.ok ? 'ready' : 'error');
    }).catch(() => { if (active) setStatus('error'); });
    return () => { active = false; };
  }, [apiFetch]);
  return (
    <main className="auth-shell">
      <section className="auth-card" aria-label="Naija Bites owner area">
        <div className="auth-brand"><strong>Naija Bites · Owner's Mode</strong></div>
        <h1>Welcome, {customer.name}</h1>
        <p className="auth-subtitle">{status === 'checking' ? 'Verifying owner access…' : status === 'ready' ? 'Owner access verified. The management dashboard is coming in Phase 2.' : 'Unable to verify owner access. Please sign in again or try later.'}</p>
        <button type="button" className="auth-submit" onClick={() => void logout()}>Sign out</button>
      </section>
    </main>
  );
}
