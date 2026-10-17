"""The /sfx and /video chat shortcuts are offered only when their feature has a provider; with none they are hidden, not a dead end. Offline."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from openjarvis.server import atlas_ui
from openjarvis.server.media import sfx as sfx_mod
from openjarvis.server.media import video as video_mod

SRC = Path(atlas_ui.__file__).read_text(encoding="utf-8")
LIMITS = {"sfx": {"max_seconds": 10}, "video": {"max_seconds": 5, "max_height": 720}}


def run_limits(status):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    fn = SRC[SRC.index("function limitsText"):SRC.index("function mediaShortcutOn")]
    js = fn + "\nconsole.log(JSON.stringify(limitsText(" + json.dumps(status) + ")));"
    r = subprocess.run([node, "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:300]
    return json.loads(r.stdout)


class TestShortcutsAreHiddenWithoutAProvider:
    def test_no_provider_means_no_limits_line_and_no_shortcut_is_mentioned(self):
        assert run_limits({"generation": True, "sfx": False, "video": False, "limits": LIMITS}) == ""
        assert run_limits({"generation": False, "sfx": True, "video": True, "limits": LIMITS}) == ""                     # media switched off
        assert run_limits({"generation": True, "limits": LIMITS}) == ""

    def test_only_the_available_feature_is_shown(self):
        sfx = run_limits({"generation": True, "sfx": True, "video": False, "limits": LIMITS})
        assert "Sound FX: up to 10 s" in sfx and "/sfx" in sfx and "Video" not in sfx and "/video" not in sfx
        vid = run_limits({"generation": True, "sfx": False, "video": True, "limits": LIMITS})
        assert "Video: up to 5 s, 720p max" in vid and "/video" in vid and "Sound FX" not in vid and "/sfx" not in vid

    def test_both_available_shows_both_limits(self):
        both = run_limits({"generation": True, "sfx": True, "video": True, "limits": LIMITS})
        assert "Sound FX: up to 10 s" in both and "Video: up to 5 s, 720p max" in both and "/sfx or /video" in both

    def test_a_shortcut_that_is_off_is_not_intercepted_so_it_is_just_a_chat_message(self):
        assert "mediaShortcutOn(fxCmd[1].toLowerCase())" in SRC
        assert "function mediaShortcutOn(name) { return !!(lib.status && lib.status.generation && lib.status[name]); }" in SRC
        guard = SRC[SRC.index("const fxCmd"):SRC.index("const fxCmd") + 400]
        assert "if (fxCmd && mediaShortcutOn(" in guard                                                                   # no match, or the feature is off: normal chat

    def test_the_video_filter_is_hidden_until_video_works(self):
        assert "vb.style.display = (lib.status && lib.status.video) ? '' : 'none'" in SRC

    def test_the_routes_still_answer_503_until_a_provider_is_configured(self):
        assert sfx_mod.DEFAULT_PROVIDER == "none" and video_mod.DEFAULT_PROVIDER == "none"
        assert not sfx_mod.get_provider({}).configured() and not video_mod.get_provider({}).configured()
