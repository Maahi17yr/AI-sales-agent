'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  Bell, ChartNoAxesCombined, ChefHat, ChevronRight, CircleAlert,
  ClipboardList, CreditCard, LayoutDashboard, LogOut, Megaphone,
  MessageSquareText, RefreshCw, Settings2, ShieldCheck, Users, UtensilsCrossed,
} from 'lucide-react';
import { useAuth } from '../auth-provider';
import './owner.css';

type Tab = 'overview' | 'orders' | 'menu' | 'customers';
type Overview = {
  order_count: number; pending_orders: number; confirmed_orders: number;
  paid_revenue: number; paid_revenue_today: number; currency: string;
  popular_meals: { name: string; quantity: number }[];
  low_stock: { product_id: number; name: string; variant: string; quantity: number }[];
  sales_trend: { date: string; revenue: number }[];
};
type Order = {
  id: number; customer_id: number; customer_name: string | null;
  status: string; payment_status: string; total: number;
  created_at: string; delivery_area: string; delivery_address: string;
};
type Product = {
  id: number; name: string; category: string; description: string | null;
  price: number; available: boolean; variants: { variant: string; quantity: number }[];
};
type Customer = {
  id: number; name: string | null; email: string | null; phone: string | null;
  created_at: string; order_count: number; paid_spend: number;
};

const menuItems: { id: Tab; title: string; icon: typeof LayoutDashboard }[] = [
  { id: 'overview', title: 'Overview', icon: LayoutDashboard },
  { id: 'orders', title: 'Orders', icon: ClipboardList },
  { id: 'menu', title: 'Menu & stock', icon: UtensilsCrossed },
  { id: 'customers', title: 'Customers', icon: Users },
];
const upcoming = [
  { title: 'Payments', icon: CreditCard },
  { title: 'Owner AI', icon: MessageSquareText },
  { title: 'Conversations', icon: MessageSquareText },
  { title: 'Promotions', icon: Megaphone },
  { title: 'Settings', icon: Settings2 },
];
const nextStatus: Record<string, string | undefined> = {
  confirmed: 'preparing',
  preparing: 'out_for_delivery', out_for_delivery: 'delivered',
};
const money = (amount: number) => '₦' + Number(amount || 0).toLocaleString('en-NG');
const human = (text: string) => text.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());

