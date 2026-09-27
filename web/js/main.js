// App shell: settings panel, data polling, hover card, wiring scene <-> detail panel.
import { t, tr, locale, setLang, startAutoTranslate } from './i18n.js';
import { Galaxy } from './scene.js';
import { DetailPanel } from './panel.js';
import { ServiceWindow } from './service.js';
import { Alerts } from './alerts.js';
import { SummaryPanel } from './summary.js';
import { METRIC, SEVERITY, STATE, SAT_ICON, pct, bps, esc, hostTone } from './util.js';
import { OS_STYLE, OS_LEGEND } from './os.js';

const DEFAULTS = {
  sizeBy: 'disk',
  layout: 'state',
  metrics: 'card',
  satellites: true,
  flows: true,
  users: true,
  alerts: true,
  summary: true,
  summaryCollapsed: false,
  onlyProblems: false,
  autoRotate: true,
  bloom: true,
  dust: true,
  labels: 'all',
  satLabels: 'focus',
  groups: [],
  search: '',
  refresh: 30,
  leftCollapsed: false,
};

const SIZE_OPTIONS = [
  ['disk', 'Заполнение диска'],
  ['cpu', 'Загрузка CPU'],
  ['mem', 'Загрузка RAM'],
  ['mem_total', 'Объём RAM'],
  ['disk_total', 'Объём диска'],
  ['problems', 'Число проблем'],
  ['containers', 'Число контейнеров'],
  ['net', 'Сетевой трафик'],
  ['uniform', 'Одинаковый'],
];

// Interface language comes from the server's settings file (GALAXY_LANG).
const appConfig = await fetch('/api/config').then((r) => r.json()).catch(() => ({}));
setLang(appConfig.lang);
startAutoTranslate();

const settings = loadSettings();
let overview = null;
let selected = null;
let pollTimer = null;

const $ = (sel) => document.querySelector(sel);

const galaxy = new Galaxy($('#scene'), {
  onSelect: (id, opts) => select(id, opts),
  onHover: (h, px) => hoverCard(h, px),
});
const service = new ServiceWindow();
const alerts = new Alerts($('#alerts'), { galaxy, onOpen: (id) => select(id) });
alerts.setEnabled(settings.alerts);
const summary = new SummaryPanel($('#summary-panel'), {
  onSelect: (id) => select(id),
  onToggle: () => update({ summaryCollapsed: !settings.summaryCollapsed }),
});
summary.setCollapsed(settings.summaryCollapsed);
window.galaxy = galaxy; // handy for debugging from the browser console
const panel = new DetailPanel($('#detail'), $('#overlay'), {
  onClose: () => select(null),
  onService: (id, kind) => service.open(id, kind),
});

buildSettings();
applyLayout();
poll();

// ------------------------------------------------------------------ settings

function loadSettings() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem('galaxy.settings') || '{}'); } catch { /* storage unavailable */ }
  // URL overrides for kiosk screens, e.g. ?autoRotate=0&sizeBy=cpu
  const url = {};
  for (const [k, v] of new URLSearchParams(location.search)) {
    if (!(k in DEFAULTS)) continue;
    url[k] = typeof DEFAULTS[k] === 'boolean' ? v !== '0' && v !== 'false'
      : typeof DEFAULTS[k] === 'number' ? Number(v) : Array.isArray(DEFAULTS[k]) ? v.split(',') : v;
  }
  return { ...DEFAULTS, ...saved, ...url, search: '' };
}

function saveSettings() {
  try {
    const { search, ...rest } = settings;
    localStorage.setItem('galaxy.settings', JSON.stringify(rest));
  } catch { /* storage unavailable */ }
}

function update(patch) {
  Object.assign(settings, patch);
  saveSettings();
  galaxy.applySettings(settings);
  renderHostList();
  if ('refresh' in patch) poll();
  if ('alerts' in patch) alerts.setEnabled(settings.alerts);
  if ('summary' in patch) { showSummary(); requestAnimationFrame(updateOffset); }
  if ('summaryCollapsed' in patch) { summary.setCollapsed(settings.summaryCollapsed); showSummary(); }
}

