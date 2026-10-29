# Polymarket Desk

A fair-value market maker for Polymarket US's **BTC Up/Down 5-minute** contracts. It shows up in Aibussol as the **Polymarket Desk** factory on the Station.

- **The worker** (`src/openjarvis/polybot`) trades. It is plain code with no AI in the trading loop. It runs as its own Render background worker.
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

1. **Polymarket US account.** In the Polymarket US app, finish identity verification and deposit the money you want to trade, e.g. $50.
2. **API key.** At polymarket.us/developer, create an API key. Copy the **Key ID** and the **Secret key**; the secret is shown only once. Paste them only into Render, never into a chat.
3. **Merge this branch** into `main`. Atlas auto-deploys, and the Station gets the Polymarket Desk factory.
4. **Create the worker** on Render: New → Background Worker, same repo `FlipDeckCards/Atlas`, branch `main`, region Oregon, Starter plan.
   - Build command: `pip install asyncpg "httpx>=0.23,<0.28" "cryptography>=43" python-dotenv`
   - Start command: `cd src && python -m openjarvis.polybot run`
   - Environment:
     - `DATABASE_URL`: the same value as the Atlas web service
     - `CREW_NOTIFY_URL`: the same ntfy topic as Atlas, for phone alerts
     - `POLYMARKET_KEY_ID`, `POLYMARKET_SECRET_KEY`
     - `POLYBOT_ALLOW_LIVE`: leave **unset** (paper) until the paper results justify going live
     - Optional: `POLYBOT_BANKROLL_USD=50`, `POLYBOT_MAX_WINDOW_USD=5`, `POLYBOT_MAX_ORDER_USD=2`, `POLYBOT_DAILY_LOSS_USD=10`, `POLYBOT_MIN_EDGE=0.04`
5. **Check it.** In the worker's Shell, run `cd src && python -m openjarvis.polybot check`. It reads the index and the markets and does a signed balance read. It trades nothing.
6. **Let it paper trade.** Watch the scoreboard. Going live is a deliberate step: set `POLYBOT_ALLOW_LIVE=true` on the worker, then ask Helix to take the desk live and tap Approve.

## Commands

```
python -m openjarvis.polybot run      # the worker
python -m openjarvis.polybot check    # keys + feeds, no trading
python -m openjarvis.polybot status   # last status and control row
```

## Tables

`polybot_kv` (status, control), `polybot_fills`, `polybot_windows`, `polybot_events`. They are created on first run, in the same database as Aibussol.
