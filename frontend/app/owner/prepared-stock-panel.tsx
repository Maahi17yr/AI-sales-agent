'use client';

import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { useAuth } from '../auth-provider';

type MenuProduct = {
  id: number;
  name: string;
  variants: { variant: string; quantity: number }[];
};
type Unit = 'g' | 'ml' | 'piece';
type Action = 'add' | 'remove' | 'set';
type StockItem = {
  active: boolean; inventory_source: 'prepared' | 'legacy';
  product_id: number; product_name: string; variant: string; unit: Unit;
  quantity_units: number; serving_units: number; tolerance_units: number;
  extra_units: number; extra_tolerance_units: number; extra_price: number;
  safe_standard_servings: number; safe_extra_servings: number;
  checkout_enabled: boolean;
};
type AddonSource = 'legacy_ingredient' | 'self_extra' | 'prepared_other';
type Addon = { ingredient_id: number; name: string; extra_price: number; max_quantity: number; source: AddonSource; target_product_id: number | null; target_variant: string | null };
type AddonDraft = { source: AddonSource; target: string };
type StockMovement = {
  id: number; product_id: number; variant: string; product_name: string;
  order_id?: number | null;
  action: Action | 'order'; change_units: number; before_units: number;
  after_units: number; reason: string; created_at: string; actor_name: string;
};
type DraftConfig = {
  unit: Unit; serving_units: number; tolerance_units: number;
  extra_units: number; extra_tolerance_units: number; extra_price: number;
};
const initialDraft: DraftConfig = {
  unit: 'g', serving_units: 250, tolerance_units: 10,
  extra_units: 125, extra_tolerance_units: 10, extra_price: 700,
};
const labelUnit = (unit: Unit) => unit === 'piece' ? 'pieces' : unit === 'g' ? 'kg' : 'L';
const smallUnit = (unit: Unit) => unit === 'piece' ? 'pieces' : unit;
function displayStock(value: number, unit: Unit): string {
  const amount = unit === 'piece' ? value : value / 1000;
  return amount.toLocaleString('en-NG', { maximumFractionDigits: 3 }) + ' ' + labelUnit(unit);
}
function stockAmount(value: string, unit: Unit): number {
  const number = Number(value);
  const raw = number * (unit === 'piece' ? 1 : 1000);
  const rounded = Math.round(raw);
  if (!Number.isFinite(number) || value.trim() === '' || number < 0 ||
      !Number.isSafeInteger(rounded) || Math.abs(raw - rounded) > 0.000001 ||
      rounded > 1000000000) {
    throw new Error('Enter a valid non-negative quantity; use up to three decimal places.');
  }
  return rounded;
}
async function apiError(response: Response): Promise<string> {
  const body = await response.json().catch(() => null);
  return typeof body?.detail === 'string' ? body.detail : 'Request failed (' + response.status + ').';
}

