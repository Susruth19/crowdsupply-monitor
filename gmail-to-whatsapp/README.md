# Crowd Supply emails → WhatsApp

Crowd Supply blocks automated visits to its website with a "Verify you are human" check, so this repo's site monitor can't run unattended. This forwarder uses the emails Crowd Supply already sends you instead, and passes them to your WhatsApp:

| Crowd Supply email | WhatsApp message |
|---|---|
| Newsletter item **"New: <project>"** | 🆕 New on Crowd Supply, with name, one-line description, link |
| Newsletter item **"Last Chance: / Final Hours: <project>"** | ⏰ Ending soon |
| Update from a project you follow saying **final hours / last chance / ends in 48 hours** | ⏰ Ending soon |
| Update from a project you follow saying **our campaign is live** | 🚀 Now live |
| Anything else (shipping updates, orders) | skipped (set `FORWARD_OTHER_CROWD_SUPPLY_EMAILS = true` to include) |

It runs inside your own Google account (Google Apps Script). It's free, and your computer doesn't need to be on.

**Limits, honestly:** new-project alerts arrive when the newsletter does (every few weeks), not the moment a project launches. "Ending soon" alerts depend on the project's creator posting a final-hours update, and most do, but not all.

## Setup (about 10 minutes)

### 1. Get Crowd Supply's emails
In your normal browser, on crowdsupply.com:
- Subscribe to the **Crowd Supply newsletter** (the box at the bottom of most pages).
- On any project you care about (including **Coming Soon** ones), click **Sign up / Subscribe for project updates**. Use the same Gmail address for both.

### 2. Get your CallMeBot WhatsApp key
1. On your phone, open https://www.callmebot.com/blog/free-api-whatsapp-messages/ and save the bot's number shown there as a contact.
2. In WhatsApp, send it: `I allow callmebot to send me messages`
3. The bot replies with your API key.

### 3. Create the script
1. Go to https://script.google.com, signed in to the Gmail account from step 1, and click **New project**.
2. Delete the sample code, then paste in everything from [`Code.gs`](Code.gs).
3. At the top, set `WHATSAPP_PHONE` (e.g. `+919876543210`) and `CALLMEBOT_APIKEY`.
4. Click **Save** (💾).

### 4. Turn it on
1. In the toolbar's function menu, choose **setup**, then click **Run**.
2. Google asks you to authorise it. Choose your account. If you see "Google hasn't verified this app", click **Advanced → Go to (project name)**. That's expected for your own scripts. Then click **Allow**.
3. You'll get a WhatsApp message saying alerts are on.

From then on it checks Gmail every 15 minutes. Forwarded threads get the Gmail label **Crowd Supply/Sent to WhatsApp**. Only emails that arrive after setup are forwarded.

**Other functions:** `sendTestMessage` sends a test WhatsApp, and `stop` turns it off (run `setup` to turn it back on).

## Troubleshooting
- **Nothing arrives:** in Apps Script, open **Executions** (left sidebar) to see each run and any error.
- **"CallMeBot did not accept the message":** check the phone number and API key at the top of the script.
- **Crowd Supply emails come from a different address:** change `GMAIL_QUERY`. The default is `from:crowdsupply.com newer_than:7d`.
