// Detail view for the selected host: the right panel holds the essentials and
// history charts, secondary blocks (disks/SMART, services, ...) float as glass
// cards over the scene on wide screens.
import { tr, locale } from './i18n.js';
import { METRIC, NET, SEVERITY, STATE, SAT_STATUS, SAT_ICON, pct, bytes, bps, bpsShort, duration, ago, esc } from './util.js';

const RANGES = [
  { label: '1ч', sec: 3600 },
  { label: '6ч', sec: 6 * 3600 },
  { label: '24ч', sec: 86400 },
  { label: '7д', sec: 7 * 86400 },
];

const WIDE = window.matchMedia('(min-width: 1200px)');

export class DetailPanel {
  // root: the right-hand panel; overlay: container for glass cards over the scene.
  constructor(root, overlay, { onClose, onService }) {
    this.root = root;
    this.overlay = overlay;
    this.onClose = onClose;
    this.onService = onService;
    this.hostId = null;
    this.range = RANGES[0].sec;
    this.minimized = {};
    try {
      this.range = Number(localStorage.getItem('galaxy.range')) || this.range;
      this.minimized = JSON.parse(localStorage.getItem('galaxy.cardsMin') || '{}');
    } catch { /* storage unavailable */ }
    this.data = null;
    this.history = null;
    this.timer = null;
    this.tooltip = document.createElement('div');
    this.tooltip.className = 'chart-tip hidden';
    document.body.appendChild(this.tooltip);
    WIDE.addEventListener('change', () => { if (this.data) this.render(); });
  }

  async open(id, opts = {}) {
    const changed = id !== this.hostId;
    this.hostId = id;
    this.root.classList.add('open');
    if (changed) {
      this.data = null;
      this.history = null;
      this.overlay.innerHTML = '';
      this.root.querySelector('.panel-body').innerHTML = '<div class="loading">Загрузка…</div>';
    }
    clearInterval(this.timer);
    this.timer = setInterval(() => this.refresh(), 30000);
    await this.refresh();
    this.focus(opts);
  }

  // Scroll to / highlight a section (satellite click) or a container row (moon click).
  focus(opts) {
    const scope = [this.root, this.overlay];
    const find = (sel, pred) => scope.flatMap((r) => [...r.querySelectorAll(sel)]).find(pred);
    if (opts.container) {
      const row = find('[data-container]', (r) => r.dataset.container === opts.container);
      if (row) {
        row.closest('.card')?.classList.remove('min');
        row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        flash(row);
        return;
      }
    }
    if (opts.section) {
      const section = { smart: 'disks', svc: 'services' }[opts.section] || opts.section;
      const s = find('[data-section]', (r) => r.dataset.section === section);
      if (s) {
        s.classList.remove('min');
        s.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        flash(s);
      }
    }
  }

  close() {
    this.hostId = null;
    this.data = null;
    clearInterval(this.timer);
    this.root.classList.remove('open');
    this.overlay.innerHTML = '';
    this.tooltip.classList.add('hidden');
  }

  async refresh() {
    const id = this.hostId;
    if (!id) return;
    try {
      const [d, h] = await Promise.all([
        fetch(`/api/host/${id}`).then((r) => r.json()),
        fetch(`/api/history/${id}?period=${this.range}`).then((r) => r.json()),
      ]);
      if (id !== this.hostId) return;
      if (d.error) throw new Error(d.error);
      this.data = d;
      this.history = h.error ? null : h;
      this.render();
    } catch (e) {
      this.root.querySelector('.panel-body').innerHTML = `<div class="error">${tr('Ошибка загрузки', 'Loading error')}: ${esc(e.message)}</div>`;
    }
  }

  async setRange(sec) {
    this.range = sec;
    try { localStorage.setItem('galaxy.range', String(sec)); } catch { /* ignore */ }
    this.root.querySelectorAll('.seg button').forEach((b) => b.classList.toggle('on', Number(b.dataset.sec) === sec));
    const h = await fetch(`/api/history/${this.hostId}?period=${sec}`).then((r) => r.json());
    this.history = h.error ? null : h;
    this.renderCharts();
  }

  // ------------------------------------------------------------------ render

