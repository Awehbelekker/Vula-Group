# Visual baseline + accessibility checks

Playwright screenshots of the dashboard's key screens, rendered against a **mock API** so they
never need a backend and never contain real data.

## Run

```bash
cd vula_dashboard
UPDATE_BASELINE=1 npx playwright test baseline   # (re)write tests/visual/baseline/*.png (48 files)
npx playwright test baseline                     # same capture into tests/visual/.results/ — baseline untouched
npx playwright test a11y                         # axe + horizontal-scroll report → baseline/*.json
MOCK_DEBUG=1 npx playwright test baseline        # also log API paths the mock doesn't know
```

`playwright.config.js` starts Vite on port 4179 with `VITE_API_URL=http://api.vula.test` and
`VITE_SUPABASE_URL=http://supabase.vula.test`. Chromium comes from `PLAYWRIGHT_BROWSERS_PATH`;
don't run `playwright install`.

## What gets captured

- **baseline.spec.js**: 8 screens (home, inbox, customers, orders, invoices, documents,
  settings, master) × mobile 390×844 / tablet 834×1112 / desktop 1440×900 × light / dark.
  Full-page PNGs named `baseline/{screen}-{viewport}-{theme}.png`. A test fails if the page
  throws any `pageerror`.
- **a11y.spec.js**: axe-core violations per screen at desktop/light →
  `baseline/axe-baseline.json`, and whether the page scrolls sideways at 390px →
  `baseline/hscroll-baseline.json`. It reports and doesn't fail on existing violations.

Time is frozen at Sat 17 Oct 2026 10:30 SAST (`page.clock.setFixedTime`), the service worker
is blocked, and every request that isn't localhost or `*.vula.test` is aborted. That includes
Google Fonts and OpenStreetMap tiles, so screenshots use system fallback fonts and an empty map.
This is deliberate, so that runs stay offline and deterministic.

## Files

- `mockApi.js` has `installMockApi(page, {role})`, which routes `/vula\.test/`, plus
  `seedSession()`, which logs in through the persisted `vula-auth` store and sets
  `vula-theme-mode`. It doesn't log in through Supabase. Unknown GETs answer `{}`.
- `fixtures/demo.js` holds the seeded data. Each export notes the FastAPI handler whose
  response shape it copies. When a screen starts calling a new endpoint, read the component
  and the handler in `vula_mind/vula/api/`, then add a matching route and fixture.
- `screens.js` holds the screen list and the shared navigate-and-settle helper.

## Rule: no production data

Fixtures must be obviously fictional:

- The tenant is "Harbour & Vine Deli" (`demo-shop`).
- Phones use `+27 82 000 0xxx`.
- Emails use `example.test`.
- Money is in integer cents.

Never paste real tenant rows, names (customers, staff or businesses), phone numbers or
documents into these fixtures or screenshots.

## The redesign (Phase B onwards)

`UI_V2=1` runs the same screens with the redesign switched on: a Vite build with
`VITE_UI_V2=true` on port 4180, and the mocked tenant's `ui_v2` set (master previews it with the
`vula.ui` override). Output goes to `.results/v2/` and never touches `baseline/`.

```
UI_V2=1 npx playwright test baseline   # screenshots → .results/v2/
UI_V2=1 npx playwright test a11y       # axe + horizontal scroll → .results/v2/
```

Playwright empties `.results/` at the start of every run, so copy a capture elsewhere before
running the other mode.
