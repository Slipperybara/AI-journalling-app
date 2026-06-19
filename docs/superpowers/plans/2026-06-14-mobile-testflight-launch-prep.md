# Mobile TestFlight + App Store launch prep (JAI)

**Date:** 2026-06-14
**Branch:** `staging` (NOT promoted to `main` — prod/web untouched by this work)
**Status:** All changes committed + pushed to `origin/staging`; awaiting a fresh
TestFlight build for on-device verification.

Continues from `2026-06-11-agui-token-streaming.md`. This arc takes the app from
"web MVP + bare Expo shell" to "App-Store-ready iOS beta": a staging stack, a
conversion onboarding funnel, a RevenueCat paywall, the duck mascot, and a round
of post-TestFlight UI fixes.

---

## Context / goal

Ship **JAI: AI Chat and Journal** (formerly MindForge) to a TestFlight beta, with
the App Store Connect paperwork started and the mobile app pointed at an isolated
**staging** backend so beta testing never touches production data.

Product framing for onboarding/paywall: onboarding is the highest-leverage
conversion surface, so it's a story-driven funnel ending in a free-trial hard
paywall (RevenueCat).

---

## Environments (isolation matrix)

| Layer | Production (`main`) | Staging (`staging`) |
|---|---|---|
| Backend | Render `srv-d8ejae3bc2fs73ckcifg` | separate Render service (watches `staging`) |
| Postgres | Supabase `iazqotz…` | Supabase `eqwijryhjubgnyetufpa` |
| Neo4j | DO Droplet | Neo4j Aura Free (`cf747fd8`) |
| Web | Vercel (prod) | — none — |
| Mobile | (future) | **TestFlight build points here** (`eas.json` `testflight` profile) |
| Analytics | PostHog `environment=production` | PostHog `environment=staging` |

RLS is enabled on **all 13 tables** in both prod and staging (`postgres` role
bypasses RLS, so the backend is unaffected; anon/PostgREST is locked out). Any new
`init_db` table must `ENABLE ROW LEVEL SECURITY`.

Promotion path: merge `staging` → `main` (deploys prod backend + triggers Vercel
web). **Deliberately not done yet** — hold until the TestFlight build is verified.

---

## What shipped this session (all on `staging`)

### App Store Connect / identity
- Renamed to **"JAI: AI Chat and Journal"** (store) / **"JAI"** (home screen).
- Bundle id **`com.jaijournal.app`** (original `com.mindforge.app` was taken).
- Apple Developer account + Paid Apps agreement active.
- `eas.json` `testflight` profile → staging API/Supabase + PostHog + RevenueCat key.
- App icon: JAI duck-with-lightbulb, built to a 1024² opaque `mobile/assets/icon.png`.

### RevenueCat paywall (RC project `proj85fdee37`, app `app433ff51678`)
- Entitlement `premium` ← both products `com.jaijournal.app.premium.{monthly,yearly}`,
  both in the current `default` offering (`$rc_monthly`, `$rc_annual`).
- Config verified correct via MCP; `subscription_key_configured: true`.
- `Paywall.tsx`: two-plan cards (monthly + yearly w/ per-month + savings %), 3-day
  trial copy, Restore, Terms/Privacy.
- **Self-heal** (`lib/purchases.ts` + `Paywall.tsx`): `purchasePackage` returns
  `{ok,userCancelled,message}` and surfaces the real error; a CustomerInfo update
  listener auto-dismisses the paywall the instant `premium` goes active (fixes the
  sandbox case where the entitlement lands a beat after purchase resolved →
  previously stranded paying users on the wall).

### Onboarding funnel (`components/onboarding/`)
15-screen story funnel, AsyncStorage-gated (`jai_onboarded`), full PostHog funnel
events (`onboarding_started/step_viewed/answer/completed` + person props).
- Flow: **welcome** → name/age/gender/occupation → "how do you relate to
  journaling?" (**single-select**) → "what's weighing on you?" (multi) → benefit →
  tailored-by-issue → **stat** ("~70% can't stick with it") → "Also… (rereading)"
  → reveal1/reveal2 → **hold-to-commit** → notifications → login → paywall.
- Keyword highlighting via `==phrase==` (soft-yellow marker), used on the story beats.
- **Hold-to-commit**: circular yellow button, fills bottom-up, centered.

### Mascot (JAI duck) — `components/Mascot.tsx`
- 6 poses, beige background floodfilled to transparent (originals kept in
  `assets/mascot/src/`); `<Mascot mood size />` with a static require map.
- Placed on: login, onboarding welcome + each story beat + notifications, dashboard
  "Hi there" header, chat empty-state, and the **thinking-duck** chat loader
  (replaced the typing dot).

