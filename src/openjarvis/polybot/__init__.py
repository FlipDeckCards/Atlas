"""Polymarket Desk: a fair-value market maker for Polymarket US's BTC Up/Down markets, supervised by Helix.

It runs as its own always-on process (a Render background worker: `python -m openjarvis.polybot run`), never inside a model:
every quote, size and stop is decided by the plain code in this package. Helix only reads its state and changes its switches.

How it trades (strategy.py):
  * every BTC Up/Down window (5 minutes by default) settles on CF Benchmarks' BRTI, averaged over the last 60 seconds before the open
    (the price to beat) and before the close; Polymarket US publishes that same index second by second (/v1/asset-prices/history);
  * model.py turns the live index, recent volatility and the time left into a fair probability that the window settles Up;
  * the desk rests post-only limit orders (participateDontInitiate) only where the price is at least `min_edge` better than fair:
    a bid for Up below fair, a bid for Down below 1 - fair. It never pays the spread and never pays a taker fee;
  * positions are held to settlement ($1 or $0 per contract), so the most a window can lose is what was paid into it.

Guards (risk.py), all enforced by code: a bankroll cap, a cap per window, a daily loss limit, pulling every quote when the index
jumps, when data goes stale, around Polymarket's Thursday maintenance and in the last seconds of a window, and a kill switch.

Modes: paper (default: real market data, simulated fills, no orders) and live. Live needs BOTH the environment switch
POLYBOT_ALLOW_LIVE=true on the worker AND the owner's Approve tap in Aibussol, which writes mode=live to the control row.
"""
