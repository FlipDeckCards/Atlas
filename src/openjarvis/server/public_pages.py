"""Public marketing pages for aibussol.com: /pricing, /signup, /login, /robots.txt, /sitemap.xml and the 404 page.

Before this module, /pricing, /plans, /signup and /login fell through to the SPA catch-all in app.py and returned the
empty app shell with status 200 (see weekly review and site audit tickets 1, 3, 4).

PRICING IS A DRAFT until Michael confirms it: set PRICING_CONFIRMED to True (or env AIBUSSOL_PRICING_CONFIRMED=1)
to remove the draft banner and the noindex tag. Nothing here charges anyone: the buttons go to /signup, and no
checkout is wired to these tiers yet (Stripe products tagged app=aibussol must be created first).
"""

from __future__ import annotations

import html
import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

router = APIRouter()

SITE = "https://aibussol.com"

# Client-side routes of the console app (frontend/src/App.tsx). Any other path that is not a real file is a 404.
SPA_PATHS = frozenset({"app", "dashboard", "settings", "get-started", "data-sources", "agents", "logs"})

# Public pages listed in sitemap.xml: only pages that exist.
SITEMAP_PATHS = ("/", "/pricing", "/signup", "/privacy", "/terms")


def pricing_confirmed() -> bool:
    return os.environ.get("AIBUSSOL_PRICING_CONFIRMED", "").strip().lower() in ("1", "true", "yes")


PLANS = (
    {
        "name": "Starter", "price": 19, "for": "One business",
        "lines": ["1 business", "$10 of included work each month", "Plans, approvals and daily briefs",
                  "Stripe, Etsy, GitHub and Marnyx connections", "Social and email planning through Marnyx"],
    },
    {
        "name": "Studio", "price": 49, "for": "Several businesses", "featured": True,
        "lines": ["Up to 3 businesses", "$30 of included work each month", "Everything in Starter",
                  "Game building (needs your paired computer and your own Claude plan)"],
    },
    {
        "name": "Portfolio", "price": 99, "for": "Agencies and heavy multi-business owners",
        "lines": ["Up to 10 businesses", "$75 of included work each month", "Everything in Studio"],
    },
)

CHECKPOINTS = (
    ("Plan", "Nothing starts until you approve the plan."),
    ("Publish", "Posts, listings and site changes wait for your tap."),
    ("Charge", "Anything that spends or charges money waits for your tap."),
    ("Merge code", "Code changes are merged only after you approve."),
    ("Send email", "Every email is shown to you before it is sent."),
)

FAQ = (
    ("Do I need to code?", "No. You describe a goal by voice or chat. Game building is the one workflow that needs a paired computer and your own Claude plan."),
    ("Can it post, charge or email without me?", "No. Public, paid and permanent actions each have their own approval step."),
    ("What happens when I reach my limit?", "You get an alert at 80%. At the limit work pauses, and nothing extra is charged until you raise your cap."),
    ("Can I run more than one business?", "Yes. Studio covers up to 3 and Portfolio up to 10, each with its own dashboard."),
    ("Where are my keys kept?", "Connections use official APIs, keys are encrypted and each business keeps separate accounts. See the Privacy Policy."),
    ("Can I get a refund?", "Refund terms (a 14-day money-back guarantee is proposed) are being confirmed and will be stated here before you pay."),
)

