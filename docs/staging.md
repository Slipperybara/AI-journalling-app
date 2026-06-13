# Staging environment

A fully isolated pre-prod copy of MindForge. Push to `staging` → it auto-deploys →
verify against real infra → merge `staging` → `main` to ship. **Nothing in staging can
touch prod data** — separate database, separate auth users, separate graph, separate
analytics.

## Isolation matrix

| Layer | Prod | Staging |
|---|---|---|
| Git branch | `main` | `staging` (deploys staging) |
| Backend | Render `ai-journalling-app-api` | Render `ai-journalling-app-api-staging` (from `staging`) |
| Postgres + Auth | Supabase `AI Journalling` (`iazqotzjesomoyqfelgt`) | Supabase `AI Journalling Staging` (`eqwijryhjubgnyetufpa`) |
| Neo4j | DO droplet `mindforge-neo4j.duckdns.org` | Neo4j Aura Free (`mindforge-staging`) |
| Analytics | PostHog project (env=production) | same PostHog project, tagged `environment=staging` (free tier = 1 project) |
| Mobile | points at prod (default) | EAS `staging` profile / `.env` override |
| Batch | GitHub Actions cron → webhook | inline scheduler on the staging service + manual `/api/admin/*` |

## Resource references (non-secret)

- **Staging Supabase URL:** `https://eqwijryhjubgnyetufpa.supabase.co`
- **Staging Supabase ref:** `eqwijryhjubgnyetufpa` (org `AI Journal` / `iaqmyfeyinyvjxahgynt`)
- **Staging anon key:** committed in `mobile/eas.json` (`staging` profile) — publishable, safe.
- **Render staging service:** `ai-journalling-app-api-staging` → `https://ai-journalling-app-api-staging.onrender.com`

Secrets (DB password, Neo4j Aura password, PostHog key, webhook secret) live **only** in
Render env vars and your password manager — never in git.

## Render env vars (staging service)

Same names as prod, different values. Set on the Render staging service:

| Var | Value |
|---|---|
| `DATABASE_URL` | staging Supabase **Session pooler** string (Connect → Session pooler), with the DB password you reset |
| `SUPABASE_URL` | `https://eqwijryhjubgnyetufpa.supabase.co` (enables JWKS asymmetric verification) |
| `NEO4J_URI` | Aura `neo4j+s://<id>.databases.neo4j.io` |
| `NEO4J_USER` | `neo4j` |
| `NEO4J_PASSWORD` | Aura generated password |
| `OPENAI_API_KEY` | same key as prod (or a separate one) |
| `POSTHOG_API_KEY` | **same** `phc_…` key as prod (free tier = 1 project) |
| `POSTHOG_HOST` | `https://us.i.posthog.com` |
| `APP_ENV` | `staging` — tags every event so you can filter staging out of prod dashboards |
| `RUN_INLINE_SCHEDULER` | `true` (staging runs the batch in-process; no separate cron) |
| `BATCH_WEBHOOK_SECRET` | a fresh `openssl rand -hex 32` (only needed if you POST `/api/admin/run-batch` manually) |
| `CORS_ORIGINS` | `http://localhost:5173` (mobile sends no Origin; widen only if a staging web is added) |
| `DAY_BOUNDARY_HOUR` | `6` |

No `SUPABASE_JWT_SECRET` needed — `SUPABASE_URL` selects the JWKS path (`app/auth.py`).
Schema self-materializes on first boot via `init_db()` + `init_graph()`; nothing to migrate.

## One-time provisioning

1. **Neo4j Aura Free** — https://console.neo4j.io → create AuraDB Free `mindforge-staging`.
   Save the one-time URI + password. Status must be **Running**.
2. **Supabase staging** — already created (`eqwijryhjubgnyetufpa`). Then:
   - **Connect → Session pooler** → copy the URI. Settings → Database → **Reset password**
     so you know it; substitute it into the URI → that's `DATABASE_URL`.
   - **Authentication → Providers → Google** → enable, paste the Google OAuth client
     id/secret (can reuse the prod OAuth app or make a new one).
   - **Authentication → URL Configuration → Redirect URLs** → add `mindforge://auth-callback`
     and `mindforge://*`.
   - *(Apple sign-in deferred until the Apple Developer account is live.)*
3. **PostHog** — free tier allows only one project, so staging **reuses the prod
   `phc_` key**. Every event is tagged `environment` (`production` vs `staging`) via
   `APP_ENV`; filter `environment = production` in your dashboards to exclude staging.
4. **Render staging service** — created from the `staging` branch with the env vars above
   (provisioned via Render MCP; pick the workspace first).

## Promote flow (staging → prod)

```bash
# develop on a feature branch, then:
git checkout staging && git merge --ff-only feat/<name> && git push origin staging
#   → Render staging auto-deploys; verify (see below)
git checkout main && git merge --ff-only staging && git push origin main
#   → Render prod + Vercel auto-deploy
```

## Point the mobile app at staging

- **Local dev client:** copy the three staging lines in `mobile/.env.example` over the
  defaults in `mobile/.env`, then `expo start --dev-client`.
- **EAS build:** `eas build --profile staging` — env is injected from `eas.json`.

## Verify staging end-to-end

1. `https://ai-journalling-app-api-staging.onrender.com/docs` → 200; logs show
   `init_db` + `[graph_db] indexes ensured`.
2. New Google sign-in → user appears in the **staging** Supabase, not prod.
3. Send a chat message → streamed reply; row in **staging** Postgres; `message_sent`
   in **staging** PostHog (prod PostHog stays silent).
4. `POST /api/admin/parse-day/<day>` (authed) → extraction rows + Neo4j nodes in Aura.
5. Prod untouched throughout.