  render() {
    const { host: h, detail: d } = this.data;
    const body = this.root.querySelector('.panel-body');
    const scroll = body.scrollTop;
    const st = STATE[h.state];
    const kernel = d.os_name && /^Linux version (\S+)/.exec(d.os_name);
    const osName = [h.distro?.name, kernel ? `${tr('ядро', 'kernel')} ${kernel[1]}` : ''].filter(Boolean).join(' · ');

    const main = `
      <header class="ph">
        <div class="ph-top">
          <h2 title="${esc(h.host)}">${esc(h.name)}</h2>
          <button class="icon-btn close" title="Закрыть (Esc)" aria-label="Закрыть">✕</button>
        </div>
        <div class="ph-meta">
          <span class="pill" style="--c:${st.color}"><i></i>${st.label}</span>
          ${h.maintenance ? '<span class="pill" style="--c:#6da7ec"><i></i>Обслуживание</span>' : ''}
          ${h.ip ? `<span class="mono">${esc(h.ip)}</span>` : ''}
        </div>
        <div class="ph-sub">${esc(osName)}${d.arch ? ` · ${esc(d.arch)}` : ''}</div>
        <div class="chips">${h.groups.map((g) => `<span class="chip">${esc(g)}</span>`).join('')}</div>
      </header>

      <section class="kpis">
        ${kpi('CPU', pct(h.cpu), h.cpu, METRIC.cpu.color, h.cores ? `${h.cores} ${tr(plural(h.cores, 'ядро', 'ядра', 'ядер'), h.cores === 1 ? 'core' : 'cores')}${h.load1 != null ? ` · LA ${h.load1.toFixed(2)}` : ''}` : '')}
        ${kpi('RAM', pct(h.mem), h.mem, METRIC.mem.color, h.mem_total ? `${tr('из', 'of')} ${bytes(h.mem_total)}` : '')}
        ${kpi(`${tr('Диск', 'Disk')} ${esc(h.disk_mount || '')}`, pct(h.disk), h.disk, METRIC.disk.color, h.disk_total ? `${tr('из', 'of')} ${bytes(h.disk_total)}` : '')}
        <div class="kpi">
          <div class="kpi-l">Сеть</div>
          <div class="kpi-net"><span style="--c:${NET.in.color}">↓ ${bps(h.net_in)}</span><span style="--c:${NET.out.color}">↑ ${bps(h.net_out)}</span></div>
          <div class="kpi-s">${this.history?.net_iface ? `${tr('график', 'chart')}: ${esc(this.history.net_iface)}` : ''}</div>
        </div>
      </section>

      <section class="block hist">
        <div class="block-h"><h3>История</h3>
          <div class="seg">${RANGES.map((r) => `<button data-sec="${r.sec}" class="${r.sec === this.range ? 'on' : ''}">${r.label}</button>`).join('')}</div>
        </div>
        <div class="charts"></div>
      </section>`;

    const cards = this.cards(h, d);
    const wide = WIDE.matches;
    body.innerHTML = main + (wide ? '' : cards.map((c) => cardHtml(c, false)).join(''));
    body.scrollTop = scroll;

    if (wide) {
      // Balance cards between the two floating columns by estimated height.
      const cols = [[], []], load = [0, 0];
      for (const c of cards) {
        const i = load[0] <= load[1] ? 0 : 1;
        cols[i].push(c);
        load[i] += this.minimized[c.id] ? 1 : c.weight;
      }
      const scrolls = [...this.overlay.querySelectorAll('.ov-col')].map((e) => e.scrollTop);
      this.overlay.innerHTML = cols.map((col, i) =>
        `<div class="ov-col ov-${i ? 'right' : 'left'}">${col.map((c) => cardHtml(c, !!this.minimized[c.id])).join('')}</div>`).join('');
      this.overlay.querySelectorAll('.ov-col').forEach((e, i) => { e.scrollTop = scrolls[i] || 0; });
    } else {
      this.overlay.innerHTML = '';
    }

    body.querySelector('.close').addEventListener('click', () => this.onClose());
    body.querySelectorAll('.seg button').forEach((b) => b.addEventListener('click', () => this.setRange(Number(b.dataset.sec))));
    for (const scope of [body, this.overlay]) {
      scope.querySelectorAll('.more-toggle').forEach((b) => b.addEventListener('click', () => {
        b.closest('.block, .card').classList.toggle('expanded');
      }));
      scope.querySelectorAll('[data-service]').forEach((b) => b.addEventListener('click', (e) => {
        e.stopPropagation();
        this.onService(this.hostId, b.dataset.service);
      }));
      scope.querySelectorAll('.card-h').forEach((hd) => hd.addEventListener('click', () => {
        const card = hd.closest('.card');
        if (!card.classList.contains('floating')) return;
        card.classList.toggle('min');
        this.minimized[card.dataset.section] = card.classList.contains('min');
        try { localStorage.setItem('galaxy.cardsMin', JSON.stringify(this.minimized)); } catch { /* ignore */ }
      }));
    }
    this.renderCharts();
  }

