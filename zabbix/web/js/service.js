// Full-screen service window (PostgreSQL, 1C) that opens over everything.
import { t, tr, locale } from './i18n.js';
import { lineChart, fact } from './panel.js';
import { SAT_ICON, bytes, esc, pct } from './util.js';

const RANGES = [
  { label: '1ч', sec: 3600 },
  { label: '6ч', sec: 6 * 3600 },
  { label: '24ч', sec: 86400 },
  { label: '7д', sec: 7 * 86400 },
];

// Categorical order validated for the dark surface (dataviz reference palette).
const CAT = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'];

// ------------------------------------------------------------------ value formatting

function num(v, d = 1) {
  if (v == null || Number.isNaN(v)) return '—';
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(d)} ${tr('млрд', 'bn')}`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(d)} ${tr('млн', 'M')}`;
  if (a >= 1e4) return `${(v / 1e3).toFixed(d)} ${tr('тыс', 'K')}`;
  if (a >= 100 || Number.isInteger(v)) return Math.round(v).toLocaleString(locale());
  return v.toFixed(a < 1 ? 2 : d);
}

function secs(v) {
  if (v == null) return '—';
  if (v === 0) return '0';
  if (v < 1) return `${Math.round(v * 1000)} ${tr('мс', 'ms')}`;
  if (v < 120) return `${v.toFixed(1)} ${tr('с', 's')}`;
  if (v < 7200) return `${Math.round(v / 60)} ${tr('мин', 'min')}`;
  if (v < 172800) return `${(v / 3600).toFixed(1)} ${tr('ч', 'h')}`;
  return `${Math.round(v / 86400)} ${tr('д', 'd')}`;
}

// Format a metric record {v, u} by its Zabbix units.
function fmt(m, rate = false) {
  if (!m || m.v == null) return '—';
  const { v, u } = m;
  let s;
  if (u === 'B' || u === 'b' || u === 'Bps') s = bytes(v);
  else if (u === 's' || u === 'uptime') s = secs(v);
  else if (u === '%') s = pct(v, v < 10 ? 1 : 0);
  else s = num(v);
  return rate && s !== '—' ? `${s}/${tr('с', 's')}` : s;
}

const val = (m) => (m && m.v != null ? m.v : null);

function tile(label, value, { tone, sub, title } = {}) {
  return `<div class="sv-tile ${tone || ''}" ${title ? `title="${esc(title)}"` : ''}>
    <span>${label}</span><b>${value}</b>${sub ? `<em>${sub}</em>` : ''}</div>`;
}

function tone(v, warn, crit, higherIsBad = true) {
  if (v == null) return '';
  if (higherIsBad) return v >= crit ? 'crit' : v >= warn ? 'warn' : '';
  return v <= crit ? 'crit' : v <= warn ? 'warn' : '';
}

// Horizontal stacked bar with legend; parts: [{label, v, color}]
function stack(parts, unit = '') {
  const total = parts.reduce((s, p) => s + (p.v || 0), 0);
  const segs = total
    ? parts.filter((p) => p.v).map((p) => `<i style="flex:${p.v};--c:${p.color}" title="${esc(t(p.label))}: ${num(p.v)}${unit}"></i>`).join('')
    : '<i class="empty"></i>';
  const legend = parts.map((p) => `<span class="${p.v ? '' : 'zero'}"><i style="--c:${p.color}"></i>${esc(p.label)} <b>${num(p.v ?? 0)}</b></span>`).join('');
  return `<div class="sv-stack">${segs}</div><div class="sv-legend">${legend}</div>`;
}

// ------------------------------------------------------------------ window

