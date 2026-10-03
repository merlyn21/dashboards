// Right-hand "galaxy summary" panel shown in the overview (no host selected):
// facts about Zabbix itself, health belts, recent problems, top loads, traffic, services.
import { tr, locale } from './i18n.js';
import { METRIC, NET, SEVERITY, bps, duration, esc, logRel, pct } from './util.js';
import { HEALTH_TIERS, healthTier } from './scene.js';

const SEV_EN = ['N/C', 'Info', 'Warning', 'Average', 'High', 'Disaster'];

export class SummaryPanel {
  constructor(root, { onSelect, onToggle }) {
    this.root = root;
    this.onSelect = onSelect;
    this.body = root.querySelector('.panel-body');
    this.mini = root.querySelector('.sm-mini');
    root.addEventListener('click', (e) => {
      const el = e.target.closest('[data-host]');
      if (el) this.onSelect(el.dataset.host);
    });
    root.querySelector('.sm-toggle').addEventListener('click', () => onToggle());
  }

  setCollapsed(on) {
    this.root.classList.toggle('collapsed', on);
    this.root.querySelector('.sm-toggle').textContent = on ? '‹' : '›';
  }

  setVisible(on) {
    this.root.classList.toggle('open', on);
  }

  update(z, hosts) {
    if (!z) return;
    const scroll = this.body.scrollTop;
    const live = hosts.filter((h) => h.state === 'online');
    const offline = hosts.filter((h) => h.state !== 'online');
    const users = hosts.reduce((s, h) => s + (h.users || 0), 0);

    // Problems across hosts
    const probs = hosts.flatMap((h) => (h.problems.list || []).map((p) => ({ ...p, host: h })));
    const recent = [...probs].sort((a, b) => b.since - a.since).slice(0, 5);
    const oldest = [...probs].sort((a, b) => a.since - b.since)[0];

    // Belts
    const belts = HEALTH_TIERS.map((t, i) => ({ ...t, hosts: hosts.filter((h) => healthTier(h) === i) }));

    // Top loads
    const top = (key, n = 3) => live.filter((h) => h[key] != null).sort((a, b) => b[key] - a[key]).slice(0, n);
    const loadRows = (key) => top(key).map((h) => `<div class="bar-row sm" data-host="${h.id}">
        <div class="bar-top"><span>${esc(h.name)}</span><b>${pct(h[key])}</b></div>
        <div class="meter"><span style="width:${Math.min(100, h[key])}%;--c:${METRIC[key].color}"></span></div></div>`).join('');

    // Traffic
    const tot = (h) => (h.net_in || 0) + (h.net_out || 0);
    // Clients' traffic already passes through their uplink: count only hosts without one.
    const edge = live.filter((h) => !h.uplink);
    const netIn = edge.reduce((s, h) => s + (h.net_in || 0), 0), netOut = edge.reduce((s, h) => s + (h.net_out || 0), 0);

    // Network dependencies (galaxy.uplink tags)
    const byId = new Map(hosts.map((h) => [h.id, h]));
    const hubs = hosts.filter((h) => h.clients?.length);
    const netDeps = hubs.map((hub) => {
      const cl = hub.clients.map((id) => byId.get(id)).filter(Boolean);
      const down = cl.filter((c) => c.state !== 'online').length;
      return `<li data-host="${hub.id}"><b>${esc(hub.name)}</b>
        <span class="sm-l">${cl.map((c) => `<a data-host="${c.id}" class="${c.state !== 'online' ? 'off' : ''}">${esc(c.name)}</a>`).join(', ')}</span>
        <span class="pa">${down ? tr(`${down} нет связи`, `${down} down`) : `${cl.length}`}</span></li>`;
    }).join('');
    const warns = z.topology_warnings || [];
    const byNet = [...live].filter(tot).sort((a, b) => tot(b) - tot(a));
    const lo = byNet.length ? tot(byNet.at(-1)) : 0, hi = byNet.length ? tot(byNet[0]) : 0;
    const netRows = byNet.slice(0, 4).map((h) => {
      const w = Math.max(4, logRel(tot(h), lo, hi) * 100);
      const share = tot(h) ? (h.net_in || 0) / tot(h) : 0.5;
      return `<div class="bar-row sm" data-host="${h.id}">
        <div class="bar-top"><span>${esc(h.name)}</span><b>↓ ${bps(h.net_in)} · ↑ ${bps(h.net_out)}</b></div>
        <div class="meter split" style="width:${w}%"><span style="width:${share * 100}%;--c:${NET.in.color}"></span><span style="width:${(1 - share) * 100}%;--c:${NET.out.color}"></span></div></div>`;
    }).join('');

    // Services
    let cRun = 0, cAll = 0, cHosts = 0, onecS = 0, onecB = 0, pgOk = 0, pgAll = 0, myOk = 0, myAll = 0;
    for (const h of live) {
      for (const s of h.satellites) {
        if (s.type === 'docker' && s.containers?.length) {
          cHosts += 1; cAll += s.containers.length; cRun += s.containers.filter((c) => c.running).length;
        }
        if (s.type === '1c' && s.bases) { onecB += s.bases; onecS += s.sessions || 0; }
        if (s.type === 'postgresql') { pgAll += 1; if (s.status === 'ok') pgOk += 1; }
        if (s.type === 'mysql') { myAll += 1; if (s.status === 'ok') myOk += 1; }
      }
    }

    const n = (v) => (v == null ? '—' : Number(v).toLocaleString(locale()));
    // Collapsed strip: the few numbers worth seeing at a glance.
    this.mini.innerHTML = `
      <div title="на связи"><b>${z.hosts_online}</b><span>связь</span></div>
      <div class="${offline.length ? 'bad' : ''}" title="нет связи"><b>${offline.length}</b><span>${tr('нет', 'off')}</span></div>
      ${[5, 4, 3, 2].map((s) => `<div class="${z.problems[s] ? '' : 'zero'}" style="--c:${SEVERITY[s].color}" title="${SEV_EN[s]}"><b>${z.problems[s]}</b><span>${SEV_EN[s].slice(0, 4)}</span></div>`).join('')}
      <div title="трафик"><b>${bps(netIn + netOut).replace(/ .*/, '')}</b><span>${bps(netIn + netOut).split(' ')[1] || ''}</span></div>`;
    this.body.innerHTML = `
      <header class="ph sm-head">
        <div class="sm-kicker">◢ Сводка галактики ◣</div>
        <h2>Zabbix ${esc(z.version || '')}</h2>
        <div class="ph-sub">${esc(z.server || '')} · ${tr('обновлено', 'updated')} ${new Date().toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</div>
      </header>

      <section class="sm-hero">
        <div><b>${z.hosts_online}</b><span>на связи</span></div>
        <div class="${offline.length ? 'bad' : ''}"><b>${offline.length}</b><span>нет связи</span></div>
        <div><b>${z.hosts_enabled}</b><span>включено</span></div>
        <div class="muted-b"><b>${z.hosts_disabled}</b><span>отключено</span></div>
      </section>
      ${offline.length ? `<div class="sm-offline">${offline.map((h) => `<button data-host="${h.id}" class="chip">${esc(h.name)}</button>`).join('')}</div>` : ''}

      <section class="block">
        <h3>Проблемы</h3>
        <div class="sm-sev">${[5, 4, 3, 2, 1, 0].map((s) => `<div class="${z.problems[s] ? '' : 'zero'}" style="--c:${SEVERITY[s].color}">
          <b>${z.problems[s]}</b><span>${SEV_EN[s]}</span></div>`).join('')}</div>
        ${recent.length ? `<h4 class="sm-sub">Свежие</h4><ul class="sm-list">${recent.map((p) => `<li data-host="${p.host.id}">
          <span class="sev" style="--c:${SEVERITY[p.severity].color}">${SEV_EN[p.severity]}</span>
          <span class="sm-l"><b>${esc(p.host.name)}</b> ${esc(p.name)}</span><span class="pa">${duration(Date.now() / 1000 - p.since)}</span></li>`).join('')}</ul>` : ''}
        ${oldest ? `<div class="sm-oldest" data-host="${oldest.host.id}">${tr('Самая старая', 'Oldest')}: <b>${esc(oldest.host.name)}</b> — ${esc(oldest.name)} · <b>${duration(Date.now() / 1000 - oldest.since)}</b></div>` : ''}
      </section>

      <section class="block">
        <h3>Пояса состояния</h3>
        <ul class="sm-belts">${belts.map((b) => `<li><i style="--c:${b.color}"></i><span>${b.short}</span><b>${b.hosts.length}</b>
          <em>${b.hosts.map((h) => `<a data-host="${h.id}">${esc(h.name)}</a>`).join(', ')}</em></li>`).join('')}</ul>
      </section>

      ${hubs.length || warns.length ? `<section class="block">
        <h3>${tr('Сетевые зависимости', 'Network dependencies')}</h3>
        ${hubs.length ? `<ul class="sm-list sm-deps">${netDeps}</ul>` : ''}
        ${warns.map((w) => `<div class="sm-warn">⚠ ${esc(w)}</div>`).join('')}
      </section>` : ''}

      <section class="block">
        <h3>Самые нагруженные</h3>
        <div class="sm-cols">
          <div><h4 class="sm-sub">CPU</h4>${loadRows('cpu')}</div>
          <div><h4 class="sm-sub">RAM</h4>${loadRows('mem')}</div>
          <div><h4 class="sm-sub">Диск</h4>${loadRows('disk')}</div>
        </div>
      </section>

      <section class="block">
        <h3>Трафик галактики</h3>
        <div class="sm-net"><span style="--c:${NET.in.color}">↓ <b>${bps(netIn)}</b></span><span style="--c:${NET.out.color}">↑ <b>${bps(netOut)}</b></span></div>
        ${netRows}
      </section>

      <section class="block">
        <h3>Сервисы</h3>
        <div class="facts tight">
          ${cAll ? `<div class="fact"><span>🐳 Docker</span><b>${tr(`${cRun}/${cAll} на ${cHosts} хостах`, `${cRun}/${cAll} on ${cHosts} hosts`)}</b></div>` : ''}
          ${pgAll ? `<div class="fact"><span>🐘 PostgreSQL</span><b>${pgOk}/${pgAll} OK</b></div>` : ''}
          ${myAll ? `<div class="fact"><span>🐬 MySQL</span><b>${myOk}/${myAll} OK</b></div>` : ''}
          ${onecB ? `<div class="fact"><span>▣ 1С</span><b>${tr(`${onecS} сеанс. в ${onecB} базах`, `${onecS} sessions in ${onecB} infobases`)}</b></div>` : ''}
          <div class="fact"><span>👤 Залогинено на хостах</span><b>${users}</b></div>
          ${z.web_users != null ? `<div class="fact"><span>Пользователи веба</span><b>${n(z.web_users)}</b></div>` : ''}
        </div>
      </section>

      <section class="block">
        <h3>Мониторинг</h3>
        <div class="facts tight">
          <div class="fact"><span>Метрик</span><b>${n(z.items)}</b></div>
          <div class="fact"><span>С ошибками сбора</span><b>${n(z.unsupported)}</b></div>
          <div class="fact"><span>Очередь &gt;10 мин</span><b>${n(z.queue_10m)}</b></div>
        </div>
      </section>`;
    this.body.scrollTop = scroll;
  }
}
