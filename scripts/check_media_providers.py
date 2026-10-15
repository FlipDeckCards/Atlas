"""One real call per media provider, to check the setup. Run it yourself with the settings in your environment:

    PowerShell:  $env:CLOUDFLARE_ACCOUNT_ID = '...'; $env:CLOUDFLARE_API_TOKEN = '...'; uv run python scripts/check_media_providers.py

Prints the provider, the result size and the time, never the token. Uses a few neurons of the free daily quota (about 60 for the image).
"""
from __future__ import annotations

import asyncio
import sys
import time

from openjarvis.server.media import image as image_mod
from openjarvis.server.media import sound as sound_mod
from openjarvis.server.media.base import MediaError


async def main() -> int:
    bad = 0
    for label, provider, request in (
        ("image", image_mod.get_provider(), {"prompt": "a small red fox sitting in snow, soft light", "steps": 4}),
        ("sound", sound_mod.get_provider(), {"text": "Hello from Helix.", "lang": "en"}),
    ):
        if not provider.configured():
            print(f"{label}: {provider.id}: NOT CONFIGURED (set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN)")
            bad += 1
            continue
        t0 = time.time()
        try:
            r = await provider.generate(**request)
            print(f"{label}: {provider.id}: OK {len(r.data)} bytes, {r.mime}, {time.time() - t0:.1f}s")
        except MediaError as exc:
            print(f"{label}: {provider.id}: FAILED ({exc.public_message})")
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