### Chat / navigation restructure
- **Streaming fix**: replaced `react-native-sse` (XHR, coalesced tokens into a burst
  on iOS) with **`expo/fetch`** streaming (real ReadableStream) — same mechanism the
  web uses. Same SSE frame parsing + AbortController cancel.
- **Ambient background**: warm (conversing) ↔ cool (GraphRAG retrieval) crossfade via
  `expo-linear-gradient`, driven by the stream's `retrieval` phase. Mirrors web
  `BG_WARM`/`BG_COOL`.
- **Top bar**: slim — ☰ menu + date + small duck (tabs + Sign out removed).
- **Left panel (drawer)**: Dashboard entry on top, conversations in the middle,
  Sign out pinned at the bottom.
- Smaller message type (user 18→16, bot 19→17) + more space before the reply.
- Keyboard drops on submit; Google button is a pill matching Apple.

### Dashboard
- Exercise uses the same **orange** bar as Emotional health.
- Journaling streak → filled green **check-circles** (distinct from the bar charts).

### Backend
- Anti-buffering SSE headers on the native stream endpoint (`X-Accel-Buffering: no`).
- Morning-brief push notification with a catchy body ("Yesterday, you were feeling
  <emotion> — your reflection's ready."); Expo Push via `app/push.py`.
- PostHog server events tagged `environment` (`app_env`).
- **Bot `==highlight==` guidance** strengthened: GPT-4 marks the single word/phrase
  that carries the reply (≤1–2×, never in short replies). Both clients already
  render `==…==`.

### Commits (this conversation, on `staging`)
`127bacf` paywall/streaming/ambient/pill · `540f9f6` onboarding funnel ·
`8cf4973` mascot · `17286f4` dashboard+thinking duck · `44d0f18` top bar / nav /
dashboard polish / bot highlights.

---

## Verify on the next TestFlight build (device-only)

> Note: a TestFlight **update** over an already-onboarded install lands you straight
> in your existing chat and skips login/onboarding/empty-state. **Delete + reinstall**
> to see the full funnel and reset the notification permission.

- [ ] **Streaming** trickles token-by-token (the one fix not provable locally). If
      still bursty → it's Render proxy buffering, not the client; isolate with a
      throwaway unauth test-SSE endpoint + `curl -N`.
- [ ] Ambient tint goes **blue** on an analytical question ("how was my week?"),
      warm otherwise; no white flashes / seams.
- [ ] Paywall: sandbox purchase **auto-dismisses** (use a Sandbox Apple ID; products
      at least "Ready to Submit" in ASC).
- [ ] Onboarding funnel end-to-end; circular yellow commit fills over ~3s.
- [ ] Top bar / left-panel nav (Dashboard + Sign out in drawer); message spacing.
- [ ] Dashboard: orange exercise bars + check-circle streak + "Hi there" duck.
- [ ] Bot replies show an occasional `==highlight==`.

---

## Next steps

1. **Rebuild + submit**: `cd mobile && npx eas-cli@latest build --profile testflight
   --platform ios` → `eas submit`. (EAS builds committed git state — all the above
   is committed.)
2. Run the verification checklist above on device.
3. **Then** decide on promotion: merge `staging` → `main` to ship the backend
   (bot highlights, streaming headers, push, analytics) to prod + deploy the web.
   The web already renders `==highlight==`, so highlights light up on web the moment
   prod backend has the new prompt.

### Known open items (not blockers for testing)
- **Push** only fires from the nightly morning-brief batch — won't appear just by
  tapping around; needs the cron/brief to run for the staging user.
- **Cold start**: Render free tier sleeps after ~15 min → first message slow.
  Keep-warm (UptimeRobot) still a manual TODO.
- **Android adaptive icon** still old art (iOS icon updated; Android not needed yet).
- **`react-native-sse`** is now an unused dependency (harmless; can remove).
- Optional: set the **App Store Connect API key** in RevenueCat
  (`app_store_connect_api_key_configured: false`) — not required for entitlements.
- Stray RevenueCat project `proj44b24a4e` can be deleted.

---

## Key gotchas captured

- EAS builds from **committed git state** — uncommitted changes don't ship.
- iOS prompts for notifications **once per install** — reinstall (or iOS Settings)
  to re-test.
- App Store **icons must be opaque** (no alpha); in-app mascots are transparent PNG.
- RevenueCat: **App Store Connect dictates all pricing** (StoreKit returns prices;
  RC only references products).
- Mascot `require()` paths must be **literal** (Metro) — hence the static mood map.
- `expo/fetch` streaming relies on Expo's "winter" runtime providing `TextDecoder`
  (confirmed present in SDK 54).