function buildSettings() {
  const sizeSel = $('#sizeBy');
  sizeSel.innerHTML = SIZE_OPTIONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
  sizeSel.value = settings.sizeBy;
  sizeSel.addEventListener('change', () => update({ sizeBy: sizeSel.value }));

  const layout = $('#layout');
  layout.value = settings.layout;
  layout.addEventListener('change', () => { update({ layout: layout.value }); poll(); });

  const metrics = $('#metrics');
  metrics.value = settings.metrics;
  metrics.addEventListener('change', () => update({ metrics: metrics.value }));

  const labels = $('#labels');
  labels.value = settings.labels;
  labels.addEventListener('change', () => update({ labels: labels.value }));
  const satLabels = $('#satLabels');
  satLabels.value = settings.satLabels;
  satLabels.addEventListener('change', () => update({ satLabels: satLabels.value }));
  const refresh = $('#refresh');
  refresh.value = String(settings.refresh);
  refresh.addEventListener('change', () => update({ refresh: Number(refresh.value) }));

  document.querySelectorAll('[data-toggle]').forEach((el) => {
    const key = el.dataset.toggle;
    el.checked = !!settings[key];
    el.addEventListener('change', () => update({ [key]: el.checked }));
  });

  const search = $('#search');
  search.addEventListener('input', () => update({ search: search.value.trim() }));

  $('#collapse').addEventListener('click', () => {
    settings.leftCollapsed = !settings.leftCollapsed;
    saveSettings();
    applyLayout();
  });
  $('#home').addEventListener('click', () => select(null));

  // Legend.
  $('#legend-os').innerHTML = OS_LEGEND.map((id) => {
    const o = OS_STYLE[id];
    return `<li><i class="os-sw" style="--a:${o.pal[0]};--b:${o.pal[1]}"></i>${o.label}</li>`;
  }).join('');
  $('#legend-rings').innerHTML = ['cpu', 'mem', 'disk'].map((m, i) =>
    `<li><i class="ring-sw" style="--c:${METRIC[m].color}"></i>${METRIC[m].label}<span class="muted">${tr(`кольцо ${['внутр.', 'средн.', 'внешн.'][i]}`, `${['inner', 'middle', 'outer'][i]} ring`)}</span></li>`).join('');
  $('#legend-halo').innerHTML = [
    [SEVERITY[2].color, SEVERITY[2].label],
    [SEVERITY[3].color, SEVERITY[3].label],
    [SEVERITY[4].color, tr(`${SEVERITY[4].label} / катастрофа`, 'High / Disaster')],
    [STATE.unreachable.color, 'Нет связи с агентом'],
  ].map(([c, l]) => `<li><i class="halo-sw" style="--c:${c}"></i>${l}</li>`).join('');

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && selected) select(null);
    if (e.key === '/' && document.activeElement !== search) { e.preventDefault(); search.focus(); }
  });
  window.addEventListener('hashchange', () => {
    const id = hashHost();
    if (id !== selected) select(id);
  });
}

function applyLayout() {
  document.body.classList.toggle('left-collapsed', settings.leftCollapsed);
  updateOffset();
}

// The galaxy summary occupies the right side in the overview; the host panel replaces it.
function showSummary() {
  const on = !selected && settings.summary;
  summary.setVisible(on);
  document.body.classList.toggle('summary-on', on);
  document.body.classList.toggle('summary-collapsed', settings.summaryCollapsed);
}

function updateOffset() {
  const left = settings.leftCollapsed ? 0 : $('#left').offsetWidth;
  // The summary is a transparent overlay: planets stay visible through it, so no offset for it.
  const right = selected ? $('#detail').offsetWidth : 0;
  galaxy.setViewOffset((right - left) / 2);
}

function buildGroups() {
  const box = $('#groups');
  const groups = overview.groups;
  box.innerHTML = groups.map((g) => {
    const n = overview.hosts.filter((h) => h.groups.includes(g)).length;
    return `<button class="chip ${settings.groups.includes(g) ? 'on' : ''}" data-g="${esc(g)}">${esc(g)}<span>${n}</span></button>`;
  }).join('');
  box.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
    const g = b.dataset.g;
    const set = new Set(settings.groups);
    set.has(g) ? set.delete(g) : set.add(g);
    b.classList.toggle('on', set.has(g));
    update({ groups: [...set] });
  }));
}

// ------------------------------------------------------------------ data

async function poll() {
  clearTimeout(pollTimer);
  try {
    const r = await fetch('/api/overview');
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    const first = !overview;
    // Disabled hosts are not shown anywhere.
    data.hosts = data.hosts.filter((h) => h.state !== 'disabled');
    data.groups = [...new Set(data.hosts.flatMap((h) => h.groups))].sort();
    overview = data;
    galaxy.setData(data.hosts, settings);
    alerts.update(data.hosts);
    summary.update(data.zabbix, data.hosts);
    showSummary();
    if (first && /[?&]demo=alerts\b/.test(location.search)) alerts.demo(data.hosts);
    renderSummary();
    buildGroups();
    renderHostList();
    setConn(true);
    if (first) {
      const id = hashHost();
      if (id && data.hosts.some((h) => h.id === id)) {
        const svc = location.hash.match(/svc=(postgresql|1c|mysql)/);
        select(id, svc ? { section: svc[1] } : undefined);
      }
    }
  } catch (e) {
    setConn(false, e.message);
  }
  pollTimer = setTimeout(poll, settings.refresh * 1000);
}

function setConn(ok, msg) {
  const el = $('#conn');
  el.classList.toggle('bad', !ok);
  el.title = ok ? tr('Данные получены', 'Data received') : `${tr('Ошибка', 'Error')}: ${msg}`;
  $('#updated').textContent = ok
    ? new Date(overview.generated * 1000).toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : tr('нет связи', 'offline');
}

