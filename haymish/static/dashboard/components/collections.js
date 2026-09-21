'use strict';

// Read-only view of the collections the daemon compiles from the current
// legacy rules. This module formats what /api/collections returns and nothing
// else: no editing, no toggling, no writes. Rules stay the place where a person
// changes anything, so there is exactly one surface that can.
//
// Everything that reaches the DOM goes through esc(). The payload carries rule
// names, keywords and album paths a person typed into rules.toml, plus error
// text, none of which is trusted markup.

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const shortRevision = rev => String(rev ?? '').slice(0, 12);

// A lens is a tree of evidence sources: atoms name their source, compositions
// join their children. Summarizing it keeps the section readable without
// pretending the underlying structure is flat.
export function lensSummary(lens) {
  if (!lens || typeof lens !== 'object') return '';
  if (lens.type === 'atom') return String(lens.source ?? '');
  const parts = (lens.children || []).map(lensSummary).filter(Boolean);
  if (!parts.length) return '';
  if (lens.type === 'not') return 'not ' + parts[0];
  return parts.join(lens.type === 'any' ? ' or ' : ' and ');
}

// [label, chip tone] pairs for what the disposition would add or schedule.
function dispositionChips(disposition) {
  const d = disposition || {};
  const chips = [];
  for (const keyword of d.keywords || []) chips.push(['tag ' + keyword, '']);
  for (const album of d.albums || []) chips.push(['album ' + album, '']);
  if (d.protect) chips.push(['protected', 'green']);
  if (d.hide_after_days != null) chips.push(['hide after ' + d.hide_after_days + 'd', 'amber']);
  if (d.archive_after_days != null) chips.push(['archive after ' + d.archive_after_days + 'd', '']);
  if (d.stage_delete_after_days != null) {
    chips.push(['stage delete after ' + d.stage_delete_after_days + 'd', 'amber']);
  }
  return chips;
}

function stateChips(entry) {
  const chips = [entry.enabled ? ['enabled', 'green'] : ['disabled', '']];
  if (entry.report_only) chips.push(['report only', 'amber']);
  return chips;
}

function chipHtml([label, tone]) {
  return '<span class="chip' + (tone ? ' ' + tone : '') + '">' + esc(label) + '</span>';
}

function entryHtml(entry) {
  const chips = stateChips(entry).concat(dispositionChips(entry.disposition));
  const evidence = lensSummary(entry.lens);
  const revisions = [
    ['collection', entry.collection_revision],
    ['lens', entry.lens_revision],
    ['disposition', entry.disposition_revision],
  ].filter(([, rev]) => rev);

  return '<article class="coll">' +
    '<div class="coll-head">' +
      '<span class="coll-name">' + esc(entry.name) + '</span>' +
      '<span class="tag">' + esc(entry.source) + '</span>' +
    '</div>' +
    (entry.description ? '<p class="muted">' + esc(entry.description) + '</p>' : '') +
    (entry.error
      ? '<p class="coll-err">Could not compile this rule: ' + esc(entry.error) + '</p>'
      : '<p class="coll-evidence">Evidence: ' + esc(evidence || 'none') + '</p>') +
    '<div class="chips">' + chips.map(chipHtml).join('') + '</div>' +
    (revisions.length
      ? '<p class="coll-rev">' + revisions.map(
          ([label, rev]) => esc(label) + ' ' + esc(shortRevision(rev))).join(' · ') + '</p>'
      : '') +
    '</article>';
}

export function renderCollections(root, entries) {
  if (!root) return;
  if (!entries || !entries.length) {
    root.innerHTML = '<p class="muted">No collections yet — they come from your rules.</p>';
    return;
  }
  root.innerHTML = entries.map(entryHtml).join('');
}

// api and banner are passed in rather than imported so this module stays a leaf
// the page owns, and so tests can drive it without a network or a real page.
export async function initCollections({ api, banner, root } = {}) {
  const holder = root
    || (typeof document === 'undefined' ? null : document.getElementById('collections-root'));
  if (!holder || typeof api !== 'function') return;
  try {
    const data = await api('/api/collections');
    renderCollections(holder, (data && data.collections) || []);
  } catch (e) {
    holder.innerHTML = '<p class="muted">Collections unavailable.</p>';
    if (typeof banner === 'function') {
      banner('Could not load collections: ' + e.message, 'collections');
    }
  }
}
