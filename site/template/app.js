'use strict';

const FORMAT = 'psa-sniper-aesgcm-v1';
const state = {
  payload: null,
  envelope: null,
  encrypted: true,
  view: 'all',
  status: 'all',
  expanded: new Set(),
  expandedRuns: new Set(),
  localStatus: loadLocalStatus(),
};

const $ = (id) => document.getElementById(id);
const lockView = $('lockView');
const dashboardView = $('dashboardView');
const unlockForm = $('unlockForm');
const passwordInput = $('password');
const unlockError = $('unlockError');

function loadLocalStatus() {
  try { return JSON.parse(localStorage.getItem('psa-sniper-status-v1') || '{}'); }
  catch { return {}; }
}

function saveLocalStatus() {
  localStorage.setItem('psa-sniper-status-v1', JSON.stringify(state.localStatus));
}

function b64Bytes(value) {
  const raw = atob(value);
  return Uint8Array.from(raw, char => char.charCodeAt(0));
}

async function decryptEnvelope(envelope, password) {
  if (envelope.format === 'plain') return envelope.payload;
  if (envelope.format !== FORMAT) throw new Error('Unbekanntes Datenformat.');
  const encoder = new TextEncoder();
  const keyMaterial = await crypto.subtle.importKey(
    'raw', encoder.encode(password), 'PBKDF2', false, ['deriveKey']
  );
  const key = await crypto.subtle.deriveKey(
    {
      name: 'PBKDF2',
      salt: b64Bytes(envelope.salt),
      iterations: Number(envelope.iterations),
      hash: 'SHA-256',
    },
    keyMaterial,
    { name: 'AES-GCM', length: 256 },
    false,
    ['decrypt']
  );
  const decrypted = await crypto.subtle.decrypt(
    {
      name: 'AES-GCM',
      iv: b64Bytes(envelope.iv),
      additionalData: encoder.encode(FORMAT),
    },
    key,
    b64Bytes(envelope.ciphertext)
  );
  return JSON.parse(new TextDecoder().decode(decrypted));
}

