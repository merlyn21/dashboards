/* Map of connections fed by the local service (server.py): /api/state once, then /api/events. */
(async () => {
'use strict';
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const nf = (n) => Number(n).toLocaleString('ru-RU');
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

const CATS = {
  service: { label: 'Сервисы', color: '#4f97ff' },
  p2p: { label: 'P2P и торрент', color: '#22c38e' },
  iot: { label: 'Облака умного дома', color: '#f2b33d' },
  vpn: { label: 'VPN', color: '#e0629a' },
  router: { label: 'Сам роутер', color: '#b69cff' },
  attack: { label: 'Подключения к роутеру', color: '#ff6a3d' },
  scan: { label: 'Сканы', color: '#ffe066' },
  research: { label: 'Исследовательские сканеры', color: '#a9b4c2' },
  other: { label: 'Прочее', color: '#8d98a5' },
};
const GATE_LABEL = { winbox: 'Winbox', api: 'RouterOS API', ssh: 'SSH и Telnet', btest: 'Bandwidth test', web: 'Веб', sip: 'SIP', dns: 'DNS', snmp: 'SNMP', amp: 'Усилители', vpn: 'VPN', icmp: 'ICMP', other: 'Прочие порты' };
const regionNames = (() => { try { return new Intl.DisplayNames(['ru'], { type: 'region' }); } catch (e) { return null; } })();
const country = (cc) => { if (!cc) return ''; try { return regionNames ? regionNames.of(cc) : cc; } catch (e) { return cc; } };
const orgOf = (c) => c.service || (c.org && c.org.name) || c.remote;
const placeOf = (g) => (g ? [country(g.cc), g.city].filter(Boolean).join(', ') : 'место неизвестно');
function fmtRate(bps) {
  if (bps < 1e3) return `${Math.round(bps)} бит/с`;
  if (bps < 1e6) return `${Math.round(bps / 1e3).toLocaleString('ru-RU')} кбит/с`;
  return `${(bps / 1e6).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} Мбит/с`;
}

const S = { sparks: [], links: new Map(), hidden: new Set(), device: '', places: [], meteors: [], rings: [], flashes: [], tab: 'dest',
  router: {}, ports: [], tunnels: [], inbound: {}, home: null, pollInterval: 30, nextPoll: 0, status: {} };

/* ---------- Projection: Mercator that covers the whole window ---------- */
const base = $('#base'), fx = $('#fx'), bctx = base.getContext('2d'), ctx = fx.getContext('2d');
let W = 0, H = 0, DPR = 1, zoomT = d3.zoomIdentity;
const projection = d3.geoMercator();
function fitProjection() {
  const yTop = Math.log(Math.tan(Math.PI / 4 + (78 * Math.PI) / 360)), yBot = Math.log(Math.tan(Math.PI / 4 + (-58 * Math.PI) / 360));
  const scale = Math.max(W / (2 * Math.PI), H / (yTop - yBot));
  projection.scale(scale).center([0, 0]).rotate([-25, 0]).translate([W / 2, H / 2 + ((yTop + yBot) / 2) * scale]);
}
function resize() {
  DPR = Math.min(2, devicePixelRatio); W = innerWidth; H = innerHeight;
  [base, fx].forEach((c) => { c.width = W * DPR; c.height = H * DPR; c.style.width = `${W}px`; c.style.height = `${H}px`; });
  fitProjection(); drawBase();
}
const P = (lng, lat) => { const [x, y] = projection([lng, lat]); return [zoomT.applyX(x), zoomT.applyY(y)]; };

/* ---------- Base map: land as a dot matrix ---------- */
let land = null, borders = null;
try {
  const world = await (await fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json')).json();
  land = topojson.merge(world, world.objects.countries.geometries);
  borders = topojson.mesh(world, world.objects.countries, (a, b) => a !== b);
} catch (e) { /* the grid alone still gives orientation */ }
const off = document.createElement('canvas'), octx = off.getContext('2d', { willReadFrequently: true });
function drawBase() {
  bctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  const g = bctx.createRadialGradient(W * 0.55, H * 0.4, 0, W * 0.55, H * 0.4, Math.max(W, H) * 0.8);
  g.addColorStop(0, '#0d1c36'); g.addColorStop(1, '#040810'); bctx.fillStyle = g; bctx.fillRect(0, 0, W, H);
  const path = d3.geoPath(projection, bctx);
  bctx.save(); bctx.translate(zoomT.x, zoomT.y); bctx.scale(zoomT.k, zoomT.k);
  bctx.beginPath(); path(d3.geoGraticule10()); bctx.strokeStyle = 'rgba(120,160,230,0.07)'; bctx.lineWidth = 1 / zoomT.k; bctx.stroke();
  bctx.restore();
  if (!land) return;
  off.width = W; off.height = H; octx.clearRect(0, 0, W, H);
  octx.save(); octx.translate(zoomT.x, zoomT.y); octx.scale(zoomT.k, zoomT.k);
  octx.beginPath(); d3.geoPath(projection, octx)(land); octx.fillStyle = '#fff'; octx.fill(); octx.restore();
  const data = octx.getImageData(0, 0, W, H).data; const step = 5;
  bctx.fillStyle = 'rgba(120,165,235,0.34)';
  for (let y = step / 2; y < H; y += step) for (let x = step / 2; x < W; x += step) {
    if (data[((y | 0) * W + (x | 0)) * 4 + 3] > 128) { bctx.beginPath(); bctx.arc(x, y, 1.15, 0, Math.PI * 2); bctx.fill(); }
  }
  bctx.save(); bctx.translate(zoomT.x, zoomT.y); bctx.scale(zoomT.k, zoomT.k);
  bctx.beginPath(); path(borders); bctx.strokeStyle = 'rgba(120,165,235,0.16)'; bctx.lineWidth = 0.8 / zoomT.k; bctx.stroke();
  bctx.restore();
}
let baseQueued = false;
const zoom = d3.zoom().scaleExtent([1, 12]).on('zoom', (e) => {
  zoomT = e.transform; if (!baseQueued) { baseQueued = true; requestAnimationFrame(() => { baseQueued = false; drawBase(); }); }
});
d3.select(fx).call(zoom).on('dblclick.zoom', null);
$('#btn-reset').onclick = () => d3.select(fx).transition().duration(700).call(zoom.transform, d3.zoomIdentity);
addEventListener('resize', resize); resize();

/* ---------- Aggregation: one flow per place and direction ---------- */
// "out": opened from our side (LAN devices through NAT, and the router itself); "in": opened from the internet to the router.
const rateOf = (c) => (c.down || 0) + (c.up || 0);
const isScan = (c) => c.kind === 'in' && (c.cat === 'scan' || c.cat === 'research');
const dirOf = (c) => (c.kind !== 'in' ? 'out' : isScan(c) ? 'scan' : 'in');
const what = (c) => (c.kind === 'in' ? (c.proto === 'icmp' ? 'ping' : `${GATE_LABEL[c.gate] || 'порт'} ${c.dport}/${c.proto}`) : orgOf(c));
function visible(c) { return c.geo && !S.hidden.has(c.cat) && (!S.device || c.device === S.device || (S.device === '__in' && c.kind === 'in')); }
function aggregate() {
  const m = new Map();
  for (const c of S.links.values()) {
    if (!visible(c)) continue;
    const dir = dirOf(c); const k = `${dir}|${c.geo.cc}:${c.geo.city}`;
    let p = m.get(k);
    if (!p) { p = { key: k, dir, lat: c.geo.lat, lng: c.geo.lng, n: 0, rate: 0, cats: {}, orgs: {}, evs: [], seed: Math.random(), place: placeOf(c.geo), city: c.geo.city }; m.set(k, p); }
    p.n++; p.rate += rateOf(c); p.cats[c.cat] = (p.cats[c.cat] || 0) + 1; p.evs.push(c);
    p.ports = Math.max(p.ports || 0, c.ports_tried || 1);
    const o = what(c); p.orgs[o] = (p.orgs[o] || 0) + rateOf(c) + 1;
  }
  S.places = [...m.values()].map((p) => { p.cat = Object.entries(p.cats).sort((a, b) => b[1] - a[1])[0][0]; p.inbound = p.dir === 'in'; p.scan = p.dir === 'scan';
    p.topOrgs = Object.entries(p.orgs).sort((a, b) => b[1] - a[1]).map(([o]) => o); return p; });
  // Label the busiest outbound places and every place that is talking to the router right now.
  const busiest = S.places.filter((p) => p.dir === 'out').sort((a, b) => (b.rate - a.rate) || (b.n - a.n)).slice(0, 5);
  const toRouter = S.places.filter((p) => p.inbound).sort((a, b) => (b.rate - a.rate) || (b.n - a.n)).slice(0, 4);
  const sweepers = S.places.filter((p) => p.scan && p.ports >= 3).sort((a, b) => b.ports - a.ports).slice(0, 2);
  S.labelled = new Set([...busiest, ...toRouter, ...sweepers].map((p) => p.key));
  legendCounts(); renderTab(); renderDevices(); renderPorts();
}
let aggQueued = false;
const scheduleAgg = () => { if (aggQueued) return; aggQueued = true; setTimeout(() => { aggQueued = false; aggregate(); }, 250); };
const radius = (p) => Math.min(30, 2.5 + Math.sqrt(p.n) * 1.4 + Math.sqrt(p.rate / 1e3) * 0.5);

/* ---------- Drawing ---------- */
// Outbound arcs bow one way and inbound arcs the other, so both directions to one city stay visible.
function curve(a, b, flip) {
  const dx = b[0] - a[0], dy = b[1] - a[1], d = Math.hypot(dx, dy);
  const lift = Math.min(0.32 * d, 220); const nx = -dy / (d || 1), ny = dx / (d || 1); let s = ny > 0 ? -1 : 1;
  if (flip) s = -s * 0.55;
  return { a, b, c: [(a[0] + b[0]) / 2 + nx * lift * s, (a[1] + b[1]) / 2 + ny * lift * s] };
}
const arcOf = (p, home) => { const pt = P(p.lng, p.lat); return p.inbound ? curve(pt, home, true) : curve(home, pt, false); };
const at = (q, t) => { const u = 1 - t; return [u * u * q.a[0] + 2 * u * t * q.c[0] + t * t * q.b[0], u * u * q.a[1] + 2 * u * t * q.c[1] + t * t * q.b[1]]; };
function hexA(hex, a) { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; }
function comet(q, t, col, len) {
  const n = 14;
  for (let k = 0; k < n; k++) { const tt = t - (k / n) * len; if (tt < 0) break; const [x, y] = at(q, tt);
    ctx.fillStyle = hexA(col, (1 - k / n) * 0.95); ctx.beginPath(); ctx.arc(x, y, 2.6 * (1 - k / n) + 0.6, 0, Math.PI * 2); ctx.fill(); }
}
let last = performance.now();
function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000); last = now;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0); ctx.clearRect(0, 0, W, H);
  // Home is where the WAN address is, as the service finds it in DB-IP; until then there is nothing to draw.
  if (!S.home) { requestAnimationFrame(frame); return; }
  const home = P(S.home.lng, S.home.lat);
  ctx.globalCompositeOperation = 'lighter';
  for (const p of S.places) {
    if (p.scan) continue;  // scans get no arcs: dozens of them would turn the map into a web
    const q = arcOf(p, home); const col = CATS[p.cat].color; const kbit = p.rate / 1e3;
    ctx.beginPath(); ctx.moveTo(q.a[0], q.a[1]); ctx.quadraticCurveTo(q.c[0], q.c[1], q.b[0], q.b[1]);
    if (p.inbound) ctx.setLineDash([5, 5]);
    ctx.strokeStyle = hexA(col, 0.16 + Math.min(0.3, Math.log10(1 + kbit) * 0.1)); ctx.lineWidth = Math.min(5, 0.9 + Math.log10(1 + kbit) * 1.2); ctx.stroke();
    ctx.setLineDash([]);
    if (reduced) continue;
    // Packets always move while a connection is alive: more and faster when it carries more traffic.
    const packets = Math.min(14, 2 + Math.round(Math.log10(1 + kbit) * 4));
    const speed = 0.16 + Math.min(0.6, Math.log10(1 + kbit) * 0.12);
    for (let i = 0; i < packets; i++) { const t = (now / 1000 * speed + i / packets + p.seed) % 1; const [x, y] = at(q, t);
      ctx.fillStyle = hexA(col, 0.95 * Math.sin(t * Math.PI)); ctx.beginPath(); ctx.arc(x, y, p.inbound ? 2.2 : 1.8, 0, Math.PI * 2); ctx.fill(); }
  }
  for (const p of S.places) {
    const [x, y] = P(p.lng, p.lat); const r = radius(p); const col = CATS[p.cat].color;
    if (p.scan) {
      // A scanner: a flickering diamond, larger when it tried more ports.
      const s = 2.5 + Math.min(7, Math.sqrt(p.ports) * 1.6); const a = 0.35 + 0.65 * Math.abs(Math.sin(now / 1000 * 3 + p.seed * 12));
      ctx.fillStyle = hexA(col, a); ctx.beginPath(); ctx.moveTo(x, y - s); ctx.lineTo(x + s, y); ctx.lineTo(x, y + s); ctx.lineTo(x - s, y); ctx.closePath(); ctx.fill();
      continue;
    }
    if (p.inbound) {
      // Someone out there is talking to the router: a pulsing ring around the source.
      const t = ((now / 1000) * 0.8 + p.seed) % 1;
      ctx.strokeStyle = hexA(col, 0.9 * (1 - t)); ctx.lineWidth = 1.6; ctx.beginPath(); ctx.arc(x, y, r + 2 + t * 14, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = hexA(col, 0.95); ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, Math.max(3, r * 0.6), 0, Math.PI * 2); ctx.stroke();
      continue;
    }
    const g = ctx.createRadialGradient(x, y, 0, x, y, r * 2.4); g.addColorStop(0, hexA(col, 0.55)); g.addColorStop(1, hexA(col, 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r * 2.4, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = hexA(col, 0.95); ctx.beginPath(); ctx.arc(x, y, Math.max(2, r * 0.5), 0, Math.PI * 2); ctx.fill();
  }
  for (let i = S.sparks.length - 1; i >= 0; i--) {
    // A scan attempt: a short spark toward home that burns out halfway.
    const f = S.sparks[i]; f.t += dt / 1.2; if (f.t >= 0.55) { S.sparks.splice(i, 1); continue; }
    const q = curve(P(f.lng, f.lat), home, true); const [x, y] = at(q, f.t); const fade = 1 - f.t / 0.55;
    ctx.fillStyle = hexA(f.col, 0.9 * fade); ctx.beginPath(); ctx.arc(x, y, 1.8, 0, Math.PI * 2); ctx.fill();
    const [x2, y2] = at(q, Math.max(0, f.t - 0.06)); ctx.strokeStyle = hexA(f.col, 0.5 * fade); ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(x2, y2); ctx.lineTo(x, y); ctx.stroke();
  }
  for (let i = S.flashes.length - 1; i >= 0; i--) { const f = S.flashes[i]; f.t += dt / 1.1; if (f.t >= 1) { S.flashes.splice(i, 1); continue; } comet(curve(home, P(f.lng, f.lat), false), f.t, f.col, 0.25); }
  for (let i = S.meteors.length - 1; i >= 0; i--) {
    const m = S.meteors[i]; m.t += dt / 1.5; const q = curve(P(m.lng, m.lat), home, true);
    if (m.t >= 1) { S.rings.push({ t: 0, col: m.col }); S.meteors.splice(i, 1); continue; }
    comet(q, m.t, m.col, 0.3);
  }
  for (let i = S.rings.length - 1; i >= 0; i--) { const r = S.rings[i]; r.t += dt / 0.9; if (r.t >= 1) { S.rings.splice(i, 1); continue; }
    ctx.strokeStyle = hexA(r.col, 1 - r.t); ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(home[0], home[1], 3 + r.t * 18, 0, Math.PI * 2); ctx.stroke(); }
  const load = (S.router.wan_in || 0) / 5e7;
  const period = Math.max(0.5, 1.8 - load);
  for (let k = 0; k < 3; k++) { const t = ((now / 1000) / period + k / 3) % 1;
    ctx.strokeStyle = `rgba(255,196,120,${0.7 * (1 - t)})`; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(home[0], home[1], 6 + t * 38, 0, Math.PI * 2); ctx.stroke(); }
  ctx.globalCompositeOperation = 'source-over';
  const hg = ctx.createRadialGradient(home[0], home[1], 0, home[0], home[1], 16); hg.addColorStop(0, 'rgba(255,220,160,1)'); hg.addColorStop(1, 'rgba(255,170,80,0)');
  ctx.fillStyle = hg; ctx.beginPath(); ctx.arc(home[0], home[1], 16, 0, Math.PI * 2); ctx.fill();
  ctx.font = '500 12px "Golos Text", sans-serif'; ctx.textBaseline = 'middle';
  const used = [];
  for (const p of S.places) {
    if (!S.labelled || !S.labelled.has(p.key)) continue;
    const [x, y] = P(p.lng, p.lat); const r = radius(p);
    if (Math.hypot(x - home[0], y - home[1]) < 40) continue;  // do not cover the home label
    const top = p.topOrgs[0] ? (p.topOrgs[0].length > 18 ? `${p.topOrgs[0].slice(0, 17)}…` : p.topOrgs[0]) : '';
    const text = p.scan ? `${p.city} · скан ${p.ports} портов`
      : p.inbound ? `${p.city} → роутер · ${top}${p.n > 1 ? ` · ${p.n}` : ''}`
      : [p.city, top, p.rate >= 1e3 ? fmtRate(p.rate) : `${p.n} соед.`].filter(Boolean).join(' · ');
    // Both directions of one city share a point: put the inbound label below the outbound one.
    let ly = y + (p.inbound ? 20 : p.scan ? 40 : 0);
    while (used.some(([ux, uy]) => Math.abs(ux - x) < 60 && Math.abs(uy - ly) < 18)) ly += 18;
    used.push([x, ly]);
    ctx.fillStyle = 'rgba(4,8,16,0.6)'; const w = ctx.measureText(text).width; ctx.fillRect(x + r + 4, ly - 9, w + 8, 18);
    ctx.fillStyle = p.scan ? '#fff0a8' : p.inbound ? '#ffc3ad' : '#dfe8f5'; ctx.fillText(text, x + r + 8, ly);
  }
  ctx.font = '600 13px "Exo 2", sans-serif'; ctx.fillStyle = '#ffe1b5'; ctx.fillText('Дом', home[0] + 14, home[1] - 12);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

/* ---------- Hover and click: every flow at the pointed spot ---------- */
function placesAt(mx, my) {
  return S.places.filter((p) => { const [x, y] = P(p.lng, p.lat); return Math.hypot(x - mx, y - my) < 6 + radius(p); })
    .sort((a, b) => a.dir.localeCompare(b.dir));
}
fx.addEventListener('mousemove', (e) => {
  const ps = placesAt(e.clientX, e.clientY); const h = $('#hover');
  if (!ps.length) { h.hidden = true; fx.style.cursor = ''; return; }
  fx.style.cursor = 'pointer';
  h.innerHTML = `<b>${esc(ps[0].place)}</b>` + ps.map((p) => `<br><b>${p.scan ? 'Сканы' : p.inbound ? 'К роутеру' : 'Наружу'}:</b> ${p.n} соед. · ${fmtRate(p.rate)}<br>${esc(p.topOrgs.slice(0, 3).join(', '))}`).join('');
  h.style.left = `${e.clientX + 14}px`; h.style.top = `${e.clientY + 14}px`; h.hidden = false;
});
fx.addEventListener('click', (e) => { const ps = placesAt(e.clientX, e.clientY); if (ps.length) openCard(ps); });
function openCard(ps) {
  const evs = ps.flatMap((p) => p.evs).sort((a, b) => (dirOf(a) === dirOf(b) ? rateOf(b) - rateOf(a) : dirOf(a) === 'in' ? -1 : 1));
  const total = evs.reduce((a, c) => a + rateOf(c), 0);
  const rows = evs.slice(0, 20).map((c) => `<tr><td>${isScan(c) ? '<b style="color:#fff0a8">скан</b>' : c.kind === 'in' ? '<b style="color:#ffc3ad">к роутеру</b>' : esc(c.device)}</td><td class="mono">${c.remote}</td><td class="mono">${c.dport ? `${c.dport}/${c.proto}` : c.proto}</td><td title="${esc(c.org ? `AS${c.org.asn} ${c.org.full}` : '')}">${esc(c.org ? c.org.name : '—')}</td><td style="text-align:right">${fmtRate(rateOf(c))}</td></tr>`).join('');
  $('#card').innerHTML = `<button class="x" aria-label="Закрыть">×</button><h2>${esc(ps[0].place)}</h2><p class="note">${evs.length} соединений · ${fmtRate(total)} · место по DB-IP, точность до города</p><table><thead><tr><th>Кто</th><th>Адрес</th><th>Порт</th><th>Организация</th><th style="text-align:right">Скорость</th></tr></thead><tbody>${rows}</tbody></table>${evs.length > 20 ? `<p class="note">и ещё ${evs.length - 20}</p>` : ''}`;
  $('#card').hidden = false; $('#card .x').onclick = () => { $('#card').hidden = true; };
}
addEventListener('keydown', (e) => { if (e.key === 'Escape') $('#card').hidden = true; });

/* ---------- Top strip ---------- */
function level(x, warn, bad) { return x >= bad ? 'var(--bad)' : x >= warn ? 'var(--warn)' : 'var(--good)'; }
function fmtUptime(s) {  // RouterOS format: 1w2d3h4m5s
  const m = {}; (s || '').replace(/(\d+)([wdhms])/g, (_, n, u) => { m[u] = +n; });
  const d = (m.w || 0) * 7 + (m.d || 0);
  return d ? `${d}<small>д</small> ${m.h || 0}<small>ч</small>` : `${m.h || 0}<small>ч</small> ${m.m || 0}<small>мин</small>`;
}
function topbar() {
  const r = S.router;
  $('#r-board').textContent = [r.identity, r.board].filter(Boolean).join(' · ') || 'MikroTik';
  $('#r-dot').className = `dot ${r.wan_running === false ? 'bad' : 'good'}`;
  $('#r-ver').textContent = (r.version || '—').replace(/\s*\(.*\)/, ''); $('#r-ver').title = r.version || '';
  $('#r-up').innerHTML = r.uptime ? fmtUptime(r.uptime) : '—';
  if (r.cpu != null) { $('#r-cpu').innerHTML = `${r.cpu}<small>%</small>`; $('#r-cpu-m').style.width = `${r.cpu}%`; $('#r-cpu-m').style.background = level(r.cpu, 60, 85); }
  if (r.mem_used != null) { const p = r.mem_used * 100; $('#r-mem').innerHTML = `${Math.round(p)}<small>% из ${Math.round(r.mem_total / 1048576)} МБ</small>`; $('#r-mem-m').style.width = `${p}%`; $('#r-mem-m').style.background = level(p, 75, 90); }
  $('#r-wan-ip').textContent = r.wan_ip || '—';
  $('#r-in').textContent = fmtRate(r.wan_in || 0); $('#r-out').textContent = fmtRate(r.wan_out || 0);
  const mbit = parseInt(r.wan_speed || '', 10) * (/Gbps/.test(r.wan_speed || '') ? 1000 : 1);
  $('#r-wan-k').textContent = `Канал ${r.wan || 'WAN'}${mbit ? `, ${mbit} Мбит/с` : ''}`;
  const util = mbit ? (Math.max(r.wan_in || 0, r.wan_out || 0) / (mbit * 1e6)) * 100 : 0;
  $('#r-util').innerHTML = `${util < 1 ? util.toFixed(1).replace('.', ',') : Math.round(util)}<small>%</small>`; $('#r-util-m').style.width = `${Math.min(100, util)}%`; $('#r-util-m').style.background = level(util, 70, 90);
  $('#r-conn').textContent = r.conntrack != null ? nf(r.conntrack) : '—';
  $('#r-dhcp').textContent = r.dhcp_bound != null ? nf(r.dhcp_bound) : '—';
  const up = S.tunnels.filter((t) => t.enabled && t.up);
  $('#r-tun-cell').classList.toggle('show', up.length > 0);
  $('#r-tun').innerHTML = up.map((t) => `<span class="tun">${esc(t.name)}</span>`).join('');
}
function pollClock() {
  const left = Math.max(0, Math.round(S.nextPoll - Date.now() / 1000));
  $('#r-poll-k').textContent = `опрос каждые ${S.pollInterval} с`;
  if (S.status.ok == null) { $('#mode-dot').className = 'mode-dot replay'; $('#mode-label').textContent = 'подключаюсь к роутеру…'; $('#mode-label').title = ''; }
  else if (S.status.ok === false) { $('#mode-dot').className = 'mode-dot error'; $('#mode-label').textContent = `ошибка, повтор через ${left} с`; $('#mode-label').title = S.status.error || ''; }
  else if (S.status.ok) { $('#mode-dot').className = 'mode-dot live'; $('#mode-label').textContent = `следующий через ${left} с`; $('#mode-label').title = ''; }
}
setInterval(pollClock, 1000);

/* ---------- Who is on the router: active sessions and login events from the router log ---------- */
const VIA = { web: 'веб', winbox: 'Winbox', 'winbox/dude': 'Winbox', ssh: 'SSH', telnet: 'Telnet', ftp: 'FTP', api: 'API', console: 'консоль' };
const ago = (sec) => sec == null ? '' : sec < 90 ? `${Math.round(sec)} с` : sec < 5400 ? `${Math.round(sec / 60)} мин` : sec < 172800 ? `${Math.round(sec / 3600)} ч` : `${Math.round(sec / 86400)} дн`;
const whereText = (w) => !w ? '' : w.lan ? `${w.ip} · локальная сеть` : [w.ip, w.geo ? placeOf(w.geo) : '', w.org].filter(Boolean).join(' · ');
$('#who-toggle').onclick = () => {
  const open = $('#who-toggle').getAttribute('aria-expanded') !== 'true';
  $('#who-toggle').setAttribute('aria-expanded', open); $('#who-body').hidden = !open;
};
function renderWho() {
  const ss = S.sessions || [], a = S.auth || { events: [] };
  const people = ss.filter((s) => s.interactive), service = ss.filter((s) => !s.interactive);
  const outside = people.filter((s) => !s.where.lan);
  // Interactive logins from outside the LAN are the thing to notice; service API sessions stay quiet.
  const rows = [...people, ...service].map((s) => ({
    name: s.user, value: VIA[s.via] || s.via, w: 1,
    color: !s.interactive ? '#6f7d91' : s.where.lan ? '#fab219' : '#ff4d4d',
    sub: `${whereText(s.where)} · ${s.age != null ? `вошёл ${ago(s.age)} назад` : s.when}${s.group ? ` · группа ${s.group}` : ''}` }));
  $('#who-sessions').innerHTML = list(rows, 1).replace(/<span class="bar">.*?<\/span><\/li>/g, '</li>');
  const kinds = { login: 'вход', failure: 'неверный пароль', denied: 'отказ: адрес не разрешён', logout: 'выход' };
  // Scanners hit Winbox several times a second: fold repeats of the same event from the same address.
  const folded = [];
  for (const e of a.events) {
    const f = folded.find((x) => x.kind === e.kind && x.user === e.user && x.via === e.via && x.where.ip === e.where.ip);
    if (f) f.count++; else folded.push({ ...e, count: 1 });
  }
  const ev = folded.slice(0, 10).map((e) => ({
    name: `${e.user ? `${e.user} · ` : ''}${kinds[e.kind]}${e.count > 1 ? ` ×${e.count}` : ''}`, value: VIA[e.via] || e.via, w: 1,
    color: e.kind === 'login' ? (e.where.lan ? '#2bd46b' : '#ff4d4d') : e.kind === 'failure' ? '#ff4d4d' : e.kind === 'denied' ? '#ff6a3d' : '#6f7d91',
    sub: `${e.time} (${ago(e.ago)} назад) · ${whereText(e.where)}` }));
  $('#who-events-title').textContent = `Входы за последний час: ${a.logins} вход, ${a.failures} неудачных, ${a.denied} отказов по адресу`;
  $('#who-events').innerHTML = ev.length ? list(ev, 1).replace(/<span class="bar">.*?<\/span><\/li>/g, '</li>') : '<p class="note">Ни входов, ни неудачных попыток.</p>';
  const plate = $('#who-plate');
  plate.classList.toggle('alarm', outside.length > 0);
  plate.classList.toggle('warn', !outside.length && (a.failures > 0));
  $('#who-sub').innerHTML = outside.length
    ? `<b style="color:var(--bad)">Вход не из локальной сети: ${outside.map((s) => `${esc(s.user)} (${VIA[s.via] || s.via}, ${esc(s.where.ip)})`).join(', ')}</b>`
    : people.length ? `Сейчас: ${people.map((s) => `<b>${esc(s.user)}</b> через ${VIA[s.via] || s.via}`).join(', ')}${service.length ? ` · служебных API-сессий: ${service.length}` : ''}`
      : `Интерактивных входов нет${service.length ? ` · служебных API-сессий: ${service.length}` : ''}`;
  if (a.failures) $('#who-sub').innerHTML += `<br><b style="color:var(--bad)">Неудачных входов за час: ${a.failures}</b>`;
}

/* ---------- Ports plate: starts folded, the header unfolds it ---------- */
$('#ports-toggle').onclick = () => {
  const open = $('#ports-toggle').getAttribute('aria-expanded') !== 'true';
  $('#ports-toggle').setAttribute('aria-expanded', open); $('#ports-body').hidden = !open; $('#ports-plate').classList.toggle('collapsed', !open);
};
function renderPorts() {
  const hour = (S.inbound && S.inbound.ports) || {};
  const allIn = [...S.links.values()].filter((c) => c.kind === 'in');
  const inb = allIn.filter((c) => !isScan(c)), scansNow = allIn.length - inb.length;
  const scanHour = (S.inbound && S.inbound.scan_ports) || {};
  const now = {}; inb.forEach((c) => { const k = `${c.dport}/${c.proto}`; now[k] = (now[k] || 0) + 1; });
  const shown = S.ports.filter((p) => p.status !== 'off');
  const off = S.ports.filter((p) => p.status === 'off').map((p) => p.name);
  const word = { open: 'открыт из интернета', limited: 'доступ ограничен', closed: 'закрыт firewall' };
  $('#ports').innerHTML = shown.map((p) => {
    const n = p.ports.reduce((a, k) => a + (scanHour[k] || 0), 0), cur = p.ports.reduce((a, k) => a + (now[k] || 0), 0);
    return `<li class="${p.status}" title="${word[p.status]}"><span class="st" aria-label="${word[p.status]}"><i></i></span><span class="pn">${esc(p.name)}<small>${esc(p.ports.join(', '))}</small></span><span class="pc"><b>${nf(cur)}</b><small>подключено · ${nf(n)} сканов за час</small></span><span class="why">${esc(p.why)}${p.uncertain ? '; есть правила с условиями по адресам, вывод приблизительный' : ''}</span></li>`;
  }).join('') + (off.length ? `<li class="quiet" title="сервисы выключены"><span class="st"><i></i></span><span class="pn">${esc(off.join(', '))}<small>выключены</small></span><span class="pc"></span></li>` : '');
  // Connections from the internet to the router right now, in the same form as the lists on the right.
  const rows = inb.sort((a, b) => rateOf(b) - rateOf(a)).slice(0, 12).map((c) => ({
    name: (c.org && c.org.name) || c.remote, value: c.proto === 'icmp' ? 'ping' : `${c.dport}/${c.proto}`, w: rateOf(c) || 1,
    color: CATS[c.cat].color, sub: `${c.remote} · ${placeOf(c.geo)}${c.state ? ` · ${c.state}` : ''}`, rate: rateSpan(c.down, c.up) }));
  $('#conn-title').textContent = `Сейчас подключены к роутеру: ${inb.length}${scansNow ? ` (и ${scansNow} сканов)` : ''}`;
  $('#conn-now').innerHTML = list(rows, rows[0] && rows[0].w, '#ff6a3d') + (inb.length > 12 ? `<p class="note">и ещё ${inb.length - 12}</p>` : '');
  const open = shown.filter((p) => p.status === 'open').length, lim = shown.filter((p) => p.status === 'limited').length;
  const verdict = open ? `<b style="color:var(--bad)">${open} открыто из интернета</b>${lim ? `, ${lim} с ограничением по адресу` : ''}`
    : lim ? `<b style="color:var(--warn)">${lim} доступны через firewall, но ограничены по адресу</b>` : '<b style="color:var(--good)">Из интернета ничего не доступно</b>';
  $('#ports-sub').innerHTML = `${verdict}<br>Сейчас подключено снаружи: <b>${nf(inb.length)}</b> · сканеров за час: <b>${nf((S.inbound && S.inbound.scan_sources) || 0)}</b>`;
}

/* ---------- Right plate: tabs ---------- */
document.querySelectorAll('.tabs button').forEach((b) => b.onclick = () => {
  S.tab = b.dataset.tab; document.querySelectorAll('.tabs button').forEach((x) => x.setAttribute('aria-selected', x === b)); renderTab();
});
function list(rows, max, color) {
  return `<ul class="list">${rows.map((r) => `<li><i style="background:${r.color || color}"></i><span class="name">${esc(r.name)}</span><span class="n">${r.value}</span>${r.sub ? `<span class="sub">${esc(r.sub)}</span>` : ''}${r.rate ? `<span class="rate">${r.rate}</span>` : ''}<span class="bar"><b style="width:${Math.max(3, (100 * r.w) / (max || 1))}%;background:${r.color || color}"></b></span></li>`).join('') || '<li class="empty">Пока пусто</li>'}</ul>`;
}
const rateSpan = (d, u) => `<span class="d">вход ${fmtRate(d)}</span><span class="u">выход ${fmtRate(u)}</span>`;
function renderTab() {
  const body = $('#tab-body'); const conns = [...S.links.values()].filter((c) => !S.device || c.device === S.device);
  if (S.tab === 'dest') {
    const by = new Map();
    for (const c of conns) { if (c.kind === 'in') continue; const k = orgOf(c);
      const x = by.get(k) || { name: k, n: 0, d: 0, u: 0, color: CATS[c.cat].color, dev: new Set(), places: new Set() };
      x.n++; x.d += c.down || 0; x.u += c.up || 0; if (c.device) x.dev.add(c.device); if (c.geo) x.places.add(c.geo.city); by.set(k, x); }
    const rows = [...by.values()].sort((a, b) => (b.d + b.u) - (a.d + a.u) || b.n - a.n).slice(0, 14)
      .map((x) => ({ ...x, value: `${x.n}`, w: x.d + x.u || x.n, sub: `${[...x.places].slice(0, 2).join(', ') || '—'} · ${[...x.dev].slice(0, 3).join(', ')}`, rate: rateSpan(x.d, x.u) }));
    body.innerHTML = `<div class="section">Куда идут соединения: организации по скорости</div>${list(rows, rows[0] && rows[0].w)}`;
  } else if (S.tab === 'devices') {
    const by = new Map();
    for (const c of S.links.values()) { if (c.kind !== 'out') continue; const k = c.device;
      const x = by.get(k) || { name: k, n: 0, d: 0, u: 0, svc: new Map(), maker: c.maker }; x.n++; x.d += c.down || 0; x.u += c.up || 0;
      const s = orgOf(c); x.svc.set(s, (x.svc.get(s) || 0) + rateOf(c) + 1); by.set(k, x); }
    const rows = [...by.values()].sort((a, b) => (b.d + b.u) - (a.d + a.u) || b.n - a.n)
      .map((x) => ({ name: x.name, value: `${x.n}`, w: x.d + x.u || x.n, color: '#9fc2ff', rate: rateSpan(x.d, x.u),
        sub: [x.maker && !x.name.startsWith(x.maker.split(' ')[0]) ? x.maker : '', [...x.svc.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([s]) => s).join(', ')].filter(Boolean).join(' · ') }));
    body.innerHTML = `<div class="section">Устройства дома: соединения наружу и скорость</div>${list(rows, rows[0] && rows[0].w)}`;
  } else if (S.tab === 'countries') {
    const by = new Map();
    for (const c of conns) { if (!c.geo) continue; const k = c.geo.cc;
      const x = by.get(k) || { name: country(k) || k, n: 0, d: 0, u: 0, inn: 0 }; x.n++; x.d += c.down || 0; x.u += c.up || 0; if (c.kind === 'in') x.inn++; by.set(k, x); }
    const rows = [...by.values()].sort((a, b) => (b.d + b.u) - (a.d + a.u) || b.n - a.n).slice(0, 14)
      .map((x) => ({ ...x, value: `${x.n}`, w: x.d + x.u || x.n, rate: rateSpan(x.d, x.u), sub: x.inn ? `из них входящих на роутер: ${x.inn}` : '' }));
    body.innerHTML = `<div class="section">Страны активных соединений</div>${list(rows, rows[0] && rows[0].w, '#4f97ff')}`;
  } else {
    const allIn = [...S.links.values()].filter((c) => c.kind === 'in'); const inb = allIn.filter((c) => !isScan(c)); const h = S.inbound || {};
    // One scanning outfit often uses a whole block of addresses: fold them by organization.
    const byOrg = new Map();
    for (const x of h.scanners || []) {
      const k = x.org || x.ip; const g = byOrg.get(k) || { ...x, ips: [], ports: 0, sample: new Set() };
      g.ips.push(x.ip); g.ports = Math.max(g.ports, x.ports); x.sample.forEach((p) => g.sample.add(p)); byOrg.set(k, g);
    }
    const sc = [...byOrg.values()].sort((a, b) => b.ports - a.ports || b.ips.length - a.ips.length).map((x) => ({
      name: x.org || x.ip, value: `${x.ports} ${x.ports === 1 ? 'порт' : 'портов'}`, w: x.ports + x.ips.length / 10,
      color: x.research ? CATS.research.color : CATS.scan.color,
      sub: `${x.ips.length > 1 ? `${x.ips.length} адресов` : x.ips[0]} · ${placeOf(x.geo)}${x.research ? ' · исследовательский' : ''} · ${[...x.sample].slice(0, 5).join(', ')}` }));
    const gates = Object.entries(h.gates || {}).sort((a, b) => b[1] - a[1]).map(([g, n]) => ({ name: GATE_LABEL[g] || g, value: nf(n), w: n, color: ['winbox', 'api', 'ssh', 'btest'].includes(g) ? '#ff4d4d' : '#ff6a3d' }));
    const byC = new Map(); inb.forEach((c) => { const k = [c.geo ? country(c.geo.cc) : 'место неизвестно', c.org && c.org.name].filter(Boolean).join(', '); byC.set(k, (byC.get(k) || 0) + 1); });
    const cRows = [...byC.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6).map(([name, n]) => ({ name, value: nf(n), w: n }));
    const since = h.since ? new Date(h.since * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : '';
    body.innerHTML = `<div class="kv"><span>Сейчас подключено к роутеру снаружи</span><b>${nf(inb.length)}</b><span>Сейчас сканов</span><b>${nf(allIn.length - inb.length)}</b><span>Сканеров за час</span><b>${nf(h.scan_sources || 0)}</b><span>Новых подключений с ${since}</span><b>${nf(h.total || 0)}</b><span>Уникальных источников</span><b>${nf(h.sources || 0)}</b></div>
      <div class="section">На какие порты, источники за час</div>${list(gates, gates[0] && gates[0].w)}
      <div class="section">Откуда сейчас</div>${list(cRows, cRows[0] && cRows[0].w, '#ff6a3d')}
      <div class="section">Сканеры за час: сколько портов перебрали</div>${list(sc, sc[0] && sc[0].w, CATS.scan.color)}
      <p class="note">Видны только подключения, которые прошли firewall и попали в conntrack. Отброшенные пакеты сюда не попадают.</p>`;
  }
}
function renderDevices() {
  const sel = $('#device'); const devs = [...new Set([...S.links.values()].filter((c) => c.kind === 'out').map((c) => c.device))].sort();
  const want = ['', ...devs, '__in'].join('|');
  if (sel.dataset.list === want) return;
  sel.dataset.list = want; const cur = sel.value;
  sel.innerHTML = '<option value="">Все устройства</option>' + devs.map((d) => `<option>${esc(d)}</option>`).join('') + '<option value="__in">Только входящие</option>';
  sel.value = cur;
}
$('#device').onchange = (e) => { S.device = e.target.value; aggregate(); };

/* ---------- Bottom: legend and ticker ---------- */
// Latest new connections; the filter only changes what is shown, the buffer keeps both directions.
const tick = $('#ticker');
S.ticks = []; S.tickDir = 'all';
function ticker(c) {
  const text = c.kind === 'in'
    ? `${c.remote} → роутер: ${c.proto === 'icmp' ? 'ping' : `${c.dport}/${c.proto}`}${isScan(c) ? `, скан${c.ports_tried > 1 ? ` (${c.ports_tried} портов за час)` : ''}` : ''}${c.forwarded ? `, проброшено на ${c.target}` : ''}${c.org ? ` · ${c.org.name}` : ''}`
    : `${c.device} → ${orgOf(c)}${c.dport ? `:${c.dport}` : ''} ${c.proto}`;
  S.ticks.unshift({ time: new Date().toLocaleTimeString('ru-RU'), dir: dirOf(c), scan: isScan(c), color: CATS[c.cat].color, text: text + (c.geo ? ` · ${placeOf(c.geo)}` : '') });
  S.ticks.length = Math.min(S.ticks.length, 60);
  renderTicker();
}
function renderTicker() {
  const rows = S.ticks.filter((t) => S.tickDir === 'all' || t.dir === S.tickDir).slice(0, 5);
  tick.innerHTML = rows.map((t) => `<div class="tick${t.dir === 'in' ? ' in' : t.dir === 'scan' ? ' scan' : ''}"><time>${t.time}</time><i style="background:${t.color}"></i><span>${esc(t.text)}</span></div>`).join('')
    || `<div class="tick-empty">${S.tickDir === 'in' ? 'Новых подключений к роутеру пока нет' : S.tickDir === 'scan' ? 'Сканов пока нет' : S.tickDir === 'out' ? 'Новых исходящих соединений пока нет' : 'Новых соединений пока нет'}</div>`;
}
document.querySelectorAll('.tick-head .seg button').forEach((b) => b.onclick = () => {
  S.tickDir = b.dataset.dir;
  document.querySelectorAll('.tick-head .seg button').forEach((x) => x.setAttribute('aria-checked', x === b));
  renderTicker();
});
renderTicker();
function legendCounts() {
  const c = {}; for (const e of S.links.values()) c[e.cat] = (c[e.cat] || 0) + 1;
  $('#legend').innerHTML = Object.entries(CATS).map(([k, v]) => `<li><button data-cat="${k}" aria-pressed="${!S.hidden.has(k)}"><i style="background:${v.color}"></i>${v.label}<b>${c[k] || 0}</b></button></li>`).join('');
  document.querySelectorAll('#legend button').forEach((b) => b.onclick = () => { const k = b.dataset.cat; S.hidden.has(k) ? S.hidden.delete(k) : S.hidden.add(k); aggregate(); });
}
function toast(t) { const el = $('#toast'); el.textContent = t; el.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => el.hidden = true, 6000); }

/* ---------- Data from the service ---------- */
function applyCommon(m) {
  if (m.router) S.router = m.router; if (m.tunnels) S.tunnels = m.tunnels; if (m.ports) S.ports = m.ports;
  if (m.inbound) S.inbound = m.inbound;
  if (m.sessions) S.sessions = m.sessions; if (m.auth) S.auth = m.auth; if (m.home) S.home = m.home; if (m.status) S.status = m.status;
  if (m.next_poll) S.nextPoll = m.next_poll; if (m.poll_interval) S.pollInterval = m.poll_interval;
  topbar(); renderPorts(); renderWho(); pollClock();
}
function onPoll(m) {
  applyCommon(m);
  m.removed.forEach((id) => S.links.delete(id));
  for (const [id, [down, up, state, cat]] of Object.entries(m.updated)) { const c = S.links.get(id); if (c) { c.down = down; c.up = up; c.state = state; if (cat) c.cat = cat; } }
  // New connections arrive once per poll; spread their effects over the interval so the map stays alive.
  const spread = Math.max(1, S.pollInterval - 2) * 1000;
  m.added.forEach((c) => {
    S.links.set(c.id, c);
    if (m.fresh || !c.geo) return;  // first poll after a (re)start: the table, not new events
    setTimeout(() => {
      if (!S.links.has(c.id) || !visible(c)) return;
      ticker(c);  // the list keeps filling in a background tab; only the animation is skipped
      if (document.hidden || reduced) return;
      if (isScan(c)) { if (S.sparks.length < 60) S.sparks.push({ lng: c.geo.lng, lat: c.geo.lat, t: 0, col: CATS[c.cat].color }); }
      else if (c.kind === 'in') S.meteors.push({ lng: c.geo.lng, lat: c.geo.lat, t: 0, col: CATS[c.cat].color });
      else if (S.flashes.length < 30) S.flashes.push({ lng: c.geo.lng, lat: c.geo.lat, t: 0, col: CATS[c.cat].color });
    }, Math.random() * spread);
  });
  aggregate();
}
try {
  const st = await (await fetch('api/state')).json();
  st.conns.forEach((c) => S.links.set(c.id, c));
  applyCommon(st); aggregate();
} catch (e) { toast(`Сервис не отвечает: ${e.message}`); }
const es = new EventSource('api/events');
es.onmessage = (e) => { const m = JSON.parse(e.data); if (m.type === 'poll') onPoll(m); else applyCommon(m); };
es.onerror = () => { S.status = { ok: false, error: 'нет связи с сервисом карты' }; pollClock(); };
})();
