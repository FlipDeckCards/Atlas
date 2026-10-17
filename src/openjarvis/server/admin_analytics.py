"""Owner-only analytics for Aibussol: users, activity, spend and media, counted from the existing database.

Read-only. The data endpoint answers only to the admin emails (ANALYTICS_ADMIN_EMAILS, comma-separated;
default: the owner). The page itself is plain HTML; it asks the data endpoint with the login token."""

import logging
import os

from openjarvis.server import hardening

logger = logging.getLogger(__name__)

GATHER_SQL = """
SELECT
  (SELECT count(*) FROM users)                                                        AS users_total,
  (SELECT count(*) FROM users WHERE created_at > now() - interval '24 hours')         AS new_users_24h,
  (SELECT count(*) FROM users WHERE created_at > now() - interval '7 days')           AS new_users_7d,
  (SELECT count(DISTINCT sender_id) FROM channel_sessions
     WHERE updated_at > now() - interval '24 hours')                                  AS active_24h,
  (SELECT count(DISTINCT sender_id) FROM channel_sessions
     WHERE updated_at > now() - interval '7 days')                                    AS active_7d,
  (SELECT COALESCE(sum(usd), 0) FROM daily_spend WHERE day = current_date)           AS spend_today,
  (SELECT COALESCE(sum(usd), 0) FROM daily_spend WHERE day > current_date - 7)       AS spend_7d,
  (SELECT count(*) FROM media_items WHERE created_at > now() - interval '24 hours')  AS media_24h,
  (SELECT count(*) FROM media_items WHERE created_at > now() - interval '7 days')    AS media_7d,
  (SELECT count(*) FROM media_items)                                                  AS media_total,
  (SELECT count(*) FROM briefs WHERE created_at > now() - interval '7 days')         AS briefs_7d,
  (SELECT count(*) FROM subscriptions WHERE status IN ('active','trialing'))          AS subscribers_active
"""


def admin_emails(env=None) -> set:
    e = os.environ if env is None else env
    raw = (e.get("ANALYTICS_ADMIN_EMAILS") or "").strip() or hardening.OWNER_EMAIL
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def is_admin(email: str, env=None) -> bool:
    return bool(email) and email.strip().lower() in admin_emails(env)


async def gather(pool) -> dict:
    """One read of the counts. Numbers come back as plain numbers; spend is in US dollars."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(GATHER_SQL)
    out = dict(row)
    out["subscribers_active"] = int(out.get("subscribers_active") or 0)
    for k in ("spend_today", "spend_7d"):
        out[k] = float(out[k] or 0)
    return out


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Aibussol analytics</title>
<style>
  :root { --bg:#060a10; --panel:#0a1420; --line:#11314a; --cyan:#00e5ff; --dim:#5d8aa0; --text:#c8ecf7; --orange:#ff9040; }
  @media (prefers-color-scheme: light) { :root:not([data-theme="dark"]) { --bg:#f4f8fb; --panel:#ffffff; --line:#cfe2ec; --cyan:#0a7ea4; --dim:#4f6f7f; --text:#12303d; --orange:#b35a00; } }
  :root[data-theme="dark"] { --bg:#060a10; --panel:#0a1420; --line:#11314a; --cyan:#00e5ff; --dim:#5d8aa0; --text:#c8ecf7; --orange:#ff9040; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Share Tech Mono', ui-monospace, monospace; padding: 16px; min-height: 100vh; }
  h1 { font-size: 16px; letter-spacing: 3px; color: var(--cyan); margin-bottom: 4px; }
  .sub { font-size: 11px; color: var(--dim); margin-bottom: 16px; letter-spacing: 1px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; }
  .card { background: var(--panel); border: 1px solid var(--line); padding: 12px; border-radius: 2px; }
  .card h2 { font-size: 10px; letter-spacing: 2px; color: var(--dim); margin-bottom: 8px; text-transform: uppercase; }
  .row { display: flex; justify-content: space-between; font-size: 12px; padding: 3px 0; border-bottom: 1px solid rgba(0,229,255,0.06); }
  .row span:last-child { color: var(--cyan); }
  .note { font-size: 11px; color: var(--dim); margin-top: 16px; line-height: 1.6; }
  #status { font-size: 12px; color: var(--orange); margin: 12px 0; min-height: 16px; }
  a { color: var(--cyan); }
</style></head>
<body>
  <h1>AIBUSSOL ANALYTICS</h1>
  <div class="sub">OWNER ONLY &middot; COUNTS FROM THE LIVE DATABASE</div>
  <div id="status">Loading...</div>
  <div class="grid" id="cards"></div>
  <div class="note">
    Active users are people with a chat session updated in the period. Spend is the daily cost of voice and
    media generation. Subscribers are people with an active or trial Aibussol subscription in Stripe.
  </div>
<script>
(function () {
  var TOKEN_KEY = 'aibussol_token';
  var statusEl = document.getElementById('status');
  var cards = document.getElementById('cards');
  function token() { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; } }
  function money(x) { return '$' + Number(x || 0).toFixed(2); }
  function num(x) { return Number(x || 0).toLocaleString('en-US'); }
  function card(title, rows) {
    var c = document.createElement('div'); c.className = 'card';
    var h = document.createElement('h2'); h.textContent = title; c.appendChild(h);
    rows.forEach(function (r) {
      var row = document.createElement('div'); row.className = 'row';
      var a = document.createElement('span'); a.textContent = r[0];
      var b = document.createElement('span'); b.textContent = r[1];
      row.appendChild(a); row.appendChild(b); c.appendChild(row);
    });
    cards.appendChild(c);
  }
  function show(d) {
    statusEl.textContent = '';
    card('Users', [['Total', num(d.users_total)], ['New, 24 hours', num(d.new_users_24h)], ['New, 7 days', num(d.new_users_7d)], ['Active subscribers', num(d.subscribers_active)]]);
    card('Activity', [['Active, 24 hours', num(d.active_24h)], ['Active, 7 days', num(d.active_7d)], ['Briefs posted, 7 days', num(d.briefs_7d)]]);
    card('Spend', [['Today', money(d.spend_today)], ['Last 7 days', money(d.spend_7d)]]);
    card('Media made', [['Last 24 hours', num(d.media_24h)], ['Last 7 days', num(d.media_7d)], ['All time', num(d.media_total)]]);
  }
  function fail(msg) { statusEl.textContent = msg; }
  if (!token()) { fail('Sign in to Aibussol first (open the main page and log in), then reload this page.'); return; }
  fetch('/api/admin/analytics', { headers: { 'Authorization': 'Bearer ' + token() } })
    .then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) { return { ok: r.ok, status: r.status, data: data }; });
    })
    .then(function (res) {
      if (res.ok) { show(res.data); return; }
      if (res.status === 401) fail('Your sign-in has expired. Log in on the main page, then reload.');
      else if (res.status === 403) fail('This page is for the owner account only.');
      else fail((res.data && res.data.detail) || 'Analytics are unavailable right now.');
    })
    .catch(function () { fail('Could not reach the server. Try again.'); });
})();
</script>
</body></html>
"""
