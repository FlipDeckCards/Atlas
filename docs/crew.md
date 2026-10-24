# The crew and the Station

Say "Helix, build me X" and Helix starts a **crew mission**: it plans the goal into tasks, and a crew of agents (Claude for building and
writing, Gemini for research) works them in parallel. You watch it live in the **Station** (`/station`, the STATION button in the header),
and the crew reaches you only through Helix: questions, approvals and budget requests appear as cards in the chat and in the Station.

Nothing here is deployed until you deploy it on Render. The crew is **off** until `CREW_ENABLED=true` is set.

## Turn it on (Render → Atlas service → Environment)

| Variable | What it does | Suggested |
|---|---|---|
| `CREW_ENABLED` | turns the crew on | `true` |
| `CREW_DAILY_CAP_USD` | most the crew may spend on model calls per day | `3` |
| `CREW_MISSION_BUDGET_USD` | default budget of a new mission | `1` |
| `CREW_MAX_MISSION_BUDGET_USD` | the most any one mission can be given | `25` |
| `CREW_MAX_CONCURRENT` | tasks running at once | `3` |
| `CREW_QUIET_HOURS` | LA hours with no new work unless Night Shift is on | `22-6` |
| `CREW_NOTIFY_URL` | optional phone push: an ntfy.sh topic URL, e.g. `https://ntfy.sh/aibussol-<long-random-word>` (install the free ntfy app and subscribe to the same topic) | |
| `PUBLIC_BASE_URL` | used for the push notification's link | `https://aibussol.com` |
| `ATLAS_HELIX_MODEL` | the model Helix thinks on for real work (leading, planning, diagnosing) | `claude-sonnet-5-5` |
| `ATLAS_HELIX_LIGHT_MODEL` | Helix's model for short everyday chat (set equal to the one above to turn the split off) | `claude-haiku-5-5` |
| `ATLAS_AGENT_MODEL` | the model most agents run on (Scout stays on `ATLAS_LOOKUP_MODEL`) | `claude-haiku-5-5` |
| `ATLAS_CODE_MODEL` | the model Forge writes code on | `claude-sonnet-5-5` |
| `CREW_SENTINEL` | `off` stops Helix from watching Render deploys and investigating failed ones | `on` |
| `CREW_SENTINEL_MINUTES` / `CREW_SENTINEL_PER_DAY` | how often she looks (default 10) / most investigations a day (default 3) | |

Then deploy manually. On start the server creates its tables (`crew_*`, add-only) and starts the engine inside the web service.

Only owner accounts can use the crew (`ANALYTICS_ADMIN_EMAILS`, default michael.dano1523@gmail.com).

## The crew

| Agent | Role | Hands |
|---|---|---|
| Helix | lead | plans each goal into tasks, assigns the crew, brings you questions and approvals |
| Scout | researcher | `web_search` (OpenJarvis built-in: Tavily if `TAVILY_API_KEY` is set, otherwise DuckDuckGo, no key, free); `web_research` (GPT Researcher deep report, needs Tavily) |
| Forge | builder | GitHub: list and read the product repos, commit whole files to its own `crew/<name>` branch, `request_merge` → a checkpoint; the server opens a pull request and merges it only on your Approve. Never writes to main, never touches `.github/workflows`. Needs `CREW_GITHUB_TOKEN` (or `GITHUB_TOKEN`) with Contents + Pull requests write; repos from `CREW_GITHUB_REPOS` (default: Atlas, FlipDeckCards, Marnyx, CyberVoid, deadzone-backend). Atlas deploys on merge. |
| Quill | writer | copy, listings, posts, emails, docs |
| Prism | designer | `generate_image` through the media service (its switch, caps, rate limits and storage quota apply), up to 4 per task; images land in your Aibussol media library |
| Relay | operator | Stripe and Etsy through official APIs; anything paid, public or permanent is a checkpoint |
| Lens | reviewer | added to the end of every plan with 2+ tasks: reads every deliverable, fact-checks with `web_search`, sends a task back once with `request_changes`, writes `review.md` |
| Spark | marketer | positioning, launch and campaign plans, SEO, landing-page, ad and email copy; `web_search`, `generate_image` |
| Echo | social | content calendars, platform-ready posts and captions, short-video scripts, trend checks; `web_search`, `generate_image`; never posts |
| Haven | support | FAQs, help articles, reply templates, policies; Stripe reads only (`use_connection` read actions) |
| Sage | analyst | live P&L with `business_numbers`, KPIs, trends, forecasts; Stripe reads only |
| Probe | qa | tests the account's own live sites with `check_site`, reads code (`repo_files`, `repo_read`), bug reports and test plans; never commits |
| Tally | finance | budgets, cash flow, pricing and margin math, tax-prep checklists from `business_numbers` and Stripe reads; never moves money |

