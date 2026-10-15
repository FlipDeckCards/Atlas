# Security changes — branch `security-hardening` (Stage 1)

Repo: `C:\Users\livin\jarvis-api\atlas` (remote `FlipDeckCards/Atlas`). Nothing is merged, pushed or deployed. Render is unchanged. Environment variable **names** only, never values.

## What changed

### Auth is no longer optional
| Change | Where |
|---|---|
| **`JWT_SECRET_KEY` is required.** The public fallback `change-me-in-production-set-env-var` is removed. A missing, blank, default or placeholder value (`change-me…`, `secret…`) makes `jarvis serve` print a clear error, log it, and exit 1 before anything starts. Token creation and checking also fail closed at runtime (no fallback value exists anywhere). A secret shorter than 32 characters starts but logs a warning. | `server/hardening.py` (`validate_startup`, `get_jwt_secret`), `server/auth_routes.py`, `cli/serve.py` |
| **`OPENJARVIS_API_KEY` is required.** `jarvis serve` refuses to start without it, and `create_app(..., require_auth=True)` (what `serve` uses) raises if the key is empty, so `/api/*` and `/v1/*` always require a token. | `cli/serve.py`, `server/app.py` |
| **Registration is invite-only.** `POST /api/auth/register` needs `invite_code` equal to `INVITE_CODE` (constant-time compare). With `INVITE_CODE` unset, registration returns 403 "Registration is closed". Passwords must be at least 8 characters. The registration form has an Invite Code field. | `server/auth_routes.py`, `server/atlas_ui.py` |
| **Request-size guard.** Requests to `/api/*` whose declared `Content-Length` is over the cap get 413 before the body is read. | `server/auth_middleware.py` |

### Abuse limits
In-memory sliding windows (one process; they reset on restart). Defaults can be changed with the variables in the table below.

| Rule | Default |
|---|---|
| Login, per IP | 10 attempts / 15 min |
| Login, per account (email) | 5 attempts / 15 min (a different IP does not reset it) |
| Register, per IP | 5 attempts / hour |
| Chat, per user | 20 messages / minute |
| Chat, per IP | 60 messages / minute |
| Voice (speech-to-text and text-to-speech), per user / per IP | 30 / 90 per minute |

Blocked requests get 429 with a `Retry-After` header and never reach a provider. The caller's IP is the entry the proxy added to `X-Forwarded-For` (`TRUSTED_PROXY_HOPS`, default 1), not the left side, which a caller can forge.

**Input caps**

| Input | Cap |
|---|---|
| One chat message | 8,000 characters |
| One attached image | 5 MB decoded (about 6.7 million base64 characters) |
| One chat request body (by `Content-Length`) | message cap + image cap + 4 KB |
| One voice recording upload | 10 MB (Whisper itself allows 25 MB); read in a bounded way, not fully |
| Text sent to text-to-speech | 1,500 characters |
| Model reply length | 1,024 tokens (bounds the cost of a single call) |

Known limit: a body sent with chunked transfer encoding has no `Content-Length`, so only the field-level caps (message, image) apply to it.

**Daily spend cap** (`DAILY_SPEND_CAP_USD`, per user, per UTC day, stored in Postgres table `daily_spend`, created automatically)
- Checked **before** every paid call (chat, Whisper, ElevenLabs). At the cap the app returns 429 `daily_spend_cap_reached` with a plain message and does not call the provider.
- Chat is recorded at the model's real price from LiteLLM's price table (flat $0.02 if the model is not in it). ElevenLabs is estimated at $0.30 per 1,000 characters and Whisper at $0.006 per MB (both deliberately high). Estimates are in `hardening.py`.
- Default when unset: **$1.00 per user per day**. `0` blocks every paid call. A call that starts just under the cap can finish slightly over it (bounded by the 1,024-token reply limit).

**Other fixes made while in these functions:** model errors are logged on the server and the browser gets a generic message (it used to receive the first 200 characters of the provider error); voice endpoints return a clear 503 if their provider key is not set.

### Model routing now reads configuration
Keyword routing is unchanged. Model names come from the environment, with the previous strings as defaults:

| Route | Variable | Default |
|---|---|---|
| Images and everything unmatched | `ATLAS_DEFAULT_MODEL` | `gpt-4o` |
| Writing / code keywords | `ATLAS_WRITING_MODEL` | `claude-sonnet-4-5-20250929` |
| Lookup keywords | `ATLAS_LOOKUP_MODEL` | `gemini/gemini-2.5-flash` |

