/**
 * Crowd Supply -> WhatsApp forwarder (Google Apps Script)
 *
 * Crowd Supply blocks automated visits to its website, so instead of reading
 * the site this script reads the emails Crowd Supply already sends you:
 *   - the Crowd Supply newsletter ("New: <project>" = a newly launched project)
 *   - project update emails for projects you've signed up to follow
 *     (launch announcements and "final hours" / "last chance" messages)
 * and sends a short WhatsApp message for each, via the free CallMeBot API.
 *
 * Setup (once):
 *   1. Fill in WHATSAPP_PHONE and CALLMEBOT_APIKEY below.
 *   2. Choose "setup" in the function menu at the top and click Run.
 *      Google will ask you to allow access to Gmail - allow it.
 *   3. You'll get a WhatsApp message confirming it works.
 * After that it checks your Gmail every 15 minutes by itself. Only emails that
 * arrive after setup are forwarded. To stop it, run "stop".
 */

// ============ 1. YOUR SETTINGS ============
const WHATSAPP_PHONE = '+91XXXXXXXXXX';     // your WhatsApp number with country code
const CALLMEBOT_APIKEY = 'PASTE_YOUR_KEY';  // the key the CallMeBot bot sent you

const CHECK_EVERY_MINUTES = 15;             // 1, 5, 10, 15 or 30
// Also forward Crowd Supply emails that are neither new projects nor
// launch / ending-soon updates (e.g. shipping updates, order emails):
const FORWARD_OTHER_CROWD_SUPPLY_EMAILS = false;
const GMAIL_QUERY = 'from:crowdsupply.com newer_than:7d';
const LABEL_NAME = 'Crowd Supply/Sent to WhatsApp';

// ============ 2. FUNCTIONS YOU RUN ============

/** Run once. Starts the 15-minute check and sends a test message. */
function setup() {
  checkSettings_();
  const props = PropertiesService.getScriptProperties();
  if (!props.getProperty('START_TIME')) {
    props.setProperty('START_TIME', String(Date.now()));
  }
  removeTriggers_();
  ScriptApp.newTrigger('checkCrowdSupplyEmails')
    .timeBased()
    .everyMinutes(CHECK_EVERY_MINUTES)
    .create();
  getLabel_();
  sendWhatsApp_(
    '✅ *Crowd Supply alerts are on*\n' +
    'You\'ll get a WhatsApp message when Crowd Supply emails you about a new ' +
    'project, a launch, or a campaign in its final hours.'
  );
  Logger.log('Set up. Checking Gmail every %s minutes.', CHECK_EVERY_MINUTES);
}

/** Sends one test WhatsApp message. */
function sendTestMessage() {
  checkSettings_();
  sendWhatsApp_('👋 Test message from your Crowd Supply forwarder.');
}

/** Stops the automatic checks. Run setup again to restart. */
function stop() {
  removeTriggers_();
  Logger.log('Stopped. Run setup to start again.');
}

/** Runs every few minutes (set up by setup). Safe to run by hand too. */
function checkCrowdSupplyEmails() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return; // a previous run is still going
  try {
    const props = PropertiesService.getScriptProperties();
    const start = Number(props.getProperty('START_TIME') || Date.now());
    const doneList = JSON.parse(props.getProperty('DONE_IDS') || '[]');
    const done = new Set(doneList);
    const label = getLabel_();

    const pending = [];
    GmailApp.search(GMAIL_QUERY, 0, 50).forEach(function (thread) {
      thread.getMessages().forEach(function (msg) {
        if (msg.getDate().getTime() >= start && !done.has(msg.getId())) {
          pending.push({ msg: msg, thread: thread });
        }
      });
    });
    pending.sort(function (a, b) { return a.msg.getDate() - b.msg.getDate(); });

    pending.forEach(function (item) {
      const msg = item.msg;
      const alert = buildAlert(msg.getSubject(), msg.getPlainBody(), msg.getBody(), {
        forwardOther: FORWARD_OTHER_CROWD_SUPPLY_EMAILS,
      });
      if (alert) {
        sendWhatsApp_(alert); // throws on failure -> retried next run
        item.thread.addLabel(label);
      }
      doneList.push(msg.getId());
      done.add(msg.getId());
      // Save after every email so nothing is sent twice if a later one fails.
      props.setProperty('DONE_IDS', JSON.stringify(doneList.slice(-500)));
    });
  } finally {
    lock.releaseLock();
  }
}

// ============ 3. EMAIL PARSING (no Google services - unit tested) ============

const NEW_LABELS = /^(new|now funding|now live|just launched|launched)$/i;
const ENDING_LABELS = /^(last chance|final hours|final days?|ending soon|ends soon|closing soon)$/i;
const ITEM_LINE = /^\s*([A-Za-z][A-Za-z ]{1,20}?)\s*:\s*(.+?)\s*$/;

