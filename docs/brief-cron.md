# The daily brief: how it works now, why 9:52 PM did not deliver, and how to run it inside Aibussol

Written 2026-10-17 on branch `brief-fix`. Nothing here has been deployed. No Render setting was changed.

## 1. How a brief is made and delivered today (before this fix)

| Piece | Where | What it does |
|---|---|---|
| Three scheduled routines (Claude cloud routines) | claude.ai, "Marnyx and FlipDeck morning / afternoon / evening brief" (trigger ids start `trig_01…`) | A model (claude-haiku-5-5) is started in a cloud sandbox at `CRON_TZ=America/Los_Angeles` **06:52, 12:52 and 21:52**. Its prompt tells it to read Render (deploys, logs, databases), Stripe, Gmail and GitHub through connectors, write the brief, and `curl` it to `POST https://aibussol.com/api/briefs` with a bearer token. It also sends a push notification. |
| Ingest | `src/openjarvis/server/briefs.py` (`POST /api/briefs`, token `BRIEF_INGEST_TOKEN`) | Stores the posted brief in the `briefs` table. Does not write or schedule anything. |
| Display | `src/openjarvis/server/atlas_ui.py` (`checkBriefs`, `addBriefMessage`) | Polls `/api/briefs/latest` every 60 s and shows each new brief in the chat with a Listen button. |

There is **no brief generator in this repository and no scheduler on the Aibussol server**. The "generator" is the routine's prompt, run by a language model.

## 2. Why the brief was wrong (found, not guessed)

* **Wrong greeting.** Nothing in the code chooses a greeting. The model writes one itself, from the slot's name in its prompt ("morning brief"), not from the clock. A "morning" routine run at 10:30 PM (for example by Run now) says "Good morning".
* **Repeats, and the same sections in every brief.** Each run starts empty (`persist_session: false`) and is asked for the same sections, so it lists the same deploys, approvals and notes again. There was no step that compares with the previous brief or removes repeated lines.
* **Not from live data.** The model does read live connectors, but nothing forces it to say when a source failed. In the 2026-10-16 evening run, GitHub was refused ("GitHub access … not enabled for this session"), the Stripe SQL tool needed a Sigma plan, and the brief papered over that in prose instead of labelling it.
* **The Meta Ads error.** FlipDeck's own server logs an error-level line "[Meta Ads] Token rejected by Meta … Invalid OAuth access token" every time something touches Meta Ads. The routine's "Errors" step copies every error log line, so that stopped feature kept appearing.

## 3. Why the 9:52 PM run "did not deliver"

Checked with the routine's own run history (read-only):

* The run **did fire on time**: session created 2026-10-17 04:52:22 UTC = **21:52 PDT on Oct 16**; the Oct 15 run likewise at 21:53 PDT. It finished in about two minutes with status success. The schedule (`52 21 * * *`, `America/Los_Angeles`) is correct and the routine is enabled.
* **The delivery step failed:** `curl: (56) CONNECT tunnel failed, response 403` when posting to `https://aibussol.com/api/briefs`. The cloud sandbox's network allowlist blocks aibussol.com, so the brief never reached the Aibussol feed. The run's second attempt to look at the proxy was refused by the sandbox's own safety classifier, and it correctly did not work around it. The run still sent a push notification, which is why a message arrived but not in the Aibussol chat.
* Routines can also start a few minutes late (the scheduler adds a delay of several minutes to recurring runs). That is not the cause here.

I did **not** work around the allowlist.

## 4. The fix, in two parts

### Part A: already in this branch (works with the existing routines once deployed)
`brief_content.py`, applied by `POST /api/briefs` to every brief whoever wrote it:
* a greeting written at the top is removed, and the chat page adds `Good morning / afternoon / evening, Michael.` from **the time the brief was made, in America/Los_Angeles** (05:00–11:59 morning, 12:00–16:59 afternoon, 17:00–04:59 evening),
* the Meta Ads section and any Meta Ads or Meta token error line are removed (also from the spoken summary),
* a section, bullet or paragraph that appears twice in one brief appears once,
* items already listed under Completed or Errors in the same product's previous brief (within 36 hours) are not repeated.

### Part B: the brief made inside Aibussol from live data (needs a Render cron job; NOT deployed)
`src/openjarvis/server/brief_job.py` makes the brief itself and stores it directly, so it never needs to cross the sandbox allowlist:
* reads Render (deploys and error logs), GitHub (commits) and Stripe (charges and new subscriptions) with this run's answers only; nothing is cached,
* writes `- <source>: unchecked (<reason>).` for anything it cannot read: no key, access refused, timed out, bad answer, or a source a server cannot reach (Gmail, the to-do notes, the product databases),
* chooses the greeting from the Los Angeles clock, shows each section once, and has no Meta Ads source,
* decides by itself when to run: the cron fires **every hour at minute 52 (UTC)**; the job makes a brief only when the Los Angeles clock is within 45 minutes after 06:52, 12:52 or 21:52 and that period has no brief yet today. That is correct in summer and winter because LA's offset is always a whole number of hours, so :52 UTC is :52 local.

What it does NOT cover yet: Gmail replies, the "/areas" to-do notes, and the user/visitor numbers (they live in other systems). They show as unchecked. If you want the numbers, a read-only database URL per product has to be added (a new source); the routines can keep doing Gmail until then.

## 5. Steps for you (nothing below has been done)

1. **Review and merge `brief-fix`** (Atlas repo) and deploy the Aibussol web service. This alone applies Part A (greeting from the time on the page, no Meta Ads, no repeats).
2. **Create the Render cron job** (Part B) in the same workspace as the Aibussol service:
   * Type: **Cron Job**, same repository and branch as the Aibussol service, same runtime and build command as that service.
   * Schedule: `52 * * * *` (every hour at minute 52; Render cron uses UTC).
   * Command: `python -m openjarvis.server.brief_job`
   * Environment variables (names only, values only in Render):
     * `DATABASE_URL`: the same Postgres the Aibussol service uses (the brief is stored there).
     * `RENDER_API_KEY`: a Render API key (read only is enough).
     * `GITHUB_TOKEN`: a fine-grained token with read access to `FlipDeckCards/FlipDeckCards` and `FlipDeckCards/Marnyx` (contents: read).
     * `STRIPE_READ_KEY`: a **restricted** Stripe key with read access to Charges and Subscriptions only.
   * First test from the Render shell or a one-off job: `python -m openjarvis.server.brief_job --period evening --dry-run` prints a brief and stores nothing.
3. **Switch off the three routines** (or remove the POST step from them) once the cron brief arrives, so there are not two briefs per slot. They are at claude.ai under Routines: "Marnyx and FlipDeck morning brief", "... afternoon brief", "... evening brief". Do not edit them to bypass the allowlist.
4. **Rotate `BRIEF_INGEST_TOKEN`.** Its current value is written in clear text on the first line of each routine's prompt and appears in routine run logs. With the cron job storing briefs directly, the token is only needed if some other client still posts; set a new value in Render (or remove the variable to turn the ingest route off).
5. Optional, FlipDeck repo: stop the FlipDeck server logging the "[Meta Ads] Token rejected" error line now that Meta ads have stopped (it also fires every time the admin page loads). The brief no longer shows it either way.
