# Payment and Order Policy
> Fictional development policy. Trusted backend state always overrides this document.

## Cart and checkout
Customers select items from the live catalogue and review trusted cart totals, delivery fees and discounts. Preparing a checkout summary does not create an order.

## Explicit confirmation
Only a trusted application confirmation action may create an order. The AI can prepare a checkout and explain the steps, but cannot confirm on behalf of the customer or claim success without a backend result.

## Payment status
A new order may be pending_payment until payment is verified. Other possible statuses include paid, preparing, dispatched, delivered and cancelled, but only the statuses implemented by the backend may be used as facts.

## Unpaid order expiry
Fictional business rule: an unpaid order may expire after 30 minutes. This is a proposed rule, not active automation. Until a backend expiry job and inventory-release logic exist, never claim that an order automatically expired or that reserved stock was released.

## Payment security
Do not request card PINs, passwords, CVVs or OTPs in chat. The development application uses simulated payments; never describe a simulated payment as a real charge.

## Cancellation
Customers may request cancellation before preparation. During preparation or after dispatch, cancellation is subject to review, business approval and applicable rights. Only the order service can confirm cancellation.