export class ServiceWindow {
  constructor() {
    this.el = document.createElement('div');
    this.el.className = 'sv-backdrop hidden';
    this.el.innerHTML = '<div class="sv-win glass" role="dialog" aria-modal="true"><div class="sv-body"></div></div>';
    document.body.appendChild(this.el);
    this.body = this.el.querySelector('.sv-body');
    this.tooltip = document.createElement('div');
    this.tooltip.className = 'chart-tip hidden';
    document.body.appendChild(this.tooltip);
    this.range = 3600;
    try { this.range = Number(localStorage.getItem('galaxy.svRange')) || this.range; } catch { /* ignore */ }
    this.el.addEventListener('click', (e) => { if (e.target === this.el) this.close(); });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.isOpen) { e.stopImmediatePropagation(); this.close(); }
    }, true);
    window.addEventListener('resize', () => { if (this.isOpen && this.data) this.renderCharts(); });
  }

  get isOpen() {
    return !this.el.classList.contains('hidden');
  }

  async open(hostId, kind) {
    this.hostId = hostId;
    this.kind = kind;
    this.data = null;
    this.el.classList.remove('hidden');
    document.body.classList.add('sv-open');
    this.body.innerHTML = '<div class="loading">Загрузка…</div>';
    clearInterval(this.timer);
    this.timer = setInterval(() => this.load(), 60000);
    await this.load();
  }

  close() {
    clearInterval(this.timer);
    this.el.classList.add('hidden');
    document.body.classList.remove('sv-open');
    this.tooltip.classList.add('hidden');
    this.data = null;
  }

  async load() {
    const { hostId, kind } = this;
    try {
      const d = await fetch(`/api/service/${hostId}/${kind}?period=${this.range}`).then((r) => r.json());
      if (d.error) throw new Error(d.error);
      if (hostId !== this.hostId || kind !== this.kind || !this.isOpen) return;
      this.data = d;
      this.render();
    } catch (e) {
      this.body.innerHTML = `<div class="error">${tr('Ошибка загрузки', 'Loading error')}: ${esc(e.message)}</div>`;
    }
  }

  render() {
    const d = this.data;
    const scroll = this.body.scrollTop;
    const title = { postgresql: 'PostgreSQL', mysql: 'MySQL', '1c': '1С:Предприятие' }[d.kind];
    const head = `<header class="sv-head">
      <h2><span class="sat-ic">${SAT_ICON[d.kind]}</span>${title}<small>${esc(d.host.name)}</small></h2>
      <div class="seg">${RANGES.map((r) => `<button data-sec="${r.sec}" class="${r.sec === this.range ? 'on' : ''}">${r.label}</button>`).join('')}</div>
      <span class="muted small">${tr('обновлено', 'updated')} ${new Date(d.generated * 1000).toLocaleTimeString(locale())}</span>
      <button class="icon-btn sv-close" title="Закрыть (Esc)" aria-label="Закрыть">✕</button>
    </header>`;
    this.charts = [];
    const content = d.kind === 'postgresql' ? this.pg(d) : d.kind === 'mysql' ? this.mysql(d) : this.onec(d);
    this.body.innerHTML = head + content;
    this.body.scrollTop = scroll;
    this.body.querySelector('.sv-close').addEventListener('click', () => this.close());
    this.body.querySelectorAll('.sv-head .seg button').forEach((b) => b.addEventListener('click', () => {
      this.range = Number(b.dataset.sec);
      try { localStorage.setItem('galaxy.svRange', String(this.range)); } catch { /* ignore */ }
      this.load();
    }));
    this.body.querySelectorAll('.sv-db-row').forEach((tr) => tr.addEventListener('click', () => {
      tr.classList.toggle('open');
      tr.nextElementSibling.classList.toggle('hidden');
    }));
    this.body.querySelectorAll('.sv-more').forEach((b) => b.addEventListener('click', () => b.parentElement.classList.toggle('expanded')));
    this.renderCharts();
  }

  // Charts are declared while building HTML and drawn once the DOM exists.
  chart(def) {
    const id = `svc${this.charts.length}`;
    this.charts.push({ id, def });
    return `<div class="chart sv-chart" id="${id}"></div>`;
  }

  renderCharts() {
    const d = this.data;
    for (const { id, def } of this.charts) {
      const el = this.body.querySelector(`#${id}`);
      if (!el) continue;
      const series = def.series.filter((s) => s.points && s.points.length);
      if (!series.length) {
        el.innerHTML = `<div class="chart-h"><span>${esc(def.title)}</span></div><div class="muted small sv-nodata">Нет истории</div>`;
        continue;
      }
      lineChart(el, { ...def, series, legend: series.length > 1, from: d.from, till: d.till, tooltip: this.tooltip, height: 110 });
    }
  }

  // ------------------------------------------------------------------ PostgreSQL

  pg(d) {
    const m = d.summary, S = d.series;
    const ping = m.ping;
    const role = val(m['replication.recovery_role']);
    const pingTone = !ping || ping.v == null ? 'warn' : ping.v === 1 ? 'good' : 'crit';
    const connPct = val(m['connections.sum.total_pct']);
    const cache = val(m['cache.hit']);
    const idleTx = val(m['connections.sum.idle_in_transaction']);
    const failedArch = val(m['archive.failed_trying_to_archive']);
    const deadlocks = val(m['dbstat.sum.deadlocks']);

    const tiles = [
      tile('Доступность', ping?.v === 1 ? 'OK' : ping?.v === 0 ? 'нет' : '—', { tone: pingTone, sub: ping?.age != null ? tr(`проверка ${secs(ping.age)} назад`, `checked ${secs(ping.age)} ago`) : '' }),
      tile('Роль', role == null ? '—' : role === 0 ? 'Primary' : 'Standby', { sub: `${tr('реплик', 'replicas')}: ${num(val(m['replication.count']) ?? 0)}` }),
      tile('Аптайм', secs(val(m.uptime))),
      tile('Соединения', num(val(m['connections.sum.total'])), { tone: tone(connPct, 80, 90), sub: connPct != null ? tr(`${connPct}% от max_connections`, `${connPct}% of max_connections`) : '' }),
      tile('Cache hit', fmt(m['cache.hit']), { tone: tone(cache, 95, 90, false), sub: 'доля чтений из кеша' }),
      tile('Коммиты', fmt(m['dbstat.sum.xact_commit'], true), { sub: `${tr('откаты', 'rollbacks')} ${fmt(m['dbstat.sum.xact_rollback'], true)}` }),
      tile('Deadlocks', fmt(m['dbstat.sum.deadlocks'], true), { tone: deadlocks ? 'warn' : '' }),
      tile('Idle in transaction', num(idleTx), { tone: idleTx ? 'warn' : '', sub: 'соединения, держащие транзакцию' }),
      tile('Возраст старейшей xid', num(val(m['oldest.xid'])), { tone: tone(val(m['oldest.xid']), 1e9, 1.5e9), sub: 'wraparound при ~2 млрд' }),
      tile('Autovacuum', num(val(m['autovacuum.count'])), { sub: 'активных воркеров' }),
      tile('WAL', `${num(val(m['wal.count']))} ${tr('сегм.', 'segm.')}`, { sub: `${tr('записано', 'written')} ${fmt(m['wal.write'])}` }),
      tile('Архивация', failedArch ? `${num(failedArch)} ${tr('ошибок', 'errors')}` : 'OK', { tone: failedArch ? 'crit' : '', sub: `${tr('в очереди', 'queued')} ${num(val(m['archive.count_files_to_archive']) ?? 0)}` }),
    ].join('');

    const conn = stack([
      { label: 'active', v: val(m['connections.sum.active']), color: CAT[0] },
      { label: 'idle', v: val(m['connections.sum.idle']), color: CAT[2] },
      { label: 'idle in transaction', v: val(m['connections.sum.idle_in_transaction']), color: CAT[3] },
      { label: 'idle in tx (aborted)', v: val(m['connections.sum.idle_in_transaction_aborted']), color: CAT[7] },
      { label: 'prepared', v: val(m['connections.sum.prepared']), color: CAT[6] },
      { label: 'fastpath', v: val(m['connections.sum.fastpath_function_call']), color: CAT[4] },
      { label: 'disabled', v: val(m['connections.sum.disabled']), color: '#8e8d86' },
    ]);

    const perDb = d.databases.map((db, i) => ({ name: db.name, color: CAT[i % CAT.length], points: S[`db:${db.name}:dbstat.xact_commit`] }));
    const charts = [
      this.chart({ title: 'Соединения', fmt: (v) => num(v), series: [
        { name: 'active', color: CAT[0], points: S['connections.sum.active'] },
        { name: 'idle', color: CAT[2], points: S['connections.sum.idle'] },
        { name: 'idle in tx', color: CAT[3], points: S['connections.sum.idle_in_transaction'] },
        { name: 'всего', color: CAT[6], points: S['connections.sum.total'] }] }),
      this.chart({ title: 'Транзакции, в секунду', fmt: (v) => num(v), series: [
        { name: 'commit', color: CAT[0], points: S['dbstat.sum.xact_commit'] },
        { name: 'rollback', color: CAT[1], points: S['dbstat.sum.xact_rollback'] }] }),
      this.chart({ title: 'Коммиты по базам, в секунду', fmt: (v) => num(v), series: perDb }),
      this.chart({ title: 'Cache hit, %', max: 100, fmt: (v) => pct(v, 1), series: [{ name: 'cache hit', color: CAT[2], points: S['cache.hit'] }] }),
      this.chart({ title: 'Чтение строк, в секунду', fmt: (v) => num(v), series: [
        { name: 'returned', color: CAT[0], points: S['dbstat.sum.tup_returned'] },
        { name: 'fetched', color: CAT[1], points: S['dbstat.sum.tup_fetched'] }] }),
      this.chart({ title: 'Изменение строк, в секунду', fmt: (v) => num(v), series: [
        { name: 'insert', color: CAT[2], points: S['dbstat.sum.tup_inserted'] },
        { name: 'update', color: CAT[3], points: S['dbstat.sum.tup_updated'] },
        { name: 'delete', color: CAT[7], points: S['dbstat.sum.tup_deleted'] }] }),
      this.chart({ title: 'Блоки, в секунду', fmt: (v) => num(v), series: [
        { name: 'из кеша', color: CAT[2], points: S['dbstat.sum.blks_hit'] },
        { name: 'с диска', color: CAT[1], points: S['dbstat.sum.blks_read'] }] }),
      this.chart({ title: 'Буферы bgwriter, в секунду', fmt: (v) => num(v), series: [
        { name: 'checkpoint', color: CAT[0], points: S['bgwriter.buffers_checkpoint'] },
        { name: 'clean', color: CAT[2], points: S['bgwriter.buffers_clean'] },
        { name: 'alloc', color: CAT[3], points: S['bgwriter.buffers_alloc'] }] }),
      this.chart({ title: 'Temp-файлы, байт/с', fmt: (v) => bytes(v), series: [{ name: 'temp', color: CAT[4], points: S['dbstat.sum.temp_bytes'] }] }),
    ].join('');

    const rows = d.databases.map((db) => this.pgDbRow(db)).join('');
    const svc = (title, list) => `<div class="sv-group"><h4>${title}</h4><div class="facts tight">${list.map(([k, key, rate]) => fact(k, fmt(m[key], rate))).join('')}</div></div>`;

    return `
      <section class="sv-tiles">${tiles}</section>
      <section class="sv-sec"><h3>Соединения по состояниям</h3>${conn}</section>
      <section class="sv-sec"><h3>Динамика</h3><div class="sv-charts">${charts}</div></section>
      <section class="sv-sec"><h3>Базы данных <span class="muted small">клик по строке — подробности</span></h3>
        <div class="sv-table-wrap"><table class="tbl sv-table">
          <thead><tr><th>База</th><th class="num">Размер</th><th class="num">Подключ.</th><th class="num">Commit/с</th><th class="num">Rollback/с</th>
          <th class="num">Строк/с</th><th class="num">Cache hit</th><th class="num">Блокировки</th><th class="num">Медл. запросы</th>
          <th class="num">Макс. запрос</th><th class="num">Bloat табл.</th><th class="num">Возраст xid</th><th class="num">Deadlocks</th></tr></thead>
          <tbody>${rows}</tbody></table></div></section>
      <section class="sv-sec"><h3>Служебные процессы</h3><div class="sv-groups">
        ${svc('Checkpoint', [['По расписанию', 'bgwriter.checkpoints_timed', true], ['По требованию', 'bgwriter.checkpoints_req', true], ['Время записи', 'bgwriter.checkpoint_write_time', true], ['Время sync', 'bgwriter.checkpoint_sync_time', true], ['Буферов checkpoint', 'bgwriter.buffers_checkpoint', true]])}
        ${svc('Bgwriter', [['Выделено буферов', 'bgwriter.buffers_alloc', true], ['Записано bgwriter', 'bgwriter.buffers_clean', true], ['Остановок (maxwritten)', 'bgwriter.maxwritten_clean', true]])}
        <div class="sv-group"><h4>Репликация</h4><div class="facts tight">
          ${fact('Реплик', fmt(m['replication.count']))}
          ${fact('Статус', { 0: 'down', 1: 'up', 2: 'master' }[val(m['replication.status'])] ?? '—')}
          ${fact('Отставание', fmt(m['replication.lag.sec']))}${fact('Отставание, байт', fmt(m['replication.lag.b']))}</div></div>
        ${svc('WAL и архив', [['Сегментов WAL', 'wal.count'], ['Записано', 'wal.write'], ['Получено', 'wal.receive'], ['Заархивировано', 'archive.count_archived_files'], ['Ждут архивации', 'archive.count_files_to_archive'], ['Размер очереди', 'archive.size_files_to_archive'], ['Ошибок архивации', 'archive.failed_trying_to_archive']])}
        ${svc('Ввод-вывод (сумма)', [['Время чтения блоков', 'dbstat.sum.blk_read_time'], ['Время записи блоков', 'dbstat.sum.blk_write_time'], ['Temp-файлов', 'dbstat.sum.temp_files', true], ['Конфликтов', 'dbstat.sum.conflicts', true], ['Ошибок checksum', 'dbstat.sum.checksum_failures', true], ['Бэкендов', 'dbstat.sum.numbackends']])}
      </div></section>
      ${this.unsupported(d.unsupported)}`;
  }

  pgDbRow(db) {
    const m = db.m;
    const hit = val(m['dbstat.blks_hit']), read = val(m['dbstat.blks_read']);
    const ratio = hit != null && read != null && hit + read > 0 ? (hit / (hit + read)) * 100 : null;
    const slow = ['queries.query.slow_count', 'queries.tx.slow_count', 'queries.mro.slow_count'].reduce((s, k) => s + (val(m[k]) || 0), 0);
    const bloat = val(m['db.bloating_tables']);
    const dl = val(m['dbstat.deadlocks']);
    const size = m['db.size'];
    const locks = Object.entries(m).filter(([k]) => k.startsWith('locks.') && k !== 'locks.total')
      .map(([k, v]) => fact(k.slice(6), fmt(v))).join('');
    const q = (kind, label) => fact(label, `${fmt(m[`queries.${kind}.slow_count`])} ${tr('медл. · макс', 'slow · max')} ${fmt(m[`queries.${kind}.time_max`])} · ${tr('сумма', 'total')} ${fmt(m[`queries.${kind}.time_sum`])}`);
    return `<tr class="sv-db-row">
        <td><span class="sv-caret">▸</span><b>${esc(db.name)}</b></td>
        <td class="num">${size ? fmt(size) : '<span class="warn-t" title="метрика не собирается">н/д</span>'}</td>
        <td class="num">${fmt(m['dbstat.numbackends'])}</td>
        <td class="num">${fmt(m['dbstat.xact_commit'])}</td>
        <td class="num">${fmt(m['dbstat.xact_rollback'])}</td>
        <td class="num">${fmt(m['dbstat.tup_returned'])}</td>
        <td class="num ${tone(ratio, 95, 90, false)}-t">${ratio == null ? '—' : pct(ratio, 1)}</td>
        <td class="num">${fmt(m['locks.total'])}</td>
        <td class="num ${slow ? 'warn-t' : ''}">${num(slow)}</td>
        <td class="num">${fmt(m['queries.query.time_max'])}</td>
        <td class="num ${bloat ? 'warn-t' : ''}">${bloat == null ? '—' : num(bloat)}</td>
        <td class="num">${fmt(m['db.age'])}</td>
        <td class="num ${dl ? 'warn-t' : ''}">${fmt(m['dbstat.deadlocks'])}</td>
      </tr>
      <tr class="sv-db-detail hidden"><td colspan="13"><div class="sv-groups">
        <div class="sv-group"><h4>Строки, в секунду</h4><div class="facts tight">
          ${fact('returned', fmt(m['dbstat.tup_returned']))}${fact('fetched', fmt(m['dbstat.tup_fetched']))}${fact('inserted', fmt(m['dbstat.tup_inserted']))}
          ${fact('updated', fmt(m['dbstat.tup_updated']))}${fact('deleted', fmt(m['dbstat.tup_deleted']))}</div></div>
        <div class="sv-group"><h4>Блоки и I/O</h4><div class="facts tight">
          ${fact('из кеша/с', fmt(m['dbstat.blks_hit']))}${fact('с диска/с', fmt(m['dbstat.blks_read']))}${fact('время чтения', fmt(m['dbstat.blk_read_time']))}
          ${fact('время записи', fmt(m['dbstat.blk_write_time']))}${fact('temp-файлов/с', fmt(m['dbstat.temp_files']))}${fact('temp байт/с', fmt(m['dbstat.temp_bytes']))}</div></div>
        <div class="sv-group"><h4>Блокировки по типам</h4><div class="facts tight">${locks || '<span class="muted small">нет данных</span>'}</div></div>
        <div class="sv-group wide"><h4>Запросы</h4><div class="facts tight one">${q('query', 'Запросы')}${q('tx', 'Транзакции')}${q('mro', 'Обслуживание')}</div></div>
        <div class="sv-group"><h4>Прочее</h4><div class="facts tight">
          ${fact('конфликты/с', fmt(m['dbstat.conflicts']))}${fact('ошибки checksum', fmt(m['dbstat.checksum_failures']))}${fact('bloat таблиц', fmt(m['db.bloating_tables']))}</div></div>
      </div></td></tr>`;
  }

  unsupported(list) {
    if (!list?.length) return '';
    return `<section class="sv-sec sv-unsup ${list.length > 4 ? 'collapsible' : ''}"><h3>Метрики с ошибкой сбора <span class="count">${list.length}</span></h3>
      <ul>${list.map((u) => `<li><b>${esc(u.name)}</b><span title="${esc(u.error)}">${esc(u.error.split('\n').pop().slice(0, 160))}</span></li>`).join('')}</ul>
      ${list.length > 4 ? '<button class="more-toggle sv-more">Показать все</button>' : ''}</section>`;
  }

  // ------------------------------------------------------------------ MySQL

  mysql(d) {
    const m = d.summary, S = d.series;
    const ping = m.ping;
    const conns = val(m.threads_connected), maxUsed = val(m.max_used_connections);
    const reqs = val(m.innodb_buffer_pool_read_requests), reads = val(m.innodb_buffer_pool_reads);
    // Share of logical reads served from memory (buffer pool hit ratio), from the per-second rates.
    const hit = reqs ? Math.max(0, 100 * (1 - (reads || 0) / reqs)) : null;
    const slow = val(m.slow_queries), aborted = val(m.aborted_connects);
    const bpUtil = val(m.buffer_pool_utilization);
    const tmpDisk = val(m.created_tmp_disk_tables);
    const lockTime = val(m.innodb_row_lock_time), lockMax = val(m.innodb_row_lock_time_max);
    const errs = Object.entries(m).filter(([k]) => k.startsWith('connection_errors_'));
    const errSum = errs.reduce((s, [, v]) => s + (val(v) || 0), 0);

    const tiles = [
      tile('Статус', ping?.v === 1 ? 'Работает' : ping?.v === 0 ? 'Недоступен' : '—', { tone: ping?.v === 1 ? 'good' : 'crit', sub: esc((m.version?.v || '').split('-')[0]) }),
      tile('Аптайм', secs(val(m.uptime))),
      tile('Подключено', num(conns), { sub: `${tr('макс. за аптайм', 'max since start')} ${num(maxUsed)}` }),
      tile('Выполняется', num(val(m.threads_running)), { tone: tone(val(m.threads_running), 20, 50), sub: 'потоков прямо сейчас' }),
      tile('Запросов', fmt(m.queries, true), { sub: `${tr('клиентских', 'client')} ${fmt(m.questions, true)}` }),
      tile('Медленные', fmt(m.slow_queries, true), { tone: slow ? 'warn' : '' }),
      tile('Buffer pool hit', hit == null ? '—' : pct(hit, 2), { tone: tone(hit, 99, 95, false), sub: 'чтения из памяти' }),
      tile('Buffer pool', pct(bpUtil, 1), { sub: 'заполнен' }),
      tile('Ожидания блокировок', num(val(m.innodb_row_lock_waits)), { sub: `${tr('всего', 'total')} ${secs(lockTime)} · ${tr('макс', 'max')} ${secs(lockMax)}` }),
      tile('Tmp-таблицы на диске', fmt(m.created_tmp_disk_tables, true), { tone: tmpDisk ? 'warn' : '' }),
      tile('Отказы подключений', fmt(m.aborted_connects, true), { tone: aborted ? 'warn' : '', sub: `${tr('ошибок подключения', 'connection errors')} ${num(errSum)}/${tr('с', 's')}` }),
      tile('Трафик', `↓ ${fmt(m.bytes_received)}/${tr('с', 's')}`, { sub: `↑ ${fmt(m.bytes_sent)}/${tr('с', 's')}` }),
    ].join('');

    const cmds = stack([
      { label: 'SELECT', v: val(m.com_select), color: CAT[0] },
      { label: 'INSERT', v: val(m.com_insert), color: CAT[2] },
      { label: 'UPDATE', v: val(m.com_update), color: CAT[3] },
      { label: 'DELETE', v: val(m.com_delete), color: CAT[7] },
    ], `/${tr('с', 's')}`);

    const hitSeries = (S.innodb_buffer_pool_read_requests || []).map(([t, v], i) => {
      const r = S.innodb_buffer_pool_reads?.[i]?.[1] || 0;
      return [t, v ? Math.max(0, 100 * (1 - r / v)) : 100];
    });
    const charts = [
      this.chart({ title: 'Запросы, в секунду', fmt: (v) => num(v), series: [
        { name: 'все', color: CAT[0], points: S.queries }, { name: 'клиентские', color: CAT[2], points: S.questions }] }),
      this.chart({ title: 'Команды, в секунду', fmt: (v) => num(v), series: [
        { name: 'SELECT', color: CAT[0], points: S.com_select }, { name: 'INSERT', color: CAT[2], points: S.com_insert },
        { name: 'UPDATE', color: CAT[3], points: S.com_update }, { name: 'DELETE', color: CAT[7], points: S.com_delete }] }),
      this.chart({ title: 'Потоки', fmt: (v) => num(v), series: [
        { name: 'подключено', color: CAT[0], points: S.threads_connected }, { name: 'выполняется', color: CAT[1], points: S.threads_running }] }),
      this.chart({ title: 'Buffer pool hit, %', fmt: (v) => pct(v, 2), series: [{ name: 'hit', color: CAT[2], points: hitSeries }] }),
      this.chart({ title: 'Трафик, байт/с', fmt: (v) => bytes(v), series: [
        { name: 'принято', color: CAT[0], points: S.bytes_received }, { name: 'отправлено', color: CAT[1], points: S.bytes_sent }] }),
      this.chart({ title: 'Подключения, в секунду', fmt: (v) => num(v), series: [
        { name: 'новые', color: CAT[0], points: S.connections }, { name: 'отказы', color: CAT[7], points: S.aborted_connects },
        { name: 'обрывы клиентов', color: CAT[3], points: S.aborted_clients }] }),
      this.chart({ title: 'Медленные запросы, в секунду', fmt: (v) => num(v), series: [{ name: 'slow', color: CAT[4], points: S.slow_queries }] }),
      this.chart({ title: 'Временные таблицы, в секунду', fmt: (v) => num(v), series: [
        { name: 'в памяти', color: CAT[2], points: S.created_tmp_tables }, { name: 'на диске', color: CAT[1], points: S.created_tmp_disk_tables }] }),
      this.chart({ title: 'Ожидания блокировок строк (всего)', fmt: (v) => num(v), series: [{ name: 'waits', color: CAT[6], points: S.innodb_row_lock_waits }] }),
    ].join('');

    const total = d.databases.reduce((s, x) => s + (x.v || 0), 0);
    const rows = d.databases.map((db) => {
      const share = total ? (db.v || 0) / total * 100 : 0;
      return `<tr><td><b>${esc(db.name)}</b></td><td class="num">${fmt(db)}</td>
        <td class="sv-share"><div class="meter"><span style="width:${Math.max(share ? 1 : 0, share)}%;--c:${CAT[0]}"></span></div></td>
        <td class="num">${pct(share, 1)}</td><td class="num muted">${db.age != null ? tr(`${secs(db.age)} назад`, `${secs(db.age)} ago`) : '—'}</td></tr>`;
    }).join('');

    const group = (title, list) => `<div class="sv-group"><h4>${title}</h4><div class="facts tight">${list.map(([k, key, rate]) => fact(k, fmt(m[key], rate))).join('')}</div></div>`;
    return `
      <section class="sv-tiles">${tiles}</section>
      <section class="sv-sec"><h3>Команды по типу, в секунду</h3>${cmds}</section>
      <section class="sv-sec"><h3>Динамика</h3><div class="sv-charts">${charts}</div></section>
      <section class="sv-sec"><h3>Базы данных <span class="muted small">${tr('всего', 'total')} ${bytes(total)}</span></h3>
        <table class="tbl sv-table"><thead><tr><th>База</th><th class="num">Размер</th><th>Доля</th><th class="num">%</th><th class="num">Замер</th></tr></thead>
        <tbody>${rows}</tbody></table></section>
      <section class="sv-sec"><h3>Подробности</h3><div class="sv-groups">
        ${group('InnoDB', [['Страниц всего', 'innodb_buffer_pool_pages_total'], ['Страниц свободно', 'innodb_buffer_pool_pages_free'], ['Логических чтений', 'innodb_buffer_pool_read_requests', true], ['Чтений с диска', 'innodb_buffer_pool_reads', true], ['Ожиданий свободной страницы', 'innodb_buffer_pool_wait_free'], ['Открытых файлов', 'innodb_num_open_files'], ['Записано в redo log', 'innodb_os_log_written'], ['Размер redo log', 'innodb_log_file_size']])}
        ${group('Потоки и таблицы', [['В кеше потоков', 'threads_cached'], ['Создано потоков', 'threads_created', true], ['Открытых таблиц', 'open_tables'], ['Определений таблиц', 'open_table_definitions'], ['Tmp-файлов на диске', 'created_tmp_files', true], ['Binlog cache на диске', 'binlog_cache_disk_use']])}
        ${group('Подключения', [['Новых', 'connections', true], ['Отказов', 'aborted_connects', true], ['Обрывов клиентов', 'aborted_clients', true], ...errs.map(([k]) => [`${tr('Ошибки', 'Errors')}: ${k.replace('connection_errors_', '')}`, k, true])])}
      </div></section>
      ${this.unsupported(d.unsupported)}`;
  }

  // ------------------------------------------------------------------ 1C

  onec(d) {
    const ibs = d.infobases;
    let out = '';
    if (ibs.length) {
      const sum = (k) => ibs.reduce((s, b) => s + (val(b.m[k]) || 0), 0);
      const blocked = sum('blocked_dbms') + sum('blocked_ls');
      out += `<section class="sv-tiles">
        ${tile('Информационных баз', num(ibs.length))}
        ${tile('Сеансов', num(sum('total')), { sub: `${tr('активных', 'active')} ${num(sum('active'))}` })}
        ${tile('Пользователей', num(sum('users')), { sub: 'уникальных' })}
        ${tile('Фоновых заданий', num(sum('bgjob')))}
        ${tile('Серверных вызовов', num(sum('calls_5min')), { sub: 'за 5 мин' })}
        ${tile('CPU', pct(sum('cpu_5min'), 2), { sub: 'за 5 мин, все базы' })}
        ${tile('Ждут блокировок', num(blocked), { tone: blocked ? 'crit' : '', sub: 'СУБД + управляемые' })}
      </section>
      <section class="sv-sec"><h3>Информационные базы</h3><div class="sv-ibs">${ibs.map((b) => this.ib(b, d)).join('')}</div></section>`;
    }
    if (d.legacy.length) {
      const ok = d.legacy.filter((r) => r.supported && r.v != null && !r.stale);
      const bad = d.legacy.filter((r) => !r.supported);
      out += `<section class="sv-sec"><h3>Метрики кластера 1С <span class="muted small">(шаблон уровня сервера)</span></h3>
        ${bad.length ? `<div class="sv-note crit">${tr(`${bad.length} из ${d.legacy.length} метрик не собираются`, `${bad.length} of ${d.legacy.length} items are not collected`)}: <b>${esc((bad[0].error || 'ошибка').replace(/\.+$/, ''))}</b>.
          Скорее всего, на агенте Zabbix нет UserParameter-скриптов 1С из этого шаблона.</div>` : ''}
        <table class="tbl sv-table"><thead><tr><th>Метрика</th><th class="num">Значение</th><th>Состояние</th></tr></thead><tbody>
        ${d.legacy.map((r) => `<tr><td>${esc(r.n)}</td><td class="num">${r.supported ? fmt(r) : '—'}</td>
          <td>${r.supported ? (r.stale ? '<span class="muted">нет свежих данных</span>' : '<span class="good-t">OK</span>') : `<span class="crit-t" title="${esc(r.error)}">не поддерживается</span>`}</td></tr>`).join('')}
        </tbody></table>${ok.length ? '' : ''}</section>`;
    }
    if (!out) out = '<div class="muted">Нет метрик 1С</div>';
    return out;
  }

  ib(b, d) {
    const m = b.m, S = d.series, k = (f) => S[`${b.uuid}:${f}`];
    const blocked = (val(m.blocked_dbms) || 0) + (val(m.blocked_ls) || 0);
    const longCall = val(m.duration_current_max), longDb = val(m.duration_current_dbms_max);
    const clients = stack([
      { label: 'Тонкий', v: val(m.thin), color: CAT[0] },
      { label: 'Толстый', v: val(m.thick), color: CAT[1] },
      { label: 'Web', v: val(m.web), color: CAT[2] },
      { label: 'Конфигуратор', v: val(m.designer), color: CAT[3] },
      { label: 'Фоновые', v: val(m.bgjob), color: CAT[6] },
      { label: 'Спящие', v: val(m.hibernated), color: '#8e8d86' },
      { label: 'Прочие', v: val(m.other), color: CAT[4] },
    ]);
    const stale = Object.values(m).every((x) => x.stale);
    return `<article class="sv-ib">
      <header><h4>${esc(b.name)}</h4><span class="muted small mono">${esc(b.uuid)}</span>
        ${stale ? '<span class="pill" style="--c:#6b6a64"><i></i>нет свежих данных</span>' : ''}</header>
      <div class="sv-tiles small">
        ${tile('Сеансов', fmt(m.total), { sub: `${tr('активных', 'active')} ${fmt(m.active)}` })}
        ${tile('Пользователей', fmt(m.users))}
        ${tile('Ждут блокировок', num(blocked), { tone: blocked ? 'crit' : '', sub: `${tr('СУБД', 'DBMS')} ${fmt(m.blocked_dbms)} · ${tr('упр.', 'managed')} ${fmt(m.blocked_ls)}` })}
        ${tile('Самый долгий вызов', secs(longCall), { tone: tone(longCall, 10, 60), sub: `${tr('запрос к СУБД', 'DBMS query')} ${secs(longDb)}` })}
      </div>
      <h5>Сеансы по типу клиента</h5>${clients}
      <div class="sv-groups">
        <div class="sv-group"><h4>Нагрузка за 5 мин</h4><div class="facts tight">
          ${fact('Вызовов', fmt(m.calls_5min))}${fact('CPU', fmt(m.cpu_5min))}${fact('Время вызовов', fmt(m.duration_5min))}
          ${fact('Время СУБД', fmt(m.duration_dbms_5min))}${fact('Сервисы кластера', fmt(m.duration_service_5min))}${fact('Память', fmt(m.memory_5min))}
          ${fact('Память вызова, max', fmt(m.memory_current_max))}</div></div>
        <div class="sv-group"><h4>Трафик за 5 мин</h4><div class="facts tight">
          ${fact('Клиент ↔ сервер', fmt(m.bytes_5min))}${fact('Сервер ↔ СУБД', fmt(m.dbms_bytes_5min))}
          ${fact('Чтение с диска', fmt(m.read_5min))}${fact('Запись на диск', fmt(m.write_5min))}</div></div>
      </div>
      <div class="sv-charts two">
        ${this.chart({ title: 'Сеансы', fmt: (v) => num(v), series: [
          { name: 'всего', color: CAT[0], points: k('total') }, { name: 'активные', color: CAT[2], points: k('active') },
          { name: 'пользователи', color: CAT[3], points: k('users') }] })}
        ${this.chart({ title: 'Серверные вызовы за 5 мин', fmt: (v) => num(v), series: [{ name: 'вызовов', color: CAT[0], points: k('calls_5min') }] })}
        ${this.chart({ title: 'Время за 5 мин', fmt: secs, series: [
          { name: 'вызовы', color: CAT[0], points: k('duration_5min') }, { name: 'СУБД', color: CAT[1], points: k('duration_dbms_5min') }] })}
        ${this.chart({ title: 'Трафик за 5 мин', fmt: (v) => bytes(v), series: [
          { name: 'клиент', color: CAT[0], points: k('bytes_5min') }, { name: 'СУБД', color: CAT[1], points: k('dbms_bytes_5min') }] })}
        ${this.chart({ title: 'Ожидание блокировок', fmt: (v) => num(v), series: [
          { name: 'СУБД', color: CAT[7], points: k('blocked_dbms') }, { name: 'управляемые', color: CAT[3], points: k('blocked_ls') }] })}
        ${this.chart({ title: 'CPU за 5 мин, %', fmt: (v) => pct(v, 2), series: [{ name: 'CPU', color: CAT[2], points: k('cpu_5min') }] })}
      </div>
      ${b.errors?.length ? `<div class="sv-note crit">${tr('Ошибки сбора', 'Collection errors')}: ${esc(b.errors[0])}</div>` : ''}
    </article>`;
  }
}