Every agent can `read_output` files other agents saved. Still not built: Google Play and App Store connections, anything on a platform
without an official API.

## The limits, enforced by the server

* Every model call is priced; the task budget, the mission budget and the daily cap are checked before the next call.
  When a task runs out it **asks you** for more (Approve / Decline). At the daily cap the crew stops until midnight UTC.
* Anything public, paid or permanent is a **checkpoint**: it runs only when you tap **Approve**. Helix cannot approve it for you,
  and a model cannot approve its own action.
* "Build me X" in chat is read back with its budget and starts only on a plain **yes** as your next message.
* **Night Shift** (Station top bar): off = no new work starts 10 PM to 6 AM LA; on = the crew keeps working, inside the same limits.
  **Pause all** stops all new work at once. Every step is in the Station's ledger.

## Connecting Etsy

1. Register an app at etsy.com/developers and wait for Etsy's approval.
2. Set `ETSY_API_KEY` (the keystring), `ETSY_SHARED_SECRET` if Etsy shows one, and
   `ETSY_REDIRECT_URI=https://aibussol.com/crew/connect/etsy/callback` (also add that exact URL as the app's callback on Etsy).
3. Deploy, open the Station → Connections → **Connect** next to Etsy, and allow access. Tokens are stored in `crew_secrets` and refreshed automatically.
4. The crew then runs a listing end to end: Prism makes the artwork and `make_design_files` turns it into a design pack
   (`crew/designs.py`, traced by `crew/vector.py`: color and black-and-white PNG and JPG in the media library, SVG and DXF cut
   files in the outbox). `create_draft_listing` (draft, with tags), `add_listing_image` (a photo from the media library), and
   `add_listing_file` with `pack=<name>.pack.json` (one zip for the buyer, with color/ and bw/ folders and a README). The owner
   approves the designs in his library, then one `publish_listings` checkpoint ($0.20 per listing on Etsy) makes them live.

Stripe uses the existing `STRIPE_SECRET_KEY`. Crew-made products carry `metadata app=crew`, so the Aibussol subscription webhook
(which only reads `app=aibussol`) ignores them.

## Code map

| File | What |
|---|---|
| `src/openjarvis/server/crew/engine.py` | planning, the task loop, budgets, questions, checkpoints, the scheduler |
| `src/openjarvis/server/crew/store.py` | Postgres tables and an in-memory twin for tests |
| `src/openjarvis/server/crew/connections.py` | Stripe and Etsy through their official APIs; risk levels |
| `src/openjarvis/server/crew/github.py` | Forge's GitHub access: branch-only commits, merge on Approve |
| `src/openjarvis/server/crew/finance.py` | P&L per factory and in total (Stripe revenue, crew + AI + fixed costs) |
| `src/openjarvis/server/crew/helix.py` | Helix's crew tools and the confirm-before-start rule |
| `src/openjarvis/server/crew/routes.py` | `/api/crew/*`, the Etsy connect flow, `/station` |
| `src/openjarvis/server/crew/station.py` | the Station page (all art drawn in code) |
| `src/openjarvis/server/crew/notify.py` | notices into Helix chat, optional phone push |
| `tests/server/test_crew.py`, `tests/server/test_crew_routes.py` | offline tests |

## Next (phase 2 ideas)

Google Play and App Store connections (official APIs) and web push notifications from the Aibussol app itself.

## The Workbench (Rig): building on Michael's PC

Game builds (Unity, Unreal) need the editors on Michael's computer, so they run there, through Claude Code on his own plan (no API
cost), the way Deadzone Run was built. On the Station: Connections → Your PC → **Pair your PC** gives a token once and the command to
run. The helper (`/crew/workbench/aibussol_workbench.py`, source `src/openjarvis/server/crew/workbench_helper.py`) needs Python 3.9+,
Claude Code (signed in), Git, and Unity/Unreal for game projects:

    python aibussol_workbench.py --server https://aibussol.com --token wb.… --project "Deadzone=C:\path\to\DeadzoneRun" --unity "C:\…\Unity.exe"

