// Event alerts: new problems and new logins light up the planet and show a
// toast in the top-right corner. Average and lower fade out after 3 minutes;
// High and Disaster stay until the problem is resolved, and the planet pulses red.
import { tr } from './i18n.js';
import { METRIC, SEVERITY, esc, pct, ago } from './util.js';

export const ALERT_TTL = 3 * 60 * 1000;
const HIGH = 4;
const LOGIN_COLOR = '#86b6ef';
const MAX_VISIBLE = 6;

export class Alerts {
  constructor(root, { galaxy, onOpen }) {
    this.root = root;
    this.galaxy = galaxy;
    this.onOpen = onOpen;
    this.toasts = new Map(); // key -> toast
    this.prevProblems = null; // hostId -> Set(problem ids)
    this.prevUsers = new Map();
    this.enabled = true;
    // In host view the toasts collapse into this pill (they would cover the cards).
    this.pill = document.createElement('button');
    this.pill.className = 'al-pill glass hidden';
    this.pill.addEventListener('click', () => this.root.classList.toggle('open'));
    this.root.appendChild(this.pill);
    setInterval(() => this.tick(), 1000);
  }

  setEnabled(on) {
    this.enabled = on;
    this.root.classList.toggle('hidden', !on);
  }

  // Called after every overview poll.
  update(hosts) {
    const now = Date.now();
    const first = this.prevProblems === null;
    const current = new Map();
    const alive = new Set();

    for (const h of hosts) {
      const ids = new Set();
      for (const p of h.problems.list || []) {
        ids.add(p.id);
        const key = `p${p.id}`;
        alive.add(key);
        const sinceMs = p.since * 1000;
        if (p.severity >= HIGH) {
          // Persistent: shown on first load too, until the problem disappears.
          this.upsert(key, { kind: 'problem', host: h, problem: p, persistent: true, start: sinceMs });
        } else if (!this.toasts.has(key)) {
          const isNew = first ? now - sinceMs < ALERT_TTL : !this.prevProblems.get(h.id)?.has(p.id);
          if (isNew) {
            const start = first ? sinceMs : now;
            this.upsert(key, { kind: 'problem', host: h, problem: p, persistent: false, start });
            this.galaxy.flash(h.id, SEVERITY[p.severity].color, start, ALERT_TTL);
          }
        } else {
          this.toasts.get(key).host = h;
        }
      }
      current.set(h.id, ids);

      // New logins: the count grew since the previous poll.
      const prev = this.prevUsers.get(h.id);
      if (!first && prev != null && h.users != null && h.users > prev) {
        const key = `u${h.id}-${now}`;
        alive.add(key);
        this.upsert(key, { kind: 'login', host: h, from: prev, to: h.users, persistent: false, start: now });
        this.galaxy.flash(h.id, LOGIN_COLOR, now, ALERT_TTL);
      }
      if (h.users != null) this.prevUsers.set(h.id, h.users);

      const worst = (h.problems.list || []).reduce((m, p) => Math.max(m, p.severity), -1);
      // No red pulsing on a host we cannot reach: its data is stale, it is drawn lifeless.
      this.galaxy.setAlarm(h.id, (worst >= HIGH && h.state === 'online') || this.demoAlarm === h.id);
    }

    // Resolved persistent problems go away; transient ones live out their 5 minutes.
    for (const [key, t] of this.toasts) {
      if (t.kind === 'problem' && t.persistent && !alive.has(key) && !key.startsWith('demo')) this.remove(key);
    }
    this.prevProblems = current;
    this.render();
  }

  // ?demo=alerts: synthetic events on real hosts to preview the effects.
  demo(hosts) {
    const live = hosts.filter((h) => h.state === 'online');
    if (live.length < 2) return;
    const now = Date.now();
    const [a, b] = live;
    this.upsert('demo-avg', { kind: 'problem', host: a, persistent: false, start: now - 20000,
      problem: { id: 'demo-avg', name: 'Демо: высокая загрузка CPU (>90% в течение 5 мин)', severity: 3, since: now / 1000 - 20, ack: false } });
    this.galaxy.flash(a.id, SEVERITY[3].color, now - 20000, ALERT_TTL);
    this.upsert('demo-login', { kind: 'login', host: b, from: 0, to: 1, persistent: false, start: now - 90000 });
    this.galaxy.flash(b.id, LOGIN_COLOR, now - 90000, ALERT_TTL);
    if (live[2]) {
      const c = live[2];
      this.upsert('demo-high', { kind: 'problem', host: c, persistent: true, start: now,
        problem: { id: 'demo-high', name: 'Демо: диск / заполнен на 98%', severity: 5, since: now / 1000 - 240, ack: false } });
      this.galaxy.setAlarm(c.id, true);
      this.demoAlarm = c.id;
    }
    this.render();
  }

