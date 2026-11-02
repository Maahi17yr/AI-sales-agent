'use client';

import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, LoaderCircle, Search, ShoppingBag, UtensilsCrossed, X } from 'lucide-react';

export type Meal = {
  id: number; name: string; category: string; description: string;
  price: number;
};
export type CartLine = {
  product_id: number; name: string; variant?: string;
  quantity: number; unit_price: number; line_total: number;
};
export type CartSnapshot = {
  items: CartLine[]; subtotal: number; discount: number;
  discount_percent: number; discount_eligible: boolean; discount_message: string;
};
type DiscountPolicy = {
  enabled: boolean; min_order: number; max_percent: number;
  tiers: { minimum: number; percent: number }[];
};
type Props = {
  open: boolean; onClose: () => void; onOrder: (meal: string) => void;
  onViewCart: () => void; apiFetch: (path: string, options?: RequestInit) => Promise<Response>;
  refreshKey: number; disabled: boolean; checkoutTotal?: number;
};

const money = (amount: number) => new Intl.NumberFormat('en-NG', {
  style: 'currency', currency: 'NGN', maximumFractionDigits: 0,
}).format(amount);
const foodIcons: Record<string, string> = {
  'Main Meals': '🍛', Soups: '🥘', Swallows: '🍽️', Proteins: '🍗',
  Sides: '🍌', Drinks: '🥤', Snacks: '🥟',
};
const foodEmoji = (category: string) => foodIcons[category] || '🍲';

// Curated, individually researched Wikimedia Commons photographs for every
// food in the current Nigerian menu. File pages identify the photographer and
// Creative Commons licence. These are representative images, not actual stock.
const mealPhotoFiles: { match: RegExp; file: string }[] = [
  { match: /party.*jollof|jollof/, file: 'Party Nigerian jollof rice.jpg' },
  { match: /fried rice/, file: 'Fried rice with chicken and coslow.jpg' },
  { match: /white rice.*stew|rice and stew/, file: 'White rice and stew (Banga stew) (Nigerian dish).jpg' },
  { match: /ofada|ayamase/, file: 'OfadaRice with assorted meat and egg.png' },
  { match: /coconut rice/, file: 'COCONUT RICE AND SALAD.jpg' },
  { match: /beans.*plantain/, file: 'Beans and plantain (African good).jpg' },
  { match: /yam porridge|asaro/, file: 'Asaro Yam porridge.jpg' },
  { match: /ewa agoyin|ewa aganyin/, file: 'Ewa Aganyin.jpg' },
  { match: /moi.?moi/, file: 'Images of a local dish in Nigeria called Moi-Moi.jpg' },
  { match: /boiled yam.*egg|yam.*egg sauce/, file: 'Boiled Yam and Egg Sauce.jpg' },
  { match: /egusi/, file: 'Ẹgusi Soup.jpg' },
  { match: /efo riro/, file: 'Efo riro.jpg' },
  { match: /ogbono/, file: 'Ogbono Soup.jpg' },
  { match: /afang/, file: 'Freshly Cooked Afang Soup.jpg' },
  { match: /edikang|edikan/, file: 'Edikang ikong.jpg' },
  { match: /^eba$|^garri swallow$/, file: 'Eba Swallow.jpg' },
  { match: /pounded yam|iyan/, file: 'Pounded yam (Iyàn).jpg' },
  { match: /amala/, file: 'Amala;Swallow food.jpg' },
  { match: /semo|semovita/, file: 'Semo.jpg' },
  { match: /fried chicken/, file: 'Fried chicken in serving tray.jpg' },
  { match: /fried fish/, file: 'Fried fish ready.jpg' },
  { match: /assorted meat/, file: 'Assorted meat.jpg' },
  { match: /^beef$|^beef meat$/, file: 'Cooked beef meat.jpg' },
  { match: /ponmo|pomo|kpomo/, file: 'Cooked Ponmo with pepper and vegetables.jpg' },
  { match: /fried plantain|^dodo$/, file: 'Fried Plantain (Dòdò).jpg' },
  { match: /coleslaw|coslaw/, file: 'Coleslaw.jpg' },
  { match: /extra stew|^stew$/, file: 'TomatoStew.jpg' },
  { match: /boiled egg/, file: 'Boiled egg.jpg' },
  { match: /small chops/, file: 'SmallChops.jpg' },
  { match: /puff.?puff/, file: 'Puff-Puff.jpg' },
];
function photoForMeal(meal: Meal): { src: string; creditUrl: string } | null {
  const name = meal.name.toLowerCase().trim();
  const match = mealPhotoFiles.find(photo => photo.match.test(name));
  if (!match) return null;
  const file = encodeURIComponent(match.file.replace(/ /g, '_'));
  return {
    src: `https://commons.wikimedia.org/wiki/Special:FilePath/${file}?width=640`,
    creditUrl: `https://commons.wikimedia.org/wiki/File:${file}`,
  };
}