function renderSummary() {
  const hs = overview.hosts;
  const online = hs.filter((h) => h.state === 'online').length;
  const sev = [0, 0, 0, 0, 0, 0];
  hs.forEach((h) => h.problems.by_severity.forEach((n, i) => { sev[i] += n; }));
  $('#summary').innerHTML = `
    <div class="sum-main"><b>${online}</b><span>/ ${hs.length} ${tr('в сети', 'online')}</span></div>
    <div class="sev-row">${[5, 4, 3, 2, 1].map((s) => `<span class="sev-c ${sev[s] ? '' : 'zero'}" style="--c:${SEVERITY[s].color}" title="${SEVERITY[s].label}">${sev[s]}</span>`).join('')}</div>`;
}

function renderHostList() {
  if (!overview) return;
  const list = [...overview.hosts]
    .filter((h) => galaxy.matches(h, settings))
    .sort((a, b) => rank(b) - rank(a) || a.name.localeCompare(b.name));
  $('#host-count').textContent = list.length;
  $('#hosts').innerHTML = list.map((h) => {
    const tone = hostTone(h) || STATE[h.state].color;
    const mini = ['cpu', 'mem', 'disk'].map((m) =>
      `<span class="mini" title="${t(METRIC[m].label)} ${pct(h[m])}"><span style="height:${h[m] == null ? 0 : Math.max(6, h[m])}%;--c:${METRIC[m].color}"></span></span>`).join('');
    return `<li class="${h.id === selected ? 'on' : ''} ${h.state}" data-id="${h.id}">
      <span class="dot" style="--c:${tone}"></span>
      <span class="hn">${esc(h.name)}</span>
      ${h.problems.count ? `<span class="hp" style="--c:${SEVERITY[Math.max(0, h.problems.worst)].color}">${h.problems.count}</span>` : ''}
      <span class="minis">${mini}</span></li>`;
  }).join('');
  $('#hosts').querySelectorAll('li').forEach((li) => li.addEventListener('click', () => select(li.dataset.id)));
}

function rank(h) {
  if (h.state === 'disabled') return -2;
  if (h.state === 'unreachable') return 10;
  return h.problems.worst;
}

// ------------------------------------------------------------------ selection

function hashHost() {
  const m = location.hash.match(/host=(\d+)/);
  return m ? m[1] : null;
}

function select(id, opts) {
  selected = id;
  galaxy.select(id);
  hoverCard(null);
  if (id) {
    panel.open(id, opts);
    // Clicking a PostgreSQL / 1C satellite opens its full window straight away.
    if (['postgresql', '1c', 'mysql'].includes(opts?.section)) service.open(id, opts.section);
    if (hashHost() !== id) history.replaceState(null, '', `#host=${id}`);
  } else {
    panel.close();
    if (location.hash) history.replaceState(null, '', location.pathname + location.search);
  }
  document.body.classList.toggle('has-selection', !!id);
  showSummary();
  requestAnimationFrame(updateOffset);
  renderHostList();
}

// ------------------------------------------------------------------ hover card

function hoverCard(h, px) {
  const el = $('#hovercard');
  if (!h || h.id === selected) {
    el.classList.add('hidden');
    return;
  }
  const st = STATE[h.state];
  const rows = h.state === 'disabled' ? '' : `
    <div class="hc-m">${['cpu', 'mem', 'disk'].map((m) => `<div><i style="--c:${METRIC[m].color}"></i>${METRIC[m].label}<b>${pct(h[m])}</b></div>`).join('')}
      <div><i style="--c:#3987e5"></i>Вход<b>↓ ${bps(h.net_in)}</b></div>
      <div><i style="--c:#d95926"></i>Выход<b>↑ ${bps(h.net_out)}</b></div>
      ${h.users != null ? `<div><i style="--c:#c3c2b7"></i>${h.users_src === 'rdp' ? 'RDP-сессий' : 'Пользователей'}<b>${Math.round(h.users)}</b></div>` : ''}</div>`;
  const sats = h.satellites.length
    ? `<div class="hc-s">${h.satellites.map((s) => `<span>${SAT_ICON[s.type] || '•'} ${esc(s.label)}${s.type === 'docker' ? ` ${esc(s.summary)}` : ''}</span>`).join('')}</div>` : '';
  el.innerHTML = `<div class="hc-h"><b>${esc(h.name)}</b><span class="pill" style="--c:${st.color}"><i></i>${st.label}</span></div>
    <div class="hc-os">${esc(h.distro?.name || '')}</div>
    ${rows}${h.problems.count ? `<div class="hc-p" style="--c:${SEVERITY[Math.max(0, h.problems.worst)].color}">⚠ ${tr('проблем', 'problems')}: ${h.problems.count}</div>` : ''}${sats}
    <div class="hc-hint">клик — подробнее</div>`;
  el.classList.remove('hidden');
  const x = Math.min(window.innerWidth - el.offsetWidth - 12, px.x + 18);
  const y = Math.min(window.innerHeight - el.offsetHeight - 12, px.y + 18);
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
}