_STYLE = (
    "*{box-sizing:border-box}body{font:16px/1.6 system-ui,sans-serif;margin:0;color:#1b1f24;background:#fff}"
    "main{max-width:980px;margin:0 auto;padding:24px 20px 48px}header,footer{max-width:980px;margin:0 auto;padding:16px 20px}"
    "a{color:#0b6bcb}h1{font-size:32px;margin:24px 0 8px}h2{font-size:22px;margin-top:40px}"
    ".sub{font-size:18px;color:#3b4550}.small{color:#5b6570;font-size:14px}"
    ".draft{background:#fff4d6;border:1px solid #e0b84a;padding:10px 14px;border-radius:8px;margin:16px 0}"
    ".plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px;margin-top:16px}"
    ".plan{border:1px solid #cfd6dd;border-radius:12px;padding:20px}.plan.featured{border:2px solid #0b6bcb}"
    ".price{font-size:34px;font-weight:700}.price span{font-size:15px;font-weight:400;color:#5b6570}"
    ".plan ul{padding-left:18px}.btn{display:inline-block;background:#0b6bcb;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;border:0;font-size:16px;cursor:pointer}"
    "dt{font-weight:600;margin-top:12px}dd{margin:2px 0 0}"
    "form{max-width:380px;display:flex;flex-direction:column;gap:12px}label{display:flex;flex-direction:column;gap:4px;font-weight:600}"
    "input{padding:10px;font-size:16px;border:1px solid #9aa4b0;border-radius:6px}.err{color:#b3261e;min-height:1.4em}.ok{color:#1b7a3d}"
    "@media (prefers-color-scheme: dark){body{color:#e6e9ee;background:#10141a}.sub{color:#b5bec8}a{color:#6cb6ff}"
    ".plan{border-color:#3a4450}.draft{background:#3a3115;border-color:#8a7228}input{background:#1a2029;color:#e6e9ee;border-color:#3a4450}}"
)


def _page(title: str, body: str, *, description: str = "", noindex: bool = False) -> str:
    robots = "<meta name='robots' content='noindex'>" if noindex else ""
    desc = f"<meta name='description' content='{html.escape(description, quote=True)}'>" if description else ""
    return (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>{desc}{robots}<style>{_STYLE}</style></head><body>"
        f"<header><a href='/'><b>Aibussol</b></a> &nbsp; <a href='/pricing'>Pricing</a> &nbsp; <a href='/login'>Sign in</a></header>"
        f"<main>{body}</main>"
        f"<footer class='small'><a href='/privacy'>Privacy</a> &middot; <a href='/terms'>Terms</a> &middot; <a href='/pricing'>Pricing</a></footer>"
        f"</body></html>"
    )


def pricing_html() -> str:
    confirmed = pricing_confirmed()
    banner = "" if confirmed else (
        "<div class='draft'><b>Draft pricing.</b> These prices are proposed and not final. Nothing on this page charges you.</div>"
    )
    cards = ""
    for p in PLANS:
        items = "".join(f"<li>{html.escape(x)}</li>" for x in p["lines"])
        cls = "plan featured" if p.get("featured") else "plan"
        cards += (
            f"<div class='{cls}'><h3>{p['name']}</h3><p class='small'>{html.escape(p['for'])}</p>"
            f"<p class='price'>${p['price']}<span> / month</span></p><ul>{items}</ul>"
            f"<a class='btn' href='/signup?plan={p['name'].lower()}'>Start with {p['name']}</a></div>"
        )
    checks = "".join(f"<dt>{a}</dt><dd>{html.escape(b)}</dd>" for a, b in CHECKPOINTS)
    faq = "".join(f"<dt>{html.escape(q)}</dt><dd>{html.escape(a)}</dd>" for q, a in FAQ)
    body = (
        "<h1>Describe the goal. Approve the plan. Your crew does the rest.</h1>"
        "<p class='sub'>Tell your assistant, Helix, what you want in plain words. Specialist AI agents do the work, and nothing goes public, "
        "gets paid or is sent until you say yes.</p>" + banner +
        f"<div class='plans'>{cards}</div>"
        "<h2>You set the limit</h2><p>Each plan includes a set amount of work each month. You see what is used and what is left. "
        "At 80% you get an alert. At the limit work pauses, and nothing extra is charged until you raise your cap.</p>"
        f"<h2>You approve before anything goes live</h2><dl>{checks}</dl>"
        f"<h2>Questions</h2><dl>{faq}</dl>"
    )
    return _page("Pricing | Aibussol", body,
                 description="Aibussol plans: Starter $19, Studio $49, Portfolio $99 a month. You approve before anything goes live.",
                 noindex=not confirmed)