async function loadEnvelope() {
  if (state.envelope) return state.envelope;
  const response = await fetch(`data.enc.json?t=${Date.now()}`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Trefferdatei konnte nicht geladen werden (${response.status}).`);
  state.envelope = await response.json();
  state.encrypted = state.envelope.format !== 'plain';
  return state.envelope;
}

unlockForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  unlockError.textContent = '';
  const button = unlockForm.querySelector('button');
  button.disabled = true;
  button.textContent = 'Entschlüssele …';
  try {
    const envelope = await loadEnvelope();
    state.payload = await decryptEnvelope(envelope, passwordInput.value);
    passwordInput.value = '';
    showDashboard();
  } catch (error) {
    console.error(error);
    unlockError.textContent = 'Entsperren fehlgeschlagen. Prüfe Passwort und Dashboard-Datei.';
  } finally {
    button.disabled = false;
    button.textContent = 'Entsperren';
  }
});

$('lockButton').addEventListener('click', () => {
  state.payload = null;
  state.expandedRuns.clear();
  $('hitGrid').replaceChildren();
  $('stats').replaceChildren();
  $('runsTable').replaceChildren();
  dashboardView.classList.add('hidden');
  lockView.classList.remove('hidden');
  passwordInput.focus();
});

function showDashboard() {
  lockView.classList.add('hidden');
  dashboardView.classList.remove('hidden');
  $('lockButton').classList.toggle('hidden', !state.encrypted);
  const generated = state.payload?.generated_at;
  $('updatedAt').textContent = generated
    ? `Datenstand ${formatDate(generated, true)}`
    : 'Datenstand unbekannt';
  bindControls();
  renderAll();
}

let controlsBound = false;
function bindControls() {
  if (controlsBound) return;
  controlsBound = true;
  ['searchInput', 'minScore', 'maxPop', 'maxPrice', 'sortSelect'].forEach(id => {
    $(id).addEventListener('input', renderAll);
    $(id).addEventListener('change', renderAll);
  });
  $('viewChips').addEventListener('click', event => {
    const button = event.target.closest('[data-view]');
    if (!button) return;
    state.view = button.dataset.view;
    setActiveChip('viewChips', button);
    renderAll();
  });
  $('statusChips').addEventListener('click', event => {
    const button = event.target.closest('[data-status]');
    if (!button) return;
    state.status = button.dataset.status;
    setActiveChip('statusChips', button);
    renderAll();
  });
  $('resetFilters').addEventListener('click', resetFilters);
}

function setActiveChip(containerId, selected) {
  $(containerId).querySelectorAll('.chip').forEach(button => button.classList.remove('active'));
  selected.classList.add('active');
}

function resetFilters() {
  $('searchInput').value = '';
  $('minScore').value = '7';
  $('maxPop').value = '';
  $('maxPrice').value = '';
  $('sortSelect').value = 'newest';
  state.view = 'all';
  state.status = 'all';
  setActiveChip('viewChips', $('viewChips').querySelector('[data-view="all"]'));
  setActiveChip('statusChips', $('statusChips').querySelector('[data-status="all"]'));
  renderAll();
}

function renderAll() {
  if (!state.payload) return;
  const all = Array.isArray(state.payload.hits) ? state.payload.hits : [];
  renderStats(all);
  renderHealth();
  const rows = filterAndSort(all);
  $('resultCount').textContent = `${rows.length} ${rows.length === 1 ? 'Treffer' : 'Treffer'}`;
  const grid = $('hitGrid');
  grid.replaceChildren(...rows.map(renderCard));
  $('emptyState').classList.toggle('hidden', rows.length !== 0);
  renderRuns();
}

function filterAndSort(rows) {
  const query = normalize($('searchInput').value);
  const minScore = Number($('minScore').value || 0);
  const maxPop = numberOrNull($('maxPop').value);
  const maxPrice = numberOrNull($('maxPrice').value);
  const filtered = rows.filter(row => {
    if (Number(row.score || 0) < minScore) return false;
    const pop = row.cert?.population;
    if (maxPop !== null && (pop == null || Number(pop) > maxPop)) return false;
    const price = row.total_cost?.value ?? row.price?.value;
    if (maxPrice !== null && (price == null || Number(price) > maxPrice)) return false;
    if (query && !searchBlob(row).includes(query)) return false;
    if (state.view === 'hits' && !row.is_hit) return false;
    if (state.view === 'watch' && row.is_hit) return false;
    if (state.view === 'fixed' && row.pure_auction) return false;
    if (state.view === 'auction' && !row.pure_auction) return false;
    const status = state.localStatus[row.item_id] || 'new';
    if (state.status !== 'all' && status !== state.status) return false;
    return true;
  });
  const sort = $('sortSelect').value;
  filtered.sort((a, b) => {
    if (sort === 'score') return Number(b.score || 0) - Number(a.score || 0);
    if (sort === 'pop') return nullLast(a.cert?.population, b.cert?.population, true);
    if (sort === 'discount') return nullLast(a.discount_pct, b.discount_pct, false);
    if (sort === 'price') return nullLast(a.total_cost?.value ?? a.price?.value, b.total_cost?.value ?? b.price?.value, true);
    return String(b.last_seen_at || '').localeCompare(String(a.last_seen_at || ''));
  });
  return filtered;
}

function nullLast(a, b, ascending) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return ascending ? Number(a) - Number(b) : Number(b) - Number(a);
}

function searchBlob(row) {
  return normalize([
    row.title,
    row.seller,
    row.cert_number,
    row.cert?.year,
    row.cert?.brand_title,
    row.cert?.subject,
    row.cert?.card_number,
    row.cert?.variety,
    ...(row.reasons || []),
  ].filter(Boolean).join(' '));
}

function normalize(value) {
  return String(value || '').toLocaleLowerCase('de-DE').normalize('NFKD').replace(/[\u0300-\u036f]/g, '');
}

function numberOrNull(value) {
  if (value === '' || value == null) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function renderStats(rows) {
  const hits = rows.filter(row => row.is_hit).length;
  const strong = rows.filter(row => Number(row.score || 0) >= 16).length;
  const lowPop = rows.filter(row => row.cert?.population != null && Number(row.cert.population) <= 10).length;
  const discounts = rows.map(row => row.discount_pct).filter(value => Number.isFinite(value));
  const medianDiscount = discounts.length ? median(discounts) : null;
  const latestRun = state.payload.runs?.[0];
  const cards = [
    ['Hits', hits, 'ab deinem Schwellenwert'],
    ['Sehr stark', strong, 'Score 16 oder höher'],
    ['POP ≤ 10', lowPop, 'bestätigte Cert-Daten'],
    ['Median-Abstand', medianDiscount == null ? '–' : percent(medianDiscount), 'nur verfügbare Preisindikatoren'],
    ['Letzter Scan', latestRun ? Number(latestRun.fresh_listings || 0) : '–', 'frische Listings'],
  ];
  $('stats').replaceChildren(...cards.map(([label, value, note]) => {
    const card = el('article', 'stat-card');
    card.append(el('div', 'stat-label', label), el('div', 'stat-value', String(value)), el('div', 'stat-note', note));
    return card;
  }));
}

function renderHealth() {
  const run = state.payload.runs?.[0];
  const pill = $('runHealth');
  if (!run) {
    pill.textContent = 'Noch kein Lauf';
    return;
  }
  const ageHours = (Date.now() - new Date(run.completed_at).getTime()) / 3_600_000;
  pill.textContent = ageHours <= 1.5 ? '● Scanner aktuell' : `● Letzter Lauf ${formatRelative(run.completed_at)}`;
  pill.style.color = ageHours <= 1.5 ? 'var(--good)' : 'var(--warn)';
}

function renderCard(row) {
  const card = el('article', `hit-card ${Number(row.score || 0) >= 16 ? 'strong' : ''}`);
  const media = el('div', 'card-media');
  if (row.image_url) {
    const image = document.createElement('img');
    image.src = row.image_url;
    image.alt = row.title || 'Kartenbild';
    image.loading = 'lazy';
    image.referrerPolicy = 'no-referrer';
    image.addEventListener('error', () => {
      image.remove();
      media.prepend(el('div', 'image-placeholder', 'PSA 10'));
    });
    media.append(image);
  } else {
    media.append(el('div', 'image-placeholder', 'PSA 10'));
  }
  const badges = el('div', 'card-badges');
  const score = el('span', `score-badge ${Number(row.score || 0) >= 16 ? 'hot' : ''}`, `Score ${row.score ?? 0}`);
  const type = el('span', 'type-badge', row.pure_auction ? 'Auktion' : 'Sofortkauf');
  badges.append(score, type);
  media.append(badges);

  const content = el('div', 'card-content');
  content.append(el('h2', 'card-title', row.title || 'Ohne Titel'));
  const identity = certIdentity(row.cert);
  if (identity) content.append(el('p', 'identity', identity));

  const metrics = el('div', 'metrics');
  metrics.append(
    metric('Gesamt', money(row.total_cost || row.price)),
    metric('PSA-10-POP', row.cert?.population ?? '–'),
    metric('Abstand', row.discount_pct == null ? '–' : distanceLabel(row.discount_pct), row.discount_pct >= .15),
  );
  content.append(metrics);

  if (row.reasons?.length) content.append(list(row.reasons.slice(0, 5), 'reason-list'));
  if (row.warnings?.length) content.append(list(row.warnings.slice(0, 3), 'reason-list warning-list'));

  const sellerBits = [];
  if (row.seller) sellerBits.push(row.seller);
  if (row.seller_feedback_percentage != null) sellerBits.push(`${row.seller_feedback_percentage}% positiv`);
  if (row.created_at) sellerBits.push(`eingestellt ${formatRelative(row.created_at)}`);
  if (sellerBits.length) content.append(el('p', 'seller-line', sellerBits.join(' · ')));

  const actions = el('div', 'card-actions');
  const ebay = el('a', 'ebay-button', 'Auf eBay öffnen');
  ebay.href = row.url;
  ebay.target = '_blank';
  ebay.rel = 'noopener noreferrer';
  const more = el('button', 'more-button', state.expanded.has(row.item_id) ? 'Weniger' : 'Details');
  more.type = 'button';
  more.addEventListener('click', () => {
    if (state.expanded.has(row.item_id)) state.expanded.delete(row.item_id);
    else state.expanded.add(row.item_id);
    renderAll();
  });
  actions.append(ebay, more);
  content.append(actions, statusButtons(row));

  if (state.expanded.has(row.item_id)) content.append(renderDetails(row));
  card.append(media, content);
  return card;
}

function metric(label, value, good = false) {
  const box = el('div', 'metric');
  box.append(el('span', '', label), el('strong', good ? 'good' : '', String(value)));
  return box;
}

function list(items, className) {
  const ul = el('ul', className);
  items.forEach(item => ul.append(el('li', '', item)));
  return ul;
}

function statusButtons(row) {
  const wrap = el('div', 'status-actions');
  const current = state.localStatus[row.item_id] || 'new';
  [['saved', '★ Merken'], ['bought', '✓ Gekauft'], ['ignored', 'Ausblenden']].forEach(([status, label]) => {
    const button = el('button', `status-button ${current === status ? 'active' : ''}`, label);
    button.type = 'button';
    button.addEventListener('click', () => {
      if (state.localStatus[row.item_id] === status) delete state.localStatus[row.item_id];
      else state.localStatus[row.item_id] = status;
      saveLocalStatus();
      renderAll();
    });
    wrap.append(button);
  });
  return wrap;
}

function renderDetails(row) {
  const box = el('div', 'detail-box');
  const lines = [
    row.cert_number ? `Cert: ${row.cert_number} (${row.cert_source || 'Quelle unbekannt'})` : 'Cert: nicht erkannt',
    row.market_value ? `Preisindikator: ${money(row.market_value.money)} · ${row.market_value.source} · Vertrauen ${row.market_value.confidence}` : 'Preisindikator: nicht verfügbar',
    row.shipping ? `Preis ${money(row.price)} + Versand ${money(row.shipping)}` : `Preis: ${money(row.price)}`,
    row.returns_accepted == null ? 'Rückgabe: unbekannt' : `Rückgabe: ${row.returns_accepted ? 'akzeptiert' : 'nicht akzeptiert'}`,
    row.matched_queries?.length ? `Gefunden über ${row.matched_queries.length} Suchabfrage(n)` : null,
  ].filter(Boolean);
  lines.forEach(line => box.append(el('div', '', line)));
  if (row.cert?.source_url) {
    const link = el('a', '', 'PSA-Cert öffnen');
    link.href = row.cert.source_url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    box.append(link);
  }
  return box;
}

function runKey(run) {
  return `${run.started_at || ''}|${run.completed_at || ''}`;
}

function runResults(run) {
  if (Array.isArray(run.results)) return run.results;

  // Legacy fallback for runs created before per-run snapshots existed. History rows
  // receive first_seen_at during the scan, so they can usually be mapped back to it.
  const archive = Array.isArray(state.payload?.archive_hits) ? state.payload.archive_hits : [];
  const start = Date.parse(run.started_at || '');
  const end = Date.parse(run.completed_at || '');
  if (!Number.isFinite(start) || !Number.isFinite(end)) return [];
  return archive.filter(item => {
    const seen = Date.parse(item.first_seen_at || item.last_seen_at || '');
    return Number.isFinite(seen) && seen >= start - 5_000 && seen <= end + 10_000;
  });
}

function renderRunResult(item, legacy = false) {
  const card = el('article', 'run-result-card');
  const head = el('div', 'run-result-head');
  const badges = el('div', 'run-result-badges');
  badges.append(el('span', `run-result-badge ${item.is_hit ? 'hit' : 'watch'}`, item.is_hit ? 'Hit' : 'Beobachtung'));
  if (legacy) badges.append(el('span', 'run-result-badge legacy', 'Historisch zugeordnet'));
  head.append(badges, el('strong', 'run-result-score', `Score ${item.score ?? '–'}`));
  card.append(head, el('h3', 'run-result-title', item.title || 'Ohne Titel'));

  const identity = certIdentity(item.cert);
  if (identity) card.append(el('p', 'run-result-identity', identity));

  const metrics = el('div', 'run-result-metrics');
  metrics.append(
    metric('Gesamt', money(item.total_cost || item.price)),
    metric('POP', item.cert?.population ?? '–'),
    metric('Abstand', item.discount_pct == null ? '–' : distanceLabel(item.discount_pct), item.discount_pct >= .15),
  );
  card.append(metrics);

  const detailBits = [];
  if (item.market_value) detailBits.push(`Preisindikator ${money(item.market_value.money)}`);
  if (item.cert_number) detailBits.push(`Cert ${item.cert_number}`);
  if (item.created_at) detailBits.push(`eingestellt ${formatRelative(item.created_at)}`);
  if (detailBits.length) card.append(el('p', 'run-result-meta', detailBits.join(' · ')));

  if (item.url) {
    const link = el('a', 'run-result-link', 'Auf eBay öffnen');
    link.href = item.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    card.append(link);
  }
  return card;
}

function renderRunPanel(run, results) {
  const panel = el('div', 'run-results-panel');
  const legacy = !Array.isArray(run.results);
  const title = el('div', 'run-results-title');
  title.append(
    el('strong', '', `Ergebnisse vom ${formatDate(run.completed_at, true)}`),
    el('span', '', `${run.hits ?? 0} Hit(s) · ${run.near_hits ?? 0} Beobachtung(en)`),
  );
  panel.append(title);

  if (!results.length) {
    panel.append(el(
      'p',
      'run-results-empty',
      legacy
        ? 'Für diesen älteren Lauf ist der Treffer-Zähler erhalten, aber die Kartendetails lassen sich aus dem aktuellen State nicht mehr eindeutig rekonstruieren.'
        : 'Dieser Lauf hatte keine gespeicherten Trefferdetails.'
    ));
    return panel;
  }

  if (legacy) {
    panel.append(el('p', 'run-results-note', 'Legacy-Zuordnung anhand des Zeitstempels. Die heutige Sniper-Logik kann diese Karte inzwischen anders bewerten.'));
  }
  const grid = el('div', 'run-results-grid');
  grid.replaceChildren(...results.map(item => renderRunResult(item, legacy)));
  panel.append(grid);
  return panel;
}

function renderRuns() {
  const runs = state.payload.runs || [];
  const container = $('runsTable');
  const children = [];
  const header = el('div', 'run-row header');
  ['Zeit', 'Queries', 'Frisch', 'Details', 'Hits / Watch', 'Calls'].forEach(value => header.append(el('span', '', value)));
  children.push(header);

  runs.slice(0, 20).forEach(run => {
    const key = runKey(run);
    const expected = Number(run.hits || 0) + Number(run.near_hits || 0);
    const canExpand = expected > 0 || (Array.isArray(run.results) && run.results.length > 0);
    const open = state.expandedRuns.has(key);
    const row = el('div', `run-row ${canExpand ? 'clickable' : ''}`);
    const timeText = `${open ? '▾' : canExpand ? '›' : '·'} ${formatDate(run.completed_at)}`;
    [
      timeText,
      run.queries_used ?? '–',
      run.fresh_listings ?? '–',
      run.detailed_candidates ?? '–',
      `${run.hits ?? 0} / ${run.near_hits ?? 0}`,
      run.ebay_calls ?? '–',
    ].forEach(value => row.append(el('span', '', String(value))));

    if (canExpand) {
      const toggle = () => {
        if (state.expandedRuns.has(key)) state.expandedRuns.delete(key);
        else state.expandedRuns.add(key);
        renderRuns();
      };
      row.tabIndex = 0;
      row.setAttribute('role', 'button');
      row.setAttribute('aria-expanded', open ? 'true' : 'false');
      row.addEventListener('click', toggle);
      row.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          toggle();
        }
      });
    }

    children.push(row);
    if (open) children.push(renderRunPanel(run, runResults(run)));
  });

  container.replaceChildren(...children);
}

function certIdentity(cert) {
  if (!cert) return '';
  return [cert.year, cert.brand_title, cert.subject, cert.card_number ? `#${cert.card_number}` : null, cert.variety].filter(Boolean).join(' · ');
}

function money(value) {
  if (!value || value.value == null || !value.currency) return '–';
  try {
    return new Intl.NumberFormat('de-DE', { style: 'currency', currency: value.currency }).format(value.value);
  } catch {
    return `${Number(value.value).toFixed(2)} ${value.currency}`;
  }
}

function percent(value) {
  return new Intl.NumberFormat('de-DE', { style: 'percent', maximumFractionDigits: 0 }).format(value);
}

function distanceLabel(value) {
  if (!Number.isFinite(Number(value))) return '–';
  const number = Number(value);
  return number >= 0 ? `${percent(number)} unter` : `${percent(Math.abs(number))} über`;
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function formatDate(value, withTime = false) {
  if (!value) return '–';
  const options = withTime
    ? { dateStyle: 'medium', timeStyle: 'short' }
    : { dateStyle: 'short', timeStyle: 'short' };
  return new Intl.DateTimeFormat('de-DE', options).format(new Date(value));
}

function formatRelative(value) {
  const date = new Date(value);
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const units = [
    ['year', 31_536_000], ['month', 2_592_000], ['day', 86_400],
    ['hour', 3_600], ['minute', 60], ['second', 1],
  ];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size || unit === 'second') {
      return new Intl.RelativeTimeFormat('de-DE', { numeric: 'auto' }).format(Math.round(seconds / size), unit);
    }
  }
  return 'gerade eben';
}

async function bootstrap() {
  try {
    const envelope = await loadEnvelope();
    if (envelope.format === 'plain') {
      passwordInput.required = false;
      passwordInput.minLength = 0;
      state.payload = await decryptEnvelope(envelope, '');
      showDashboard();
    }
  } catch (error) {
    console.error(error);
    unlockError.textContent = 'Dashboard-Datei konnte nicht geladen werden.';
  }
}

bootstrap();

function el(tag, className = '', text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== null && text !== undefined) node.textContent = text;
  return node;
}