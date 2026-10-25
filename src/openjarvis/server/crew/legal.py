"""The public Privacy Policy and Terms pages (aibussol.com/privacy and /terms).

Google requires a homepage and a privacy policy on the app's own domain before the Gmail and Drive sign-in can be published, and the policy must
say how Google data is used (the 'Limited Use' statement). The contact line comes from PRIVACY_CONTACT on Render.
"""

from __future__ import annotations

import html
import os
from typing import Mapping, Optional

UPDATED = "October 25, 2026"

_STYLE = ("body{font:16px/1.6 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;color:#1b1f24;background:#fff}"
          "h1{font-size:28px}h2{font-size:19px;margin-top:28px}a{color:#0b6bcb}.small{color:#5b6570;font-size:14px}"
          "@media (prefers-color-scheme: dark){body{color:#e6e9ee;background:#10141a}a{color:#6cb6ff}.small{color:#9aa4b0}}")


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)} | Aibussol</title><style>{_STYLE}</style></head><body>"
            f"<p class='small'><a href='https://aibussol.com'>Aibussol</a></p>{body}"
            f"<p class='small'>Last updated {UPDATED}. <a href='/privacy'>Privacy Policy</a> · <a href='/terms'>Terms</a></p></body></html>")


def _contact(env: Optional[Mapping[str, str]] = None) -> str:
    c = ((os.environ if env is None else env).get("PRIVACY_CONTACT") or "").strip()
    return html.escape(c) if c else "the owner of the Aibussol account you use (their contact details are on your account)"


def privacy_html(env: Optional[Mapping[str, str]] = None) -> str:
    return _page("Privacy Policy", f"""
<h1>Privacy Policy</h1>
<p>Aibussol (aibussol.com) is a workspace where you run a crew of AI agents for your business. This page explains what we collect, how we use it, and the choices you have.</p>
<h2>What we collect</h2>
<ul><li>Your account details (name, email) and the things you put in the workspace: missions, files, chat messages, and the business information you type in.</li>
<li>Logins you choose to connect (for example Stripe, Etsy, Marnyx, GitHub, Render and Google). You decide which to connect, and you can disconnect each one at any time.</li></ul>
<h2>Google data (Gmail and Google Drive)</h2>
<p>If you connect your Google account, Aibussol asks for these permissions, and uses each only to do what you ask Helix to do:</p>
<ul><li><b>Read your Gmail</b> (gmail.readonly): to search and read messages when you ask, for example &ldquo;did my approval email arrive?&rdquo;.</li>
<li><b>Send email from your Gmail</b> (gmail.send): to send a message you have seen and approved. Nothing is sent until you tap Approve on the exact message.</li>
<li><b>Read your Google Drive</b> (drive.readonly): to find and read documents, sheets and text files when you ask.</li>
<li><b>Save files you ask for to your Drive</b> (drive.file): to put a finished file (such as a book PDF) in a folder called Aibussol. Aibussol cannot change or delete other files in your Drive.</li></ul>
<p>Google data is read only at the moment you ask, to answer you. We do not keep a copy of your mail or files beyond the conversation in which you asked for them, and we do not read your mail in the background.</p>
<p><b>Limited Use.</b> Aibussol&rsquo;s use and transfer to any other app of information received from Google APIs will adhere to the
<a href='https://developers.google.com/terms/api-services-user-data-policy#additional_requirements_for_specific_api_scopes'>Google API Services User Data Policy</a>,
including the Limited Use requirements. We do not use Google data for advertising, do not sell it, and do not let people read it unless you ask for help, it is needed for security or to follow the law.
Our AI model providers process your request to produce the answer; they are not allowed to use it to train models.</p>
<h2>How your information is used and kept</h2>
<p>We use your information only to run your workspace and the crew you start. Connection logins are stored on our servers so the crew can act for you, and are deleted when you disconnect. We do not sell your information. We share it only with the services that run Aibussol (hosting, database, and the AI providers that answer your requests) and the services you connect.</p>
<h2>Your choices</h2>
<ul><li>Disconnect any connection in the Station&rsquo;s Connections panel. For Google this also cancels the login at Google. You can also remove Aibussol at <a href='https://myaccount.google.com/permissions'>myaccount.google.com/permissions</a>.</li>
<li>Ask for your account and data to be deleted by contacting us below.</li></ul>
<h2>Contact</h2>
<p>Questions or deletion requests: {_contact(env)}.</p>
""")


def terms_html(env: Optional[Mapping[str, str]] = None) -> str:
    return _page("Terms of Service", f"""
<h1>Terms of Service</h1>
<p>By using Aibussol you agree to these terms.</p>
<h2>The service</h2>
<p>Aibussol lets you run AI agents that plan and do work for your business. AI output can be wrong, so you are responsible for reviewing it. Actions that publish, charge, send email or change live systems wait for your approval.</p>
<h2>Your account and connections</h2>
<p>Keep your login private. You may connect only accounts you are allowed to use, and you are responsible for what is done through them after you approve it.</p>
<h2>Acceptable use</h2>
<p>Do not use Aibussol to break the law, send spam, infringe others&rsquo; rights, or attack other systems.</p>
<h2>No warranty</h2>
<p>The service is provided as is. To the extent the law allows, we are not liable for indirect losses, and our total liability is limited to what you paid for the service in the previous three months.</p>
<h2>Contact</h2>
<p>{_contact(env)}.</p>
""")
