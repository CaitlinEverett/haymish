'use strict';

import { initCollections } from './components/collections.js';

// This file is a static packaged asset, so the daemon can't substitute the
// per-run token into it. The token is injected into the page markup instead and
// read back out here; every /api/* call still carries it.
const TOKEN = document.querySelector('meta[name="haymish-token"]')?.content || '';

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---- API plumbing ----------------------------------------------------------

async function api(path, body) {
  const opts = { headers: { 'X-Haymish-Token': TOKEN } };
  if (body !== undefined) {
    opts.method = 'POST';
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let resp;
  try {
    resp = await fetch(path, opts);
  } catch (e) {
    throw new Error('Could not reach the Haymish daemon — is it still running?');
  }
  let data = null;
  try { data = await resp.json(); } catch (e) { /* non-JSON body */ }
  if (!resp.ok) {
    throw new Error((data && data.error) ? data.error : (path + ' failed (HTTP ' + resp.status + ')'));
  }
  return data;
}

// Start a job via POST, then poll /api/jobs/<id> until done/error.
let jobInFlight = false;

async function runJob(startPath, body, onProgress) {
  if (jobInFlight) throw new Error('Another job is already running — wait for it to finish.');
  jobInFlight = true;
  try {
    const started = await api(startPath, body);
    const jobId = started.job;
    let delay = 600;
    for (;;) {
      await sleep(delay);
      delay = Math.min(Math.round(delay * 1.6), 8000);
      const j = await api('/api/jobs/' + encodeURIComponent(jobId));
      if (onProgress) onProgress(j.progress || {});
      if (j.state === 'done') return j.result;
      if (j.state === 'error') {
        const msg = (j.error && String(j.error).trim()) ? j.error : 'job failed';
        throw new Error(msg);
      }
    }
  } finally {
    jobInFlight = false;
  }
}

function phaseText(p) {
  if (!p || !p.phase) return 'working…';
  if (p.phase === 'applying' && p.rule) {
    const base = 'Applying ' + p.rule;
    if (p.total) return base + ' (' + (p.done || 0) + '/' + p.total + ')';
    return base + '…';
  }
  if (p.total) return p.phase + ' ' + (p.done || 0) + '/' + p.total;
  return p.phase + '…';
}

// ---- error banners (dedupe by key so the 15s poll doesn't stack them) ------

function banner(msg, key) {
  const holder = $('banners');
  if (key) {
    const existing = holder.querySelector('[data-key="' + key + '"] .msg');
    if (existing) { existing.textContent = msg; return; }
  }
  const div = document.createElement('div');
  div.className = 'banner';
  div.setAttribute('role', 'alert');
  if (key) div.dataset.key = key;
  const span = document.createElement('span');
  span.className = 'msg';
  span.textContent = msg;
  const btn = document.createElement('button');
  btn.className = 'dismiss';
  btn.type = 'button';
  btn.textContent = '✕';
  btn.setAttribute('aria-label', 'Dismiss');
  btn.addEventListener('click', () => div.remove());
  div.append(span, btn);
  holder.appendChild(div);
}
function clearBanner(key) {
  const el = $('banners').querySelector('[data-key="' + key + '"]');
  if (el) el.remove();
}

// ---- status chips ----------------------------------------------------------

function relTime(ts) {
  if (!ts) return null;
  const d = new Date(ts);
  if (isNaN(d)) return String(ts);
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.round(s / 60) + 'm ago';
  if (s < 86400) return Math.round(s / 3600) + 'h ago';
  return Math.round(s / 86400) + 'd ago';
}

function renderStatus(s) {
  const idx = $('chip-index');
  if (s.index && s.index.total != null) {
    const covered = s.index.covered || 0, total = s.index.total;
    const captioned = s.index.captioned;
    let label = 'Index ' + covered + '/' + total + ' embedded';
    if (captioned != null) label += ' · ' + captioned + ' captioned';
    idx.textContent = label;
    idx.className = 'chip ' + (covered === total ? 'green' : 'amber');
  } else if (s.library && s.library.error) {
    idx.textContent = 'Library failed to load';
    idx.className = 'chip amber';
    banner('Photos library failed to load: ' + s.library.error, 'library');
  } else {
    idx.textContent = 'Library loading…';
    idx.className = 'chip';
  }

  const staged = $('chip-staged');
  const n = s.staged_deletes || 0;
  staged.hidden = false;
  staged.textContent = n + ' staged delete' + (n === 1 ? '' : 's');

  const act = $('chip-activity');
  const rel = relTime(s.last_action_at);
  act.hidden = !rel;
  if (rel) act.textContent = 'last activity ' + rel;

  renderRules(s.rules || []);
}

async function refreshStatus() {
  try {
    renderStatus(await api('/api/status'));
    clearBanner('status');
  } catch (e) {
    banner('Status check failed: ' + e.message, 'status');
  }
}
refreshStatus();
setInterval(refreshStatus, 15000);

// ---- staged-deletes panel --------------------------------------------------

$('chip-staged').addEventListener('click', async () => {
  const panel = $('staged-panel');
  if (!panel.hidden) { panel.hidden = true; return; }
  try {
    const data = await api('/api/staged-deletes');
    const rows = data.staged || [];
    let html;
    if (!rows.length) {
      html = '<p class="muted">Nothing staged for deletion.</p>';
    } else {
      html = '<table><tr><th></th><th>Photo</th><th>Rule</th><th>Staged</th><th>Legacy export intact</th></tr>' +
        rows.map(r =>
          '<tr><td><img src="/thumb/' + encodeURIComponent(r.uuid) + '" alt="" width="36" height="36" ' +
          'style="object-fit:cover;border-radius:4px;" onerror="this.remove()"></td>' +
          '<td>' + esc(r.filename || r.uuid) +
          (r.filename && r.filename !== r.uuid
            ? '<br><span class="muted">' + esc(r.uuid) + '</span>' : '') + '</td>' +
          '<td>' + esc(r.rule) + '</td>' +
          '<td>' + esc(r.staged_at) + '</td>' +
          '<td>' + (r.backed_up ? 'yes' : 'no') + '</td></tr>').join('') +
        '</table>';
    }
    html += '<p class="note">' + esc(data.note || '') + '</p>';
    $('staged-body').innerHTML = html;
    panel.hidden = false;
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (e) {
    banner('Could not load staged deletes: ' + e.message);
  }
});

// ---- review grid (shared by ask, find-with-album, build) -------------------

let currentSession = null;   // the session payload behind the grid in #review-root

function noThumbTile() { return '<div class="no-thumb">no preview</div>'; }

// Sub-groups: when a rule's queue is big and heterogeneous the server splits it
// into labelled clusters so one decision can cover hundreds of photos. Grouping
// is a review convenience only -- the checkboxes stay the source of truth and
// the apply payload is unchanged.
const CARD_CAP = 48;
const foldedGroups = new Set();   // "<rule>\n<groupKey>" for groups folded away
const flatRules = new Set();      // rules the user switched back to a flat grid
const gridPageByKey = new Map();  // expand key -> 0-based page (CARD_CAP photos each)
let sessionPicks = null;          // ruleName -> Map(uuid -> checked); off-page picks live here
let gridSeq = 0;                  // unique ids so fold buttons can aria-control

function gridExpandKey(ruleName, groupKey) {
  return groupKey != null ? ruleName + '\n' + groupKey : ruleName;
}

function doctorHintHtml() {
  return '<p class="muted">Try <code>haymish doctor</code> for library and index health, ' +
    'or run index catch-up if captions or embeddings are behind.</p>';
}

function emptyStateMessage(payload, rules) {
  const state = payload.empty_state;
  const hasErrors = rules.some(r => (r.errors || []).length);
  if (state === 'all_rejected') {
    return '<p class="muted"><strong>Everything here was previously rejected.</strong> ' +
      'Unchecking in review remembers those photos for each rule — they will not reappear ' +
      'until you clear rejections in the catalog.</p>' + doctorHintHtml();
  }
  if (state === 'errors' || hasErrors) {
    return '<p class="muted"><strong>Rules could not produce a review queue.</strong> ' +
      'See errors below — often the AI index is missing or Ollama is unreachable.</p>' +
      doctorHintHtml();
  }
  return '<p class="muted"><strong>Nothing matched</strong> your enabled rules right now.</p>' +
    doctorHintHtml();
}

function initSessionPicks(rules) {
  const prior = sessionPicks;
  sessionPicks = new Map();
  for (const rule of rules) {
    const m = new Map();
    const old = prior && prior.get(rule.name);
    for (const c of rule.candidates || []) {
      const u = String(c.uuid);
      m.set(u, old && old.has(u) ? old.get(u) : true);
    }
    sessionPicks.set(rule.name, m);
  }
}

function syncPicksFromSection(section) {
  if (!sessionPicks || !section) return;
  section.querySelectorAll('.pick').forEach(cb => {
    const rm = sessionPicks.get(cb.dataset.rule);
    if (rm) rm.set(String(cb.dataset.uuid), cb.checked);
  });
}

function isPicked(ruleName, uuid, legacyMap) {
  const u = String(uuid);
  if (legacyMap && legacyMap.has(u)) return legacyMap.get(u) !== false;
  const rm = sessionPicks && sessionPicks.get(ruleName);
  if (rm && rm.has(u)) return rm.get(u) !== false;
  return true;
}

function renderSession(payload) {
  currentSession = payload;
  foldedGroups.clear();
  flatRules.clear();
  gridPageByKey.clear();
  $('apply-outcome').innerHTML = '';
  clearBanner('apply-session');
  const root = $('review-root');

  let html = '';
  if (payload.plan && payload.plan.description) {
    html += '<p class="plan-desc">' + esc(payload.plan.description) + '</p>';
  }
  const rules = payload.rules || [];
  initSessionPicks(rules);
  for (const rule of rules) {
    for (const g of ruleSubgroups(rule)) foldedGroups.add(rule.name + '\n' + g.key);
  }
  const hasCandidates = rules.some(r => (r.candidates || []).length);
  const hasErrors = rules.some(r => (r.errors || []).length);
  if (!rules.length || (!hasCandidates && !hasErrors)) {
    root.innerHTML = html + emptyStateMessage(payload, rules);
    $('apply-bar').hidden = true;
    root.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    return;
  }

  for (const rule of rules) {
    // Keep a rule visible with zero candidates when it has an error to explain
    // why (e.g. "no photos indexed yet") -- otherwise this silently reads as
    // "nothing to do" when the truth is "couldn't check".
    if (!(rule.candidates || []).length && !(rule.errors || []).length) continue;
    html += ruleSectionHtml(rule);
  }
  root.innerHTML = html;
  $('apply-status').textContent = '';
  $('apply-btn').disabled = false;
  $('apply-bar').hidden = !hasCandidates;
  updateCount();
  root.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// Resolve a rule's declared subgroups against its candidate list. Every uuid
// gets at most one card: a uuid named by two groups lands in the first, a uuid
// named by no group falls into a trailing "not grouped" bucket, and a uuid the
// server named but never listed as a candidate is dropped. That keeps the
// checkbox count identical to the flat grid, so the apply bar can't drift.
function ruleSubgroups(rule) {
  const declared = Array.isArray(rule.subgroups) ? rule.subgroups : [];
  if (!declared.length) return [];
  const byUuid = new Map();
  for (const c of rule.candidates || []) {
    if (c && c.uuid != null && !byUuid.has(String(c.uuid))) byUuid.set(String(c.uuid), c);
  }
  const seen = new Set();
  const groups = [];
  declared.forEach((g, i) => {
    const items = [];
    for (const u of (g && g.uuids) || []) {
      const uuid = String(u);
      if (seen.has(uuid) || !byUuid.has(uuid)) continue;
      seen.add(uuid);
      items.push(byUuid.get(uuid));
    }
    if (!items.length) return;
    const key = (g && g.key != null && g.key !== '') ? String(g.key) : ('group-' + i);
    const label = (g && g.label) ? String(g.label) : key;
    groups.push({ key, label, items });
  });
  if (!groups.length) return [];
  const leftover = [];
  for (const [uuid, c] of byUuid) {
    if (!seen.has(uuid)) leftover.push(c);
  }
  if (leftover.length) groups.push({ key: '__ungrouped__', label: 'not grouped', items: leftover });
  return groups;
}

function photoCount(n) { return n + ' photo' + (n === 1 ? '' : 's'); }

function candidateCardHtml(ruleName, c, picked) {
  const thumb = c.thumb
    ? '<img src="/thumb/' + encodeURIComponent(c.uuid) + '" alt="" loading="lazy">'
    : noThumbTile();
  return '<label class="card" tabindex="0">' +
    '<input type="checkbox" class="pick" data-rule="' + esc(ruleName) + '" data-uuid="' +
    esc(c.uuid) + '"' + (picked ? ' checked' : '') + '>' +
    '<div class="thumb">' + thumb + '</div>' +
    '<p class="filename" title="' + esc(c.filename) + (c.date ? ' · ' + esc(c.date) : '') + '">' +
    esc(c.filename) + '</p>' +
    (c.detail ? '<p class="detail" title="' + esc(c.detail) + '">' + esc(c.detail) + '</p>' : '') +
    '</label>';
}

function gridHtml(ruleName, items, attrs, expandKey) {
  const key = expandKey != null ? expandKey : gridExpandKey(ruleName, null);
  const total = items.length;
  const totalPages = Math.max(1, Math.ceil(total / CARD_CAP));
  let page = gridPageByKey.get(key) || 0;
  if (page >= totalPages) page = totalPages - 1;
  if (page < 0) page = 0;
  gridPageByKey.set(key, page);
  const start = page * CARD_CAP;
  const shown = items.slice(start, start + CARD_CAP);
  const end = start + shown.length;
  let html = '<div class="grid"' + (attrs || '') + '>' +
    shown.map(c => candidateCardHtml(ruleName, c, isPicked(ruleName, c.uuid))).join('') +
    '</div>';
  if (total > CARD_CAP) {
    html += '<div class="grid-pager" role="navigation" aria-label="Photos in this group">';
    if (page > 0) {
      html += '<button type="button" class="link-btn" data-action="grid-page" data-expand-key="' +
        esc(key) + '" data-page="' + (page - 1) + '">Previous</button>';
    }
    html += '<span class="grid-pager-meta">' + (start + 1) + '–' + end + ' of ' + total + '</span>';
    if (page < totalPages - 1) {
      html += '<button type="button" class="link-btn" data-action="grid-page" data-expand-key="' +
        esc(key) + '" data-page="' + (page + 1) + '">Next</button>';
    }
    html += '</div>';
  }
  return html;
}

function subgroupHtml(rule, group) {
  const foldKey = rule.name + '\n' + group.key;
  const folded = foldedGroups.has(foldKey);
  const expandKey = gridExpandKey(rule.name, group.key);
  const gridId = 'rg-' + (++gridSeq);
  const body = folded
    ? '<p class="grid-folded muted">' + esc(photoCount(group.items.length)) +
      ' in this group — choose “show group” to review (' + CARD_CAP + ' per page).</p>'
    : gridHtml(rule.name, group.items, ' id="' + gridId + '"', expandKey);
  return '<section class="subgroup" data-folded="' + (folded ? '1' : '0') +
    '" data-fold-key="' + esc(foldKey) + '" data-group-key="' + esc(group.key) + '">' +
    '<div class="subgroup-header">' +
    '<h4 class="subgroup-title">' + esc(group.label) +
    ' <span class="subgroup-count">— ' + photoCount(group.items.length) + '</span></h4>' +
    '<div class="subgroup-actions">' +
    '<button type="button" class="link-btn" data-action="group-select-all">select all</button>' +
    '<button type="button" class="link-btn" data-action="group-select-none">select none</button>' +
    '<button type="button" class="link-btn" data-action="group-fold" aria-controls="' + gridId +
    '" aria-expanded="' + (folded ? 'false' : 'true') + '">' +
    (folded ? 'show group' : 'hide group') + '</button>' +
    '</div></div>' + body +
    '</section>';
}

function ruleSectionHtml(rule) {
  const cands = rule.candidates || [];
  const errors = rule.errors || [];
  const groups = ruleSubgroups(rule);
  const grouped = groups.length > 0 && !flatRules.has(rule.name);

  let meta = cands.length + ' matched';
  if (grouped) meta += ' · ' + groups.length + ' group' + (groups.length === 1 ? '' : 's');
  meta += ' · ' + esc(rule.action);

  const viewToggle = groups.length
    ? '<span class="view-toggle" role="group" aria-label="Review layout for ' + esc(rule.name) + '">' +
      '<button type="button" class="link-btn" data-action="rule-view" data-view="grouped" aria-pressed="' +
      (grouped ? 'true' : 'false') + '">Grouped</button>' +
      '<button type="button" class="link-btn" data-action="rule-view" data-view="flat" aria-pressed="' +
      (grouped ? 'false' : 'true') + '">Flat</button></span>'
    : '';
  const bulk = cands.length
    ? '<button type="button" class="link-btn" data-action="select-all">select all</button>' +
      '<button type="button" class="link-btn" data-action="select-none">select none</button>'
    : '';

  const body = grouped
    ? groups.map(g => subgroupHtml(rule, g)).join('')
    : gridHtml(rule.name, cands, '', gridExpandKey(rule.name, null));

  const needGroupBanner = cands.length >= 24 && !(rule.subgroups || []).length;
  const groupBanner = needGroupBanner
    ? '<p class="rule-banner">Grouping unavailable — run ' +
      '<code>haymish index --catch-up-captions</code> (or refresh index with captions) ' +
      'so embeddings exist for clustering.</p>'
    : '';

  return '<section class="rule-section" data-rule="' + esc(rule.name) + '">' +
    '<div class="rule-header"><div>' +
    '<h3>' + esc(rule.name) + '</h3>' +
    '<p class="rule-meta">' + meta + '</p>' +
    errors.map(e => '<p class="rule-error">' + esc(e) + '</p>').join('') +
    '</div><div class="rule-actions">' + viewToggle + bulk + '</div></div>' +
    groupBanner + body + '</section>';
}

function ruleSectionFor(name) {
  return Array.from($('review-root').querySelectorAll('.rule-section'))
    .find(sec => sec.dataset.rule === name) || null;
}

function refreshSubgroup(ruleName, groupKey) {
  if (!currentSession) return;
  const rule = (currentSession.rules || []).find(r => r.name === ruleName);
  const group = rule && ruleSubgroups(rule).find(g => g.key === groupKey);
  const section = ruleSectionFor(ruleName);
  if (!rule || !group || !section) return;
  const sel = '.subgroup[data-group-key="' + CSS.escape(groupKey) + '"]';
  const el = section.querySelector(sel);
  if (!el) return;
  const foldBtn = el.querySelector('[data-action="group-fold"]');
  const hadFocus = foldBtn && document.activeElement === foldBtn;
  el.outerHTML = subgroupHtml(rule, group);
  if (hadFocus) {
    const fresh = section.querySelector(sel);
    const btn = fresh && fresh.querySelector('[data-action="group-fold"]');
    if (btn) btn.focus();
  }
}

function toggleGroupFold(group) {
  const key = group.dataset.foldKey;
  if (!key || !currentSession) return;
  const nl = key.indexOf('\n');
  if (nl < 0) return;
  const ruleName = key.slice(0, nl);
  const groupKey = key.slice(nl + 1);
  syncPicksFromSection(group.closest('.rule-section'));
  if (group.dataset.folded === '1') foldedGroups.delete(key);
  else foldedGroups.add(key);
  refreshSubgroup(ruleName, groupKey);
}

// Re-render one rule in the other layout, carrying every checkbox across.
function setRuleView(name, view) {
  if (!currentSession) return;
  const rule = (currentSession.rules || []).find(r => r.name === name);
  const section = ruleSectionFor(name);
  if (!rule || !section) return;
  if (view === 'flat') flatRules.add(name); else flatRules.delete(name);
  syncPicksFromSection(section);
  section.outerHTML = ruleSectionHtml(rule);
  updateCount();
  const fresh = ruleSectionFor(name);
  if (fresh) {
    const btn = fresh.querySelector('[data-action="rule-view"][data-view="' + view + '"]');
    if (btn) btn.focus();
  }
}

function updateCount() {
  let n = 0;
  if (sessionPicks) {
    for (const rm of sessionPicks.values()) {
      for (const v of rm.values()) if (v) n++;
    }
  } else {
    n = $('review-root').querySelectorAll('.pick:checked').length;
  }
  $('sel-count').textContent = n;
}

$('review-root').addEventListener('change', e => {
  if (!e.target.classList.contains('pick')) return;
  const rm = sessionPicks && sessionPicks.get(e.target.dataset.rule);
  if (rm) rm.set(String(e.target.dataset.uuid), e.target.checked);
  updateCount();
});
$('review-root').addEventListener('click', e => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const action = btn.dataset.action;

  if (action === 'group-select-all' || action === 'group-select-none') {
    const group = btn.closest('.subgroup');
    if (!group) return;
    const checked = action === 'group-select-all';
    const ruleName = group.closest('.rule-section')?.dataset.rule;
    const groupKey = group.dataset.groupKey;
    if (ruleName && groupKey && sessionPicks && currentSession) {
      const rule = (currentSession.rules || []).find(r => r.name === ruleName);
      const g = rule && ruleSubgroups(rule).find(x => x.key === groupKey);
      const rm = sessionPicks.get(ruleName);
      if (g && rm) for (const c of g.items) rm.set(String(c.uuid), checked);
    }
    group.querySelectorAll('.pick').forEach(cb => { cb.checked = checked; });
    updateCount();
    return;
  }
  if (action === 'group-fold') {
    const group = btn.closest('.subgroup');
    if (group) toggleGroupFold(group);
    return;
  }
  if (action === 'grid-page') {
    const key = btn.dataset.expandKey;
    const page = parseInt(btn.dataset.page, 10);
    if (key && !Number.isNaN(page)) {
      gridPageByKey.set(key, page);
      const section = btn.closest('.rule-section');
      if (section && currentSession) {
        syncPicksFromSection(section);
        setRuleView(section.dataset.rule, flatRules.has(section.dataset.rule) ? 'flat' : 'grouped');
      }
    }
    return;
  }
  if (action === 'rule-view') {
    const section = btn.closest('.rule-section');
    if (section) setRuleView(section.dataset.rule, btn.dataset.view);
    return;
  }
  if (action !== 'select-all' && action !== 'select-none') return;
  const section = btn.closest('.rule-section');
  if (!section) return;
  const checked = action === 'select-all';
  const rm = sessionPicks && sessionPicks.get(section.dataset.rule);
  if (rm) for (const k of rm.keys()) rm.set(k, checked);
  section.querySelectorAll('.pick').forEach(cb => { cb.checked = checked; });
  updateCount();
});

function reviewSelectionTotals() {
  let selected = 0;
  let rejects = 0;
  for (const rule of (currentSession && currentSession.rules) || []) {
    const total = (rule.candidates || []).length;
    if (!total) continue;
    let sel = 0;
    const rm = sessionPicks && sessionPicks.get(rule.name);
    if (rm) {
      for (const v of rm.values()) if (v) sel++;
    } else {
      sel = Array.from($('review-root').querySelectorAll('.pick:checked'))
        .filter(cb => cb.dataset.rule === rule.name).length;
    }
    selected += sel;
    rejects += total - sel;
  }
  return { selected, rejects };
}

function isSessionExpiredError(msg) {
  const m = String(msg || '').toLowerCase();
  return m.includes('expired session') || m.includes('unknown or expired session')
    || m.includes('unknown session');
}

function offerRebuildQueue(message) {
  const holder = $('banners');
  const key = 'apply-session';
  let div = holder.querySelector('[data-key="' + key + '"]');
  if (!div) {
    div = document.createElement('div');
    div.className = 'banner';
    div.dataset.key = key;
    div.setAttribute('role', 'alert');
    holder.appendChild(div);
  }
  div.innerHTML = '';
  const span = document.createElement('span');
  span.className = 'msg';
  span.textContent = message;
  const rebuild = document.createElement('button');
  rebuild.type = 'button';
  rebuild.className = 'link-btn';
  rebuild.textContent = 'Rebuild queue';
  rebuild.addEventListener('click', () => { div.remove(); rebuildReviewQueue(); });
  const dismiss = document.createElement('button');
  dismiss.className = 'dismiss';
  dismiss.type = 'button';
  dismiss.textContent = '✕';
  dismiss.setAttribute('aria-label', 'Dismiss');
  dismiss.addEventListener('click', () => div.remove());
  div.append(span, rebuild, dismiss);
}

async function rebuildReviewQueue() {
  const prog = $('review-progress');
  prog.textContent = 'starting…';
  try {
    const result = await runJob('/api/review/build', {},
      p => { prog.textContent = phaseText(p); });
    prog.textContent = '';
    renderSession(result);
  } catch (err) {
    prog.textContent = '';
    banner('Review build failed: ' + err.message);
  }
}

const REVIEW_CONTROL = 'input, button, label, select, textarea, a';

$('review-root').addEventListener('keydown', e => {
  if (e.target.closest('input[type="text"], textarea')) return;
  const card = e.target.closest('label.card');
  if (e.key === ' ' || e.key === 'Spacebar') {
    if (e.target.closest(REVIEW_CONTROL) && !card) return;
    if (card) {
      e.preventDefault();
      const cb = card.querySelector('.pick');
      if (cb) {
        cb.checked = !cb.checked;
        const rm = sessionPicks && sessionPicks.get(cb.dataset.rule);
        if (rm) rm.set(String(cb.dataset.uuid), cb.checked);
        updateCount();
      }
      return;
    }
  }
  if (e.key !== 'a' && e.key !== 'n') return;
  if (e.target.closest(REVIEW_CONTROL)) return;
  const scope = card
    ? (card.closest('.subgroup') || card.closest('.rule-section'))
    : (document.activeElement && document.activeElement.closest('.subgroup, .rule-section'));
  if (!scope) return;
  e.preventDefault();
  const checked = e.key === 'a';
  const ruleName = scope.closest('.rule-section')?.dataset.rule;
  const groupKey = scope.classList.contains('subgroup') ? scope.dataset.groupKey : null;
  if (sessionPicks && ruleName && currentSession) {
    const rule = (currentSession.rules || []).find(r => r.name === ruleName);
    const rm = sessionPicks.get(ruleName);
    if (rm && rule) {
      const uuids = groupKey
        ? ((ruleSubgroups(rule).find(g => g.key === groupKey) || {}).items || []).map(c => String(c.uuid))
        : [...rm.keys()];
      for (const u of uuids) rm.set(u, checked);
    }
  }
  scope.querySelectorAll('.pick').forEach(cb => { cb.checked = checked; });
  updateCount();
});

$('apply-btn').addEventListener('click', async () => {
  if (!currentSession || jobInFlight) return;
  const btn = $('apply-btn'), status = $('apply-status');
  const totals = reviewSelectionTotals();
  const impact = totals.selected + totals.rejects;
  if (impact > 200) {
    const ok = window.confirm(
      'Apply will act on ' + totals.selected + ' selected photo(s) and record ' +
      totals.rejects + ' rejection(s) (' + impact + ' total). Continue?');
    if (!ok) return;
  }
  // Every rule in the session is submitted, so unchecked candidates get recorded
  // as rejections even when a whole rule is deselected.
  const selections = {};
  for (const rule of currentSession.rules || []) {
    const rm = sessionPicks && sessionPicks.get(rule.name);
    if (rm) {
      selections[rule.name] = [];
      for (const [uuid, picked] of rm) if (picked) selections[rule.name].push(uuid);
    } else {
      selections[rule.name] = [];
      $('review-root').querySelectorAll('.pick:checked').forEach(cb => {
        if (cb.dataset.rule === rule.name) selections[rule.name].push(cb.dataset.uuid);
      });
    }
  }
  syncPicksFromSection($('review-root'));
  btn.disabled = true;
  status.textContent = 'Applying…';
  try {
    const result = await runJob('/api/apply',
      { session: currentSession.session, selections },
      p => { status.textContent = phaseText(p); });
    status.textContent = '';
    $('apply-bar').hidden = true;
    $('review-root').innerHTML = '';
    currentSession = null;
    renderApplyOutcome(result);
    refreshStatus();
  } catch (e) {
    status.textContent = '';
    btn.disabled = false;
    if (isSessionExpiredError(e.message)) {
      offerRebuildQueue('Apply failed — review session expired. Rebuild the queue and try again.');
    } else {
      banner('Apply failed: ' + e.message);
    }
  }
});

function renderApplyOutcome(result) {
  const outcomes = (result && result.outcomes) || [];
  let html = '<div class="outcome"><strong>Applied.</strong><ul>';
  if (!outcomes.length) html += '<li class="muted">No outcomes reported.</li>';
  for (const o of outcomes) {
    const bits = [];
    if (o.filed) bits.push(o.filed + ' filed');
    if (o.hidden) bits.push(o.hidden + ' hidden');
    if (o.archived) bits.push(o.archived + ' archived');
    if (o.staged_deletes) bits.push(o.staged_deletes + ' staged for delete');
    if (o.rejected) bits.push(o.rejected + ' rejected');
    html += '<li><strong>' + esc(o.rule) + '</strong> — ' +
      (bits.length ? esc(bits.join(' · ')) : 'no actions taken') +
      ((o.errors && o.errors.length)
        ? '<div class="err">' + o.errors.map(esc).join('<br>') + '</div>' : '') +
      '</li>';
  }
  html += '</ul></div>';
  $('apply-outcome').innerHTML = html;
  $('apply-outcome').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ---- Ask -------------------------------------------------------------------

$('ask-form').addEventListener('submit', async e => {
  e.preventDefault();
  const request = $('ask-input').value.trim();
  if (!request) return;
  const btn = $('ask-btn'), prog = $('ask-progress');
  btn.disabled = true;
  prog.textContent = 'starting…';
  try {
    const result = await runJob('/api/ask', { request },
      p => { prog.textContent = phaseText(p); });
    prog.textContent = '';
    renderSession(result);
  } catch (err) {
    prog.textContent = '';
    banner('Ask failed: ' + err.message);
  } finally {
    btn.disabled = false;
  }
});

// ---- Find ------------------------------------------------------------------

let lastFindQuery = '';

$('find-form').addEventListener('submit', async e => {
  e.preventDefault();
  const query = $('find-input').value.trim();
  if (!query) return;
  const btn = $('find-btn'), prog = $('find-progress');
  btn.disabled = true;
  prog.textContent = 'searching…';
  try {
    const result = await runJob('/api/find', { query, top: 24 },
      p => { prog.textContent = phaseText(p); });
    prog.textContent = '';
    lastFindQuery = query;
    renderFindResults(query, result);
  } catch (err) {
    prog.textContent = '';
    banner('Find failed: ' + err.message);
  } finally {
    btn.disabled = false;
  }
});

function renderFindResults(query, result) {
  const matches = (result && result.matches) || [];
  $('find-summary').textContent = matches.length
    ? matches.length + ' match' + (matches.length === 1 ? '' : 'es') + ' for “' + query +
      '” (searched ' + (result.indexed || 0) + ' indexed photos)'
    : 'No matches for “' + query + '”.';
  $('find-grid').innerHTML = matches.map(m =>
    '<div class="card" title="' + esc(m.caption || '') + '">' +
    '<span class="score">' + esc(m.score) + '</span>' +
    '<div class="thumb"><img src="/thumb/' + encodeURIComponent(m.uuid) + '" alt="" loading="lazy" ' +
    'onerror="this.parentNode.innerHTML=\'<div class=&quot;no-thumb&quot;>no preview</div>\'"></div>' +
    '<p class="filename" title="' + esc(m.filename) + '">' + esc(m.filename) + '</p>' +
    (m.date ? '<p class="detail">' + esc(m.date) + '</p>' : '') +
    '</div>').join('');
  $('find-results-block').hidden = false;
  $('find-results-block').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

$('album-form').addEventListener('submit', async e => {
  e.preventDefault();
  const album = $('album-input').value.trim();
  if (!album || !lastFindQuery) return;
  const btn = $('album-btn'), prog = $('find-progress');
  btn.disabled = true;
  prog.textContent = 'building preview…';
  try {
    const result = await runJob('/api/find', { query: lastFindQuery, top: 24, album },
      p => { prog.textContent = phaseText(p); });
    prog.textContent = '';
    renderSession(result);   // find-with-album returns a session payload
  } catch (err) {
    prog.textContent = '';
    banner('File-into-album failed: ' + err.message);
  } finally {
    btn.disabled = false;
  }
});

// ---- Build review queue ----------------------------------------------------

$('build-btn').addEventListener('click', async () => {
  const btn = $('build-btn');
  if (jobInFlight) return;
  btn.disabled = true;
  try {
    await rebuildReviewQueue();
  } finally {
    btn.disabled = false;
  }
});

// ---- Galleries -------------------------------------------------------------
// Photos.app can't show one cover standing in for the other 456 photos -- hiding
// a photo hides it everywhere, albums included. So the collapsed view lives here:
// covers only, click to expand the members inline.

const GALLERY_CAP = 120;    // member thumbs rendered before "Show all"
const GALLERY_MAX = 400;    // /api/gallery/thumbs honors 400 server-side

let galleryEvents = [];              // events from the last /api/galleries run
let galleryTotal = 0;                // total_events (may exceed events.length)
const galleryMembers = new Map();    // event key -> photos[] (cache; re-expand is instant)
const galleryShowAll = new Set();    // event keys rendered past the cap
let galleryExpanded = null;          // key of the one expanded gallery, or null
let galleryLoading = null;           // key currently fetching member thumbs

// ---- editing state (kept in JS, not the DOM, because every state change
// re-renders the whole grid and would otherwise throw typed album names away) --
const galleryAlbums = new Map();     // key -> album name as edited by the user
const galleryChecked = new Set();    // keys opted in to saving (default: none)
const galleryRemoved = new Map();    // key -> Set of uuids removed this session
const galleryDeclined = new Set();   // keys declined this session (undo still offered)

// Anything focusable that lives inside a gal-card: activating it must never
// reach the card's expand/collapse handlers. Covers the Space-in-a-text-field
// case, which would otherwise scroll-and-toggle instead of typing a space.
const GAL_CONTROL = 'input, button, label, select, textarea, a';

function galleryRemovedSet(key) {
  let s = galleryRemoved.get(key);
  if (!s) { s = new Set(); galleryRemoved.set(key, s); }
  return s;
}

// photo_count is what the server found; subtract what the user pulled out here.
function galleryCount(ev) {
  const removed = galleryRemoved.get(ev.key);
  return Math.max(0, (ev.photo_count || 0) - (removed ? removed.size : 0));
}

function galleryDefaultAlbum(ev) {
  return 'Trips/' + String(ev.label || 'Untitled').trim();
}

function galleryAlbumValue(ev) {
  if (galleryAlbums.has(ev.key)) return galleryAlbums.get(ev.key);
  return (ev.album && String(ev.album).trim()) ? ev.album : galleryDefaultAlbum(ev);
}

// `excluded` is what the server already filtered out on previous runs; the
// session count is what's dimmed on screen right now and still undoable.
function galleryRemovedNote(ev) {
  const bits = [];
  const prev = (ev.excluded || []).length;
  if (prev) bits.push(prev + ' photo' + (prev === 1 ? '' : 's') + ' previously removed');
  const now = (galleryRemoved.get(ev.key) || new Set()).size;
  if (now) bits.push(now + ' removed here');
  return bits.join(' · ');
}

function galleryCaption(ev) {
  const n = galleryCount(ev);
  const bits = [n + ' photo' + (n === 1 ? '' : 's')];
  const d = ev.days || 0;
  if (d) bits.push(d + ' day' + (d === 1 ? '' : 's'));
  return bits.join(' · ');
}

function galleryWhen(ev) {
  const a = ev.start ? String(ev.start).slice(0, 10) : '';
  const b = ev.end ? String(ev.end).slice(0, 10) : '';
  if (a && b && a !== b) return a + ' – ' + b;
  return a || b || '';
}

function galleryTooltip(ev) {
  return [ev.label, ev.place, galleryWhen(ev)].filter(Boolean).join(' · ');
}

const THUMB_FALLBACK =
  ' onerror="this.parentNode.innerHTML=\'<div class=&quot;no-thumb&quot;>no preview</div>\'"';

function galleryDeclinedHtml(ev, idx) {
  return '<div class="card gal-declined">' +
    '<p class="filename" title="' + esc(ev.label) + '">' + esc(ev.label) + '</p>' +
    '<p class="detail">Declined — won\'t be suggested again.</p>' +
    '<button type="button" class="link-btn" data-gal-undecline="' + idx + '">Undo</button>' +
    '</div>';
}

function galleryCardHtml(ev, idx) {
  if (galleryDeclined.has(ev.key)) return galleryDeclinedHtml(ev, idx);
  const open = galleryExpanded === ev.key;
  const cover = ev.cover
    ? '<img src="/thumb/' + encodeURIComponent(ev.cover) + '" alt="Cover photo for ' +
      esc(ev.label) + '" loading="lazy"' + THUMB_FALLBACK + '>'
    : noThumbTile();
  const note = galleryRemovedNote(ev);
  const scoreTag = (ev.score != null)
    ? '<span class="score">' + esc(String(ev.score)) + '</span>'
    : '';
  return '<div class="card gal-card" role="button" tabindex="0" data-gal-idx="' + idx + '"' +
    ' aria-expanded="' + (open ? 'true' : 'false') + '" aria-controls="gal-panel-' + idx + '"' +
    ' title="' + esc(galleryTooltip(ev)) + '">' +
    '<input type="checkbox" class="gal-pick" data-gal-pick="' + idx + '"' +
      (galleryChecked.has(ev.key) ? ' checked' : '') +
      ' aria-label="Save ' + esc(ev.label) + '">' +
    scoreTag +
    '<div class="thumb">' + cover + '</div>' +
    '<p class="filename" title="' + esc(ev.label) + '">' + esc(ev.label) + '</p>' +
    '<p class="detail">' + esc(galleryCaption(ev)) + '</p>' +
    '<p class="gal-note" data-gal-note="' + idx + '"' + (note ? '' : ' hidden') + '>' +
      esc(note) + '</p>' +
    '<div class="gal-edit">' +
      '<div class="gal-row">' +
        '<label class="gal-lbl" for="gal-album-' + idx + '">Album</label>' +
        '<button type="button" class="link-btn" data-gal-decline="' + idx + '">Not an event</button>' +
      '</div>' +
      '<input type="text" class="gal-album" id="gal-album-' + idx + '" data-gal-album="' + idx + '"' +
        ' value="' + esc(galleryAlbumValue(ev)) + '" placeholder="Album name"' +
        ' autocomplete="off" spellcheck="false">' +
    '</div>' +
    '</div>';
}

function galleryPanelHtml(ev, idx) {
  let body;
  if (galleryLoading === ev.key) {
    body = '<p class="progress" id="gallery-panel-progress">loading photos…</p>';
  } else {
    const photos = galleryMembers.get(ev.key) || [];
    if (!photos.length) {
      body = '<p class="muted" style="margin-top:.8rem;">No photos to preview.</p>';
    } else {
      const showAll = galleryShowAll.has(ev.key);
      const shown = showAll ? photos : photos.slice(0, GALLERY_CAP);
      const removed = galleryRemovedSet(ev.key);
      body = '<div class="gal-members">' + shown.map(p => {
        const name = p.filename || 'photo';
        const img = p.thumb
          ? '<img src="/thumb/' + encodeURIComponent(p.uuid) + '" alt="' + esc(name) +
            '" loading="lazy"' + THUMB_FALLBACK + '>'
          : noThumbTile();
        const out = removed.has(p.uuid);
        return '<div class="gal-member' + (out ? ' gal-removed' : '') +
          '" data-uuid="' + esc(p.uuid) + '" data-name="' + esc(name) + '">' +
          '<div class="thumb" title="' + esc(p.filename || '') + '">' + img + '</div>' +
          '<button type="button" class="gal-x" data-gal-remove="1"' +
          ' aria-pressed="' + (out ? 'true' : 'false') + '"' +
          ' aria-label="' + (out ? 'Restore ' : 'Remove ') + esc(name) + '">' +
          (out ? '↺' : '✕') + '</button>' +
          '</div>';
      }).join('') + '</div>';
      const notes = [];
      if (!showAll && photos.length > shown.length) {
        // Every member is loaded now (paged in above), so this number is the
        // real total — it used to read "Show all 400" for a 457-photo event.
        notes.push('<button type="button" class="link-btn" data-gal-action="show-all">Show all ' +
          photos.length + '</button>');
      }
      const count = galleryCount(ev);
      if (count > photos.length) {
        // Only reachable when the library couldn't produce some photos at all
        // (e.g. missing from the library since clustering) -- not a display cap.
        notes.push('<span class="muted">' + photos.length + ' of ' + count +
          ' photos available.</span>');
      }
      if (notes.length) {
        body += '<p class="gal-more">' + notes.join(' &nbsp; ') + '</p>';
      }
    }
  }
  const note = galleryRemovedNote(ev);
  return '<div class="gal-panel" id="gal-panel-' + idx + '" data-gal-panel-idx="' + idx + '"' +
    ' role="region" aria-label="' + esc(ev.label) + '">' +
    '<div class="gal-panel-head"><div>' +
    '<h3>' + esc(ev.label) + '</h3>' +
    '<p class="rule-meta" data-gal-meta="' + idx + '">' + esc(galleryMetaText(ev)) + '</p>' +
    '<p class="gal-note" data-gal-note="' + idx + '"' + (note ? '' : ' hidden') + '>' +
      esc(note) + '</p>' +
    '</div><div class="rule-actions">' +
    '<button type="button" class="link-btn" data-gal-decline="' + idx + '">Not an event</button>' +
    '<button type="button" class="link-btn" data-gal-action="collapse">Collapse</button>' +
    '</div></div>' + body + '</div>';
}

function galleryMetaText(ev) {
  return [ev.place, galleryWhen(ev), galleryCaption(ev)].filter(Boolean).join(' · ');
}

// Removing a photo only moves numbers, so patch them in place rather than
// re-rendering -- a full render would yank focus off the button just clicked.
function updateGalleryMeta(idx) {
  const ev = galleryEvents[idx];
  if (!ev) return;
  const root = $('gallery-root');
  const detail = root.querySelector('[data-gal-idx="' + idx + '"] .detail');
  if (detail) detail.textContent = galleryCaption(ev);
  const meta = root.querySelector('[data-gal-meta="' + idx + '"]');
  if (meta) meta.textContent = galleryMetaText(ev);
  const note = galleryRemovedNote(ev);
  root.querySelectorAll('[data-gal-note="' + idx + '"]').forEach(el => {
    el.textContent = note;
    el.hidden = !note;
  });
}

function renderGalleries() {
  const root = $('gallery-root');
  const summary = $('gallery-summary');
  if (!galleryEvents.length) {
    root.innerHTML = '<p class="muted">No galleries found — try a lower minimum.</p>';
    summary.hidden = true;
    updateGalleryBar();
    return;
  }
  summary.hidden = false;
  summary.textContent = galleryTotal > galleryEvents.length
    ? 'Showing ' + galleryEvents.length + ' of ' + galleryTotal + ' galleries.'
    : galleryEvents.length + ' galler' + (galleryEvents.length === 1 ? 'y' : 'ies') + '.';
  let html = '<div class="grid">';
  galleryEvents.forEach((ev, i) => {
    html += galleryCardHtml(ev, i);
    if (galleryExpanded === ev.key) html += galleryPanelHtml(ev, i);
  });
  root.innerHTML = html + '</div>';
  updateGalleryBar();
}

function gallerySelection() {
  return galleryEvents.filter(ev => galleryChecked.has(ev.key) && !galleryDeclined.has(ev.key));
}

function updateGalleryBar() {
  const bar = $('gallery-bar'), btn = $('gallery-save-btn');
  if (!galleryEvents.length) { bar.hidden = true; return; }
  bar.hidden = false;
  const picks = gallerySelection();
  let photos = 0;
  for (const ev of picks) photos += galleryCount(ev);
  btn.disabled = !picks.length;
  btn.textContent = 'Save ' + picks.length + ' galler' + (picks.length === 1 ? 'y' : 'ies');
  $('gal-sel-count').textContent = picks.length
    ? picks.length + ' selected · ' + photos + ' photo' + (photos === 1 ? '' : 's')
    : 'Nothing selected';
}

// Every state change re-renders the grid, which throws away the focused node --
// put focus back on the card that was activated so keyboard users keep their place.
function focusGallery(sel) {
  const el = $('gallery-root').querySelector(sel);
  if (el) el.focus();
}

async function toggleGallery(idx) {
  const ev = galleryEvents[idx];
  if (!ev) return;
  if (galleryExpanded === ev.key) {           // collapse
    galleryExpanded = null;
    renderGalleries();
    focusGallery('[data-gal-idx="' + idx + '"]');
    return;
  }
  galleryExpanded = ev.key;                   // only one open at a time
  galleryShowAll.delete(ev.key);
  if (galleryMembers.has(ev.key)) {
    renderGalleries();
    focusGallery('[data-gal-idx="' + idx + '"]');
    return;
  }

  galleryLoading = ev.key;
  renderGalleries();
  try {
    // The endpoint caps each request at GALLERY_MAX, so page through rather
    // than truncating: a 457-photo event previously lost 57 photos outright and
    // offered a "Show all 400" button that couldn't reach them.
    const all = ev.uuids || [];
    const photos = [];
    for (let start = 0; start < all.length; start += GALLERY_MAX) {
      const batch = all.slice(start, start + GALLERY_MAX);
      const result = await runJob('/api/gallery/thumbs', { uuids: batch }, p => {
        const el = $('gallery-panel-progress');
        if (!el) return;
        // Report progress across the whole event, not just the current page.
        const done = start + (p && p.done ? p.done : 0);
        el.textContent = 'loading photos… ' + Math.min(done, all.length) + '/' + all.length;
      });
      photos.push(...((result && result.photos) || []));
    }
    galleryMembers.set(ev.key, photos);
  } catch (err) {
    if (galleryExpanded === ev.key) galleryExpanded = null;
    banner('Could not load gallery photos: ' + err.message);
  } finally {
    galleryLoading = null;
    const wasFocused = !document.activeElement || document.activeElement === document.body;
    renderGalleries();
    if (wasFocused) focusGallery('[data-gal-idx="' + idx + '"]');
  }
}

function galleryMinPhotos() {
  const minEl = $('gallery-min');
  let n = parseInt(minEl.value, 10);
  if (!Number.isFinite(n) || n < 1) { n = 40; minEl.value = '40'; }
  return n;
}

// Shared by the Find-galleries button and the post-save refresh. Resets all
// editing state -- the server has just changed what it will hand back.
async function scanGalleries(minPhotos) {
  const prog = $('gallery-progress');
  const query = ($('gallery-query') || {}).value;
  prog.textContent = 'starting…';
  try {
    const body = { min_photos: minPhotos };
    if (query && query.trim()) body.query = query.trim();
    const result = await runJob('/api/galleries', body,
      p => { prog.textContent = phaseText(p); });
    galleryEvents = (result && result.events) || [];
    galleryTotal = (result && result.total_events) || galleryEvents.length;
    // Surface query errors or partial-coverage warnings.
    if (result && result.query) {
      if (result.query.error) {
        banner('Gallery query: ' + result.query.error, 'gallery-query');
      } else {
        clearBanner('gallery-query');
      }
    } else {
      clearBanner('gallery-query');
    }
  } finally {
    prog.textContent = '';
  }
  galleryMembers.clear();
  galleryShowAll.clear();
  galleryAlbums.clear();
  galleryChecked.clear();
  galleryRemoved.clear();
  galleryDeclined.clear();
  galleryExpanded = null;
  galleryLoading = null;
  renderGalleries();
}

$('gallery-form').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = $('gallery-btn');
  btn.disabled = true;
  $('gallery-outcome').innerHTML = '';
  try {
    await scanGalleries(galleryMinPhotos());
    $('galleries-block').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (err) {
    banner('Finding galleries failed: ' + err.message);
  } finally {
    btn.disabled = false;
  }
});

// ---- gallery editing actions ----------------------------------------------

function toggleGalleryPhoto(btn) {
  const member = btn.closest('.gal-member');
  const panel = btn.closest('.gal-panel');
  if (!member || !panel) return;
  const idx = Number(panel.dataset.galPanelIdx);
  const ev = galleryEvents[idx];
  if (!ev) return;
  const uuid = member.dataset.uuid;
  const removed = galleryRemovedSet(ev.key);
  const out = !removed.has(uuid);
  if (out) removed.add(uuid); else removed.delete(uuid);
  const name = member.dataset.name || 'photo';
  member.classList.toggle('gal-removed', out);
  btn.textContent = out ? '↺' : '✕';
  btn.setAttribute('aria-pressed', out ? 'true' : 'false');
  btn.setAttribute('aria-label', (out ? 'Restore ' : 'Remove ') + name);
  updateGalleryMeta(idx);
  updateGalleryBar();
}

async function declineGallery(idx) {
  const ev = galleryEvents[idx];
  if (!ev || galleryDeclined.has(ev.key)) return;
  try {
    await api('/api/galleries/decline', { key: ev.key, label: ev.label });
  } catch (err) {
    banner('Could not decline that gallery: ' + err.message);
    return;
  }
  galleryDeclined.add(ev.key);
  galleryChecked.delete(ev.key);
  if (galleryExpanded === ev.key) galleryExpanded = null;
  renderGalleries();
  focusGallery('[data-gal-undecline="' + idx + '"]');
}

async function undeclineGallery(idx) {
  const ev = galleryEvents[idx];
  if (!ev) return;
  try {
    await api('/api/galleries/decline', { key: ev.key, undo: true });
  } catch (err) {
    banner('Could not undo that decline: ' + err.message);
    return;
  }
  galleryDeclined.delete(ev.key);
  renderGalleries();
  focusGallery('[data-gal-decline="' + idx + '"]');
}

$('gallery-root').addEventListener('click', e => {
  const undecline = e.target.closest('[data-gal-undecline]');
  if (undecline) {
    e.stopPropagation();
    undeclineGallery(Number(undecline.dataset.galUndecline));
    return;
  }
  const decline = e.target.closest('[data-gal-decline]');
  if (decline) {
    e.stopPropagation();
    declineGallery(Number(decline.dataset.galDecline));
    return;
  }
  const remove = e.target.closest('[data-gal-remove]');
  if (remove) {
    e.stopPropagation();
    toggleGalleryPhoto(remove);
    return;
  }
  const act = e.target.closest('[data-gal-action]');
  if (act) {
    e.stopPropagation();
    const panel = act.closest('.gal-panel');
    const idx = panel ? panel.id.slice('gal-panel-'.length) : '';
    const key = galleryExpanded;
    if (act.dataset.galAction === 'collapse') {
      galleryExpanded = null;
      renderGalleries();
      focusGallery('[data-gal-idx="' + idx + '"]');
    } else {
      if (key) galleryShowAll.add(key);
      renderGalleries();
      focusGallery('#gal-panel-' + idx + ' [data-gal-action="collapse"]');
    }
    return;
  }
  // The save checkbox and the album field live inside the card; clicking them
  // must not expand or collapse it.
  if (e.target.closest(GAL_CONTROL)) return;
  const card = e.target.closest('.gal-card');
  if (card) toggleGallery(Number(card.dataset.galIdx));
});

$('gallery-root').addEventListener('change', e => {
  const pick = e.target.closest('[data-gal-pick]');
  if (!pick) return;
  e.stopPropagation();
  const ev = galleryEvents[Number(pick.dataset.galPick)];
  if (!ev) return;
  if (pick.checked) galleryChecked.add(ev.key); else galleryChecked.delete(ev.key);
  updateGalleryBar();
});

// Album names are stored on every keystroke but never re-rendered mid-typing,
// so the caret stays where the user put it.
$('gallery-root').addEventListener('input', e => {
  const field = e.target.closest('[data-gal-album]');
  if (!field) return;
  const ev = galleryEvents[Number(field.dataset.galAlbum)];
  if (ev) galleryAlbums.set(ev.key, field.value);
});

$('gallery-root').addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
  // Space in the album field must type a space, and Enter/Space on a button
  // must activate that button -- neither may toggle the card underneath.
  if (e.target.closest(GAL_CONTROL)) return;
  const card = e.target.closest('.gal-card');
  if (!card) return;
  e.preventDefault();
  toggleGallery(Number(card.dataset.galIdx));
});

// ---- saving galleries ------------------------------------------------------

$('gallery-save-btn').addEventListener('click', async () => {
  const picks = gallerySelection();
  if (!picks.length) return;
  const btn = $('gallery-save-btn'), status = $('gallery-save-status');
  const galleries = picks.map(ev => {
    const removed = galleryRemovedSet(ev.key);
    const album = String(galleryAlbumValue(ev) || '').trim() || galleryDefaultAlbum(ev);
    return {
      key: ev.key,
      album,
      uuids: (ev.uuids || []).filter(u => !removed.has(u)),
      excluded: Array.from(removed)
    };
  });
  btn.disabled = true;
  status.textContent = 'Saving…';
  try {
    const result = await runJob('/api/galleries/create', { galleries },
      p => { status.textContent = phaseText(p); });
    status.textContent = '';
    renderGalleryOutcome(result);
    refreshStatus();
    try {
      await scanGalleries(galleryMinPhotos());
    } catch (err) {
      banner('Saved, but refreshing the gallery list failed: ' + err.message);
    }
  } catch (err) {
    status.textContent = '';
    banner('Saving galleries failed: ' + err.message);
  } finally {
    updateGalleryBar();
  }
});

function renderGalleryOutcome(result) {
  const rows = (result && result.results) || [];
  let html = '<div class="outcome"><strong>Saved.</strong><ul>';
  if (!rows.length) html += '<li class="muted">No galleries reported.</li>';
  for (const r of rows) {
    const bits = [];
    if (r.filed) bits.push(r.filed + ' filed');
    if (r.failed) bits.push(r.failed + ' failed');
    html += '<li><strong>' + esc(r.album || r.key) + '</strong> — ' +
      (bits.length ? esc(bits.join(' · ')) : 'nothing filed') +
      (r.error ? '<div class="err">' + esc(r.error) + '</div>' : '') +
      '</li>';
  }
  html += '</ul></div>';
  $('gallery-outcome').innerHTML = html;
  $('gallery-outcome').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ---- Rules -----------------------------------------------------------------

function renderRules(rules) {
  const holder = $('rules-list');
  if (!rules.length) {
    holder.innerHTML = '<p class="muted">No rules configured.</p>';
    return;
  }
  holder.innerHTML = rules.map(r =>
    '<div class="rule-row">' +
    '<label class="toggle"><input type="checkbox" data-rule="' + esc(r.name) + '"' +
    (r.enabled ? ' checked' : '') + (r.report_only ? ' disabled' : '') + '></label>' +
    '<span class="rname">' + esc(r.name) + '</span>' +
    (r.overridden ? '<span class="tag">overridden</span>' : '') +
    (r.report_only ? '<span class="tag">report only</span>' : '') +
    '<span class="raction">' + esc(r.action) + '</span>' +
    '</div>').join('');
}

$('rules-list').addEventListener('change', async e => {
  const cb = e.target;
  if (!cb.matches('input[data-rule]')) return;
  try {
    await api('/api/rules/toggle', { rule: cb.dataset.rule, enabled: cb.checked });
    refreshStatus();
    await refreshCollections();
  } catch (err) {
    cb.checked = !cb.checked;
    banner('Could not toggle rule: ' + err.message);
  }
});

// ---- Collections -----------------------------------------------------------

// Read-only. Refresh after a rule toggle so its effective enabled state cannot
// drift from the editable Rules section immediately above it.
const refreshCollections = () => initCollections({
  api, banner, root: $('collections-root')
});
refreshCollections();

// ---- Index -----------------------------------------------------------------

const indexCatchUp = $('index-catch-up');
const indexNoCaptions = $('index-no-captions');
if (indexCatchUp && indexNoCaptions) {
  indexCatchUp.addEventListener('change', () => {
    if (indexCatchUp.checked) indexNoCaptions.checked = false;
  });
  indexNoCaptions.addEventListener('change', () => {
    if (indexNoCaptions.checked) indexCatchUp.checked = false;
  });
}

$('index-btn').addEventListener('click', async () => {
  const btn = $('index-btn'), prog = $('index-progress');
  if (jobInFlight) return;
  btn.disabled = true;
  prog.textContent = 'starting…';
  $('index-result').innerHTML = '';
  const body = {
    catch_up_captions: !!(indexCatchUp && indexCatchUp.checked),
    no_captions: !!(indexNoCaptions && indexNoCaptions.checked),
    captions: !(indexNoCaptions && indexNoCaptions.checked),
  };
  try {
    const r = await runJob('/api/index/build', body,
      p => { prog.textContent = phaseText(p); });
    prog.textContent = '';
    const bits = [];
    if (r.embedded) bits.push(r.embedded + ' embedded');
    if (r.captioned) bits.push(r.captioned + ' captioned');
    if (r.already_indexed) bits.push(r.already_indexed + ' already up to date');
    if (!bits.length) bits.push('already up to date');
    const errs = r.errors || [];
    $('index-result').innerHTML = '<div class="outcome">' +
      esc(bits.join(' · ')) +
      (errs.length ? '<ul>' + errs.map(x => '<li class="err">' + esc(x) + '</li>').join('') + '</ul>' : '') +
      '</div>';
    refreshStatus();
  } catch (err) {
    prog.textContent = '';
    banner('Index build failed: ' + err.message);
  } finally {
    btn.disabled = false;
  }
});