  // Secondary information: shown as floating glass cards on wide screens,
  // appended to the panel otherwise. weight ~ expected height in rows.
  cards(h, d) {
    const out = [this.problemsCard(d.problems)];
    out.push({
      id: 'system', icon: 'ⓘ', title: 'Система', weight: 3,
      html: `<div class="facts tight">
        ${fact('Аптайм', duration(h.uptime))}${fact('Процессы', d.procs ?? '—')}${fact('Swap свободно', pct(d.swap_free_pct))}
        ${fact(h.users_src === 'rdp' ? 'RDP-сессии' : 'Пользователи', h.users == null ? '—' : Math.round(h.users))}${fact('Метрик', h.items)}${d.agent_version ? fact('Агент', esc(d.agent_version)) : ''}
        ${d.hostname ? fact('Hostname', `<span title="${esc(d.hostname)}">${esc(d.hostname)}</span>`) : ''}</div>`,
    });
    if (d.disks?.length) out.push(this.disksCard(d.disks));
    if (d.filesystems.length) {
      out.push({
        id: 'fs', icon: '🗂', title: 'Файловые системы', weight: 1 + d.filesystems.length,
        html: d.filesystems.map((f) => bar(esc(f.mount), f.pused, METRIC.disk.color, f.total ? `${bytes(f.used)} / ${bytes(f.total)}` : '')).join(''),
      });
    }
    for (const s of h.satellites) {
      if (s.type === 'smart' || s.type === 'svc') continue; // shown inside the disks / services cards
      const c = this.satCard(s, d.satellites[s.type]);
      if (c) out.push(c);
    }
    if (d.interfaces.length) out.push(this.ifCard(d.interfaces));
    if (d.iface_errors.length) {
      out.push({ id: 'iface-errors', icon: '⚠', title: 'Ошибки интерфейса', weight: 2,
        html: d.iface_errors.map((e) => `<div class="muted small">${esc(e)}</div>`).join('') });
    }
    return out;
  }

  disksCard(disks) {
    const worst = disks.some((x) => x.status === 'bad') ? 'down' : disks.some((x) => x.status === 'warn') ? 'warn' : 'ok';
    const html = disks.map((x) => {
      const sm = x.smart, io = x.io;
      const color = x.status === 'bad' ? '#d03b3b' : x.status === 'warn' ? '#fab219' : '#0ca30c';
      const kind = sm ? (sm.type === 'nvme' ? 'NVMe' : 'SATA') : x.virtual ? 'VIRT' : '';
      let smart = '';
      if (sm) {
        const a = sm.attrs || {};
        const cells = [
          sm.temperature != null && fact('Темп.', `${sm.temperature}°C`),
          sm.hours != null && fact('Наработка', `${Math.round(sm.hours).toLocaleString(locale())} ${tr('ч', 'h')}`),
          sm.test && fact('Самотест', sm.test === 'true' ? '✓ пройден' : '✗ ошибка'),
          sm.percentage_used != null && fact('Износ', pct(sm.percentage_used)),
          sm.media_errors != null && fact('Ошибки носителя', sm.media_errors),
          sm.critical_warning != null && fact('Crit. warning', sm.critical_warning),
          a.reallocated_sector_ct != null && fact('Переназнач.', a.reallocated_sector_ct),
          a.reported_uncorrect != null && fact('Неисправимые', a.reported_uncorrect),
          a.power_cycle_count != null && fact('Включений', a.power_cycle_count.toLocaleString(locale())),
        ].filter(Boolean);
        smart = cells.length ? `<div class="facts tight">${cells.join('')}</div>` : '<div class="muted small">SMART: нет свежих данных</div>';
      }
      const hasIo = io && Object.values(io).some((v) => v != null);
      const utilTxt = io.util == null ? '—' : `${io.util < 1 ? io.util.toFixed(2) : io.util.toFixed(1)}%`;
      const ioBlock = hasIo ? `
        ${bar('Загрузка устройства', io.util, METRIC.disk.color, '').replace(/<b>[^<]*<\/b>/, `<b>${utilTxt}</b>`)}
        <div class="facts tight io-facts">
          ${fact('Чтение', `${num1(io.r_iops)} ${OPS()}`)}${fact('Запись', `${num1(io.w_iops)} ${OPS()}`)}${fact('Очередь', num1(io.queue))}
          ${fact('Задержка чтения', ms(io.r_await))}${fact('Задержка записи', ms(io.w_await))}
        </div>
        <div class="chart disk-chart" data-dev="${esc(x.name)}"></div>` : '';
      return `<div class="disk">
        <div class="disk-h"><span class="dot" style="--c:${color}"></span><b>${esc(x.name)}</b>
          ${kind ? `<span class="tag" ${kind === 'VIRT' ? 'title="виртуальный диск"' : ''}>${kind}</span>` : ''}<span class="muted small ell">${esc(sm?.model || '')}</span></div>
        ${smart}${ioBlock}</div>`;
    }).join('');
    return {
      id: 'disks', icon: '💽', title: 'Диски', status: worst,
      weight: disks.reduce((n, x) => n + (x.smart ? 7 : 5), 1), html,
    };
  }

