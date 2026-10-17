# Sound effects and video: what was found, what was built, what is missing

Written 2026-10-16 on branch `media-sfx-video` (from `main` at `04fb8ba`). Nothing here was pushed or deployed.

## What I found of the scope
There is no separate "plan" file for sound effects and video on disk. The scope I found is:

1. **The instruction you gave in this working session ("Part 2: Aibussol / Atlas media work")**: add a sound-effects tool and a video tool using the same provider pattern as the existing Sound tool; mocked providers for all tests, no paid API calls; a hard length and resolution limit in the video tool's config, shown in the UI; video asynchronous (create a job, poll for status, return a job ID); API routes; UI controls in `atlas_ui.py` matching the existing layout; tests; new environment variable names in `.env.example` with placeholders; work on a branch, nothing on `main`.
2. **`MEDIA-PROVIDERS.md` (Stage 2 research, checked 2026-10-13)**: images = Cloudflare FLUX.1 schnell, speech = Cloudflare MeloTTS, **video = none** (every provider checked failed the free-tier / commercial-use criteria), **music and sound effects = not implemented** (no hosted API with explicit commercial terms found; ElevenLabs SFX needs a paid plan).

## What was built (all provider-independent)
- `server/media/sfx.py`: sound-effect request checks, a hard length limit (`MEDIA_SFX_MAX_SECONDS`, default 10, never above 15), provider registry (`MEDIA_SFX_PROVIDER`).
- `server/media/video.py`: hard length and resolution limits (`VIDEO_MAX_SECONDS` default 5, ceiling 10; `VIDEO_MAX_HEIGHT` default 720, ceiling 1080), the three-call provider interface (`start`, `status`, `fetch`), at most 2 running videos per user.
- `server/media/jobs.py`: the `media_jobs` table (created with the other tables, add-only `IF NOT EXISTS`) and an in-memory twin for tests.
- `server/media/service.py`: `generate_sfx`, `create_video_job`, `get_job` (a poll advances the job and saves the finished video to the library once), with the same gates as every paid call (flag, budget, rate limit, per-user and global daily cap, storage quota).
- Routes: `POST /api/media/sfx`, `POST /api/media/video` (answers **202** with a job id at once), `GET /api/media/jobs/{job_id}`; `GET /api/media/status` now reports `sfx`, `video`, `limits` and `estimates`.
- UI (`atlas_ui.py`): `/sfx ...` and `/video ...` shortcuts next to `/image` and `/sound`, the Video filter and player in the library and viewer, a line under the library filters that shows the hard limits ("Sound FX: up to 10 s. Video: up to 5 s, 720p max."), and a progress line while a video job runs.
- Tests: `tests/server/test_media_sfx_video.py` (fake providers only, in-memory storage, catalog and jobs).

## What is missing, so nothing was guessed
1. **No provider is selected for sound effects or video.** The only registered provider for each is `none`, which is never configured, so both routes answer **503 "not configured"** until one is added. I did not pick a vendor: I could not verify any vendor's current API contract, pricing or commercial terms from here (MEDIA-PROVIDERS.md says the free options all fail), and a wrong API shape would be a guess. Adding a provider is one class with `configured`, `estimate_cost_usd`, `generate` (sound effects) or `start`/`status`/`fetch` (video), registered in `PROVIDERS`.
2. **Estimated cost per call: not available** (no provider). The estimate shown in the status endpoint is 0 until a real provider reports one; the spend caps work on those estimates, so a real provider's `estimate_cost_usd` must be honest before this is switched on.
3. **Helix chat tools** (`generate_sound_effect`, `generate_video`) were not added. `helix_tools.py` deliberately refuses sound-effect requests today and its tests assert that; changing it needs your decision, and a paid tool there must go through the explicit "yes" confirmation flow. The `/sfx` and `/video` shortcuts and the HTTP routes are the only way in for now.
4. **Video re-generate and edit** are not supported (a new video is made with `/video`). Videos can be deleted.
5. **No background worker**: a job advances only when polled. A job nobody polls stays "running" and is stopped as "took too long" after 15 minutes on the next poll. If you want videos to finish while the browser is closed, a worker is needed.
6. **Two existing tests were changed** because they asserted that no video route exists (`test_video_is_not_offered`, `test_there_is_no_video_route_and_nothing_is_written_to_disk`). They now assert that the routes exist, no provider is configured and nothing is started. No other test was changed.

7. **The `/sfx` and `/video` shortcuts are hidden until a provider works.** The page offers them (in the limits line under the library filters) and intercepts them only when `/api/media/status` says that feature is available; the Video filter is hidden too. With no provider, typing `/sfx ...` is an ordinary chat message, so nobody reaches a dead end. The routes themselves keep answering 503 until a provider is configured. No provider was chosen.

## Environment variable names (placeholders in `deploy/docker/.env.example`, rows in `SECURITY-CHANGES.md`)
`MEDIA_SFX_PROVIDER`, `MEDIA_VIDEO_PROVIDER`, `MEDIA_SFX_MAX_SECONDS`, `VIDEO_MAX_SECONDS`, `VIDEO_MAX_HEIGHT` (plus the existing `MEDIA_ENABLED`, `DAILY_MEDIA_SPEND_CAP_USD`, `MEDIA_GLOBAL_DAILY_CAP_USD`). Nothing needs to be set in Render for this branch to be safe: with no provider named, both features stay off.
