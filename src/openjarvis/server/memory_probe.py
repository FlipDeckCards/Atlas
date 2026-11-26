"""Memory probe (2026-11-26): find what is growing ~80 MB/h until Render kills the server at the 2 GiB limit.

Read-only. Every PROBE_EVERY seconds it logs one line starting "MEMPROBE": resident memory, Python heap blocks,
asyncio task count (and the most common task names), thread count, open file descriptors, and a few container sizes.
Once an hour it also logs "MEMPROBE-CENSUS": the object types with the most live instances, and the biggest client /
session / connection-like types. Nothing here trades, writes or changes behaviour; every step is wrapped so the probe
can never raise into the server. Turn it off with MEMPROBE=off.
"""

from __future__ import annotations

import asyncio
import collections
import gc
import logging
import os
import sys
import threading
import time

logger = logging.getLogger("memprobe")

PROBE_EVERY = 600          # seconds between cheap lines
CENSUS_EVERY = 3600        # seconds between the object-type census


def _rss_mb() -> float:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return -1.0


def _fds() -> int:
    try:
        return len(os.listdir("/proc/self/fd"))
    except Exception:
        return -1


def _container_sizes() -> dict:
    out: dict = {}
    try:
        from openjarvis.polybot import client as _c

        out["signed_book_seen"] = len(getattr(_c, "_SIGNED_BOOK_SEEN", {}) or {})
        out["gates"] = len(getattr(_c, "_GATES", {}) or {})
    except Exception:
        pass
    return out


def _task_summary() -> tuple[int, str]:
    try:
        tasks = asyncio.all_tasks()
        names = collections.Counter()
        for t in tasks:
            try:
                coro = t.get_coro()
                names[getattr(coro, "__qualname__", None) or t.get_name()] += 1
            except Exception:
                names["?"] += 1
        top = ", ".join(f"{k}x{v}" for k, v in names.most_common(6))
        return len(tasks), top
    except Exception:
        return -1, ""


def _census() -> tuple[str, str]:
    counts: collections.Counter = collections.Counter()
    for o in gc.get_objects():
        counts[type(o).__module__ + "." + type(o).__name__] += 1
    top = ", ".join(f"{k}={v}" for k, v in counts.most_common(25))
    conn_like = {k: v for k, v in counts.items()
                 if any(w in k.lower() for w in ("client", "session", "connection", "transport", "socket", "stream", "future"))}
    cl = ", ".join(f"{k}={v}" for k, v in sorted(conn_like.items(), key=lambda kv: -kv[1])[:15])
    return top, cl


async def loop() -> None:
    last_census = 0.0
    first = True
    while True:
        try:
            await asyncio.sleep(60 if first else PROBE_EVERY)
            first = False
            n_tasks, top_tasks = _task_summary()
            logger.warning(
                "MEMPROBE rss_mb=%.0f heap_blocks=%d tasks=%d threads=%d fds=%d gc=%s containers=%s top_tasks=[%s]",
                _rss_mb(), sys.getallocatedblocks(), n_tasks, threading.active_count(), _fds(),
                gc.get_count(), _container_sizes(), top_tasks,
            )
            if time.time() - last_census >= CENSUS_EVERY:
                last_census = time.time()
                top, cl = await asyncio.to_thread(_census)
                logger.warning("MEMPROBE-CENSUS top_types=[%s] client_like=[%s]", top, cl)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("memory probe step failed; continuing")