  ifCard(list) {
    const many = list.length > 5;
    // SNMP devices also report link state and speed; show them in front of the name.
    const rows = list.map((i) => {
      const dot = i.status == null ? '' : `<span class="dot" style="--c:${i.status === 1 ? '#0ca30c' : '#6b6a64'}" title="${i.status === 1 ? tr('подключён', 'up') : tr('отключён', 'down')}"></span>`;
      const tip = esc(i.name) + (i.speed ? ` · ${bps(i.speed)}` : '');
      return `<tr class="${i.virtual ? 'virt' : ''}"><td title="${tip}">${dot}${esc(i.name)}</td>
      <td class="num" style="--c:${NET.in.color}">↓ ${bps(i.in)}</td><td class="num" style="--c:${NET.out.color}">↑ ${bps(i.out)}</td></tr>`;
    });
    return {
      id: 'net', icon: '⇅', title: 'Сетевые интерфейсы', weight: 1 + Math.min(list.length, 5), collapsible: many,
      html: `<table class="tbl net">${rows.join('')}</table>${many ? '<button class="more-toggle">Показать все</button>' : ''}`,
    };
  }

  satCard(sat, d) {
    if (!d) return null;
    const base = { id: sat.type, icon: SAT_ICON[sat.type] || '•', title: sat.label, status: sat.status, problems: sat.problems };
    if (sat.type === 'docker') {
      return { ...base, weight: 2 + d.containers.length, html: `
        <div class="facts tight">${fact('Контейнеры', `${d.running}/${d.total}`)}${fact('Образы', d.images ?? '—')}${d.version ? fact('Версия', esc(d.version)) : ''}</div>
        ${d.containers.length ? `<table class="tbl"><thead><tr><th>Контейнер</th><th class="num">CPU</th><th class="num">RAM</th></tr></thead>
        ${d.containers.map((ct) => `<tr data-container="${esc(ct.name)}"><td><span class="dot" style="--c:${ct.running ? '#0ca30c' : '#d03b3b'}" title="${esc(ct.status || '')}"></span>
          <span title="${esc(ct.image || '')}">${esc(ct.name)}</span>${ct.restarts ? `<span class="muted small"> ↻${ct.restarts}</span>` : ''}</td>
          <td class="num">${ct.cpu == null ? '—' : pct(ct.cpu, 1)}</td><td class="num">${bytes(ct.mem)}</td></tr>`).join('')}</table>` : ''}` };
    }
    if (sat.type === 'postgresql') {
      return { ...base, weight: 4 + d.databases.length, html: `
        <div class="facts tight">${fact('Ping', d.ping == null ? '—' : d.ping === 1 ? 'OK' : 'нет')}
        ${fact('Соединения', d.connections == null ? '—' : `${d.connections}${d.connections_pct != null ? ` (${pct(d.connections_pct)})` : ''}`)}${fact('Активные', d.active ?? '—')}
        ${fact('Cache hit', pct(d.cache_hit, 1))}${fact('TPS', d.tps == null ? '—' : d.tps.toFixed(1))}${fact('Аптайм', duration(d.uptime))}</div>
        ${d.databases.length ? `<table class="tbl"><thead><tr><th>База</th><th class="num">Размер</th></tr></thead>
        ${d.databases.map((db) => `<tr><td>${esc(db.name)}</td><td class="num">${bytes(db.size)}</td></tr>`).join('')}</table>` : ''}
        ${serviceBtn('postgresql', 'Все метрики PostgreSQL')}` };
    }
    if (sat.type === 'ports') {
      const pingTxt = d.ping == null ? '—' : d.ping === 1 ? tr('отвечает', 'responding') : tr('нет ответа', 'no reply');
      const rows = d.ports.map((p) => {
        const c = p.up == null ? '#6b6a64' : p.up ? '#0ca30c' : '#d03b3b';
        const errs = (p.in_errors || 0) + (p.out_errors || 0);
        const virt = p.iftype != null && ![6, 62, 69, 71, 117].includes(p.iftype);
        return `<li class="${virt ? 'virt' : ''}" title="${esc(p.name)}">
          <div class="pt-top"><span class="dot" style="--c:${c}"></span><b>${esc(p.name)}</b>${p.uplink ? '<span class="tag">WAN</span>' : ''}</div>
          <div class="pt-sub"><span>${p.speed ? bps(p.speed) : '—'}</span>
            <span style="--c:${NET.in.color}">↓ ${bpsShort(p.in)}</span><span style="--c:${NET.out.color}">↑ ${bpsShort(p.out)}</span>
            <span class="${errs ? 'crit-t' : ''}" title="${tr('ошибок в секунду', 'errors per second')}">${tr('ош.', 'err.')} ${errs || 0}</span></div></li>`;
      }).join('');
      return { ...base, weight: 3 + d.ports.length * 1.3, html: `
        <div class="facts tight">${fact('Модель', esc(d.model || '—'))}${d.ping_src === 'agent' ? fact('Агент', pingTxt) : `${fact('ICMP ping', pingTxt)}
        ${fact('Отклик', d.rtt == null ? '—' : `${(d.rtt * 1000).toFixed(1)} ${tr('мс', 'ms')}`)}${fact('Потери', pct(d.loss))}`}</div>
        <ul class="port-list">${rows}</ul>` };
    }
    if (sat.type === 'mysql') {
      const up = d.ping == null ? '—' : d.ping === 1 ? 'работает' : 'недоступен';
      return { ...base, title: 'MySQL — главный сервис', weight: 4 + Math.min(d.databases.length, 6), collapsible: d.databases.length > 5, html: `
        <div class="facts tight">${fact('Статус', up)}${fact('Версия', esc((d.version || '—').split('-')[0]))}${fact('Аптайм', duration(d.uptime))}
        ${fact('Подключено', d.connections ?? '—')}${fact('Выполняется', d.running ?? '—')}${fact('Макс. подключений', d.max_used ?? '—')}
        ${fact('Запросов/с', d.qps == null ? '—' : Math.round(d.qps))}${fact('Медленных/с', d.slow == null ? '—' : d.slow.toFixed(2))}${fact('Buffer pool', pct(d.buffer_pool))}</div>
        ${d.databases.length ? `<table class="tbl"><thead><tr><th>База</th><th class="num">Размер</th></tr></thead>
        ${d.databases.map((db) => `<tr><td>${esc(db.name)}</td><td class="num">${bytes(db.size)}</td></tr>`).join('')}</table>
        ${d.databases.length > 5 ? '<button class="more-toggle">Показать все</button>' : ''}` : ''}
        ${serviceBtn('mysql', 'Все метрики MySQL')}` };
    }
    if (sat.type === '1c') {
      const ibs = d.infobases || [];
      const table = ibs.length ? `<table class="tbl"><thead><tr><th>База</th><th class="num">Сеансы</th><th class="num">Польз.</th><th class="num">CPU 5м</th></tr></thead>
        ${ibs.map((b) => `<tr><td>${b.blocked ? '<span class="dot" style="--c:#d03b3b" title="есть ожидания блокировок"></span>' : ''}${esc(b.name)}</td>
          <td class="num">${b.total ?? '—'}</td><td class="num">${b.users ?? '—'}</td><td class="num">${b.cpu == null ? '—' : pct(b.cpu, 2)}</td></tr>`).join('')}</table>`
        : '';
      const legacy = d.legacy_items ? `<div class="muted small">${tr(`Метрики кластера: ${d.legacy_items}, со свежими данными ${d.legacy_fresh}`, `Cluster items: ${d.legacy_items}, with fresh data ${d.legacy_fresh}`)}</div>` : '';
      return { ...base, weight: 3 + ibs.length, html: `${table}${legacy}${serviceBtn('1c', 'Все метрики 1С по базам')}` };
    }
    if (sat.type === 'services') {
      const many = d.services.length > 8;
      return { ...base, weight: 1 + Math.min(d.services.length, 8) / 2, collapsible: many, html: `
        <ul class="svc">${d.services.map((s) => {
          const c = s.ok == null ? '#6b6a64' : s.ok ? '#0ca30c' : '#d03b3b';
          const t = s.ok == null ? tr('нет свежих данных', 'no fresh data') : s.ok ? tr('работает', 'running') : tr('остановлена', 'stopped');
          return `<li class="${s.ok === false ? 'bad' : 'ok'} ${s.important ? 'imp' : ''}" title="${esc(s.label || s.name)} — ${t}"><span class="dot" style="--c:${c}"></span>${s.important ? '★ ' : ''}${esc(s.name)}</li>`;
        }).join('')}</ul>
        ${many ? '<button class="more-toggle">Показать все</button>' : ''}` };
    }
    if (sat.type === 'zabbix') {
      return { ...base, weight: 2, html: `<div class="facts tight">${fact('Очередь >10м', d.queue_10m ?? '—')}${fact('Хостов', d.hosts ?? '—')}${fact('Метрик', d.items ?? '—')}</div>` };
    }
    return null;
  }

