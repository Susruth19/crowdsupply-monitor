#!/usr/bin/env python3
"""Crowd Supply crowdfunding monitor.

Checks https://www.crowdsupply.com/crowdfunding and alerts you by WhatsApp
and/or email:
  1. when a new crowdfunding project appears, and
  2. once more when a project has about one day of funding left.

State (which projects have been seen / reminded) is kept in state.json so
nothing is sent twice. Designed to run on a schedule (GitHub Actions),
but works the same from any computer with cron.

Configure at least one channel. Each is used only if its variables are set.

WhatsApp (via the free CallMeBot API - personal use, sends to your own number)
  WHATSAPP_PHONE      Your WhatsApp number with country code, e.g. +919876543210
  CALLMEBOT_APIKEY    The API key CallMeBot sends you on WhatsApp

Email (Gmail)
  SMTP_USER           Gmail address that sends the mail
  SMTP_PASSWORD       Gmail *app password*
  EMAIL_TO            Where to send alerts (defaults to SMTP_USER)
  SMTP_HOST           Default smtp.gmail.com
  SMTP_PORT           Default 465 (SSL)

Other
  REMIND_HOURS_BEFORE Default 24 - send the "ending soon" alert inside this window
  STATE_FILE          Default state.json
  DRY_RUN             Set to 1 to print alerts instead of sending them
"""
from __future__ import annotations

import json
import os
import re
import smtplib
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlencode, urlparse

LIST_URL = "https://www.crowdsupply.com/crowdfunding"
SITE_HOST = "www.crowdsupply.com"
STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))
REMIND_WINDOW = timedelta(hours=float(os.environ.get("REMIND_HOURS_BEFORE", "24")))
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}

IST = timezone(timedelta(hours=5, minutes=30))
# Crowd Supply is based in Portland, so unlabeled times are taken as Pacific.
TZ_OFFSETS = {
    "PST": -8, "PDT": -7, "PT": -8,
    "MST": -7, "MDT": -6, "CST": -6, "CDT": -5,
    "EST": -5, "EDT": -4, "ET": -5,
    "UTC": 0, "GMT": 0, "IST": 5.5,
}
DEFAULT_TZ = timezone(timedelta(hours=-8))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

# First path segments on crowdsupply.com that are site pages, not projects.
NON_PROJECT_SEGMENTS = {
    "about", "account", "accounts", "api", "blog", "browse", "c", "cart",
    "checkout", "coming-soon", "contact", "crowdfunding", "explore", "faq",
    "guide", "help", "in-stock", "jobs", "labs", "launch", "legal", "login",
    "logout", "media", "newsletter", "pre-launch", "press", "privacy",
    "programs", "search", "signup", "static", "teardown", "terms", "u",
    "updates", "users",
}

MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
END_DATE_RE = re.compile(
    r"(?:ends?|ending|closes?|closing)\s+(?:on\s+)?"
    rf"(?P<date>{MONTH}\s+\d{{1,2}},?\s+\d{{4}})"
    r"(?:\s+at\s+(?P<time>\d{1,2}:\d{2}\s*[AaPp]\.?\s?[Mm]\.?)"
    r"(?:\s*(?P<tz>[A-Za-z]{2,4}))?)?",
    re.IGNORECASE,
)
HOURS_LEFT_RE = re.compile(
    r"(?:ends\s+in\s+)?(\d+)\s+hours?\s+(?:left|to\s+go|remaining)", re.I
)
DAYS_LEFT_RE = re.compile(
    r"(?:(?:ends\s+in\s+)(\d+)\s+days?)|(?:(\d+)\s+days?\s+(?:left|to\s+go|remaining))",
    re.I,
)
PCT_RE = re.compile(r"(\d[\d,]*)\s*%\s*funded", re.I)
RAISED_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s+raised", re.I)
GOAL_RE = re.compile(r"\$\s?([\d,]+)\s+goal", re.I)
CROWDFUNDING_HINT_RE = re.compile(
    r"\$\s?[\d,]+\s+raised|%\s*funded|funding\s+ends|days?\s+left|hours?\s+left",
    re.I,
)


# --------------------------------------------------------------------------
# Parsing helpers (pure functions - unit-testable without a browser)
# --------------------------------------------------------------------------

