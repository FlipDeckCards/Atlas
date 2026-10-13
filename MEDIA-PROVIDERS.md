# Media providers — research and choice (Stage 2)

Checked **2026-10-13** from each provider's own pages (URL listed per row). Where a page did not state something, it says "not stated" and I did not fill the gap from memory. Criteria, in order: (1) commercial use of outputs explicitly allowed on the free tier (or the free tier is all that is needed); (2) a usable free tier or quota that works without paying; (3) output quality, kept brief.

## Chosen

| Type | Provider and model | Why |
|---|---|---|
| **Image** | **Cloudflare Workers AI — `@cf/black-forest-labs/flux-1-schnell`** | (1) The model is Apache-2.0, which the model card says "can be used for personal, scientific, and commercial purposes" ([HF model card](https://huggingface.co/black-forest-labs/FLUX.1-schnell), 2026-10-13). Cloudflare's data-usage page says generated images are the account holder's own content: "You own, and are responsible for, all of your Customer Content", and the models are third-party services whose licenses apply between you and the model provider ([Workers AI data usage](https://developers.cloudflare.com/workers-ai/platform/data-usage/), 2026-10-13). (2) 10,000 neurons per day free ([pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/), 2026-10-13). A 1024×1024 image at 4 steps is about 58 neurons, so roughly 170 images per day for the whole account. (3) FLUX.1 schnell is a fast, 1–8-step distilled model; good prompt following, lower detail than FLUX.1 dev/pro. |
| **Sound** | **Cloudflare Workers AI — `@cf/myshell-ai/melotts`** (text-to-speech) | (1) MeloTTS is MIT licensed: "free for both commercial and non-commercial use" ([HF model card](https://huggingface.co/myshell-ai/MeloTTS-English), 2026-10-13); same Cloudflare output-ownership terms as above. (2) 18.63 neurons per audio minute ([pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/)), so the daily free quota covers hundreds of minutes. (3) Natural but plainer than ElevenLabs; fast; multi-lingual. Output is MP3 ([model page](https://developers.cloudflare.com/workers-ai/models/melotts/), 2026-10-13). |
| **Video** | **None. Video is left out; no video route exists.** | No provider I could check passes criteria 1 and 2 (table below). Self-hosting an open-weight video model is not possible on Render Starter (512 MB, no GPU). |

The Cloudflare Free plan is "the default" for accounts ([Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/), 2026-10-13). **Not verified:** none of the pages I read says whether signing up needs a payment card (the pricing page and the Workers pricing page are silent). Above the daily free allowance, Workers Free simply fails ("further operations will fail with an error"); a charge ($0.011 per 1,000 neurons) happens only if you move to Workers Paid. Check the sign-up flow once before relying on "no card".

## Image candidates

| Provider | Free tier / quota | Commercial use of outputs | Key needed | Quality notes | Source (checked 2026-10-13) |
|---|---|---|---|---|---|
| **Cloudflare Workers AI, FLUX.1 schnell** (chosen) | 10,000 neurons/day account-wide, no charge | Yes: Apache-2.0 model; outputs are the customer's content | `CLOUDFLARE_ACCOUNT_ID` + API token | Fast 4-step model, good for general use | [pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/), [data usage](https://developers.cloudflare.com/workers-ai/platform/data-usage/), [license](https://huggingface.co/black-forest-labs/FLUX.1-schnell) |
| Hugging Face Inference Providers | **None for free users**: "Free Users: None … (credits purchase required)" | Model-dependent | HF token | n/a | [pricing](https://huggingface.co/docs/inference-providers/pricing) — fails (2) |
| Google Gemini API image models | **Not available on the free tier** (every image model shows "Not available" for free) | Google claims no ownership of outputs; free-tier content is used to improve Google products and reviewed by humans; the EEA/Switzerland/UK need paid services | API key | Strong quality | [pricing](https://ai.google.dev/gemini-api/docs/pricing), [terms](https://ai.google.dev/gemini-api/terms) — fails (2) |
| Together AI | No free image model on the pricing page (FLUX.2 dev is paid per image) | — | API key | — | [pricing](https://www.together.ai/pricing) — fails (2) |
| Pollinations.ai | Anonymous use with rate limits, per third-party descriptions | **Not stated** on its own terms page (the page text did not load; third-party reviews say terms are hard to verify) | none / optional | Variable (aggregator) | [terms page](https://pollinations.ai/terms) — fails (1) as unverifiable |
| Stability AI platform | Could not read the pricing page (page content did not load) | Not verified | API key | Strong | [platform](https://platform.stability.ai/pricing) — not verified, not chosen |

## Sound candidates

| Provider | Free tier / quota | Commercial use | Key needed | Notes | Source |
|---|---|---|---|---|---|
| **Cloudflare Workers AI, MeloTTS** (chosen, speech) | Shared 10,000 neurons/day | Yes (MIT model, customer owns outputs) | Cloudflare token | Speech only, no music or sound effects | [pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/), [license](https://huggingface.co/myshell-ai/MeloTTS-English) |
| ElevenLabs (already used for spoken replies) | Free: 10,000 credits/month (1 credit per character) | **No on Free.** Free has no commercial license; Starter ($5/month and up) does; Free also needs attribution when published | existing `ELEVENLABS_API_KEY` | Best voice quality | [pricing](https://elevenlabs.io/pricing), [help center](https://elevenlabs.io/docs/help-center/legal/can-i-publish-the-content-i-generate-on-the-platform) — fails (1) on Free |
| Google Gemini TTS | "Free of charge" on the free tier | Free-tier content is used to improve Google products and reviewed by people; sensitive data must not be sent; EEA users need paid | Gemini key | Good | [pricing](https://ai.google.dev/gemini-api/docs/pricing), [terms](https://ai.google.dev/gemini-api/terms) — usable but weaker on (1) than Cloudflare |
| Cloudflare Deepgram Aura | Same shared quota but 1,363 neurons per 1,000 characters (about 7 clips of 1,000 characters a day) | Third-party license not verified | Cloudflare token | Better voice, costly | [pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/) — not chosen |
| Music and sound effects | No hosted free API with explicit commercial terms found (ElevenLabs Music/SFX need a paid plan; other vendors not verified) | — | — | — | **Not implemented.** |

## Video candidates (all fail criteria 1 or 2)

| Provider | Free tier | Commercial use | Source (checked 2026-10-13) |
|---|---|---|---|
| Google Veo 3.1 (Gemini API) | **Not available** on the free tier ("Not available" for every Veo row) | — | [pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| Pika | "0 credits / month · packs only" | **"Not included: Commercial license"** on Free; API credits are separate | [pricing](https://pika.art/pricing) |
| Runway | 125 one-time credits, no recurring quota | Page does not address Free commercial use | [pricing](https://runway.com/pricing) |
| Luma Dream Machine API | Pay-as-you-go only; no free tier mentioned | Not stated | [API pricing](https://lumalabs.ai/api/pricing) |
| Kling, others | Consumer free credits; commercial terms inconsistent in third-party reviews; I could not read a vendor terms page | Unverified | search results only |
| Open-weight models (Wan, LTX, Mochi) | License allows commercial use, but need a GPU host | — | Cannot self-host on Render Starter |

**Result:** no provider offers a free tier with explicitly commercial-use outputs for video, so video is out. Options for later (not in code): pay for a commercial plan with a provider's API (Luma, Runway, Pika API) or host an Apache-2.0 model on a GPU service.

## Notes
- **ElevenLabs on the Free plan is not licensed for commercial use.** The existing spoken-reply feature uses `ELEVENLABS_API_KEY`. If that key is on the Free plan, the product's replies do not have a commercial license. Either move to a paid ElevenLabs plan (Starter or higher) or switch spoken replies to the Cloudflare voice. I did not change the existing feature.
- Model terms can change; the dates above are when I read them. Keep the pages with your records.
- No key was used for this research.
