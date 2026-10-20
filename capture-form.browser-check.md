# Capture form — 375×667 browser check (LB-082)

Run 2026-10-20 by the LB-082 builder. This is the mobile-first verification required by the plan
`docs/plans/ready/LB-082-capture-plan.md` (Task 6, Step 6).

## What was actually run

| Item | Value |
|---|---|
| Page under test | `http://127.0.0.1:8123/index.html` — this branch served locally (`python3 -m http.server`), unmodified except for `window.__CAPTURE_API_BASE__` injection described below |
| API target | `http://127.0.0.1:8124` — a local mock capture API (201 / 200 / 429 / 503 per email prefix, CORS enabled). **No production API call was made and no real email was sent.** |
| Browser | Google Chrome 155.0.8059.80 (headless=new), driven by puppeteer-core 23 |
| Viewport | 375 × 667 CSS px, `isMobile: true`, `hasTouch: true`, DPR 2 |
| Driver scripts | `/Users/ryanfagin/.hermes/cache/scratch/lb082-browser/check.js`, `check2.js` |
| Raw measurements | `/tmp/lb082-browser2-result.json`, `/tmp/lb082-browser-result.json`; the whole run was repeated after the reference-module rewrite with identical measurements (`/tmp/lb082-browser3-result.json`) |
| Screenshots | `/tmp/lb082-browser2-375x667-initial.png`, `/tmp/lb082-browser2-375x667-founding-open.png` |

The API origin used by the page itself is `https://cardswapapi-production.up.railway.app`, verified
live on 2026-10-20 (`GET /health` → HTTP 200, `{ ok: true, service: "cardswap-api", status: "healthy" }`).
Pointer to the local mock was done only inside the page at runtime, through the same
`window.__CAPTURE_API_BASE__` seam the locked test injects.

## Layout measurements (initial load)

```text
viewport           375 x 667
scrollWidth        375          horizontalScroll: false
waitlist form      present
founding details   closed by default
status line        44 px reserved height (a message cannot shift the layout)
```

Interactive targets inside the capture block (measured bounding boxes):

```text
input   email    waitlist-email          245 x 45   OK (>=44x44)
label   checkbox waitlist-consent        245 x 90   OK (whole row is the label = the tap target)
button  submit   (Get early access)      245 x 51   OK
summary          (Apply to be a Founding Member) 245 x 44  OK
```

Computed font size of every capture input: **16px** (waitlist email, founding email, sell/trade
tools, monthly volume) — no iOS zoom-on-focus.

Label association: every text/email input has a bound `<label>` (`labelsBound: true`).

Honeypot: `position: absolute; left: -10000px; display: block` — present in the layout, off-screen,
`tabindex="-1"`, and not `display: none`. It is deliberately excluded from the tap-target list
because it is not a human control.

## Interaction states (real submits against the local mock)

| Case | Result |
|---|---|
| 201 new signup | “You're on the list. Check your inbox for a confirmation.” — form reset (email field cleared) |
| 200 already on list | “You're already on the list.” — form reset |
| 429 rate limited | “Too many attempts. Please wait a few minutes and try again.” — values preserved |
| 503 capture disabled | “Early access signup is not open yet. Please check back soon.” — values preserved |
| Honeypot filled | “Thanks — you're on the list.” and **no network request** is issued |
| Founding Member form (opened via summary) | “Application received. Check your inbox for a confirmation.”, submit button 245 × 51, `scrollWidth` still 375 |

Only network errors in the console are the deliberate 429/503 responses above and the page's
pre-existing `favicon.ico` 404 (unrelated to this change).

## Keyboard-open surrogate

A headless browser cannot raise a real software keyboard, so this is the closest honest check:
scroll the email field into view, focus it, wait 400 ms, then measure.

```text
focused input rect   top 287, bottom 333, height 45   (viewport height 667)
focusedVisible       true
submit still on screen and >= 44 px tall: true
section height change on focus: 0 px   (layout shift 0)
scrollWidth before / after: 375 / 375  (no horizontal scroll)
```

**Not device-proven:** a real iOS/Android keyboard open/close cycle, safe-area insets, and Safari's
visual-viewport resizing have not been exercised — that remains a device check on the real phone,
and the plan forbids claiming it from a headless run.

## Not done here (deliberately)

- No deploy, no Cloudflare Pages publish, no DNS change. The landing repository's publication
  remains Ryan's call.
- No production API request and no real confirmation email: the form was pointed at a local mock.