- **Note: the "lookup" route is a plain Gemini call with no web access.** It cannot search or fetch anything; "latest" and "news" questions get the model's built-in knowledge only.
- `ATLAS_MODEL_URL` is **not** a model-name setting. It is the download URL of the 3D face file (`atlas-model.glb`) and is left exactly as it was. I used new variables for the model names instead. If you meant something else by it, tell me.
- The engine label in the UI is now the model name shortened and upper-cased (for example `GPT-4O`, `CLAUDE-SONNET-4-5`, `GEMINI-2.5-FLASH`).

### Dependencies
`fastapi`, `uvicorn`, `pydantic`, `python-multipart`, `asyncpg`, `bcrypt`, `PyJWT` and `python-dotenv` moved from the optional `server` extra into the core dependencies (with `litellm` and `anthropic`, which were already core), and `pytest`, `pytest-asyncio` and `respx` were added to the default `dev` dependency group. A plain `uv sync` now installs everything the app and the test suite need. `uv.lock` is updated.

## New environment variables

| Name | Needed? | Meaning |
|---|---|---|
| `INVITE_CODE` | Only to allow sign-ups | Registration code. Unset = registration disabled |
| `DAILY_SPEND_CAP_USD` | Optional (default 1.00) | Per user, per UTC day; 0 = block all paid calls |
| `ATLAS_DEFAULT_MODEL`, `ATLAS_WRITING_MODEL`, `ATLAS_LOOKUP_MODEL` | Optional | Model names for the three routes |
| `RL_LOGIN_IP_MAX`, `RL_LOGIN_USER_MAX`, `RL_REGISTER_IP_MAX`, `RL_CHAT_USER_MAX`, `RL_CHAT_IP_MAX`, `RL_VOICE_USER_MAX`, `RL_VOICE_IP_MAX` | Optional | Override the rate-limit counts above |
| `TRUSTED_PROXY_HOPS` | Optional (default 1) | How many proxies sit in front of the app |

Database change: one new table `daily_spend` (created automatically on startup, `CREATE TABLE IF NOT EXISTS`).

## Environment variables the app needs on Render (names only; I cannot see Render's values)

| Name | Status from the code |
|---|---|
| `JWT_SECRET_KEY` | **Required now.** Before this change the code silently used a public default when it was missing, so check that it is set to a long random value. If it is missing, the app will not start after you deploy |
| `OPENJARVIS_API_KEY` | **Required now.** Before, auth was off if it was missing, so check it is set |
| `DATABASE_URL` | Required (Neon Postgres) |
| `INVITE_CODE` | Required if anyone should be able to register. Unset = nobody can |
| `OPENAI_API_KEY` | Required for GPT-4o chat and Whisper |
| `ANTHROPIC_API_KEY` | Required for the Claude route (LiteLLM reads it) |
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | Required for the Gemini route (LiteLLM reads it) |
| `ELEVENLABS_API_KEY` | Required for spoken replies; without it `/api/voice/speak` answers 503 |
| `ELEVENLABS_VOICE_ID` | Optional; has a built-in default voice |
| `ATLAS_MODEL_URL` | Optional; only used if the 3D file is not on disk |
| `DAILY_SPEND_CAP_USD`, `ATLAS_*_MODEL`, `RL_*`, `TRUSTED_PROXY_HOPS` | Optional |

Flags I can raise from the code alone: `JWT_SECRET_KEY` and `OPENJARVIS_API_KEY` were previously allowed to be missing, so any Render service that was running without them was unsafe; a value that was ever the default string is rejected and must be replaced. Which ones are actually set on Render I cannot tell without reading the dashboard.

**Before deploying:** set `JWT_SECRET_KEY` (new random value — this signs everyone out), confirm `OPENJARVIS_API_KEY`, set `INVITE_CODE`. Users who already have an account are unaffected by the invite code (it only gates new sign-ups). Existing user tokens signed with the old default secret stop working if you change the secret.

## Tests

**New tests:** `tests/server/test_atlas_hardening.py`, 44 tests, **all pass**. They cover: startup refusal without `JWT_SECRET_KEY` (missing, blank, the old default, placeholders) and without `OPENJARVIS_API_KEY`, `jarvis serve` exiting with code 1 for each, no fallback secret for tokens, a token forged with the old default secret rejected, registration closed without `INVITE_CODE` / refused with a missing or wrong code / working with the right code, login rate limits (per IP and per account across IPs), register rate limit, chat rate limits (per user and per IP), the provider not being called once limited, size caps (audio upload, text-to-speech text, request body, image, message, reply length), the spend cap (recorded per call, blocks before the provider is called, per user, zero cap, voice calls counted), provider errors not leaking, and model names read from the environment.

**Dependencies:** `uv sync` (no extras) now installs `litellm`, `bcrypt`, `PyJWT`, `asyncpg`, FastAPI, `python-dotenv` and the test tools. `uv lock --check` passes. The earlier test run on this machine had failed only because the shared venv lacked these.