const ENDING_RE = new RegExp([
  '\\b(final|last)\\s+(hours?|day|days|call|chance)\\b',
  '\\bends?\\s+(today|tonight|tomorrow|soon|in\\s+\\d+)',
  '\\bending\\s+(soon|today|tomorrow)',
  '\\b\\d+\\s+hours?\\s+(left|to\\s+go|remaining)',
  '\\bcampaign\\s+(ends|closes|is\\s+ending|is\\s+closing)',
  '\\blast\\s+\\d+\\s+hours\\b',
].join('|'), 'i');

const LAUNCH_RE = new RegExp([
  '\\bcampaign\\s+is\\s+(now\\s+)?live\\b',
  '\\b(we|we\'re|we are|is|are)\\s+(now\\s+)?live\\b',
  '\\bnow\\s+(live|funding)\\b',
  '\\bjust\\s+launched\\b',
  '\\bhas\\s+launched\\b',
  '\\blaunch(es|ed)?\\s+today\\b',
  '\\blive\\s+on\\s+crowd\\s*supply\\b',
].join('|'), 'i');

const SKIP_LINK_RE = /unsubscribe|preferences|mailto:|\/newsletter|\/account|privacy|\.(png|jpe?g|gif|svg)(\?|$)|facebook|twitter|x\.com|instagram|linkedin|youtube|mastodon/i;

function stripTags_(html) {
  return String(html || '')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&#39;|&rsquo;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\s+/g, ' ')
    .trim();
}

/** All links in an HTML email as [{text, href}]. */
function extractLinks(html) {
  const out = [];
  const re = /<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;
  let m;
  while ((m = re.exec(String(html || ''))) !== null) {
    out.push({ href: m[1].replace(/&amp;/g, '&'), text: stripTags_(m[2]) });
  }
  return out;
}