  // Per-device I/O sparklines inside the disk card (floating or in-panel).
  renderDiskCharts() {
    const hs = this.history;
    const scope = [this.root, this.overlay].flatMap((r) => [...r.querySelectorAll('.disk-chart')]);
    for (const el of scope) {
      const dev = el.dataset.dev, s = hs?.series || {};
      const r = s[`dev:${dev}:r_iops`], w = s[`dev:${dev}:w_iops`];
      const series = [
        r?.length && { name: 'чтение', color: NET.in.color, points: r },
        w?.length && { name: 'запись', color: NET.out.color, points: w },
      ].filter(Boolean);
      if (!series.length) { el.innerHTML = ''; continue; }
      lineChart(el, { title: 'Операции, в секунду', series, legend: true, fmt: (v) => `${num1(v)} ${OPS()}`,
        from: hs.from, till: hs.till, tooltip: this.tooltip, height: 64 });
    }
  }

  renderCharts() {
    this.renderDiskCharts();
    const box = this.root.querySelector('.charts');
    if (!box) return;
    const hs = this.history;
    if (!hs || !Object.keys(hs.series).length) {
      box.innerHTML = '<div class="muted small">Нет данных истории</div>';
      return;
    }
    const s = hs.series;
    const defs = [
      s.cpu && { title: 'CPU, %', series: [{ name: 'CPU', color: METRIC.cpu.color, points: s.cpu }], max: 100, fmt: (v) => pct(v, 1) },
      s.mem && { title: 'RAM, %', series: [{ name: 'RAM', color: METRIC.mem.color, points: s.mem }], max: 100, fmt: (v) => pct(v, 1) },
      s.disk && { title: 'Диск, %', series: [{ name: 'Диск', color: METRIC.disk.color, points: s.disk }], max: 100, fmt: (v) => pct(v, 1) },
      (s.net_in || s.net_out) && {
        title: `${tr('Сеть', 'Network')}${hs.net_iface ? ` · ${hs.net_iface}` : ''}`, legend: true,
        series: [
          s.net_in && { name: NET.in.label, color: NET.in.color, points: s.net_in },
          s.net_out && { name: NET.out.label, color: NET.out.color, points: s.net_out },
        ].filter(Boolean),
        fmt: bps,
      },
      // Load average only when the panel has room (narrow layout scrolls anyway).
      !WIDE.matches && s.load && { title: 'Load average (1 мин)', series: [{ name: 'LA', color: '#8e8d86', points: s.load }], fmt: (v) => v.toFixed(2) },
    ].filter(Boolean);
    box.innerHTML = '';
    for (const def of defs) {
      const wrap = document.createElement('div');
      wrap.className = 'chart';
      box.appendChild(wrap);
      lineChart(wrap, { ...def, from: hs.from, till: hs.till, tooltip: this.tooltip });
    }
  }