**Full suite** (`uv run pytest`, Windows, no Rust extension, no Postgres, no network): **6,826 passed, 202 failed, 30 errors, 84 skipped** in 11 minutes. I did not run a second full baseline on the untouched code, so the split below is by cause, not by comparison:
- **Caused by my change, fixed:** `tests/cli/test_serve_single_build.py` started `jarvis serve` without the two required secrets (2 failures). I added a test fixture that sets test values for them; one of the two now passes, the other still fails for the environment reason below.
- **Not related to this change (environment):** the rest. The groups are `tests/security/*` (scanner, guardrails, sandbox), `tests/memory/*`, `tests/telemetry/*`, `tests/install/*`, `tests/server/test_session_store.py` and `test_channel_bridge*.py` (need a real Postgres / the Rust extension), live-network connector tests, and the Telegram channel tests. I checked `tests/server/test_api_routes.py` and `tests/server/test_middleware.py` against the untouched code with my changes set aside and they fail the same way there (3 failures before my change); the memory-backend one is the missing Rust extension. Building the Rust extension (`maturin develop`, needs rustc) and a local Postgres would clear most of the rest.
- After the fix: targeted rerun of the hardening tests plus the serve tests: 45 passed, 1 failed (the Rust-extension one).
- Lint: the two new files and `atlas_ui.py` are clean under the repo's ruff settings (I added per-file E501 ignores for the new files and the HTML page); some older long lines remain in `auth_routes.py` and `session_store.py` that were already there.

## Not changed / still open
- The lookup route still has no web access (see above).
- Rate limits are per process and in memory; a second instance or a restart resets them. Fine for one Starter instance.
- WebSocket endpoints still accept only the system key (not user tokens).
- JWTs last 30 days and cannot be revoked.
- Chunked uploads have no `Content-Length` to check up front (field-level caps still apply).
- I left `Aibussol-analysis.md` untracked on purpose.

---

# Stage 2 — media generation (branch `media-stage-2`)

Provider research, licenses, quotas, source URLs and dates: see `MEDIA-PROVIDERS.md`. Summary: images use Cloudflare Workers AI FLUX.1 [schnell] (Apache-2.0); speech uses Cloudflare Workers AI MeloTTS (MIT); **video is left out and has no route** (no provider passed the license and free-quota checks); music and sound effects are not offered.

## What was added
| Item | Detail |
|---|---|
| `POST /api/media/image` | `{prompt (1–500 chars), steps (1–8, default 4)}` → base64 JPEG |
| `POST /api/media/sound` | `{text (1–1,000 chars), lang (en, fr, es, zh, jp, kr)}` → base64 MP3 (speech only) |
| `GET /api/media/status` | which of image / sound are on; `video` is always false |
| Chat | typing `/image a red fox` or `/sound hello there` in the chat shows the image or an audio player in the conversation |
| Code | `server/media/` (`base.py` interface, `cloudflare.py`, `image.py`, `sound.py`, `routes.py`); providers are chosen by `MEDIA_IMAGE_PROVIDER` / `MEDIA_SOUND_PROVIDER` |

## Rules enforced
- **Off by default, two switches.** `MEDIA_ENABLED` must be on **and** `DAILY_MEDIA_SPEND_CAP_USD` must be above 0 (default 0). Either missing: every media call answers 403 and no provider is called.
- **Its own spend cap.** Media is checked against `DAILY_MEDIA_SPEND_CAP_USD` per user per UTC day, **before** any provider call; at the cap: 429, no provider call. It is tracked separately from chat (`DAILY_SPEND_CAP_USD`, default $1, unchanged). A second ceiling for all users together, `MEDIA_GLOBAL_DAILY_CAP_USD` (default $1.00), protects the shared account. Costs are estimated at Cloudflare's paid-plan prices (an image about $0.0006, a 1,000-character clip about $0.0004), even while the free quota makes the real charge $0, so the cap also limits use of the free quota.
- **Same protection as chat.** The auth middleware, the per-IP and per-user rate limits (new rules `media_user` 6/min and `media_ip` 20/min; `RL_MEDIA_USER_MAX`, `RL_MEDIA_IP_MAX`), and input caps (above).
- **Errors.** A provider failure returns a fixed sentence (502 or 503); details go to the server log as an HTTP status only. A failed call costs nothing.
- **Keys.** `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` are read only from the environment, sent only in the `Authorization` header, never logged and never returned (tests send the token through success and every failure path and search the response and the log for it).
- **No storage.** Generated files are returned in the answer and shown in the chat; they are not written to Render's disk or to the database and are gone when the page reloads. (Saving them needs object storage: still open.)

