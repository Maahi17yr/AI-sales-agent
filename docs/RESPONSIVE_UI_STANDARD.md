# Naija Bites responsive UI release standard

**Mandatory for every frontend change in Customer Mode and Owner Mode.** Do not merge or release a UI change solely because its desktop build succeeds.

## Viewports to verify in a real browser

- Small phone: 320 × 700
- Typical phone: 390 × 850
- Large phone: 430 × 932
- Tablet portrait: 768 × 1024
- Tablet landscape: 1024 × 768
- Laptop: 1366 × 850
- Large monitor: 1920 × 1080

## Acceptance criteria

1. Navigation, cards, tables, forms, modals, text and action buttons work at **each** size; drawer navigation includes all visible and planned entries without claiming that a placeholder feature works.
2. No accidental document-level horizontal overflow. Data tables may scroll inside their own labeled containers without clipping controls or requiring the entire page to scroll sideways.
3. At least 44 × 44 CSS-pixel touch targets for primary interactive controls on touch layouts; no unreadable or clipped text.
4. Usable focus order, visible keyboard focus, semantic button labels, dismissible dialogs and accessible responsive navigation.
5. Equivalent customer and owner functionality on phone, tablet portrait/landscape and desktop. No client-side cosmetic check may substitute for an authenticated interaction test when changed behavior involves accounts, stock, payment or orders.
6. Theme changes must style cards, forms, menus and overlays consistently, survive reload when storage is available, and not change or leak business data.
7. Execute automated browser checks against **isolated disposable test data**. Never use production records, payment traffic or live stock mutations to validate responsive layouts.
8. Capture representative screenshots and investigate any failed viewport before calling the change **READY FOR REVIEW**.

The isolated Chromium journey in `.github/workflows/browser-journey.yml` covers real Customer Mode behavior and Owner Mode checks from `scripts/test_browser_owner_redesign.py`. Extend those browser checks for every newly changed interface. Keep the PR unmerged and production unchanged without explicit release approval.