  problemsCard(list) {
    const worst = list.reduce((m, p) => Math.max(m, p.severity), -1);
    const many = list.length > 5;
    const html = list.length
      ? `<ul class="problems">${list.map((p) => {
        const sv = SEVERITY[p.severity];
        return `<li><span class="sev" style="--c:${sv.color}" title="${sv.label}">${sv.label}</span>
          <span class="pn">${esc(p.name)}</span>
          <span class="pa">${ago(p.since)}${p.acknowledged ? ` · ✓ ${tr('подтв.', 'ack.')}` : ''}</span></li>`;
      }).join('')}</ul>${many ? '<button class="more-toggle">Показать все</button>' : ''}`
      : '<div class="muted small">Активных проблем нет</div>';
    return {
      id: 'problems', icon: '⚠', title: list.length ? `${tr('Проблемы', 'Problems')} · ${list.length}` : tr('Проблемы', 'Problems'),
      badge: worst >= 0 ? SEVERITY[worst] : null, collapsible: many,
      weight: list.length ? 1 + Math.min(list.length, 5) * 1.6 : 1.5, html,
    };
  }
}

function cardHtml(c, min) {
  const st = c.status ? SAT_STATUS[c.status] || SAT_STATUS.nodata : null;
  const pill = st ? `<span class="pill" style="--c:${st.color}"><i></i><span>${st.label}</span>${c.problems ? ` · ${c.problems}` : ''}</span>`
    : c.badge ? `<span class="pill" style="--c:${c.badge.color}"><i></i>${c.badge.label}</span>` : '';
  const floating = WIDE.matches;
  return `<section class="card ${floating ? 'floating glass' : 'block'} ${c.collapsible ? 'collapsible' : ''} ${min && floating ? 'min' : ''}" data-section="${c.id}">
    <div class="card-h" ${floating ? 'title="Свернуть / развернуть"' : ''}><h3><span class="sat-ic">${c.icon}</span>${esc(c.title)}</h3>${pill}${floating ? '<span class="card-tg" aria-hidden="true">▾</span>' : ''}</div>
    <div class="card-b">${c.html}</div></section>`;
}