export default function OwnerHome() {
  const { customer, apiFetch, logout } = useAuth();
  const [tab, setTab] = useState<Tab>('overview');
  const [overview, setOverview] = useState<Overview | null>(null);
  const [orders, setOrders] = useState<Order[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [showAlerts, setShowAlerts] = useState(false);
  const [editProduct, setEditProduct] = useState<number | null>(null);
  const [draftPrice, setDraftPrice] = useState('');
  const [draftAvailable, setDraftAvailable] = useState(true);
  const [search, setSearch] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const paths = ['/owner/overview', '/owner/orders', '/owner/menu', '/owner/customers'];
      const responses = await Promise.all(paths.map(path => apiFetch(path)));
      for (const response of responses) {
        if (!response.ok) {
          let detail = 'Could not load owner dashboard.';
          try { const body = await response.json(); if (typeof body.detail === 'string') detail = body.detail; } catch { /* empty error */ }
          throw new Error(detail);
        }
      }
      const [summary, orderData, menuData, customerData] = await Promise.all(responses.map(r => r.json()));
      setOverview(summary);
      setOrders(orderData.orders);
      setProducts(menuData.products);
      setCustomers(customerData.customers);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load dashboard.');
    } finally { setLoading(false); }
  }, [apiFetch]);

  useEffect(() => { void load(); }, [load]);

  async function changeOrder(order: Order) {
    const next = nextStatus[order.status];
    if (!next || busy) return;
    if (!window.confirm(`Change order #${order.id} from ${human(order.status)} to ${human(next)}?`)) return;
    setBusy(true); setNotice(''); setError('');
    try {
      const response = await apiFetch(`/owner/orders/${order.id}/status`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: next }),
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || 'Unable to update order.');
      }
      setNotice(`Order #${order.id} updated.`);
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Order update failed.'); }
    finally { setBusy(false); }
  }

  async function saveProduct(product: Product) {
    const price = Number(draftPrice);
    if (!Number.isSafeInteger(price) || price < 0 || draftPrice.trim() === '') {
      setError('Enter a valid non-negative price in naira.'); return;
    }
    if (!window.confirm(`Save changes to ${product.name}?`)) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const response = await apiFetch(`/owner/menu/${product.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ price, available: draftAvailable }),
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || 'Unable to update product.');
      }
      setNotice(`${product.name} updated.`);
      setEditProduct(null);
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Product update failed.'); }
    finally { setBusy(false); }
  }

  const alerts = overview ? [
    ...(overview.pending_orders > 0 ? [`${overview.pending_orders} order(s) awaiting payment`] : []),
    ...(overview.confirmed_orders > 0 ? [`${overview.confirmed_orders} paid order(s) ready to prepare`] : []),
    ...overview.low_stock.map(item => `Low stock: ${item.name} (${item.variant}) — ${item.quantity} left`),
  ] : [];
  const maxSales = Math.max(1, ...(overview?.sales_trend.map(day => day.revenue) || [1]));
  const filter = search.trim().toLowerCase();

  return (
    <main className="owner-shell">
      <aside className="owner-sidebar">
        <div className="owner-logo"><span className="owner-logo-mark"><ChefHat size={24}/></span><div><strong>Naija Bites<span className="brand-dot">.</span></strong><small>OWNER'S WORKSPACE</small></div></div>
        <div className="owner-nav-caption">MANAGE</div>
        <nav aria-label="Owner navigation">
          {menuItems.map(item => {
            const Icon = item.icon;
            return <button key={item.id} className={tab === item.id ? 'owner-nav active' : 'owner-nav'} onClick={() => { setTab(item.id); setSearch(''); }}><Icon size={19}/>{item.title}{tab === item.id && <ChevronRight size={15} className="owner-nav-end"/>}</button>;
          })}
          <div className="owner-nav-caption owner-nav-caption-spaced">COMING NEXT</div>
          {upcoming.map(item => {
            const Icon = item.icon;
            return <div className="owner-nav disabled" key={item.title} aria-disabled="true"><Icon size={19}/>{item.title}<small>Soon</small></div>;
          })}
        </nav>
        <div className="owner-sidebar-footer"><span className="owner-avatar">{customer.name?.charAt(0)?.toUpperCase() || 'O'}</span><div><strong>{customer.name}</strong><small>Owner account</small></div><button onClick={() => void logout()} aria-label="Sign out" title="Sign out"><LogOut size={19}/></button></div>
      </aside>

      <section className="owner-main">
        <header className="owner-topbar">
          <div className="owner-topbar-title"><span className="owner-topbar-kicker">NAIJA BITES / OWNER'S MODE</span><strong>{menuItems.find(item => item.id === tab)?.title}</strong></div>
          <div className="owner-topbar-actions">
            <button className="owner-icon-btn" onClick={() => void load()} disabled={loading} title="Refresh dashboard" aria-label="Refresh dashboard"><RefreshCw size={19}/></button>
            <button className="owner-icon-btn owner-alert-trigger" onClick={() => setShowAlerts(!showAlerts)} aria-label="Toggle notifications" aria-expanded={showAlerts}><Bell size={19}/>{alerts.length > 0 && <span>{alerts.length}</span>}</button>
          </div>
        </header>
        <div className="owner-content">
          {showAlerts && <section className="owner-alerts" aria-label="In-app notifications"><div className="owner-section-title"><h2>Notifications</h2><button onClick={() => setShowAlerts(false)}>Close</button></div>{alerts.length ? alerts.map((alert, i) => <p key={i}><CircleAlert size={16}/>{alert}</p>) : <p>You're all caught up.</p>}</section>}
          {notice && <p className="owner-message success" role="status">{notice}</p>}
          {error && <p className="owner-message error" role="alert">{error}</p>}
          {loading && <p className="owner-message">Loading current business records…</p>}

          {!loading && tab === 'overview' && overview && <>
            <div className="owner-heading"><div><span className="owner-eyebrow">YOUR BUSINESS AT A GLANCE</span><h1>Welcome back, {customer.name?.split(' ')[0] || 'Owner'}.</h1><p>Here's what's happening at Naija Bites.</p></div><span className="owner-live"><ShieldCheck size={16}/> Live database</span></div>
            <div className="owner-kpi-grid">
              <article className="owner-kpi"><span>Total verified revenue</span><strong>{money(overview.paid_revenue)}</strong><small>All-time paid transactions</small></article>
              <article className="owner-kpi"><span>Paid today</span><strong>{money(overview.paid_revenue_today)}</strong><small>Verified payments today (UTC)</small></article>
              <article className="owner-kpi"><span>Total orders</span><strong>{overview.order_count.toLocaleString()}</strong><small>All order statuses</small></article>
              <article className="owner-kpi"><span>Ready to prepare</span><strong>{overview.confirmed_orders}</strong><small>Confirmed orders</small></article>
            </div>
            <div className="owner-two-columns">
              <article className="owner-panel"><div className="owner-section-title"><div><h2>Sales this week</h2><p>Verified paid transactions · UTC</p></div><ChartNoAxesCombined size={20}/></div>
                <div className="owner-chart" role="img" aria-label="Last seven days of verified payment revenue">{overview.sales_trend.map(day => <div className="owner-chart-day" key={day.date}><div className="owner-chart-bar-area"><div className="owner-chart-bar" style={{ height: `${Math.max(3, (day.revenue / maxSales) * 100)}%` }} title={`${day.date}: ${money(day.revenue)}`}/></div><small>{day.date.slice(5)}</small></div>)}</div>
              </article>
              <article className="owner-panel"><div className="owner-section-title"><div><h2>Popular meals</h2><p>By portions on paid orders</p></div><UtensilsCrossed size={20}/></div>
                {overview.popular_meals.length ? overview.popular_meals.map((meal, i) => <div className="owner-popular-row" key={meal.name}><span className="owner-rank">{i + 1}</span><strong>{meal.name}</strong><span>{meal.quantity} sold</span></div>) : <p className="owner-empty">No verified sales yet.</p>}
              </article>
            </div>
            <article className="owner-panel owner-quick"><div className="owner-section-title"><div><h2>Needs attention</h2><p>Operational alerts based on current records</p></div><Bell size={20}/></div>
              {alerts.length ? alerts.slice(0, 5).map((alert, i) => <p key={i}><CircleAlert size={17}/>{alert}</p>) : <p>No outstanding alerts.</p>}
            </article>
          </>}

          {!loading && tab === 'orders' && <>
            <div className="owner-heading"><div><span className="owner-eyebrow">FULFILMENT</span><h1>Orders</h1><p>Latest 50 orders. Advance only eligible, verified orders.</p></div></div>
            <div className="owner-panel"><div className="owner-section-title"><h2>Recent orders</h2><span>{orders.length} shown</span></div>
              <div className="owner-table-scroll"><table className="owner-table"><thead><tr><th>Order</th><th>Customer</th><th>Total</th><th>Payment</th><th>Order status</th><th>Action</th></tr></thead><tbody>
                {orders.map(order => <tr key={order.id}><td><strong>#{order.id}</strong><small>{order.created_at}</small></td><td>{order.customer_name || 'Customer'}<small>{order.delivery_area}</small></td><td>{money(order.total)}</td><td><span className={'owner-pill ' + (order.payment_status === 'paid' ? 'green' : 'neutral')}>{human(order.payment_status)}</span></td><td><span className="owner-pill neutral">{human(order.status)}</span></td><td>{nextStatus[order.status] && order.payment_status === 'paid' ? <button className="owner-small-btn" disabled={busy} onClick={() => void changeOrder(order)}>{human(nextStatus[order.status]!)}</button> : <span className="owner-muted">—</span>}</td></tr>)}
              </tbody></table></div>{!orders.length && <p className="owner-empty">No orders yet.</p>}
            </div>
          </>}

          {!loading && tab === 'menu' && <>
            <div className="owner-heading"><div><span className="owner-eyebrow">YOUR CATALOGUE</span><h1>Menu & stock</h1><p>Manage product prices and availability. Stock is shown for reference.</p></div></div>
            <div className="owner-panel"><div className="owner-section-title"><h2>Products</h2><input className="owner-search" placeholder="Search meals…" aria-label="Search meals" value={search} onChange={e => setSearch(e.target.value)}/></div>
              <div className="owner-table-scroll"><table className="owner-table"><thead><tr><th>Meal</th><th>Price</th><th>Stock</th><th>Available</th><th>Action</th></tr></thead><tbody>
                {products.filter(p => !filter || (p.name + ' ' + p.category).toLowerCase().includes(filter)).map(product => <tr key={product.id}><td><strong>{product.name}</strong><small>{product.category}</small></td><td>{editProduct === product.id ? <input className="owner-number" type="number" min="0" max="100000000" value={draftPrice} onChange={e => setDraftPrice(e.target.value)} aria-label={`Price for ${product.name}`}/> : money(product.price)}</td><td>{product.variants.length ? product.variants.map(v => `${v.variant}: ${v.quantity}`).join(', ') : 'No stock entries'}</td><td>{editProduct === product.id ? <label className="owner-check"><input type="checkbox" checked={draftAvailable} onChange={e => setDraftAvailable(e.target.checked)}/> Available</label> : <span className={'owner-pill ' + (product.available ? 'green' : 'neutral')}>{product.available ? 'Available' : 'Hidden'}</span>}</td><td>{editProduct === product.id ? <div className="owner-row-actions"><button className="owner-small-btn" disabled={busy} onClick={() => void saveProduct(product)}>Save</button><button className="owner-small-btn ghost" disabled={busy} onClick={() => setEditProduct(null)}>Cancel</button></div> : <button className="owner-small-btn" disabled={busy} onClick={() => { setEditProduct(product.id); setDraftPrice(String(product.price)); setDraftAvailable(product.available); }}>Edit</button>}</td></tr>)}
              </tbody></table></div>{!products.length && <p className="owner-empty">No products yet.</p>}
            </div>
          </>}

          {!loading && tab === 'customers' && <>
            <div className="owner-heading"><div><span className="owner-eyebrow">YOUR COMMUNITY</span><h1>Customers</h1><p>Latest 100 customer accounts and their verified purchase totals.</p></div></div>
            <div className="owner-panel"><div className="owner-section-title"><h2>Customer records</h2><input className="owner-search" placeholder="Search customers…" aria-label="Search customers" value={search} onChange={e => setSearch(e.target.value)}/></div>
              <div className="owner-table-scroll"><table className="owner-table"><thead><tr><th>Customer</th><th>Contact</th><th>Orders</th><th>Verified spend</th><th>Joined</th></tr></thead><tbody>
                {customers.filter(c => !filter || (String(c.name || '') + ' ' + String(c.email || '')).toLowerCase().includes(filter)).map(c => <tr key={c.id}><td><strong>{c.name || 'Unnamed customer'}</strong><small>Customer #{c.id}</small></td><td>{c.email || '—'}<small>{c.phone || ''}</small></td><td>{c.order_count}</td><td>{money(c.paid_spend)}</td><td>{c.created_at.slice(0, 10)}</td></tr>)}
              </tbody></table></div>{!customers.length && <p className="owner-empty">No customer accounts yet.</p>}
            </div>
          </>}
          <footer className="owner-footer">Naija Bites Owner's Mode · Phase 2 · Business data is access-controlled by FastAPI.</footer>
        </div>
      </section>
    </main>
  );
}
