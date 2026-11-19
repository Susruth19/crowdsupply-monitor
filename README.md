# Crowd Supply crowdfunding monitor

> **Status: the site monitor is paused.** Crowd Supply now shows automated visitors (including GitHub's servers) a Cloudflare "Verify you are human" check, so it can't read the project list. Its schedule is switched off in `.github/workflows/monitor.yml`.
>
> **Use [`gmail-to-whatsapp/`](gmail-to-whatsapp/) instead.** It forwards Crowd Supply's own newsletter and project-update emails to your WhatsApp.

The original site monitor is described below, in case Crowd Supply's check is ever lifted.

Watches https://www.crowdsupply.com/crowdfunding and sends you a **WhatsApp message** (and/or email):

- **New project**: as soon as a new crowdfunding campaign shows up (checked every 6 hours).
- **Ending soon**: once, when a campaign has less than 24 hours of funding left.

The first run sends one summary of everything currently live, so you aren't flooded. After that, each new project gets its own message. It runs free on GitHub Actions, so your computer doesn't need to be on.

WhatsApp messages go through **CallMeBot**, a free service for sending alerts to your *own* WhatsApp number (personal use only).

## Setup (about 10 minutes)

### 1. Get your CallMeBot WhatsApp key
1. On your phone, open https://www.callmebot.com/blog/free-api-whatsapp-messages/ and save the bot's phone number shown there as a contact.
2. In WhatsApp, send that contact: `I allow callmebot to send me messages`
3. The bot replies with your API key. If nothing arrives within 2 minutes, CallMeBot says to try again after 24 hours.

### 2. (Optional) Email too: create a Gmail app password
1. Turn on 2-Step Verification for your Google account.
2. Go to https://myaccount.google.com/apppasswords and create one.
3. Copy the 16-character password.

Skip this step if WhatsApp alone is enough.

### 3. Create a GitHub repository
1. On github.com, click **New repository**. Make it **Private**.
2. Upload all files from this folder, keeping the `.github/workflows/monitor.yml` path.

### 4. Add your secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Name               | Value                                                   |
|--------------------|---------------------------------------------------------|
| `WHATSAPP_PHONE`   | your WhatsApp number with country code, e.g. `+919876543210` |
| `CALLMEBOT_APIKEY` | the key the bot sent you                                |
| `SMTP_USER`        | *(optional, for email)* your Gmail address              |
| `SMTP_PASSWORD`    | *(optional, for email)* the app password                |
| `EMAIL_TO`         | *(optional)* where emails go; defaults to `SMTP_USER`   |

Set up WhatsApp, email, or both. Whichever you set up gets used.

### 5. Run it once
Repo → **Actions → Crowd Supply monitor → Run workflow**. Within a few minutes you should get the summary on WhatsApp, and a `state.json` file will appear in the repo. From then on it runs by itself every 6 hours.

## How it works
- `monitor.py` opens the crowdfunding page in a headless Chrome (Playwright), collects project links, then opens each project page to read the title, funding progress and end date.
- `state.json` remembers which projects you've already been alerted about, so nothing is sent twice. If a campaign is extended, you'll get a fresh reminder before the new end date.
- If the script finds **no** projects (e.g. the site changed its layout), or no alert could be sent on any channel, the run fails on purpose and GitHub emails you about the failed run.
- Long WhatsApp messages (like the first summary) are split into several messages.

## Options
Set these as extra `env:` entries in `.github/workflows/monitor.yml`:
- `REMIND_HOURS_BEFORE`: the reminder window, default `24`.
- `DRY_RUN=1`: print alerts in the run log instead of sending them (for testing).

To check more or less often, change the `cron` line (it's in UTC; `"17 */6 * * *"` = every 6 hours).

## Run on your own computer instead
```bash
pip install -r requirements.txt
python -m playwright install chromium
DRY_RUN=1 python monitor.py          # test without sending anything
WHATSAPP_PHONE=+919876543210 CALLMEBOT_APIKEY=123456 python monitor.py
```
Then schedule it with cron (macOS/Linux) or Task Scheduler (Windows).