export default function FoodPanel({
  open, onClose, onOrder, onViewCart, apiFetch, refreshKey, disabled, checkoutTotal,
}: Props) {
  const [tab, setTab] = useState<'menu' | 'cart'>('menu');
  const [meals, setMeals] = useState<Meal[]>([]);
  const [cart, setCart] = useState<CartSnapshot | null>(null);
  const [discountPolicy, setDiscountPolicy] = useState<DiscountPolicy | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('All');

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const response = await apiFetch('/menu', { cache: 'no-store' });
        if (!response.ok) throw new Error('Menu unavailable');
        const data: { products: Meal[]; discount_policy: DiscountPolicy } = await response.json();
        if (!alive) return;
        setMeals(data.products);
        setDiscountPolicy(data.discount_policy);
        setError('');
      } catch {
        if (alive) setError('We could not load the menu. Please try again later.');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [apiFetch]);

  // Refresh the trusted server cart after each completed agent response.
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const response = await apiFetch('/cart', { cache: 'no-store' });
        if (!response.ok) return;
        const data: CartSnapshot = await response.json();
        if (alive) setCart(data);
      } catch { /* The conversation still works when cart status is unavailable. */ }
    })();
    return () => { alive = false; };
  }, [apiFetch, refreshKey]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  const categories = useMemo(() => ['All', ...new Set(meals.map(m => m.category))], [meals]);
  const filtered = useMemo(() => meals.filter(m =>
    (category === 'All' || m.category === category) &&
    (m.name + ' ' + m.description).toLowerCase().includes(search.toLowerCase().trim())
  ), [meals, category, search]);
  const itemCount = cart?.items.reduce((count, item) => count + item.quantity, 0) || 0;
  const rate = cart?.discount_percent ?? 0;
  const discount = cart?.discount ?? 0;

  return (
    <>
      {open && <button type="button" className="food-panel-backdrop" onClick={onClose} aria-label="Close menu and order summary" />}
      <aside id="food-menu-panel" className={`food-panel ${open ? 'food-panel-open' : ''}`} aria-label="Menu and order summary">
        <div className="food-panel-heading">
          <div><span className="food-panel-eyebrow">NAIJA BITES</span><h2>What’s cooking?</h2><p>Fresh flavours, made for you.</p></div>
          <button type="button" className="food-panel-close" onClick={onClose} aria-label="Close menu"><X size={20} /></button>
        </div>
        <div className="food-panel-tabs" role="tablist" aria-label="Food panel">
          <button type="button" role="tab" aria-selected={tab === 'menu'} className={tab === 'menu' ? 'active' : ''} onClick={() => setTab('menu')}><UtensilsCrossed size={16}/> Menu</button>
          <button type="button" role="tab" aria-selected={tab === 'cart'} className={tab === 'cart' ? 'active' : ''} onClick={() => setTab('cart')}><ShoppingBag size={16}/> My order {itemCount > 0 && <span className="cart-count">{itemCount}</span>}</button>
        </div>
        {tab === 'menu' ? (
          <div className="food-panel-content" role="tabpanel" aria-label="Menu">
            {discountPolicy?.enabled && discountPolicy.tiers.length > 0 && (
              <div className="promo-strip"><strong>Bulk savings</strong><span>{discountPolicy.tiers.map(tier => `${tier.percent}% from ${money(tier.minimum)}`).join(' · ')}. Discounts apply to eligible cart subtotals, not individual meals.</span></div>
            )}
            <label className="menu-search"><Search size={17} aria-hidden="true"/><span className="sr-only">Search meals</span><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Search meals…" /></label>
            <div className="menu-categories" aria-label="Food categories">
              {categories.map(item => <button key={item} type="button" aria-pressed={category === item} className={category === item ? 'active' : ''} onClick={() => setCategory(item)}>{item}</button>)}
            </div>
            {loading ? <div className="food-empty" role="status"><LoaderCircle className="spin" size={22}/> Loading today’s menu…</div> :
              error ? <p className="food-empty" role="alert">{error}</p> :
              filtered.length === 0 ? <p className="food-empty">No meals match that search.</p> :
              <div className="meal-grid">{filtered.map(meal => {
                const photo = photoForMeal(meal);
                return (
                <article className="meal-card" key={meal.id}>
                  <div className="meal-art" role="img" aria-label={photo ? `Representative photo of ${meal.name}` : `Illustration of ${meal.name}`}>
                    <span aria-hidden="true">{foodEmoji(meal.category)}</span>
                    {photo && <img className="meal-photo" src={photo.src} alt="" loading="lazy" decoding="async" onError={event => { event.currentTarget.style.display = 'none'; }} />}
                  </div>
                  {photo && <a className="meal-photo-credit" href={photo.creditUrl} target="_blank" rel="noopener noreferrer" aria-label={`View photographer and photo licence for ${meal.name}`}>Photo & licence</a>}
                  <div className="meal-card-body">
                    <span className="meal-category">{meal.category}</span>
                    <h3>{meal.name}</h3>
                    <p>{meal.description}</p>
                    <div className="meal-price"><strong>{money(meal.price)}</strong></div>
                    <button type="button" className="meal-add" disabled={disabled} onClick={() => { onOrder(meal.name); onClose(); }}>Ask to order <ArrowRight size={15}/></button>
                  </div>
                </article>
              );})}</div>
            }
          </div>
        ) : (
          <div className="food-panel-content" role="tabpanel" aria-label="Current order">
            <div className="order-status"><ShoppingBag size={19}/><span>Live order summary</span></div>
            {!cart || itemCount === 0 ? <div className="food-empty"><span className="empty-plate" aria-hidden="true">🍽️</span><strong>Your plate is waiting</strong><p>Choose a meal and tell our assistant what you’d like.</p><button type="button" className="panel-link" onClick={() => setTab('menu')}>Explore the menu <ArrowRight size={16}/></button></div> :
              <>
                <div className="order-lines">{cart.items.map((item, i) => (
                  <div className="order-line" key={`${item.product_id}-${item.variant || ''}-${i}`}>
                    <span className="order-line-icon" aria-hidden="true">🍽️</span>
                    <div><strong>{item.name}</strong><small>{item.variant || 'Standard'} · Qty {item.quantity}</small></div>
                    <span>{money(item.line_total)}</span>
                  </div>
                ))}</div>
                <div className="order-totals"><div><span>Food subtotal</span><span>{money(cart.subtotal)}</span></div>{discount > 0 && <div className="order-discount"><span>Bulk discount ({rate}%)</span><strong>−{money(discount)}</strong></div>}<div className="order-grand"><strong>Food total</strong><strong>{money(cart.subtotal - discount)}</strong></div>{discount === 0 && cart.discount_message && <p>{cart.discount_message}</p>}{checkoutTotal === undefined ? <p>Delivery is calculated when you confirm your address.</p> : <p>Full checkout total, including delivery: <strong>{money(checkoutTotal)}</strong></p>}</div>
                <button type="button" className="panel-primary" disabled={disabled} onClick={() => { onViewCart(); onClose(); }}>Continue in chat <ArrowRight size={17}/></button>
              </>
            }
          </div>
        )}
      </aside>
    </>
  );
}
