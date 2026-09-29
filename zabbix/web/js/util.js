// Formatting and small helpers shared by the scene and the panels.
import { tr } from './i18n.js';

export const SEVERITY = [
  { key: 'nc', label: 'Не классиф.', color: '#8e8d86' },
  { key: 'info', label: 'Информация', color: '#6da7ec' },
  { key: 'warning', label: 'Предупреждение', color: '#fab219' },
  { key: 'average', label: 'Средняя', color: '#ec835a' },
  { key: 'high', label: 'Высокая', color: '#d03b3b' },
  { key: 'disaster', label: 'Катастрофа', color: '#ff2d55' },
];

// Metric identity colors (validated for the dark surface, all pairs).
export const METRIC = {
  cpu: { label: 'CPU', color: '#3987e5' },
  mem: { label: 'RAM', color: '#d95926' },
  disk: { label: 'Диск', color: '#199e70' },
};

export const NET = { in: { label: 'Вход', color: '#3987e5' }, out: { label: 'Выход', color: '#d95926' } };

export const STATE = {
  online: { label: 'В сети', color: '#0ca30c' },
  unreachable: { label: 'Нет связи', color: '#d03b3b' },
  unknown: { label: 'Неизвестно', color: '#8e8d86' },
  disabled: { label: 'Отключён', color: '#6b6a64' },
};

export const SAT_STATUS = {
  ok: { label: 'ОК', color: '#dfe7f5' },
  warn: { label: 'Есть проблемы', color: '#fab219' },
  down: { label: 'Недоступен', color: '#d03b3b' },
  nodata: { label: 'Нет данных', color: '#6b6a64' },
};

export const SAT_ICON = {
  docker: '🐳', postgresql: '🐘', mysql: '🐬', svc: '⬡', ports: '⇄', '1c': '▣', smart: '💽', services: '⚙', zabbix: '◎',
};

export function pct(v, digits = 0) {
  return v == null ? '—' : `${v.toFixed(digits)}%`;
}

export function bytes(v, digits = 1) {
  if (v == null) return '—';
  const u = tr(['Б', 'КБ', 'МБ', 'ГБ', 'ТБ', 'ПБ'], ['B', 'KB', 'MB', 'GB', 'TB', 'PB']);
  let i = 0;
  while (Math.abs(v) >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i === 0 ? 0 : digits)} ${u[i]}`;
}

export function bps(v) {
  if (v == null) return '—';
  const u = tr(['бит/с', 'Кбит/с', 'Мбит/с', 'Гбит/с'], ['bit/s', 'Kbit/s', 'Mbit/s', 'Gbit/s']);
  let i = 0;
  while (Math.abs(v) >= 1000 && i < u.length - 1) { v /= 1000; i++; }
  return `${v.toFixed(v < 10 && i ? 1 : 0)} ${u[i]}`;
}

// Short bit rate for tight spots: 3.2M, 150K, 900b.
export function bpsShort(v) {
  if (v == null) return '—';
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}G`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M`;
  if (v >= 1e3) return `${Math.round(v / 1e3)}K`;
  return `${Math.round(v)}b`;
}

// Log scale for traffic: 1 Kbit/s -> 0, 1 Gbit/s -> 1.
export function trafficLevel(v) {
  if (!v || v <= 0) return 0;
  return Math.min(1, Math.max(0, (Math.log10(v) - 3) / 6));
}

// Log position of v between lo and hi (0..1): relative comparison across hosts.
export function logRel(v, lo, hi) {
  if (!v || v <= 0 || !(hi > 0)) return 0;
  const a = Math.log10(Math.max(lo, 1)), b = Math.log10(hi);
  if (b - a < 1e-6) return 1;
  return Math.min(1, Math.max(0, (Math.log10(v) - a) / (b - a)));
}

export function duration(sec) {
  if (sec == null) return '—';
  sec = Math.max(0, Math.floor(sec));
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d) return tr(`${d} д ${h} ч`, `${d} d ${h} h`);
  if (h) return tr(`${h} ч ${m} мин`, `${h} h ${m} min`);
  return tr(`${m} мин`, `${m} min`);
}

export function ago(ts) {
  return duration(Date.now() / 1000 - ts);
}

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Deterministic PRNG from a string: stable planet positions and looks.
export function seeded(str) {
  let h = 1779033703 ^ str.length;
  for (let i = 0; i < str.length; i++) {
    h = Math.imul(h ^ str.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  return () => {
    h = Math.imul(h ^ (h >>> 16), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    return ((h ^= h >>> 16) >>> 0) / 4294967296;
  };
}

// Color of the host "status halo": worst problem, else connectivity.
export function hostTone(h) {
  if (h.state === 'disabled') return STATE.disabled.color;
  if (h.state === 'unreachable') return STATE.unreachable.color;
  if (h.problems.worst >= 2) return SEVERITY[h.problems.worst].color;
  return null;
}
