"""2026-11-18: a position Polymarket still lists right after the desk settled it is not an "Unrecorded trade"."""
import asyncio

from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


def test_a_just_settled_window_is_not_flagged_but_a_truly_unknown_one_is():
    d = Desk(from_env({}, asset="sol"), None, None)
    pushed = []

    async def push(title, msg):
        pushed.append(title)
    d.push = push
    d._settled_slugs = ["cpcsol-sol-updown-5m-2026-11-18-2105z"]
    asyncio.run(d.flag_untracked({"cpcsol-sol-updown-5m-2026-11-18-2105z": {"cost": "3.71", "netPosition": "-4"}}))
    assert pushed == []
    asyncio.run(d.flag_untracked({"cpcsol-sol-updown-5m-2026-11-18-2110z": {"cost": "3.71", "netPosition": "-4"}}))
    assert pushed == ["Unrecorded trade"]
