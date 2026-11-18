# Prepared-food inventory — Phase 2 implementation

**Status:** development-only draft PR #4. Do not merge or sync to the original
production repository without explicit approval. Tests use disposable SQLite /
PostgreSQL and browser environments, never the production Neon database.

## Business model

- Customers order meals and named extras, not grams, kg, mL or tolerances.
- Owner stores cooked food in integer grams, millilitres or whole pieces.
- Owner configures target serving, allowable ± variation, optional extra-serving
  target/variation and NGN selling price. Physical stock can be added, removed
  or corrected with reasons and a per-account audit record.
- Conservative allocation from each stock source:
  `standard_count × (standard_units + tolerance_units) +
  extra_count × (extra_units + extra_tolerance_units)`.
- Example: 2 Jollof Rice standards at 250 ± 10 g and exactly one extra serving
  at 125 ± 10 g => `2 × 260 + 135 = 655 g`; 5,000 g becomes 4,345 g.
  Extras are **per cart line/per meal**. For 2 meals and only 1 extra total,
  add two distinct cart lines, one standard and one customized.

## Authoritative sources

Each `inventory(product_id,variant)` is legacy by default.
A configured `prepared_stock` row is inactive until the owner explicitly
activates it. Once active, customer menu/cart/checkout use exclusively
`prepared_stock.quantity_units` plus its serving estimates for that product
and variant. Unconfigured and inactive variants remain on legacy
`inventory.quantity`. No dual deductions or automatic import of legacy
counts into prepared physical units.

## Add-ons and pricing

An existing `meal_addons(product_id,ingredient_id)` has exactly one source:

1. No explicit mapping: legacy `ingredients.stock_quantity` in whole counts,
   priced using `meal_addons.extra_price`.
2. `self_extra`: additional serving from the same activated product/standard
   variant. Uses configured `extra_units + extra_tolerance_units` and
   `prepared_stock.extra_price`. Does not deduct `ingredients`.
3. `prepared_other`: separate activated product/variant (e.g. fried chicken
   in pieces). Uses its standard + tolerance; pricing remains the existing
   `meal_addons.extra_price`. Does not deduct the side's legacy inventory or
   the ingredients ledger.

A customer-friendly extra such as "Extra Rice" can be created by an owner
without raw ingredient accounting; the existing ingredient/addon IDs are only
used as identifiers for the order customization. The stock source is explicit.

The owner's mapping interface can change the source with authorization checks,
but no source changes are accepted for unresolved placed orders. Deactivation
also blocks any add-on mapping that still relies on that prepared stock.

## Checkout lifecycle and concurrency

No change in payment lifecycle. The existing application-confirmed checkout
creates the order, items and stock allocations in one transaction at the
`pending_payment` stage, **before Paystack verification**. Paystack success
only marks the existing order paid/confirmed; it never deducts again.

`OrderService._commit_approved_checkout`:
- Locks the authenticated customer's checkout session, as before.
- Locks base product rows for stock source/mapping consistency on PostgreSQL.
- Re-prices and revalidates all cart lines, including shared-pot extra demand.
- Uses deterministic prepared-stock row-level locking (`FOR UPDATE`) and
  conditional SQL `quantity_units >= allocation`; SQLite uses BEGIN IMMEDIATE.
- Inserts order, order items, legacy and prepared stock allocations, and
  audit entries within the **same commit**. An exception rolls them all back.
- Logs a unique row per `(order_id,product_id,variant)` in
  `prepared_order_deductions`. Replaying the same approved checkout token
  returns the previous durable order and does not deduct again.

Owner adjustments lock the same prepared rows and use compare-and-set updates.
Inventory never becomes negative even during concurrent ordering and correction.

## Customers and Owner Mode

Customers see only product names, prices, variant labels and familiar extras,
with public-safe quantity as number of portions where necessary.
No prepared physical unit, tolerance, or internal stock source is part of
customer API payloads, AI product tools, cart, order review or confirmation.

Owner Menu & Pricing shows the source beside effective available portions.
Prepared Stock shows source activation, safe capacity, physical corrections,
extra mappings, and combined owner/checkout audit history. Activation is
blocked when related orders are in `pending_payment`, `confirmed`,
`preparing` or `out_for_delivery`, preserving historical order records.

## Merge boundary and concurrency safety

The development repository's automatic production-repository proposal workflow
has a literal false guard on its write-capable job. A development-main merge
therefore cannot propose or push to the original production repository.
Re-enabling requires a separate explicitly approved change.

On PostgreSQL, checkout locks base product rows, then all prepared rows
needed by the cart (including separately prepared add-ons), **before**
calculating serving sizes, price or stock demand. Owner configuration locks
the same product row before changing a prepared row. This prevents
reconfiguration races that could deduct using stale serving estimates.

## Validation and operator notes

- Backend: `scripts.test_prepared_stock`, `scripts.test_prepared_checkout`
  plus all preexisting checkout/auth/customization regression modules.
- PostgreSQL CI: `scripts.test_prepared_checkout_postgres` tests row locks,
  last-serving competition, rollback and idempotency.
- Chromium CI: legacy customer journey and new
  `scripts.test_browser_prepared_stock` through the Next.js proxy with
  disposable local database and mocked providers.
- Real production data, multi-business tenancy, raw ingredient purchasing,
  formal accounting, automatic expiry/restocking of abandoned unpaid placed
  orders, and real Paystack network traffic are explicitly outside this phase.
  Unpaid placed orders preserve their original stock reservation; restoring
  them requires a separate audited cancellation/reconciliation design.