## New environment variables
| Name | Needed? | Meaning |
|---|---|---|
| `MEDIA_ENABLED` | Yes, to turn media on | off unless 1 / true / yes / on |
| `DAILY_MEDIA_SPEND_CAP_USD` | Yes, above 0 | per user per UTC day; 0 = off |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | Yes | Cloudflare account id and an API token with Workers AI permission |
| `MEDIA_GLOBAL_DAILY_CAP_USD` | Optional (default 1.00) | all users together per UTC day |
| `MEDIA_IMAGE_PROVIDER`, `MEDIA_SOUND_PROVIDER` | Optional | provider ids (only the Cloudflare ones exist) |
| `RL_MEDIA_USER_MAX`, `RL_MEDIA_IP_MAX` | Optional | rate limits |

## Still open
- The real Cloudflare calls have not been run (no token on this machine); the adapter is tested against mocked Cloudflare responses. `scripts/check_media_providers.py` makes one real call per provider when you set the two Cloudflare variables.
- Cloudflare's docs do not say whether the Free plan needs a payment card to sign up. Check once.
- The free allowance is shared by the whole Cloudflare account (10,000 neurons per day, about 170 images). When it is used up Cloudflare returns an error and the app says the provider is busy.
- No music or sound effects, and no video.
- **ElevenLabs:** the existing spoken replies use `ELEVENLABS_API_KEY`. ElevenLabs' Free plan has no commercial license (Starter and above do). Not changed here; see `MEDIA-PROVIDERS.md`.
- Generated media is not saved (see above), and the same login/IP rate limits are in memory, so they reset on restart.

## Stage 2 test results
- **New:** `tests/server/test_atlas_media.py`, 23 tests, all pass. They cover: flag off blocks image and sound (403, provider not called); cap 0 or unset blocks everything even with the flag on; the cost is recorded under a media key, separate from chat; cap reached returns 429 without calling the provider (per user, and the global ceiling); chat cap and media cap independent; not-configured provider returns 503 with no call; auth required; per-user rate limit; input caps (prompt, text, steps, language); provider errors show only a fixed sentence and a failed call costs nothing; the Cloudflare token appears only in the request's Authorization header, never in the response, the headers or the log, including on 401, 429, 500 and connection failures; no video route; nothing written to disk.
- **With the Stage 1 tests:** 67 passed.
- **Full suite** (`uv run pytest`, Windows, no Rust extension, no Postgres, no network): 6,853 passed, 200 failed, 30 errors, 82 skipped.
- **Pre-existing failures:** the same suite on `main` (`f17e6af`, run from a separate checkout) gives 6,830 passed, 200 failed, 30 errors. The two failing sets are identical except for one test in `tests/agents/test_manager.py::TestCheckpoints` that fails on a different one of its two checkpoint cases from run to run (a same-second timestamp race on Windows; both fail when run alone, on both branches). So Stage 2 adds no failures; the 23 extra passes are the new tests. The causes of the pre-existing failures are environmental (no Rust extension, no Postgres, Windows paths, live-network tests).
- **Not run:** a real call to Cloudflare (no token here). `scripts/check_media_providers.py` does it once you set the two Cloudflare variables.

---

# Fix: media and voice routes returned 401 for a logged-in user (branch `auth-fix-media`)

**Root cause.** `AuthMiddleware` ran the route *inside* its token-check `try/except`, so any error raised by a route was caught and answered as `401 Invalid or expired token` (and only logged at debug level, so nothing showed in Render). The error itself came from the daily-spend queries added in Stage 1: they passed the day as a text string to a `$2::date` parameter, and asyncpg only accepts a real date object. Chat, media and voice all check the spend first, so they failed; `/api/session/history` does not, so it kept working.

**Changes.**
- `server/auth_middleware.py`: the token is checked in the `try`; the route runs outside it. A route error is now a 500 with a logged traceback, never a 401. Auth is not weakened (no token, bad scheme, forged, expired, or a token without a user id are all still 401).
- `server/session_store.py`: `get_spend_today` and `add_spend` convert the day to a `datetime.date` (accepting a string or a date) and the SQL no longer casts a text parameter.
- `server/hardening.py`, `cli/serve.py`: `JWT_SECRET_KEY` shorter than 32 **bytes** now produces a clear startup warning (printed in yellow by `jarvis serve` and logged) that gives the length; it still starts. Setting `JWT_SECRET_STRICT=1` makes the server refuse to start with a short key. The Render value was not changed.
- Tests: `tests/server/test_auth_media_fix.py` (18 tests, real `create_app`).

**New environment variable (optional).** `JWT_SECRET_STRICT`: set to 1 once the secret is 32 bytes or longer.