function serviceBtn(kind, label) {
  return `<button class="sv-open-btn" data-service="${kind}">${label} <span aria-hidden="true">↗</span></button>`;
}

const OPS = () => tr('оп/с', 'ops/s');

function flash(el) {
  el.classList.add('flash');
  setTimeout(() => el.classList.remove('flash'), 1800);
}

function num1(v) {
  return v == null ? '—' : v < 10 ? v.toFixed(1) : Math.round(v).toString();
}

function ms(v) {
  return v == null ? '—' : `${v < 10 ? v.toFixed(1) : Math.round(v)} ${tr('мс', 'ms')}`;
}

function kpi(label, text, v, color, sub) {
  const w = v == null ? 0 : Math.max(0, Math.min(100, v));
  return `<div class="kpi"><div class="kpi-l"><i style="--c:${color}"></i>${label}</div>
    <div class="kpi-v">${text}</div>
    <div class="meter"><span style="width:${w}%;--c:${color}"></span></div>
    <div class="kpi-s">${sub || ''}</div></div>`;
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

export function fact(k, v) {
  return `<div class="fact"><span>${k}</span><b>${v}</b></div>`;
}

function bar(label, v, color, sub) {
  const w = v == null || v <= 0 ? 0 : Math.max(1.5, Math.min(100, v)); // tiny non-zero loads stay visible
  return `<div class="bar-row"><div class="bar-top"><span>${label}</span><b>${pct(v, 1)}</b></div>
    <div class="meter"><span style="width:${w}%;--c:${color}"></span></div>
    ${sub ? `<div class="muted small">${sub}</div>` : ''}</div>`;
}

// ------------------------------------------------------------------ SVG line chart

const NS = 'http://www.w3.org/2000/svg';

export function lineChart(root, { title, series, max, fmt, from, till, legend, tooltip, height = 80 }) {
  const W = Math.max(240, root.clientWidth || 340), H = height;
  const pad = { l: 40, r: 8, t: 5, b: 16 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  let yMax = max;
  if (yMax == null) {
    const m = Math.max(0, ...series.flatMap((s) => s.points.map((p) => p[1])));
    yMax = niceMax(m || 1);
  }
  const x = (t) => pad.l + ((t - from) / (till - from)) * iw;
  const y = (v) => pad.t + ih - (Math.min(v, yMax) / yMax) * ih;

  const legendHtml = legend
    ? `<div class="legend">${series.map((s) => `<span><i style="--c:${s.color}"></i>${esc(s.name)}</span>`).join('')}</div>` : '';
  const last = series.map((s) => s.points.at(-1)?.[1]);
  root.innerHTML = `<div class="chart-h"><span>${esc(title)}</span>
    <b>${series.length === 1 && last[0] != null ? fmt(last[0]) : ''}</b></div>${legendHtml}`;

  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', H);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', title);
  root.appendChild(svg);

  const add = (tag, attrs, parent = svg) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    parent.appendChild(e);
    return e;
  };

  // Grid + y labels.
  for (let i = 0; i <= 2; i++) {
    const v = (yMax / 2) * i, yy = y(v);
    add('line', { x1: pad.l, x2: W - pad.r, y1: yy, y2: yy, class: 'grid' });
    const tx = add('text', { x: pad.l - 6, y: yy + 3, class: 'axis', 'text-anchor': 'end' });
    tx.textContent = max === 100 ? `${Math.round(v)}%` : shortFmt(v, fmt);
  }
  // X labels.
  const span = till - from;
  for (let i = 0; i <= 3; i++) {
    const t = from + (span / 3) * i;
    const tx = add('text', { x: x(t), y: H - 4, class: 'axis', 'text-anchor': i === 0 ? 'start' : i === 3 ? 'end' : 'middle' });
    const d = new Date(t * 1000);
    tx.textContent = span > 2 * 86400
      ? d.toLocaleDateString(locale(), { day: '2-digit', month: '2-digit' })
      : d.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' });
  }

  const defs = add('defs', {});
  series.forEach((s, i) => {
    if (!s.points.length) return;
    const pts = s.points.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`);
    if (series.length === 1) {
      const gid = `g${Math.random().toString(36).slice(2)}`;
      const lg = add('linearGradient', { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
      add('stop', { offset: '0%', 'stop-color': s.color, 'stop-opacity': 0.28 }, lg);
      add('stop', { offset: '100%', 'stop-color': s.color, 'stop-opacity': 0 }, lg);
      add('path', {
        d: `M${x(s.points[0][0])},${y(0)} L${pts.join(' L')} L${x(s.points.at(-1)[0])},${y(0)} Z`,
        fill: `url(#${gid})`,
      });
    }
    add('polyline', { points: pts.join(' '), fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' });
    s._i = i;
  });

  // Hover layer.
  const cross = add('line', { y1: pad.t, y2: pad.t + ih, class: 'cross', visibility: 'hidden' });
  const dots = series.map((s) => add('circle', { r: 4, fill: s.color, stroke: '#0d0f18', 'stroke-width': 2, visibility: 'hidden' }));
  const hit = add('rect', { x: pad.l, y: pad.t, width: iw, height: ih, fill: 'transparent' });
  hit.addEventListener('pointermove', (e) => {
    const r = svg.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const t = from + ((px - pad.l) / iw) * (till - from);
    let rows = [], cx = null;
    series.forEach((s, i) => {
      const p = nearest(s.points, t);
      if (!p) { dots[i].setAttribute('visibility', 'hidden'); return; }
      cx = x(p[0]);
      dots[i].setAttribute('cx', cx);
      dots[i].setAttribute('cy', y(p[1]));
      dots[i].setAttribute('visibility', 'visible');
      rows.push(`<div><i style="--c:${s.color}"></i>${esc(s.name)} <b>${fmt(p[1])}</b></div>`);
    });
    if (cx == null) return;
    cross.setAttribute('x1', cx);
    cross.setAttribute('x2', cx);
    cross.setAttribute('visibility', 'visible');
    const p0 = nearest(series[0].points, t) || nearest(series.at(-1).points, t);
    const when = new Date(p0[0] * 1000).toLocaleString(locale(), { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    tooltip.innerHTML = `<div class="tt-h">${when}</div>${rows.join('')}`;
    tooltip.classList.remove('hidden');
    const tw = tooltip.offsetWidth;
    tooltip.style.left = `${Math.max(8, e.clientX - tw - 14)}px`;
    tooltip.style.top = `${e.clientY - 12}px`;
  });
  hit.addEventListener('pointerleave', () => {
    cross.setAttribute('visibility', 'hidden');
    dots.forEach((d) => d.setAttribute('visibility', 'hidden'));
    tooltip.classList.add('hidden');
  });
}

function nearest(points, t) {
  if (!points.length) return null;
  let lo = 0, hi = points.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid][0] < t) lo = mid; else hi = mid;
  }
  return Math.abs(points[lo][0] - t) < Math.abs(points[hi][0] - t) ? points[lo] : points[hi];
}

function niceMax(v) {
  const e = 10 ** Math.floor(Math.log10(v));
  const f = v / e;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e;
}

function shortFmt(v, fmt) {
  return fmt(v).replace('бит/с', '').trim();
}