It says hello every minute (the Station shows the PC online), collects Rig's jobs, runs `claude -p` in the mapped folder (edits,
git, Unity batch builds), posts progress every 2 minutes and reports the result into the mission (`rig-*.md` in the outbox). Rules it
gives Claude Code: that folder only, a `crew/<name>` branch, never push to main, never publish or upload. The server never runs Rig's
tasks; they wait until the PC collects them. Re-pair makes a new token (the old one stops working).

## Subscribers: their own Station

A subscriber (an active Aibussol subscription) gets the same Station with every expansion bay open and the whole crew working for them.

- **Isolation.** The owner's crew runs on the server's environment. A subscriber's crew runs on `tenant.subscriber_env`: only the
  platform settings in `tenant.PLATFORM_KEYS` plus the subscriber's own connections. It never sees the owner's keys, databases, phone,
  repos or house rules; prompts name the subscriber, and briefs, P&L, site checks and alerts are theirs alone.
- **Guided connections** (`crew/guide.py`): Stripe (a restricted key), GitHub (a fine-grained token on chosen repos), phone (an ntfy
  topic) and house rules. Each one is tested against the real service before it is saved; keys are stored encrypted
  (`CREW_SECRET_KEY`, else derived from `JWT_SECRET_KEY`). Etsy is OAuth; the PC Workbench pairs per account.
- **Businesses**: up to six per account, each a factory on their Station (`POST /api/crew/businesses`).
- **Usage** (`crew/usage.py`): the monthly price stays the same and includes `CREW_PLAN_INCLUDED_USD` of crew work. Past that, with
  `STRIPE_PRICE_USAGE` set (a metered price on a Billing Meter, $0.01 per unit, event `CREW_USAGE_METER_EVENT`), extra work is billed
  at `CREW_OVERAGE_MULTIPLIER` × cost, up to `CREW_MONTHLY_HARD_CAP_USD`, and reported to Stripe's Meter Events API as it accrues.
  Without a usage price the crew stops when the included work is used up. The owner is never metered.

## Marnyx: social posts and email

- Connect it once: in Marnyx, Settings → Connected apps → Create key; on the Station, Connections → Marnyx → Connect, paste, Test & save.
- Echo (social) and Spark (marketing) may use the `marnyx` connection: `list_accounts` and `post_status` run at once;
  `schedule_week` (one brand's week, up to 28 posts) and `send_email` (one campaign to opted-in subscribers) are each ONE approval.
  Every other platform stays read-only for them.
- The weekly social plan (`crew/weekly.py`) starts itself each week (`CREW_WEEKLY_SOCIAL`, default "sun 17:00" Los Angeles, "off" to stop)
  while Marnyx is connected: one approval per brand.

## The landing page

Signed-out visitors to aibussol.com see what Aibussol does, the crew, tools, safety, pricing (live from Stripe via the public
`/crew/plan.json`) and a sign-in / sign-up box. Signed-in users go straight to their Station.


## Helix as the lead engineer

* **Looks for herself (read-only):** `github_files/read/search/activity/failed_runs`, `render_services/deploys/logs`. Repos are limited to `CREW_GITHUB_REPOS`, secrets files are refused and keys, tokens and passwords are masked in anything she reads.
* **Changes the live service only on your tap:** `render_redeploy/restart/rollback` and a Forge fix each become an Approve card in the Helix inbox (kind `ops`); the server runs it, never the model.
* **Forge checks its own work:** after each commit `repo_checks` opens the pull request, waits for GitHub's tests and lint, and returns the failing lines. `request_merge` is refused while checks fail, and the merge card says whether they passed.
* **Sentinel:** every few minutes Helix looks at the newest deploy of each Render service. A failed one is investigated and she posts the cause with a proposal (fix / redeploy / needs you) as a chat message, a phone push and an Approve card.
* **Memory:** `remember` and `forget` keep short notes (newest 60) that she carries into every conversation.
