// OS-styled planet surfaces: palette, surface pattern and an emblem drawn
// twice around the equator so it is visible while the planet spins.
import * as THREE from 'three';

// style: 0 = banded gas giant, 1 = tiled "window panes", 2 = swirl
export const OS_STYLE = {
  windows: { label: 'Windows', pal: ['#3a96dd', '#0b3f8f', '#bfe3ff'], emblem: '#eaf6ff', style: 1, bands: 3, warp: 1.2 },
  ubuntu: { label: 'Ubuntu', pal: ['#e95420', '#5e1b56', '#ffb38a'], emblem: '#ffffff', style: 0, bands: 7, warp: 4 },
  debian: { label: 'Debian', pal: ['#d70a53', '#4d0019', '#ffc2d4'], emblem: '#ffffff', style: 2, bands: 5, warp: 6 },
  raspbian: { label: 'Raspberry Pi OS', pal: ['#c51a4a', '#3e0a1c', '#8cc04b'], emblem: '#f4ffe6', style: 0, bands: 9, warp: 3 },
  manjaro: { label: 'Manjaro', pal: ['#35bf5c', '#0e3d20', '#c5f5d3'], emblem: '#ffffff', style: 0, bands: 6, warp: 3 },
  arch: { label: 'Arch', pal: ['#1793d1', '#0b2f4a', '#bfe6fa'], emblem: '#ffffff', style: 0, bands: 6, warp: 3 },
  fedora: { label: 'Fedora', pal: ['#51a2da', '#294172', '#d6ecff'], emblem: '#ffffff', style: 0, bands: 5, warp: 3 },
  rhel: { label: 'RHEL', pal: ['#ee0000', '#4a0000', '#ffb3b3'], emblem: '#ffffff', style: 0, bands: 5, warp: 3 },
  openwrt: { label: 'OpenWrt', pal: ['#00b5e2', '#002b49', '#a8ecff'], emblem: '#ffffff', style: 2, bands: 4, warp: 2.5 },
  routeros: { label: 'RouterOS', pal: ['#2a5f8a', '#0b1a2b', '#7fd0f0'], emblem: '#eaf7ff', style: 1, bands: 3, warp: 1.5 },
  alpine: { label: 'Alpine', pal: ['#0d597f', '#062a3d', '#9fd8f5'], emblem: '#ffffff', style: 0, bands: 4, warp: 2 },
  linux: { label: 'Linux', pal: ['#f0b429', '#2b2b2b', '#fff1bf'], emblem: '#161616', style: 0, bands: 6, warp: 3 },
  device: { label: 'Только ping', pal: ['#4f8f9a', '#14303a', '#bfe9ef'], emblem: '#e8fbff', style: 1, bands: 3, warp: 1 },
  unknown: { label: 'Неизвестно', pal: ['#8fa6b8', '#3f5566', '#dfe7ee'], emblem: null, style: 0, bands: 5, warp: 3 },
};

// Shown in the legend (order matters).
export const OS_LEGEND = ['windows', 'ubuntu', 'debian', 'raspbian', 'manjaro', 'routeros', 'openwrt', 'linux', 'device'];

export function osStyle(id) {
  return OS_STYLE[id] || OS_STYLE.unknown;
}

