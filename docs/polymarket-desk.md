# Polymarket Desk

A fair-value market maker for Polymarket US's **BTC Up/Down 5-minute** contracts. It shows up in Aibussol as the **Polymarket Desk** factory on the Station.

- **The worker** (`src/openjarvis/polybot`) trades. It is plain code with no AI in the trading loop. It runs inside Aibussol's own server.
- **Aibussol** (`server/crew/desk.py`) watches the worker and holds its switches. The Station factory panel shows a scoreboard (success rate, total profit, trades, today, 7 days, return on staked, running-profit chart), a trade log you can tap open to see every fill, and Pause / Resume / Kill switch buttons. Helix has the `trading_desk` and `trading_desk_control` tools.

## How it trades

Each window settles on CF Benchmarks' BRTI index, averaged over the 60 seconds before the open and before the close. Polymarket US publishes that same index once a second. The worker:

1. Prices "Up" from the live index, recent one-minute volatility and the time left (`model.py`).
2. Rests **post-only** limit orders only where the price beats fair value by `min_edge` (`strategy.py`). It never pays the spread or a taker fee.
3. Holds every contract to settlement, so the most a window can lose is what was paid into it.

## Guards (all in code)

- Bankroll cap.
- Per-window cap.
- Per-order size.
- Daily loss limit. The desk can never commit more than it could lose before hitting this limit, so with defaults at most $10 is at risk at once.
- Quotes are pulled:
  - on a $25 BTC jump in 3 seconds
  - on stale data
  - in the first 20 seconds and last 25 seconds of each window
  - during Polymarket's Thursday 6–8 AM ET maintenance
  - after five API errors in a row
- Kill switch.

## Paper vs live

The worker starts in **paper** mode: real prices, simulated fills, no orders. Paper fills are conservative: the market must trade *through* the desk's price. Paper and live results are always kept and shown apart.

Live needs **both** keys:

1. `POLYBOT_ALLOW_LIVE=true` on the worker.
2. Michael's **Approve** tap. Helix's `go_live` puts an Approve card on the inbox, and the tap writes `mode=live` to the control row.

Loosening any limit also needs an Approve. Tightening applies at once.

## Setup

The desk runs **inside Aibussol** (the Atlas web service), next to Helix and the crew. It starts on every deploy, in paper mode, with nothing to set up. A Postgres advisory lock plus a heartbeat check make sure only one copy ever trades, including while a deploy overlaps old and new instances. Set `POLYBOT_IN_APP=false` to turn it off there; `python -m openjarvis.polybot run` then runs it as a separate worker using the same lock.

Paper needs nothing. Before going live:

1. **Polymarket US account.** In the Polymarket US app, finish identity verification and deposit the bankroll, e.g. $50.
2. **API key.** At polymarket.us/developer, create an API key. Put the Key ID and Secret on the **Atlas** service in Render as `POLYMARKET_KEY_ID` and `POLYMARKET_SECRET_KEY`. Never paste them into a chat.
3. Optional limits on Atlas: `POLYBOT_BANKROLL_USD=50`, `POLYBOT_MAX_WINDOW_USD=5`, `POLYBOT_MAX_ORDER_USD=2`, `POLYBOT_DAILY_LOSS_USD=10`, `POLYBOT_MIN_EDGE=0.04`.
4. **Check the keys.** In the Atlas Shell, run `cd src && python -m openjarvis.polybot check`. It trades nothing.
5. **Go live** only when the paper scoreboard justifies it: set `POLYBOT_ALLOW_LIVE=true` on Atlas, then ask Helix to take the desk live and tap **Approve**.

## Commands

```
python -m openjarvis.polybot run      # the worker
python -m openjarvis.polybot check    # keys + feeds, no trading
python -m openjarvis.polybot status   # last status and control row
```

## Tables

`polybot_kv` (status, control), `polybot_fills`, `polybot_windows`, `polybot_events`. They are created on first run, in the same database as Aibussol.