_SIGNUP_JS = """
const f=document.getElementById('f'),msg=document.getElementById('msg');
f.addEventListener('submit',async e=>{e.preventDefault();msg.className='err';msg.textContent='';
 const b=f.querySelector('button');b.disabled=true;
 try{const r=await fetch('/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({email:f.email.value,password:f.password.value,display_name:f.name.value})});
  const d=await r.json().catch(()=>({}));
  if(r.ok){msg.className='ok';msg.innerHTML='Account created. <a href="/">Sign in to your station</a>.';f.style.display='none';}
  else{msg.textContent=(typeof d.detail==='string'&&d.detail)||'Could not create the account. Try again.';}
 }catch(x){msg.textContent='Network error. Try again.';}
 b.disabled=false;});
"""


def signup_html() -> str:
    body = (
        "<h1>Create your account</h1>"
        "<p class='sub'>Your first goal takes about 2 minutes. You approve the plan before any work starts.</p>"
        "<form id='f'><label>Your name<input name='name' autocomplete='name'></label>"
        "<label>Email<input name='email' type='email' required autocomplete='email'></label>"
        "<label>Password (8 or more characters)<input name='password' type='password' required minlength='8' autocomplete='new-password'></label>"
        "<button class='btn' type='submit'>Create account</button></form><p id='msg' class='err' role='status'></p>"
        "<p class='small'>Already have an account? <a href='/login'>Sign in</a>. By signing up you agree to the "
        "<a href='/terms'>Terms</a> and <a href='/privacy'>Privacy Policy</a>. Nothing is charged at signup.</p>"
        f"<script>{_SIGNUP_JS}</script>"
    )
    return _page("Create your account | Aibussol", body, description="Create an Aibussol account.")


def login_html() -> str:
    body = (
        "<h1>Sign in</h1><p class='sub'>Sign in on your station to see your businesses, plans and approvals.</p>"
        "<p><a class='btn' href='/'>Open the station</a></p>"
        "<p class='small'>No account yet? <a href='/signup'>Create one</a>.</p>"
    )
    return _page("Sign in | Aibussol", body)


def not_found_html() -> str:
    body = (
        "<h1>Page not found</h1><p class='sub'>That page does not exist.</p>"
        "<p><a href='/'>Home</a> &middot; <a href='/pricing'>Pricing</a> &middot; <a href='/signup'>Create an account</a></p>"
    )
    return _page("Page not found | Aibussol", body, noindex=True)


def not_found_response(path: str = "") -> Response:
    """A real 404: JSON for API paths, a small HTML page for everything else."""
    p = path.lstrip("/")
    if p.startswith(("api/", "v1/")) or p in ("api", "v1"):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "Not found"}, status_code=404)
    return HTMLResponse(not_found_html(), status_code=404)


def is_spa_path(path: str) -> bool:
    first = path.strip("/").split("/")[0]
    return first in SPA_PATHS


@router.get("/pricing", include_in_schema=False)
async def pricing():
    return HTMLResponse(pricing_html())


@router.get("/plans", include_in_schema=False)
async def plans():
    return RedirectResponse("/pricing", status_code=301)


@router.get("/signup", include_in_schema=False)
async def signup():
    return HTMLResponse(signup_html())


@router.get("/login", include_in_schema=False)
async def login():
    return HTMLResponse(login_html())


@router.get("/robots.txt", include_in_schema=False)
async def robots():
    return PlainTextResponse(f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /v1/\nSitemap: {SITE}/sitemap.xml\n")


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    urls = "".join(f"<url><loc>{SITE}{p}</loc></url>" for p in SITEMAP_PATHS)
    xml = f"<?xml version='1.0' encoding='UTF-8'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>{urls}</urlset>"
    return Response(xml, media_type="application/xml")