// Stylized emblems (not official logos), drawn in white on a transparent canvas.
const EMBLEMS = {
  windows(g, s) {
    const q = s * 0.46, gap = s * 0.08;
    g.save();
    g.transform(1, -0.08, 0, 1, 0, 0);
    for (const [x, y] of [[-1, -1], [0, -1], [-1, 0], [0, 0]]) {
      g.fillRect(x * (q + gap) + (x < 0 ? 0 : gap), y * (q + gap) + (y < 0 ? 0 : gap), q, q);
    }
    g.restore();
  },
  ubuntu(g, s) {
    g.lineWidth = s * 0.2;
    for (let i = 0; i < 3; i++) {
      const a0 = (i * 2 * Math.PI) / 3 + 0.42, a1 = a0 + (2 * Math.PI) / 3 - 0.84;
      g.beginPath();
      g.arc(0, 0, s * 0.58, a0, a1);
      g.stroke();
      const a = (i * 2 * Math.PI) / 3;
      g.beginPath();
      g.arc(Math.cos(a) * s * 0.9, Math.sin(a) * s * 0.9, s * 0.2, 0, Math.PI * 2);
      g.fill();
    }
  },
  debian(g, s) {
    g.lineCap = 'round';
    let prev = null;
    for (let th = 0.2; th < Math.PI * 4.4; th += 0.08) {
      const r = s * 0.075 * th;
      const p = [Math.cos(-th) * r, Math.sin(-th) * r];
      if (prev) {
        g.lineWidth = 2 + (th / (Math.PI * 4.4)) * s * 0.22;
        g.beginPath();
        g.moveTo(prev[0], prev[1]);
        g.lineTo(p[0], p[1]);
        g.stroke();
      }
      prev = p;
    }
  },
  raspbian(g, s) {
    const r = s * 0.2;
    for (const [x, y] of [[0, -0.2], [-0.38, 0], [0.38, 0], [0, 0.2], [-0.2, 0.52], [0.2, 0.52], [0, 0.82], [-0.4, -0.35], [0.4, -0.35]]) {
      g.beginPath();
      g.arc(x * s, y * s, r, 0, Math.PI * 2);
      g.fill();
    }
    for (const d of [-1, 1]) {
      g.beginPath();
      g.ellipse(d * s * 0.3, -s * 0.78, s * 0.32, s * 0.13, d * 0.5, 0, Math.PI * 2);
      g.fill();
    }
  },
  manjaro(g, s) {
    const w = s * 0.5, gap = s * 0.1;
    g.fillRect(-s, -s, w, 2 * s);
    g.fillRect(-s + w + gap, -s, 2 * s - w - gap, w);
    g.fillRect(-s + w + gap, -s + w + gap, w, 2 * s - w - gap);
    g.fillRect(s - w, -s + w + gap, w, 2 * s - w - gap);
  },
  arch(g, s) {
    g.beginPath();
    g.moveTo(0, -s);
    g.lineTo(s * 0.85, s);
    g.lineTo(0, s * 0.45);
    g.lineTo(-s * 0.85, s);
    g.closePath();
    g.fill();
  },
  linux(g, s) {
    // Penguin silhouette: head + body, belly cut out.
    g.beginPath();
    g.ellipse(0, -s * 0.55, s * 0.36, s * 0.4, 0, 0, Math.PI * 2);
    g.ellipse(0, s * 0.2, s * 0.62, s * 0.8, 0, 0, Math.PI * 2);
    g.fill();
    g.globalCompositeOperation = 'destination-out';
    g.beginPath();
    g.ellipse(0, s * 0.3, s * 0.38, s * 0.58, 0, 0, Math.PI * 2);
    g.fill();
    for (const d of [-1, 1]) {
      g.beginPath();
      g.arc(d * s * 0.13, -s * 0.62, s * 0.07, 0, Math.PI * 2);
      g.fill();
    }
    g.globalCompositeOperation = 'source-over';
  },
};
EMBLEMS.fedora = EMBLEMS.linux;
EMBLEMS.rhel = EMBLEMS.linux;
EMBLEMS.alpine = EMBLEMS.arch;
// Router: a box with two antennas and a row of port LEDs (network devices over SNMP).
EMBLEMS.routeros = (g, s) => {
  g.fillRect(-s, -s * 0.15, 2 * s, s * 0.75);
  g.lineWidth = s * 0.12;
  g.lineCap = 'round';
  for (const d of [-1, 1]) {
    g.beginPath();
    g.moveTo(d * s * 0.6, -s * 0.15);
    g.lineTo(d * s * 0.85, -s * 0.95);
    g.stroke();
  }
  g.globalCompositeOperation = 'destination-out';
  for (let i = 0; i < 5; i++) {
    g.beginPath();
    g.arc(-s * 0.7 + i * s * 0.35, s * 0.22, s * 0.09, 0, Math.PI * 2);
    g.fill();
  }
  g.globalCompositeOperation = 'source-over';
};
// OpenWrt: a dot radiating wireless waves to both sides.
EMBLEMS.openwrt = (g, s) => {
  g.beginPath();
  g.arc(0, 0, s * 0.2, 0, Math.PI * 2);
  g.fill();
  g.lineWidth = s * 0.14;
  g.lineCap = 'round';
  for (const r of [0.5, 0.78]) {
    for (const a of [0, Math.PI]) {
      g.beginPath();
      g.arc(0, 0, s * r, a - 0.75, a + 0.75);
      g.stroke();
    }
  }
};
// Ping-only device: a dot inside two echo rings (sonar).
EMBLEMS.device = (g, s) => {
  g.beginPath();
  g.arc(0, 0, s * 0.22, 0, Math.PI * 2);
  g.fill();
  g.lineWidth = s * 0.12;
  for (const r of [0.55, 0.9]) {
    g.beginPath();
    g.arc(0, 0, s * r, 0, Math.PI * 2);
    g.stroke();
  }
};

const cache = new Map();

export function emblemTexture(id) {
  if (cache.has(id)) return cache.get(id);
  const c = document.createElement('canvas');
  c.width = 1024;
  c.height = 512;
  const g = c.getContext('2d');
  const draw = EMBLEMS[id];
  if (draw) {
    g.fillStyle = '#fff';
    g.strokeStyle = '#fff';
    for (const x of [256, 768]) {
      g.save();
      g.translate(x, 256);
      draw(g, 70);
      g.restore();
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.anisotropy = 4;
  cache.set(id, t);
  return t;
}