function urlsIn_(text) {
  return (String(text || '').match(/https?:\/\/[^\s<>()\[\]"']+/g) || [])
    .map(function (u) { return u.replace(/[.,;:!?]+$/, ''); });
}

/** Best link for a project name: an <a> whose text mentions it. */
function linkFor_(name, links, fallbackText) {
  const n = name.toLowerCase();
  for (let i = 0; i < links.length; i++) {
    const l = links[i];
    if (!SKIP_LINK_RE.test(l.href) && l.text.toLowerCase().indexOf(n) !== -1) return l.href;
  }
  const urls = urlsIn_(fallbackText).filter(function (u) { return !SKIP_LINK_RE.test(u); });
  return urls[0] || '';
}

/**
 * Pulls "New: X" (and "Last Chance: X" etc.) items out of a newsletter.
 * Returns {newItems: [...], endingItems: [...]}, each {name, description, url}.
 */
function extractNewsletterItems(plainBody, htmlBody) {
  const lines = String(plainBody || '').split(/\r?\n/);
  const links = extractLinks(htmlBody);
  const items = [];
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(ITEM_LINE);
    if (!m) continue;
    const label = m[1].trim();
    const isNew = NEW_LABELS.test(label);
    const isEnding = ENDING_LABELS.test(label);
    if (!isNew && !isEnding) continue;
    const name = m[2].replace(/\s*[<(\[]?https?:\/\/\S+[>)\]]?/g, '').trim();
    if (!name) continue;
    // Text until the next labelled item: description + any plain-text links.
    let block = '';
    let description = '';
    for (let j = i + 1; j < lines.length && j < i + 12; j++) {
      const l = lines[j].trim();
      const lm = l.match(ITEM_LINE);
      if (lm && (NEW_LABELS.test(lm[1].trim()) || ENDING_LABELS.test(lm[1].trim()) ||
                 /^coming soon|in stock|teardown|field report$/i.test(lm[1].trim()))) break;
      block += l + '\n';
      if (!description && l && !/^(learn more|order|back|sign up|read)/i.test(l) && !/^https?:\/\//.test(l)) {
        description = l.replace(/\s*[<(\[]?https?:\/\/\S+[>)\]]?/g, '').trim();
      }
    }
    items.push({
      kind: isNew ? 'new' : 'ending',
      name: name,
      description: description.length > 200 ? description.slice(0, 197) + '...' : description,
      url: linkFor_(name, links, block),
    });
  }
  return {
    newItems: items.filter(function (x) { return x.kind === 'new'; }),
    endingItems: items.filter(function (x) { return x.kind === 'ending'; }),
  };
}

/** First 1-2 meaningful lines of an email, for a short preview. */
function snippet_(plainBody) {
  const keep = [];
  String(plainBody || '').split(/\r?\n/).some(function (raw) {
    const l = raw.replace(/\s*[<(\[]?https?:\/\/\S+[>)\]]?/g, '').trim();
    if (!l || l.length < 25) return false;
    if (/^(hi|hello|dear|view (this|in)|unsubscribe|crowd supply$)/i.test(l)) return false;
    keep.push(l);
    return keep.length >= 2;
  });
  const s = keep.join(' ');
  return s.length > 220 ? s.slice(0, 217) + '...' : s;
}

/** Main link of a project update email. */
function mainLink_(plainBody, htmlBody) {
  const hrefs = extractLinks(htmlBody).map(function (l) { return l.href; })
    .concat(urlsIn_(plainBody))
    .filter(function (u) { return !SKIP_LINK_RE.test(u); });
  const cs = hrefs.filter(function (u) { return /crowdsupply\.com\/[^\/\s]+\/[^\/\s?#]+/i.test(u); });
  return cs[0] || hrefs[0] || '';
}

function itemLines_(item) {
  return ['*' + item.name + '*', item.description, item.url]
    .filter(function (x) { return x; }).join('\n');
}

/**
 * Turns one Crowd Supply email into a WhatsApp message, or null to skip it.
 */
function buildAlert(subject, plainBody, htmlBody, opts) {
  opts = opts || {};
  subject = String(subject || '').trim();
  const plain = plainBody || stripTags_(htmlBody);

  // 1) Newsletter with "New: ..." items.
  const nl = extractNewsletterItems(plain, htmlBody);
  if (nl.newItems.length || nl.endingItems.length) {
    const parts = [];
    if (nl.newItems.length) {
      parts.push('🆕 *New on Crowd Supply*\n\n' + nl.newItems.map(itemLines_).join('\n\n'));
    }
    if (nl.endingItems.length) {
      parts.push('⏰ *Ending soon on Crowd Supply*\n\n' + nl.endingItems.map(itemLines_).join('\n\n'));
    }
    return parts.join('\n\n');
  }

  const head = subject + '\n' + String(plain).slice(0, 1500);
  const preview = snippet_(plain);
  const link = mainLink_(plain, htmlBody);
  const tail = [preview, link].filter(function (x) { return x; }).join('\n');

  // 2) A followed project's campaign is about to end.
  if (ENDING_RE.test(head)) {
    return '⏰ *Ending soon on Crowd Supply*\n*' + subject + '*' + (tail ? '\n' + tail : '');
  }
  // 3) A followed project just launched.
  if (LAUNCH_RE.test(head)) {
    return '🚀 *Now live on Crowd Supply*\n*' + subject + '*' + (tail ? '\n' + tail : '');
  }
  // 4) Anything else from Crowd Supply.
  if (opts.forwardOther) {
    return '📧 *Crowd Supply email*\n*' + subject + '*' + (tail ? '\n' + tail : '');
  }
  return null;
}

/** Splits long text on blank lines so each WhatsApp message stays short. */
function splitMessage(text, limit) {
  limit = limit || 1500;
  const chunks = [];
  let cur = '';
  String(text).split('\n\n').forEach(function (para) {
    const cand = cur ? cur + '\n\n' + para : para;
    if (cand.length <= limit) { cur = cand; return; }
    if (cur) chunks.push(cur);
    while (para.length > limit) { chunks.push(para.slice(0, limit)); para = para.slice(limit); }
    cur = para;
  });
  if (cur) chunks.push(cur);
  return chunks;
}

// ============ 4. HELPERS THAT USE GOOGLE SERVICES ============

function checkSettings_() {
  if (/X{4}/.test(WHATSAPP_PHONE) || !/^\+?\d[\d\s-]{6,}$/.test(WHATSAPP_PHONE)) {
    throw new Error('Set WHATSAPP_PHONE at the top of the script, e.g. +919876543210');
  }
  if (!CALLMEBOT_APIKEY || /PASTE/.test(CALLMEBOT_APIKEY)) {
    throw new Error('Set CALLMEBOT_APIKEY at the top of the script (the key CallMeBot sent you).');
  }
}

function sendWhatsApp_(text) {
  const phone = WHATSAPP_PHONE.replace(/[\s\-()]/g, '');
  splitMessage(text).forEach(function (chunk, i) {
    if (i > 0) Utilities.sleep(4000); // the free service is shared; space messages out
    const url = 'https://api.callmebot.com/whatsapp.php' +
      '?phone=' + encodeURIComponent(phone) +
      '&text=' + encodeURIComponent(chunk) +
      '&apikey=' + encodeURIComponent(String(CALLMEBOT_APIKEY).trim());
    const resp = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    const code = resp.getResponseCode();
    const body = resp.getContentText() || '';
    if (code >= 400 || (/apikey/i.test(body) && /invalid/i.test(body))) {
      throw new Error('CallMeBot did not accept the message (HTTP ' + code + '): ' +
        body.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').slice(0, 200));
    }
  });
}

function getLabel_() {
  return GmailApp.getUserLabelByName(LABEL_NAME) || GmailApp.createLabel(LABEL_NAME);
}

function removeTriggers_() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'checkCrowdSupplyEmails') ScriptApp.deleteTrigger(t);
  });
}

// Lets the parsing functions be unit-tested outside Google (ignored in Apps Script).
if (typeof module !== 'undefined') {
  module.exports = { buildAlert, extractNewsletterItems, extractLinks, splitMessage };
}