def is_project_url(url: str) -> bool:
    """True for links shaped like https://www.crowdsupply.com/<company>/<project>."""
    p = urlparse(url)
    if p.scheme not in {"http", "https"}:
        return False
    if p.netloc.lower() not in {SITE_HOST, "crowdsupply.com"}:
        return False
    parts = [s for s in p.path.split("/") if s]
    if len(parts) != 2:
        return False
    return parts[0].lower() not in NON_PROJECT_SEGMENTS


def normalize_url(url: str) -> str:
    p = urlparse(url)
    path = "/".join(s for s in p.path.split("/") if s)
    return f"https://{SITE_HOST}/{path}"


def _parse_explicit_end(m: re.Match) -> datetime | None:
    raw_date = re.sub(r"[.,]", " ", m.group("date"))
    mon, day, year = raw_date.split()[:3]
    try:
        d = datetime.strptime(f"{mon[:3].title()} {int(day)} {year}", "%b %d %Y")
    except ValueError:
        return None
    raw_time = m.group("time")
    if raw_time:
        t = re.sub(r"[.\s]", "", raw_time).upper()  # "3:59p.m." -> "3:59PM"
        try:
            tt = datetime.strptime(t, "%I:%M%p")
            d = d.replace(hour=tt.hour, minute=tt.minute)
        except ValueError:
            d = d.replace(hour=23, minute=59)
    else:
        d = d.replace(hour=23, minute=59)
    tz_name = (m.group("tz") or "").upper()
    tz = (
        timezone(timedelta(hours=TZ_OFFSETS[tz_name]))
        if tz_name in TZ_OFFSETS
        else DEFAULT_TZ
    )
    return d.replace(tzinfo=tz).astimezone(IST)


def parse_end(text: str, now: datetime) -> tuple[datetime | None, str | None]:
    """Find when funding ends. Returns (ends_at in IST, source)."""
    m = END_DATE_RE.search(text)
    if m:
        dt = _parse_explicit_end(m)
        if dt:
            return dt, "date"
    m = HOURS_LEFT_RE.search(text)
    if m:
        return now + timedelta(hours=int(m.group(1))), "approx"
    m = DAYS_LEFT_RE.search(text)
    if m:
        days = int(m.group(1) or m.group(2))
        return now + timedelta(days=days), "approx"
    return None, None


def parse_funding(text: str) -> dict:
    out = {}
    if m := PCT_RE.search(text):
        out["percent_funded"] = m.group(1).replace(",", "")
    if m := RAISED_RE.search(text):
        out["raised"] = m.group(1)
    if m := GOAL_RE.search(text):
        out["goal"] = m.group(1)
    return out


def looks_like_crowdfunding(text: str) -> bool:
    return bool(CROWDFUNDING_HINT_RE.search(text))


def clean_title(title: str) -> str:
    return re.sub(r"\s*[|\-–]\s*Crowd Supply\s*$", "", (title or "").strip()) or "Untitled project"


# --------------------------------------------------------------------------
# Scraping (needs Playwright, because the listing is rendered by JavaScript)
# --------------------------------------------------------------------------

def scrape(now: datetime) -> list[dict]:
    from playwright.sync_api import sync_playwright

    projects: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(
            user_agent=USER_AGENT, locale="en-US", timezone_id="Asia/Kolkata"
        )
        page = ctx.new_page()

        page.goto(LIST_URL, wait_until="networkidle", timeout=60_000)
        hrefs = page.evaluate(
            """() => {
                const root = document.querySelector('main') || document.body;
                return [...root.querySelectorAll('a[href]')].map(a => a.href);
            }"""
        )
        urls = sorted({normalize_url(h) for h in hrefs if is_project_url(h)})
        print(f"Listing page links that look like projects: {len(urls)}")

        for url in urls:
            try:
                page.goto(url, wait_until="networkidle", timeout=60_000)
                info = page.evaluate(
                    """() => {
                        const meta = n => (document.querySelector(
                            `meta[property="${n}"], meta[name="${n}"]`) || {}).content || '';
                        const h1 = document.querySelector('h1');
                        return {
                            title: meta('og:title') || (h1 && h1.innerText) || document.title,
                            description: meta('og:description') || meta('description'),
                            text: document.body.innerText,
                        };
                    }"""
                )
            except Exception as exc:  # one bad page shouldn't stop the run
                print(f"  ! could not read {url}: {exc}")
                continue

            text = info["text"]
            if not looks_like_crowdfunding(text):
                print(f"  - skipped (no funding info): {url}")
                continue
            ends_at, source = parse_end(text, now)
            projects.append({
                "url": url,
                "title": clean_title(info["title"]),
                "description": (info["description"] or "").strip(),
                "ends_at": ends_at.isoformat() if ends_at else None,
                "ends_source": source,
                **parse_funding(text),
            })
            print(f"  + {clean_title(info['title'])}  ends: {ends_at or 'unknown'}")
        browser.close()
    return projects


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

