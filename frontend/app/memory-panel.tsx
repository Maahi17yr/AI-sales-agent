'use client';

import { useCallback, useEffect, useState } from 'react';
import { Brain, Check, LoaderCircle, Trash2, X } from 'lucide-react';

type Proposal = { key: string; value: string; request_id: string };
type Props = {
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>;
  refreshKey: number;
  disabled: boolean;
};

const LABELS: Record<string, string> = {
  spice_level: 'Spice level',
  favorite_food: 'Favourite food',
  disliked_food: 'Disliked food',
  dietary_preference: 'Dietary preference',
  food_allergy: 'Reported food allergy',
  other_food_preference: 'Other food preference',
};
const label = (key: string) => LABELS[key] || 'Food preference';

export default function MemoryPanel({ apiFetch, refreshKey, disabled }: Props) {
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [saved, setSaved] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');

  const load = useCallback(async (signal?: AbortSignal) => {
    const [pending, current] = await Promise.all([
      apiFetch('/memory/pending', { signal, cache: 'no-store' }),
      apiFetch('/memory/preferences', { signal, cache: 'no-store' }),
    ]);
    if (!pending.ok || !current.ok) {
      throw new Error('Could not load saved food preferences.');
    }
    const pendingData: { pending: boolean; proposal: Proposal | null } = await pending.json();
    const currentData: { preferences: Record<string, string> } = await current.json();
    if (signal?.aborted) return;
    setProposal(pendingData.pending ? pendingData.proposal : null);
    setSaved(currentData.preferences || {});
  }, [apiFetch]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal).catch(error => {
      if (!controller.signal.aborted) {
        console.error('Food preference refresh failed:', error);
      }
    });
    return () => controller.abort();
  }, [load, refreshKey]);

  async function decide(approved: boolean) {
    if (!proposal || disabled || saving) return;
    const requestId = proposal.request_id;
    setSaving(true);
    setNotice('');
    try {
      const response = await apiFetch('/memory/decision', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: requestId, approved }),
      });
      if (!response.ok) throw new Error('Could not update this preference. Try again.');
      setProposal(null);
      await load();
      setNotice(approved ? 'Food preference saved.' : 'Preference not saved.');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Could not update preference.');
    } finally {
      setSaving(false);
    }
  }

  async function forget(key: string) {
    if (disabled || saving) return;
    setSaving(true);
    setNotice('');
    try {
      const response = await apiFetch('/memory/preferences/' + encodeURIComponent(key), {
        method: 'DELETE',
      });
      if (!response.ok) throw new Error('Could not remove this preference.');
      await load();
      setNotice('Saved preference removed.');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Could not remove preference.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="food-memory-area" aria-label="Food preferences">
      {proposal && (
        <div className="food-memory-review" role="group" aria-label="Review food preference">
          <div className="food-memory-heading"><Brain size={19} aria-hidden="true"/><strong>Remember this about your food?</strong></div>
          <p>Save this only if you want Naija Bites to remember it in future chats.</p>
          <div className="food-memory-detail"><span>{label(proposal.key)}</span><strong>{proposal.value}</strong></div>
          <div className="food-memory-actions">
            <button type="button" className="secondary-action" disabled={saving || disabled} onClick={() => void decide(false)}>
              <X size={16}/> Don't save
            </button>
            <button type="button" className="primary-action" disabled={saving || disabled} onClick={() => void decide(true)}>
              {saving ? <LoaderCircle size={16} className="spin"/> : <Check size={16}/>} Save preference
            </button>
          </div>
        </div>
      )}
      <button type="button" className="food-memory-toggle" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        <Brain size={15} aria-hidden="true"/> {expanded ? 'Hide food preferences' : 'Manage saved food preferences'}
      </button>
      {expanded && (
        <div className="food-memory-saved">
          {Object.entries(saved).length === 0 ? (
            <p>No food preferences saved yet. Ask Naija Bites to remember one, then approve it here.</p>
          ) : Object.entries(saved).map(([key, value]) => (
            <div className="food-memory-row" key={key}>
              <div><small>{label(key)}</small><strong>{value}</strong></div>
              <button type="button" disabled={disabled || saving} onClick={() => void forget(key)} aria-label={'Forget ' + label(key)} title={'Forget ' + label(key)}>
                <Trash2 size={16}/>
              </button>
            </div>
          ))}
        </div>
      )}
      {notice && <p className="food-memory-notice" role="status">{notice}</p>}
    </section>
  );
}