export default function PreparedStockPanel({ products }: { products: MenuProduct[] }) {
  const { apiFetch } = useAuth();
  const [items, setItems] = useState<StockItem[]>([]);
  const [history, setHistory] = useState<StockMovement[]>([]);
  const [addons, setAddons] = useState<Addon[]>([]);
  const [addonDrafts, setAddonDrafts] = useState<Record<number, AddonDraft>>({});
  const [extraName, setExtraName] = useState('');
  const [selectedId, setSelectedId] = useState<number>(products[0]?.id || 0);
  const [selectedVariant, setSelectedVariant] = useState('standard');
  const [config, setConfig] = useState<DraftConfig>(initialDraft);
  const [action, setAction] = useState<Action>('add');
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [inventory, changes] = await Promise.all([
        apiFetch('/owner/prepared-stock'), apiFetch('/owner/prepared-stock/history'),
      ]);
      if (!inventory.ok) throw new Error(await apiError(inventory));
      if (!changes.ok) throw new Error(await apiError(changes));
      const stock: { items: StockItem[] } = await inventory.json();
      const movements: { movements: StockMovement[] } = await changes.json();
      setItems(stock.items);
      setHistory(movements.movements);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load prepared stock.');
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => { void refresh(); }, [refresh]);

  const refreshAddons = useCallback(async (productId: number) => {
    if (!productId) return;
    try {
      const response = await apiFetch('/owner/prepared-stock/addons/' + productId);
      if (!response.ok) throw new Error(await apiError(response));
      const data: { addons: Addon[] } = await response.json();
      setAddons(data.addons);
      const drafts: Record<number, AddonDraft> = {};
      for (const addon of data.addons) {
        drafts[addon.ingredient_id] = {
          source: addon.source,
          target: addon.target_product_id
            ? addon.target_product_id + ':' + addon.target_variant : '',
        };
      }
      setAddonDrafts(drafts);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load extras.');
    }
  }, [apiFetch]);
  useEffect(() => { void refreshAddons(selectedId); }, [refreshAddons, selectedId]);

  useEffect(() => {
    if (!selectedId && products.length) setSelectedId(products[0].id);
  }, [products, selectedId]);

  const product = products.find(p => p.id === selectedId);
  const variantChoices = product?.variants.map(v => v.variant) || [];
  const selected = items.find(i => i.product_id === selectedId && i.variant === selectedVariant);

  useEffect(() => {
    const found = items.find(i => i.product_id === selectedId && i.variant === selectedVariant);
    setConfig(found ? {
      unit: found.unit, serving_units: found.serving_units,
      tolerance_units: found.tolerance_units, extra_units: found.extra_units,
      extra_tolerance_units: found.extra_tolerance_units,
      extra_price: found.extra_price,
    } : initialDraft);
  }, [items, selectedId, selectedVariant]);

  function changeProduct(nextId: number) {
    setSelectedId(nextId);
    const next = products.find(p => p.id === nextId);
    setSelectedVariant(next?.variants[0]?.variant || 'standard');
    setNotice(''); setError('');
  }

  async function saveConfig(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setError(''); setNotice('');
    try {
      const response = await apiFetch('/owner/prepared-stock/config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: selectedId, variant: selectedVariant, ...config }),
      });
      if (!response.ok) throw new Error(await apiError(response));
      await refresh();
      setNotice(selected?.active
        ? 'Serving estimates updated for active prepared stock.'
        : 'Serving estimates saved. Activate this item to use prepared stock at checkout.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save serving estimates.');
    } finally { setBusy(false); }
  }

  async function togglePrepared() {
    if (!selected || busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const response = await apiFetch('/owner/prepared-stock/activate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          product_id: selected.product_id, variant: selected.variant,
          active: !selected.active,
        }),
      });
      if (!response.ok) throw new Error(await apiError(response));
      await refresh();
      setNotice('Stock source updated. All customer ordering will use the selected source.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not switch stock source.');
    } finally { setBusy(false); }
  }

  async function saveAddonMapping(addon: Addon) {
    const draft = addonDrafts[addon.ingredient_id];
    if (!draft || busy) return;
    let targetProductId: number | undefined;
    let targetVariant: string | undefined;
    if (draft.source === 'prepared_other') {
      const pair = draft.target.split(':');
      targetProductId = Number(pair[0]);
      targetVariant = pair[1];
      if (!targetProductId || !targetVariant) {
        setError('Choose an active prepared side before saving its mapping.');
        return;
      }
    }
    setBusy(true); setError(''); setNotice('');
    try {
      const response = await apiFetch('/owner/prepared-stock/addons', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          product_id: selectedId, ingredient_id: addon.ingredient_id,
          source: draft.source, target_product_id: targetProductId,
          target_variant: targetVariant,
        }),
      });
      if (!response.ok) throw new Error(await apiError(response));
      await refreshAddons(selectedId);
      setNotice('Extra stock source mapping saved.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not update extra mapping.');
    } finally { setBusy(false); }
  }

  async function addSamePotExtra(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || !selected.active || busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const response = await apiFetch('/owner/prepared-stock/self-extra', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          product_id: selectedId, display_name: extraName.trim(),
          max_quantity: 2,
        }),
      });
      if (!response.ok) throw new Error(await apiError(response));
      setExtraName('');
      await refreshAddons(selectedId);
      setNotice('Extra serving option created for this meal.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not add extra option.');
    } finally { setBusy(false); }
  }

  async function applyAdjustment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const response = await apiFetch('/owner/prepared-stock/adjust', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          product_id: selected.product_id, variant: selected.variant,
          action, quantity_units: stockAmount(amount, selected.unit), reason: reason.trim(),
        }),
      });
      if (!response.ok) throw new Error(await apiError(response));
      setAmount(''); setReason('');
      await refresh();
      setNotice('Stock adjustment saved with an audit record.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not adjust stock.');
    } finally { setBusy(false); }
  }

  return (
    <section aria-label="Prepared food inventory">
      <div className="owner-heading">
        <div>
          <span className="owner-eyebrow">KITCHEN / PREPARED FOOD</span>
          <h1>Prepared stock</h1>
          <p>Manage cooked food in practical kitchen units. Customer portions remain unchanged.</p>
        </div>
      </div>
      <p className="prepared-summary" role="note">
        <strong>Prepared-food inventory:</strong> Every menu variant starts on
        legacy stock. On activation, customer orders use conservative prepared
        portions. Kitchen weights and measures stay inside Owner Mode.
      </p>
      {notice && <p className="owner-message success" role="status">{notice}</p>}
      {error && <p className="owner-message error" role="alert">{error}</p>}
      {loading && <p className="owner-message">Loading prepared inventory…</p>}

      <div className="owner-panel">
        <div className="owner-section-title"><h2>Food on hand</h2><button type="button" onClick={() => void refresh()}>Refresh</button></div>
        {items.length ? (
          <div className="owner-table-scroll">
            <table className="owner-table prepared-table">
              <thead><tr><th>Food / variant</th><th>Prepared quantity</th><th>Conservative servings</th><th>Stock source</th><th>Action</th></tr></thead>
              <tbody>{items.map(item => (
                <tr key={item.product_id + ':' + item.variant} className={selectedId === item.product_id && selectedVariant === item.variant ? 'selected' : ''}>
                  <td><strong>{item.product_name}</strong><small>{item.variant}</small></td>
                  <td>{displayStock(item.quantity_units, item.unit)}</td>
                  <td>{item.safe_standard_servings} standard</td>
                  <td><span className={'owner-pill ' + (item.active ? 'green' : 'neutral')}>
                    {item.active ? 'Prepared — active' : 'Legacy — active'}
                  </span></td>
                  <td><button type="button" className="owner-small-btn ghost" onClick={() => {
                    setSelectedId(item.product_id); setSelectedVariant(item.variant);
                    setNotice(''); setError('');
                  }}>Manage</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <p className="owner-empty">No prepared-food stock configured yet. Choose a meal below to start.</p>}
      </div>

      <div className="owner-panel">
        <div className="owner-section-title"><h2>Serving estimates & extras</h2><span>Internal measurements only</span></div>
        <div className="prepared-grid">
          <label className="prepared-field">Menu item
            <select value={selectedId} onChange={e => changeProduct(Number(e.target.value))}>
              {products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <label className="prepared-field">Variant
            <select value={selectedVariant} onChange={e => setSelectedVariant(e.target.value)}>
              {variantChoices.map(variant => <option value={variant} key={variant}>{variant}</option>)}
            </select>
          </label>
        </div>
        <form onSubmit={saveConfig}>
          <div className="prepared-grid" style={{ marginTop: 16 }}>
            <label className="prepared-field">Kitchen stock unit
              <select value={config.unit} onChange={e => {
                const unit = e.target.value as Unit;
                setConfig(unit === 'piece' ? {
                  unit, serving_units: 1, tolerance_units: 0,
                  extra_units: 1, extra_tolerance_units: 0, extra_price: config.extra_price,
                } : { ...initialDraft, unit, extra_price: config.extra_price });
              }}>
                <option value="g">Weight (kg stocked / g per serving)</option>
                <option value="ml">Volume (L stocked / mL per serving)</option>
                <option value="piece">Count (whole pieces)</option>
              </select>
            </label>
            <label className="prepared-field">Standard serving target ({smallUnit(config.unit)})
              <input type="number" min="1" step="1" required disabled={config.unit === 'piece'} value={config.serving_units}
                onChange={e => setConfig({ ...config, serving_units: Number(e.target.value) })}/>
            </label>
            <label className="prepared-field">Standard variation ± ({smallUnit(config.unit)})
              <input type="number" min="0" step="1" required disabled={config.unit === 'piece'} value={config.tolerance_units}
                onChange={e => setConfig({ ...config, tolerance_units: Number(e.target.value) })}/>
            </label>
            <label className="prepared-field">Extra serving target ({smallUnit(config.unit)})
              <input type="number" min="1" step="1" required disabled={config.unit === 'piece'} value={config.extra_units}
                onChange={e => setConfig({ ...config, extra_units: Number(e.target.value) })}/>
            </label>
            <label className="prepared-field">Extra variation ± ({smallUnit(config.unit)})
              <input type="number" min="0" step="1" required disabled={config.unit === 'piece'} value={config.extra_tolerance_units}
                onChange={e => setConfig({ ...config, extra_tolerance_units: Number(e.target.value) })}/>
            </label>
            <label className="prepared-field">Extra serving price (₦)
              <input type="number" min="0" max="100000000" step="1" required value={config.extra_price}
                onChange={e => setConfig({ ...config, extra_price: Number(e.target.value) })}/>
              <small>This is a planned price; existing checkout extra pricing is unchanged.</small>
            </label>
          </div>
          <div className="prepared-actions"><button className="owner-small-btn" disabled={busy || !selectedId || !variantChoices.length} type="submit">{busy ? 'Saving…' : 'Save serving configuration'}</button></div>
        </form>
      </div>

      {selected && <div className="owner-panel">
        <div className="owner-section-title"><h2>Stock source activation</h2></div>
        <p>Current checkout source: <strong>{selected.active ? 'Prepared stock' : 'Legacy portion counts'}</strong>.
        The switch is blocked if unresolved orders rely on the source or an extra still uses it.</p>
        <div className="prepared-actions">
          <button className="owner-small-btn" type="button" disabled={busy}
            onClick={() => void togglePrepared()}>
            {selected.active ? 'Switch back to legacy' : 'Activate prepared stock'}
          </button>
        </div>
      </div>}

      {selected && <div className="owner-panel">
        <div className="owner-section-title"><h2>Adjust prepared stock</h2><span>{selected.product_name} · {selected.variant}</span></div>
        <div className="prepared-grid">
          <div className="prepared-stat"><span>Physical stock estimate</span><strong>{displayStock(selected.quantity_units, selected.unit)}</strong></div>
          <div className="prepared-stat"><span>Conservative standard portions</span><strong>{selected.safe_standard_servings}</strong></div>
        </div>
        <form onSubmit={applyAdjustment} style={{ marginTop: 18 }}>
          <div className="prepared-grid">
            <label className="prepared-field">Stock operation
              <select value={action} onChange={e => setAction(e.target.value as Action)}>
                <option value="add">Add cooked stock</option>
                <option value="remove">Remove / discard stock</option>
                <option value="set">Correct physical balance</option>
              </select>
            </label>
            <label className="prepared-field">Quantity ({labelUnit(selected.unit)})
              <input type="number" required min="0" step={selected.unit === 'piece' ? '1' : '0.001'}
                value={amount} onChange={e => setAmount(e.target.value)} placeholder={selected.unit === 'piece' ? '3' : '5.000'}/>
            </label>
          </div>
          <label className="prepared-field" style={{ marginTop: 16 }}>Reason for adjustment
            <input type="text" minLength={3} maxLength={240} required value={reason}
              onChange={e => setReason(e.target.value)} placeholder="e.g. Cooked 5 kg, waste, or physical stock count"/>
          </label>
          <div className="prepared-actions"><button className="owner-small-btn" type="submit" disabled={busy}>{busy ? 'Saving…' : 'Record adjustment'}</button></div>
        </form>
        <p className="prepared-muted">Every adjustment records who changed stock, when,
        the quantity and the reason. {selected.active
          ? 'Activated prepared stock directly controls new orders.'
          : 'Currently inactive; customer orders still use legacy portions.'}</p>
      </div>}

      <div className="owner-panel">
        <div className="owner-section-title"><h2>Extra servings and sides</h2></div>
        <p className="prepared-muted">Each extra uses one stock source. Same-pot extras use
        the meal's extra-serving estimate and its configured price. Separately
        prepared sides use their own stock and the existing add-on price.
        Unmapped extras keep their existing independent piece counts.</p>
        {selected?.active && selectedVariant === 'standard' && <form
          className="prepared-actions" onSubmit={addSamePotExtra}>
          <label className="prepared-field" style={{ flex: 1 }}>Create an extra serving choice
            <input value={extraName} onChange={e => setExtraName(e.target.value)}
              minLength={3} maxLength={80} required placeholder="e.g. Extra Rice"/>
          </label>
          <button className="owner-small-btn" type="submit" disabled={busy}>Create extra</button>
        </form>}
        {addons.length ? <div className="owner-table-scroll">
          <table className="owner-table">
            <thead><tr><th>Extra</th><th>Current source</th><th>Stock mapping</th><th>Save</th></tr></thead>
            <tbody>{addons.map(addon => {
              const draft = addonDrafts[addon.ingredient_id] || {
                source: addon.source, target: '' };
              const targets = items.filter(item => item.active &&
                !(item.product_id === selectedId && item.variant === 'standard'));
              return <tr key={addon.ingredient_id}>
                <td><strong>{addon.name}</strong><small>Per-meal maximum {addon.max_quantity}</small></td>
                <td>{addon.source.replaceAll('_', ' ')}</td>
                <td>
                  <select className="owner-search" value={draft.source} disabled={busy}
                    aria-label={'Stock source for ' + addon.name}
                    onChange={e => setAddonDrafts(prev => ({
                      ...prev, [addon.ingredient_id]: {
                        source: e.target.value as AddonSource,
                        target: prev[addon.ingredient_id]?.target || '',
                      },
                    }))}>
                    <option value="legacy_ingredient">Existing separate count</option>
                    <option value="self_extra" disabled={!selected?.active || selectedVariant !== 'standard'}>Same meal / extra serving</option>
                    <option value="prepared_other" disabled={!selected?.active || !targets.length}>Other prepared product</option>
                  </select>
                  {draft.source === 'prepared_other' &&
                    <select className="owner-search" value={draft.target} disabled={busy}
                      aria-label={'Prepared target for ' + addon.name}
                      onChange={e => setAddonDrafts(prev => ({
                        ...prev, [addon.ingredient_id]: {
                          ...draft, target: e.target.value,
                        },
                      }))}>
                      <option value="">Choose prepared side</option>
                      {targets.map(target => <option
                        key={target.product_id + ':' + target.variant}
                        value={target.product_id + ':' + target.variant}>
                        {target.product_name} · {target.variant}
                      </option>)}
                    </select>}
                </td>
                <td><button className="owner-small-btn ghost" type="button"
                  disabled={busy || (draft.source === 'prepared_other' && !draft.target)}
                  onClick={() => void saveAddonMapping(addon)}>Save</button></td>
              </tr>;
            })}</tbody>
          </table>
        </div> : <p className="owner-empty">No approved extras configured for this menu item.</p>}
      </div>

      <div className="owner-panel">
        <div className="owner-section-title"><h2>Adjustment and order history</h2><span>Latest 50 recorded changes</span></div>
        {history.length ? <div className="owner-table-scroll"><table className="owner-table">
          <thead><tr><th>Meal</th><th>Action</th><th>Change</th><th>Stock after</th><th>Reason / order</th><th>By</th></tr></thead>
          <tbody>{history.map(move => {
            const configItem = items.find(item => item.product_id === move.product_id && item.variant === move.variant);
            const unit = configItem?.unit || 'g';
            return <tr key={move.id}>
              <td><strong>{move.product_name}</strong><small>{move.created_at}</small></td>
              <td>{move.action}</td>
              <td>{displayStock(move.change_units, unit)}</td>
              <td>{displayStock(move.after_units, unit)}</td>
              <td>{move.reason}{move.order_id ? ' #' + move.order_id : ''}</td><td>{move.actor_name}</td>
            </tr>;
          })}</tbody>
        </table></div> : <p className="owner-empty">No adjustments recorded yet.</p>}
      </div>
    </section>
  );
}