  upsert(key, data) {
    const t = this.toasts.get(key);
    if (t) Object.assign(t, data);
    else this.toasts.set(key, { key, ...data, dismissed: false });
  }

  remove(key) {
    const t = this.toasts.get(key);
    if (!t) return;
    this.toasts.delete(key);
    if (t.el) {
      t.el.classList.add('leaving');
      setTimeout(() => t.el.remove(), 350);
    }
  }

  tick() {
    const now = Date.now();
    let changed = false;
    for (const [key, t] of this.toasts) {
      if (!t.persistent && now - t.start >= ALERT_TTL) {
        this.remove(key);
        changed = true;
      } else if (t.el) {
        this.updateTimer(t, now);
      }
    }
    if (changed) this.render();
  }

  updateTimer(t, now) {
    const bar = t.el.querySelector('.al-progress i');
    if (bar) bar.style.width = `${Math.max(0, 100 - ((now - t.start) / ALERT_TTL) * 100)}%`;
    const age = t.el.querySelector('.al-age');
    if (age) age.textContent = tr(`${ago(t.kind === 'problem' ? t.problem.since : t.start / 1000)} назад`, `${ago(t.kind === 'problem' ? t.problem.since : t.start / 1000)} ago`);
  }

  // Persistent (High/Disaster) first, then newest first.
  render() {
    const list = [...this.toasts.values()].filter((t) => !t.dismissed)
      .sort((a, b) => (b.persistent - a.persistent) || (b.problem?.severity ?? -1) - (a.problem?.severity ?? -1) || b.start - a.start);
    const visible = list.slice(0, MAX_VISIBLE);
    const keep = new Set(visible.map((t) => t.key));
    for (const t of this.toasts.values()) {
      if (t.el && !keep.has(t.key)) { t.el.remove(); t.el = null; }
    }
    for (const t of visible) {
      if (!t.el) {
        t.el = document.createElement('div');
        t.el.addEventListener('click', (e) => {
          if (e.target.closest('.al-close')) {
            e.stopPropagation();
            t.dismissed = true;
            t.el.remove();
            t.el = null;
            this.render();
            return;
          }
          this.onOpen(t.host.id);
        });
      }
      t.el.className = `alert glass ${t.persistent ? 'alarm' : ''} ${t.kind}`;
      t.el.innerHTML = this.html(t);
      t.el.style.setProperty('--c', t.kind === 'login' ? LOGIN_COLOR : SEVERITY[t.problem.severity].color);
      this.root.appendChild(t.el); // keeps DOM order equal to sort order
      this.updateTimer(t, Date.now());
    }
    const alarms = list.filter((t) => t.persistent).length;
    this.pill.classList.toggle('hidden', !list.length);
    this.pill.classList.toggle('alarm', alarms > 0);
    this.pill.innerHTML = `🔔 <b>${list.length}</b>${alarms ? ` <span>${tr('тревог', 'alarms')}: ${alarms}</span>` : ''}<i>▾</i>`;
    this.pill.title = 'Оповещения — показать или скрыть список';
    this.root.prepend(this.pill);
    const hidden = list.length - visible.length;
    let more = this.root.querySelector('.al-more');
    if (hidden > 0) {
      if (!more) { more = document.createElement('div'); more.className = 'al-more glass'; }
      more.textContent = tr(`ещё ${hidden}…`, `${hidden} more…`);
      this.root.appendChild(more);
    } else if (more) more.remove();
  }

  html(t) {
    const h = t.host;
    const stats = ['cpu', 'mem', 'disk'].map((m) => `<span><i style="--m:${METRIC[m].color}"></i>${METRIC[m].label} <b>${pct(h[m])}</b></span>`).join('');
    const users = h.users != null ? `<span>👤 <b>${Math.round(h.users)}</b></span>` : '';
    let head, body;
    if (t.kind === 'login') {
      head = `<span class="al-sev">Вход</span>`;
      body = `${tr('Новый вход: пользователей', 'New login: users')} ${Math.round(t.from)} → <b>${Math.round(t.to)}</b>`;
    } else {
      const sv = SEVERITY[t.problem.severity];
      head = `<span class="al-sev">${sv.label}</span>${t.problem.ack ? '<span class="al-ack">✓ подтверждена</span>' : ''}`;
      body = esc(t.problem.name);
    }
    return `<div class="al-top">${head}<b class="al-host">${esc(h.name)}</b><span class="al-age"></span>
        <button class="al-close" title="Скрыть плашку" aria-label="Скрыть">✕</button></div>
      <div class="al-msg">${body}</div>
      <div class="al-meta">${h.distro ? `<span>${esc(h.distro.name)}</span>` : ''}${h.ip ? `<span class="mono">${esc(h.ip)}</span>` : ''}</div>
      <div class="al-stats">${stats}${users}</div>
      ${t.persistent ? '' : '<div class="al-progress"><i></i></div>'}`;
  }
}