def email_configured() -> bool:
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def whatsapp_configured() -> bool:
    return bool(os.environ.get("WHATSAPP_PHONE") and os.environ.get("CALLMEBOT_APIKEY"))


def split_message(text: str, limit: int = 1500) -> list[str]:
    """Split long text on blank lines so each WhatsApp message stays short."""
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        candidate = f"{cur}\n\n{para}" if cur else para
        if len(candidate) <= limit:
            cur = candidate
            continue
        if cur:
            chunks.append(cur)
        while len(para) > limit:
            chunks.append(para[:limit])
            para = para[limit:]
        cur = para
    if cur:
        chunks.append(cur)
    return chunks


_last_whatsapp = 0.0


def send_whatsapp(text: str) -> None:
    global _last_whatsapp
    phone = re.sub(r"[\s\-()]", "", os.environ["WHATSAPP_PHONE"])
    apikey = os.environ["CALLMEBOT_APIKEY"].strip()
    for chunk in split_message(text):
        # The free service is shared; space messages out so none get dropped.
        wait = 4 - (time.monotonic() - _last_whatsapp)
        if wait > 0:
            time.sleep(wait)
        url = "https://api.callmebot.com/whatsapp.php?" + urlencode(
            {"phone": phone, "text": chunk, "apikey": apikey}
        )
        req = urllib.request.Request(url, headers={"User-Agent": "crowdsupply-monitor"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            reply = resp.read().decode("utf-8", "replace")
        _last_whatsapp = time.monotonic()
        low = reply.lower()
        # Only treat clear rejections as failures, so a delivered message is
        # never re-sent just because the reply wording changed.
        if "apikey" in low and "invalid" in low:
            raise RuntimeError(f"CallMeBot rejected the API key: {reply[:300]}")
        if "queued" not in low and "sent" not in low:
            print(f"  (CallMeBot reply: {re.sub(r'<[^>]+>', ' ', reply)[:200].strip()})")
    print("WhatsApp sent")


def notify(subject: str, body: str, whatsapp_text: str) -> None:
    """Send one alert on every configured channel. Fails only if all fail."""
    if DRY_RUN:
        print(f"\n===== [DRY RUN] EMAIL: {subject} =====\n{body}\n")
        for i, chunk in enumerate(split_message(whatsapp_text), 1):
            print(f"===== [DRY RUN] WHATSAPP message {i} =====\n{chunk}\n")
        return
    channels = []
    if whatsapp_configured():
        channels.append(("WhatsApp", lambda: send_whatsapp(whatsapp_text)))
    if email_configured():
        channels.append(("email", lambda: send_email(subject, body)))
    errors = []
    for name, fn in channels:
        try:
            fn()
        except Exception as exc:
            print(f"  ! {name} failed: {exc}")
            errors.append(f"{name}: {exc}")
    if len(errors) == len(channels):
        raise RuntimeError("Could not send alert on any channel: " + "; ".join(errors))


def send_email(subject: str, body: str) -> None:
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    to = os.environ.get("EMAIL_TO") or user
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, timeout=30) as s:
        s.login(user, password)
        s.send_message(msg)
    print(f"Sent: {subject}")


def fmt_end(p: dict, now: datetime) -> str:
    if not p.get("ends_at"):
        return "End date not shown on the page"
    ends = datetime.fromisoformat(p["ends_at"])
    when = ends.strftime("%a %d %b %Y, %I:%M %p IST")
    if p.get("ends_source") == "approx":
        days = max(0, round((ends - now).total_seconds() / 86400))
        return f"About {days} day(s) left (around {ends.strftime('%d %b %Y')})"
    return when


def fmt_funding(p: dict) -> str:
    bits = []
    if p.get("raised"):
        bits.append(f"${p['raised']} raised")
    if p.get("goal"):
        bits.append(f"of ${p['goal']} goal")
    if p.get("percent_funded"):
        bits.append(f"({p['percent_funded']}% funded)")
    return " ".join(bits)


def project_block(p: dict, now: datetime) -> str:
    lines = [p["title"]]
    if p.get("description"):
        lines.append(p["description"])
    lines.append("")
    if f := fmt_funding(p):
        lines.append(f"Funding: {f}")
    lines.append(f"Funding ends: {fmt_end(p, now)}")
    lines.append(p["url"])
    return "\n".join(lines)


def wa_block(p: dict, now: datetime) -> str:
    """Compact WhatsApp version (WhatsApp shows *text* as bold)."""
    lines = [f"*{p['title']}*"]
    if f := fmt_funding(p):
        lines.append(f)
    lines.append(f"Ends: {fmt_end(p, now)}")
    lines.append(p["url"])
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Core logic (pure apart from the send callback - unit-testable)
# --------------------------------------------------------------------------

def process(state: dict, scraped: list[dict], now: datetime, send=notify) -> dict:
    known: dict = state.setdefault("projects", {})
    first_run = not known
    new_ones: list[dict] = []

    current_urls = {p["url"] for p in scraped}
    for url, rec in known.items():
        rec["active"] = url in current_urls

    for p in scraped:
        rec = known.get(p["url"])
        if rec is None:
            rec = known[p["url"]] = {
                "first_seen": now.isoformat(),
                "notified_new": False,
                "notified_ending": False,
            }
            new_ones.append(p)
        # A real (not estimated) end date that moved later = campaign extended.
        old_end = rec.get("ends_at")
        if (
            rec.get("notified_ending")
            and p.get("ends_source") == "date"
            and rec.get("ends_source") == "date"
            and old_end and p.get("ends_at")
            and datetime.fromisoformat(p["ends_at"])
            > datetime.fromisoformat(old_end) + timedelta(days=1)
        ):
            rec["notified_ending"] = False
        rec.update({k: v for k, v in p.items() if k != "url"})
        rec["last_seen"] = now.isoformat()
        rec["active"] = True

    # 1) New-project emails (one summary on the very first run).
    if first_run and new_ones:
        body = "Now watching these Crowd Supply crowdfunding projects.\n" \
               "You'll get an email for each new one, and a reminder a day " \
               "before each one ends.\n\n" + \
               "\n\n----------\n\n".join(project_block(p, now) for p in new_ones)
        wa = f"👀 *Now watching {len(new_ones)} Crowd Supply projects*\n" \
             "You'll get a message for each new one, and a reminder a day " \
             "before each one ends.\n\n" + \
             "\n\n".join(wa_block(p, now) for p in new_ones)
        send(f"Watching {len(new_ones)} Crowd Supply projects", body, wa)
        for p in new_ones:
            known[p["url"]]["notified_new"] = True
    else:
        for p in new_ones:
            send(
                f"New on Crowd Supply: {p['title']}",
                project_block(p, now),
                "🆕 *New on Crowd Supply*\n" + wa_block(p, now),
            )
            known[p["url"]]["notified_new"] = True

    # 2) Ending-soon reminders.
    for url, rec in known.items():
        if not rec.get("active") or rec.get("notified_ending") or not rec.get("ends_at"):
            continue
        remaining = datetime.fromisoformat(rec["ends_at"]) - now
        if timedelta(0) < remaining <= REMIND_WINDOW:
            p = {"url": url, **rec}
            send(
                f"Ends in about a day: {rec['title']}",
                "Funding for this project ends soon - last chance to back it.\n\n"
                + project_block(p, now),
                "⏰ *Ends in about a day*\n" + wa_block(p, now),
            )
            rec["notified_ending"] = True

    state["last_run"] = now.isoformat()
    return state


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"projects": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def main() -> int:
    now = datetime.now(IST)
    if not DRY_RUN and not (whatsapp_configured() or email_configured()):
        print("ERROR: set WHATSAPP_PHONE + CALLMEBOT_APIKEY and/or "
              "SMTP_USER + SMTP_PASSWORD (or DRY_RUN=1 to test).")
        return 1
    state = load_state()
    scraped = scrape(now)
    if not scraped:
        # Fail loudly (GitHub emails you about failed runs) instead of
        # silently doing nothing if the site layout changes or blocks us.
        print("ERROR: no crowdfunding projects found - the site may have changed.")
        return 1
    try:
        process(state, scraped, now)
    finally:
        save_state(state)  # keep record of anything already emailed
    return 0


if __name__ == "__main__":
    sys.exit(main())
