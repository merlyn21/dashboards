// Three.js galaxy: every Zabbix host is a planet styled after its OS,
// metrics float above it as a card (or rings), docker containers are moons,
// other detected integrations (postgresql, 1C, ...) are satellites.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { METRIC, NET, SAT_STATUS, SAT_ICON, SEVERITY, STATE, esc, hostTone, seeded, pct, bytes, bps, bpsShort, trafficLevel, logRel } from './util.js';
import { osStyle, emblemTexture } from './os.js';
import { tr } from './i18n.js';

const NOISE = /* glsl */`
  float hash(vec3 p) { p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
  float noise(vec3 x) {
    vec3 i = floor(x); vec3 f = fract(x); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x), mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
               mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x), mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z);
  }
  float fbm(vec3 p) { float a = 0.5, s = 0.0; for (int i = 0; i < 5; i++) { s += a * noise(p); p *= 2.03; a *= 0.5; } return s; }
`;

const PLANET_VS = /* glsl */`
  varying vec3 vObjN; varying vec3 vN; varying vec3 vView; varying vec2 vUv;
  void main() {
    vObjN = normal;
    vUv = uv;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vN = normalize(normalMatrix * normal);
    vView = -mv.xyz;
    gl_Position = projectionMatrix * mv;
  }
`;

const PLANET_FS = /* glsl */`
  uniform vec3 uA; uniform vec3 uB; uniform vec3 uC; uniform vec3 uRim;
  uniform float uSeed; uniform float uTime; uniform float uDim; uniform float uDesat; uniform float uBands; uniform float uHover;
  uniform float uWarp; uniform float uStyle; uniform sampler2D uEmblem; uniform vec3 uEmblemColor; uniform float uEmblemOn;
  uniform float uAlarm;
  varying vec3 vObjN; varying vec3 vN; varying vec3 vView; varying vec2 vUv;
  ${NOISE}
  void main() {
    vec3 p = normalize(vObjN);
    float n = fbm(p * 2.4 + uSeed);
    float warp = fbm(p * 4.5 + n * 2.0 + uSeed * 1.7 + vec3(uTime * 0.015, 0.0, 0.0));
    vec3 col;
    if (uStyle > 0.5 && uStyle < 1.5) {
      // Tiled "window panes": smooth sky gradient with a faint glowing grid.
      col = mix(uB, uA, smoothstep(-0.9, 0.9, p.y + (warp - 0.5) * 0.6));
      vec2 g = abs(fract(vec2(vUv.x * 24.0, vUv.y * 12.0)) - 0.5);
      float grid = 1.0 - smoothstep(0.44, 0.49, max(g.x, g.y));
      col = mix(col, uC, (1.0 - grid) * 0.35);
      col = mix(col, uC, smoothstep(0.62, 0.82, n) * 0.5);
    } else {
      float a = uStyle > 1.5 ? atan(p.z, p.x) * 2.0 : 0.0;
      float band = 0.5 + 0.5 * sin(p.y * uBands + a + warp * uWarp);
      col = mix(uA, uB, band);
      col = mix(col, uC, smoothstep(0.55, 0.8, n));
    }
    float e = texture2D(uEmblem, vUv).a * uEmblemOn;
    col = mix(col, uEmblemColor, e * 0.92);
    vec3 N = normalize(vN); vec3 V = normalize(vView);
    vec3 L = normalize(vec3(-0.45, 0.55, 0.7));
    float diff = clamp(dot(N, L), 0.0, 1.0);
    float rim = pow(1.0 - clamp(dot(N, V), 0.0, 1.0), 2.5);
    col = col * (0.08 + 1.05 * diff) + uRim * rim * 0.6;
    col += uHover * 0.10;
    float g = dot(col, vec3(0.299, 0.587, 0.114));
    col = mix(col, vec3(g), uDesat);
    col = mix(col, vec3(1.0, 0.12, 0.08) * (0.4 + g), uAlarm);
    gl_FragColor = vec4(col * uDim, 1.0);
  }
`;

const ATMO_VS = /* glsl */`
  varying vec3 vN; varying vec3 vView;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vN = normalize(normalMatrix * normal);
    vView = -mv.xyz;
    gl_Position = projectionMatrix * mv;
  }
`;
const ATMO_FS = /* glsl */`
  uniform vec3 uColor; uniform float uStrength;
  varying vec3 vN; varying vec3 vView;
  void main() {
    // Back faces: 0 at the outer shell edge, ~0.57 at the planet silhouette.
    float d = -dot(normalize(vN), normalize(vView));
    float i = pow(clamp(d / 0.57, 0.0, 1.0), 3.0);
    gl_FragColor = vec4(uColor * i * uStrength, 1.0);
  }
`;

// Billboarded countdown ring for alerts: a dashed arc that rotates and shrinks
// as the 5-minute alert window runs out (full and pulsing for High/Disaster).
const RING_VS = /* glsl */`
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
`;
const RING_FS = /* glsl */`
  uniform vec3 uColor; uniform float uFrac; uniform float uRot; uniform float uAlpha; uniform float uTime;
  varying vec2 vUv;
  const float PI = 3.14159265;
  void main() {
    vec2 p = (vUv - 0.5) * 4.0;
    float r = length(p);
    float band = smoothstep(1.34, 1.39, r) * (1.0 - smoothstep(1.62, 1.67, r));
    float thin = smoothstep(1.76, 1.78, r) * (1.0 - smoothstep(1.81, 1.83, r));
    float a = mod(atan(p.y, p.x) - uRot + 4.0 * PI, 2.0 * PI) / (2.0 * PI); // 0..1 from the moving head
    float arc = step(a, uFrac);
    float dash = step(0.35, fract(a * 36.0));
    float head = smoothstep(0.08, 0.0, a) * band;                               // bright leading edge
    float v = band * arc * (0.55 + 0.45 * dash) + head + thin * 0.35 * (0.5 + 0.5 * sin(a * 60.0 + uTime * 3.0));
    gl_FragColor = vec4(uColor * v * uAlpha * 1.8, 1.0);
  }
`;

const STAR_VS = /* glsl */`
  attribute float aSize; attribute float aPhase; attribute vec3 aColor;
  uniform float uTime; uniform float uPixel;
  varying vec3 vColor; varying float vTw;
  void main() {
    vColor = aColor;
    vTw = 0.65 + 0.35 * sin(uTime * (0.6 + aPhase) + aPhase * 40.0);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = aSize * uPixel * (300.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`;
const STAR_FS = /* glsl */`
  uniform float uOpacity;
  varying vec3 vColor; varying float vTw;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    float a = smoothstep(0.5, 0.0, d);
    a = a * a;
    gl_FragColor = vec4(vColor * a * vTw * uOpacity, 1.0);
  }
`;

// Planet surface palettes: healthy planets look natural and varied,
// the status is carried by the halo, not by the planet body.
const PALETTES = [
  ['#c9a27a', '#8a5a3b', '#f1dcc0'], // sand giant
  ['#7fb3d5', '#2f5d8a', '#e3f1fb'], // ice
  ['#b0643f', '#5e2d1f', '#e8a773'], // rust
  ['#6fae9a', '#2d5e57', '#d4efe3'], // teal
  ['#a48bd1', '#4d3b7a', '#e2d8f5'], // violet
  ['#d9b45f', '#7a5a22', '#fff0c4'], // amber
  ['#8fa6b8', '#3f5566', '#dfe7ee'], // slate
  ['#c47d8e', '#5e3040', '#f2c9d3'], // rose
];

const DOCKER_PAL = ['#2496ed', '#0d4f8b', '#cdeaff'];
const DOCKER_OFF = ['#8a4a4a', '#2e1616', '#d9a0a0'];

// Artificial satellites for detected services: body colour hints the service.
const CRAFT_BODY = { ports: '#7fd0f0', postgresql: '#4a79ad', mysql: '#00a3b4', '1c': '#e0b21a', smart: '#b8c0cc', services: '#c9a24a', zabbix: '#c83a3a' };

let panelTex = null;
function solarPanelTexture() {
  if (panelTex) return panelTex;
  const c = document.createElement('canvas');
  c.width = 128;
  c.height = 64;
  const g = c.getContext('2d');
  const grd = g.createLinearGradient(0, 0, 128, 64);
  grd.addColorStop(0, '#1d4c9e');
  grd.addColorStop(1, '#0b2456');
  g.fillStyle = grd;
  g.fillRect(0, 0, 128, 64);
  g.strokeStyle = 'rgba(160, 200, 255, 0.55)';
  g.lineWidth = 1.5;
  for (let x = 0; x <= 128; x += 16) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, 64); g.stroke(); }
  for (let y = 0; y <= 64; y += 16) { g.beginPath(); g.moveTo(0, y); g.lineTo(128, y); g.stroke(); }
  g.strokeStyle = '#c9d3e0';
  g.lineWidth = 4;
  g.strokeRect(0, 0, 128, 64);
  panelTex = new THREE.CanvasTexture(c);
  panelTex.colorSpace = THREE.SRGBColorSpace;
  return panelTex;
}

// Station for an important OS service (GALAXY_SERVICES): a hub inside a rotating
// habitat ring with a short mast. Visibly different from the service satellites.
function buildStation(color = '#7fd3ff') {
  const group = new THREE.Group();
  const mats = [];
  const mat = (opts) => {
    const m = new THREE.MeshStandardMaterial({ transparent: true, ...opts });
    mats.push(m);
    return m;
  };
  const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.16, 12), mat({ color: '#e8eef6', metalness: 0.8, roughness: 0.25 }));
  hub.rotation.x = Math.PI / 2;
  group.add(hub);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.17, 0.028, 10, 36),
    mat({ color, metalness: 0.6, roughness: 0.3, emissive: new THREE.Color(color).multiplyScalar(0.35), emissiveIntensity: 0.9 }));
  group.add(ring);
  for (let i = 0; i < 4; i++) { // spokes
    const spoke = new THREE.Mesh(new THREE.CylinderGeometry(0.006, 0.006, 0.17, 4), mat({ color: '#b8c4d4', metalness: 0.8, roughness: 0.4 }));
    spoke.rotation.z = (i * Math.PI) / 2;
    spoke.position.set(Math.cos((i * Math.PI) / 2 + Math.PI / 2) * 0.085, Math.sin((i * Math.PI) / 2 + Math.PI / 2) * 0.085, 0);
    group.add(spoke);
  }
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.004, 0.004, 0.14, 4), mat({ color: '#d0d4da' }));
  mast.position.set(0, 0, 0.14);
  mast.rotation.x = Math.PI / 2;
  group.add(mast);
  return { group, mats, spinPart: ring };
}

// Body, boom with two solar panels, a dish antenna. Oriented so the boom
// points radially and the body travels along +z (orbit tangent).
function buildCraft(type, important = false) {
  // Important services (GALAXY_SERVICES) are stations; template services keep their colour.
  if (type === 'svc') return buildStation();
  if (important) return buildStation(CRAFT_BODY[type] || '#7fd3ff');
  const group = new THREE.Group();
  const mats = [];
  const mat = (opts) => {
    const m = new THREE.MeshStandardMaterial({ transparent: true, ...opts });
    mats.push(m);
    return m;
  };
  const body = new THREE.Mesh(new THREE.BoxGeometry(0.11, 0.11, 0.17), mat({ color: CRAFT_BODY[type] || '#b8c0cc', metalness: 0.7, roughness: 0.35 }));
  group.add(body);
  const band = new THREE.Mesh(new THREE.BoxGeometry(0.115, 0.03, 0.175), mat({ color: '#d9dee6', metalness: 0.9, roughness: 0.2 }));
  group.add(band);
  const boom = new THREE.Mesh(new THREE.CylinderGeometry(0.007, 0.007, 0.56, 6), mat({ color: '#9aa3b0', metalness: 0.8, roughness: 0.4 }));
  boom.rotation.z = Math.PI / 2;
  group.add(boom);
  const panelMat = mat({ map: solarPanelTexture(), metalness: 0.4, roughness: 0.45, emissive: new THREE.Color('#0a2a66'), emissiveIntensity: 0.6 });
  for (const d of [-1, 1]) {
    const panel = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.006, 0.1), panelMat);
    panel.position.x = d * 0.19;
    panel.rotation.x = 0.7; // tilt toward the typical top-down camera so panels are not edge-on
    group.add(panel);
  }
  const dish = new THREE.Mesh(new THREE.SphereGeometry(0.045, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2.2),
    mat({ color: '#f0f2f5', metalness: 0.2, roughness: 0.5, side: THREE.DoubleSide }));
  dish.rotation.x = Math.PI / 2;
  dish.position.z = 0.11;
  group.add(dish);
  const rod = new THREE.Mesh(new THREE.CylinderGeometry(0.003, 0.003, 0.09, 4), mat({ color: '#d0d4da' }));
  rod.position.set(0, 0.09, -0.03);
  group.add(rod);
  return { group, mats };
}

// Routers (hosts with a "ports" satellite: MikroTik over SNMP, OpenWrt over the agent) are
// drawn as an orbital station instead of a planet: a hub painted with the planet shader (so the
// OS palette, emblem, dimming and alarm tint all apply), a rotating docking ring, spokes and a
// mast with a beacon. Each LAN port is a segment placed symmetrically around the station.
export const isRouter = (h) => h.satellites?.some((s) => s.type === 'ports');
const PORT_LIGHT = { up: '#3ddc5a', down: '#ff3b30', none: '#6b6a64' };
const ALARM_RED = new THREE.Color('#ff2a1a');
const POD_R = 2.1; // distance of the port segments from the station axis
const HUB_R = 108; // systems (uplinks with clients) sit outside the galaxy's outer belt
const MINI_TIERS = [5, 10, 15, 20, 27]; // leg length by health tier around an uplink

function buildRouterStation(uniforms, glow) {
  const group = new THREE.Group();
  const mats = [];
  const mat = (opts) => {
    const m = new THREE.MeshStandardMaterial({ transparent: true, ...opts });
    mats.push(m);
    return m;
  };
  const shell = new THREE.ShaderMaterial({ uniforms, vertexShader: PLANET_VS, fragmentShader: PLANET_FS });
  const hub = new THREE.Group();
  const body = new THREE.Mesh(new THREE.CylinderGeometry(0.46, 0.46, 0.9, 48, 1, true), shell);
  hub.add(body);
  for (const d of [-1, 1]) { // domed end caps
    const cap = new THREE.Mesh(new THREE.SphereGeometry(0.46, 40, 12, 0, Math.PI * 2, 0, Math.PI / 2), shell);
    cap.scale.y = 0.55;
    cap.position.y = d * 0.45;
    if (d < 0) cap.rotation.x = Math.PI;
    hub.add(cap);
  }
  const metal = mat({ color: '#c9d3df', metalness: 0.85, roughness: 0.3 });
  for (const y of [-0.28, 0, 0.28]) { // hull bands
    const band = new THREE.Mesh(new THREE.TorusGeometry(0.47, 0.018, 8, 48), metal);
    band.rotation.x = Math.PI / 2;
    band.position.y = y;
    hub.add(band);
  }
  group.add(hub);

  // Docking ring in the equatorial plane: the port lanes start from it.
  const ringPivot = new THREE.Group();
  const ringMat = mat({ color: '#6f8196', metalness: 0.8, roughness: 0.35, emissive: new THREE.Color('#7fd0f0'), emissiveIntensity: 0.18 });
  const ring = new THREE.Mesh(new THREE.TorusGeometry(1, 0.075, 14, 96), ringMat);
  ring.rotation.x = Math.PI / 2;
  ringPivot.add(ring);
  const inner = new THREE.Mesh(new THREE.TorusGeometry(1, 0.02, 6, 96), mat({ color: '#e8eef6', metalness: 0.9, roughness: 0.2 }));
  inner.rotation.x = Math.PI / 2;
  inner.scale.setScalar(0.9);
  ringPivot.add(inner);
  const spokeMat = mat({ color: '#b8c4d4', metalness: 0.8, roughness: 0.35 });
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2;
    const spoke = new THREE.Mesh(new THREE.CylinderGeometry(0.022, 0.022, 0.56, 6), spokeMat);
    spoke.rotation.z = Math.PI / 2;
    spoke.rotation.y = -a;
    spoke.position.set(Math.cos(a) * 0.73, 0, Math.sin(a) * 0.73);
    ringPivot.add(spoke);
  }
  group.add(ringPivot);

  // Mast with a blinking beacon on top.
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, 0.55, 6), metal);
  mast.position.y = 0.95;
  group.add(mast);
  const dish = new THREE.Mesh(new THREE.SphereGeometry(0.16, 20, 8, 0, Math.PI * 2, 0, Math.PI / 2.4),
    mat({ color: '#f0f2f5', metalness: 0.2, roughness: 0.5, side: THREE.DoubleSide }));
  dish.position.y = -0.78;
  dish.rotation.x = Math.PI;
  group.add(dish);
  const beacon = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow, color: '#ff5a4a', blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0 }));
  beacon.position.y = 1.24;
  beacon.scale.setScalar(0.28);
  group.add(beacon);

  const pods = new THREE.Group(); // port segments, see createPortMoon
  group.add(pods);
  return { group, mats, ringPivot, ringMat, beacon, pods };
}

// Hosts without a working agent: data is stale, so they are drawn as grey, lifeless bodies.
export const lifeless = (h) => h.state === 'unreachable' || h.state === 'disabled';
const LIFELESS_PAL = ['#6f6e6a', '#3b3a38', '#8b8a85'];

// Layout "по состоянию": concentric belts around the core, worst health inside.
export const HEALTH_TIERS = [
  { name: 'Критично — нет связи, High, Disaster', short: 'Критично', r0: 15, r1: 21, color: '#d03b3b' },
  { name: 'Average', short: 'Average', r0: 26, r1: 32, color: '#ec835a' },
  { name: 'Warning и Info', short: 'Warning / Info', r0: 37, r1: 43, color: '#fab219' },
  { name: 'Норма', short: 'Норма', r0: 48, r1: 58, color: '#0ca30c' },
  { name: 'Нет связи — данных нет', short: 'Нет связи', r0: 66, r1: 72, color: '#6b6a64' },
];

export function healthTier(h) {
  const w = h.problems.worst;
  if (lifeless(h)) return 4; // outside every belt: nothing is known about it right now
  if (w >= 4) return 0;
  if (w === 3) return 1;
  if (w >= 1 || h.state === 'unknown') return 2;
  return 3;
}

// Router LAN ports as moons: grey when idle, cyan and brighter with traffic.
// Traffic intensity on a LAN port: 1 kbit/s -> 0, 100 Mbit/s -> 1 (log scale).
const portLevel = (bits) => (bits > 0 ? Math.min(1, Math.max(0, (Math.log10(bits) - 3) / 5)) : 0);

const RING_ORDER = ['cpu', 'mem', 'disk'];
const RING_R = [1.34, 1.5, 1.66];
const RING_W = 0.085;

function planetUniforms(pal, seed, bands) {
  return {
    uA: { value: new THREE.Color(pal[0]) }, uB: { value: new THREE.Color(pal[1]) }, uC: { value: new THREE.Color(pal[2]) },
    uRim: { value: new THREE.Color(pal[2]) },
    uSeed: { value: seed }, uTime: { value: 0 }, uDim: { value: 1 }, uDesat: { value: 0 },
    uBands: { value: bands }, uHover: { value: 0 }, uWarp: { value: 4 }, uStyle: { value: 0 },
    uEmblem: { value: emblemTexture('none') }, uEmblemColor: { value: new THREE.Color('#ffffff') }, uEmblemOn: { value: 0 },
    uAlarm: { value: 0 },
  };
}

// Floating card above the planet: name, OS, CPU/RAM/disk bars with values.
function labelHtml(h) {
  const worst = h.problems.worst;
  const badge = h.problems.count
    ? `<span class="pl-badge" style="--c:${SEVERITY[Math.max(worst, 0)].color}">${h.problems.count}</span>` : '';
  const st = h.state !== 'online' ? `<span class="pl-state">${STATE[h.state].label}</span>` : '';
  const os = h.distro ? `<div class="pl-os">${esc(h.distro.name)}</div>` : '';
  let rows = '';
  if (h.probe && h.ping && h.state !== 'disabled') {
    // Ping-only device: answer, response time and loss instead of CPU / RAM / disk.
    const g = h.ping;
    const ans = g.ok == null ? '—' : g.ok ? tr('отвечает', 'up') : tr('нет ответа', 'no reply');
    rows = `<div class="pl-metrics pl-ping">
      <div class="pm ${g.ok === 0 ? 'crit' : ''}"><span class="pm-k">Ping</span><span class="pm-v wide">${ans}</span></div>
      <div class="pm"><span class="pm-k">${tr('Отклик', 'RTT')}</span><span class="pm-v wide">${g.rtt == null ? '—' : `${(g.rtt * 1000).toFixed(1)} ${tr('мс', 'ms')}`}</span></div>
      <div class="pm ${g.loss >= 20 ? 'crit' : g.loss > 0 ? 'warn' : ''}"><span class="pm-k">${tr('Потери', 'Loss')}</span><span class="pm-v wide">${g.loss == null ? '—' : `${Math.round(g.loss)}%`}</span></div></div>`;
  } else if (h.state !== 'disabled' && ['cpu', 'mem', 'disk'].some((m) => h[m] != null)) {
    rows = `<div class="pl-metrics">${['cpu', 'mem', 'disk'].map((m) => {
      const v = h[m];
      const lvl = v == null ? '' : v >= 90 ? 'crit' : v >= 80 ? 'warn' : '';
      return `<div class="pm ${lvl}"><span class="pm-k">${METRIC[m].label}</span>
        <span class="pm-bar"><i style="width:${v == null ? 0 : Math.max(2, Math.min(100, v))}%;--c:${METRIC[m].color}"></i></span>
        <span class="pm-v">${v == null ? '—' : `${Math.round(v)}%`}</span></div>`;
    }).join('')}</div>`;
  }
  let net = '';
  if (h.state !== 'disabled' && (h.net_in != null || h.net_out != null)) {
    const row = (dir, v) => `<div class="pm net"><span class="pm-k">${dir === 'in' ? '↓ Вх' : '↑ Исх'}</span>
      <span class="pm-bar log" title="логарифмическая шкала"><i style="width:${Math.max(2, trafficLevel(v) * 100)}%;--c:${NET[dir].color}"></i></span>
      <span class="pm-v">${bpsShort(v)}</span></div>`;
    net = `<div class="pl-metrics pl-net">${row('in', h.net_in)}${row('out', h.net_out)}</div>`;
  }
  const dep = h.blocked_by ? `<div class="pl-dep">${tr('за', 'behind')} ${esc(h.blocked_by_name)}</div>` : '';
  return `<div class="pl-head"><span class="pl-name">${esc(h.name)}</span>${badge}${st}</div>${os}${dep}${rows}${net}`;
}

function glowTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d');
  const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grd.addColorStop(0, 'rgba(255,255,255,1)');
  grd.addColorStop(0.2, 'rgba(255,255,255,0.55)');
  grd.addColorStop(0.5, 'rgba(255,255,255,0.12)');
  grd.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grd;
  g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function nebulaTexture(seed) {
  const rnd = seeded(seed);
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d');
  for (let i = 0; i < 26; i++) {
    const x = 128 + (rnd() - 0.5) * 140, y = 128 + (rnd() - 0.5) * 140, r = 30 + rnd() * 90;
    const grd = g.createRadialGradient(x, y, 0, x, y, r);
    grd.addColorStop(0, `rgba(255,255,255,${0.05 + rnd() * 0.08})`);
    grd.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grd;
    g.fillRect(0, 0, 256, 256);
  }
  // Soft circular mask so the sprite never shows square edges.
  g.globalCompositeOperation = 'destination-in';
  const mask = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  mask.addColorStop(0, 'rgba(0,0,0,1)');
  mask.addColorStop(0.55, 'rgba(0,0,0,0.6)');
  mask.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = mask;
  g.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export function planetRadius(h, sizeBy, ctx = {}) {
  if (lifeless(h)) return 1.3;
  const lo = 1.6, span = 3.0;
  let x = null;
  switch (sizeBy) {
    case 'disk': x = h.disk == null ? null : h.disk / 100; break;
    case 'cpu': x = h.cpu == null ? null : h.cpu / 100; break;
    case 'mem': x = h.mem == null ? null : h.mem / 100; break;
    case 'mem_total': x = h.mem_total ? Math.min(1, Math.max(0, Math.log2(h.mem_total / 2 ** 30) / 7)) : null; break;
    case 'disk_total': x = h.disk_total ? Math.min(1, Math.max(0, Math.log2(h.disk_total / 2 ** 30 / 8) / 8)) : null; break;
    case 'problems': x = Math.sqrt(Math.min(h.problems.count, 16) / 16); break;
    // Relative to the busiest / quietest host so the difference is visible.
    case 'net': x = ctx.net ? 0.08 + 0.92 * logRel((h.net_in || 0) + (h.net_out || 0), ctx.net[0], ctx.net[1]) : 0; break;
    case 'containers': {
      const d = h.satellites.find((s) => s.type === 'docker');
      x = d ? Math.min(1, Math.sqrt(parseInt(d.summary.split('/')[1] || '0', 10) / 16)) : 0;
      break;
    }
    default: x = 0.4;
  }
  return lo + span * Math.pow(x ?? 0, 0.85);
}

export class Galaxy {
  constructor(container, { onSelect, onHover }) {
    this.container = container;
    this.onSelect = onSelect;
    this.onHover = onHover;
    this.planets = new Map();
    this.selected = null;
    this.hovered = null;
    this.settings = {};
    this.clock = new THREE.Clock();
    this.viewOffset = 0;
    this.viewOffsetTarget = 0;
    this.flight = null;
    this.glow = glowTexture();

    const r = (this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' }));
    r.setPixelRatio(Math.min(devicePixelRatio, 2));
    r.toneMapping = THREE.ACESFilmicToneMapping;
    r.toneMappingExposure = 1.05;
    container.appendChild(r.domElement);

    this.labelRenderer = new CSS2DRenderer();
    this.labelRenderer.domElement.className = 'label-layer';
    container.appendChild(this.labelRenderer.domElement);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color('#03040a');
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.1, 3000);
    this.home = { pos: new THREE.Vector3(0, 96, 104), target: new THREE.Vector3(0, 0, 0) };
    this.camera.position.copy(this.home.pos);

    const c = (this.controls = new OrbitControls(this.camera, r.domElement));
    c.enableDamping = true;
    c.dampingFactor = 0.06;
    c.minDistance = 4;
    c.maxDistance = 420;
    c.autoRotateSpeed = 0.35;
    c.addEventListener('start', () => { this.flight = null; this.dragging = true; });
    c.addEventListener('end', () => { this.dragging = false; this.interactUntil = performance.now() + 1500; }); // damping settles
    c.addEventListener('change', () => { this.cameraMoved = true; });

    this.composer = new EffectComposer(r);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.85, 0.55, 0.62);
    // Bloom works on blurred mip levels anyway: computing it at half resolution looks the same
    // and costs about a quarter.
    const bloomSize = this.bloom.setSize.bind(this.bloom);
    this.bloom.setSize = (w, h) => bloomSize(Math.max(1, Math.round(w / 2)), Math.max(1, Math.round(h / 2)));
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());

    this.world = new THREE.Group();
    this.scene.add(this.world);
    this.scene.add(new THREE.AmbientLight('#8fa3c7', 0.9));
    const sun = new THREE.DirectionalLight('#ffffff', 2.2);
    sun.position.set(-0.45, 0.55, 0.7); // same light direction as the planet shader, camera-relative
    this.camera.add(sun);
    this.scene.add(this.camera);
    this.buildBackground();
    this.flows = new THREE.Group();
    this.scene.add(this.flows);
    this.tethers = new THREE.Group(); // port legs to clients (see updateTethers)
    this.scene.add(this.tethers);
    this.hubRings = new THREE.Group(); // orbits around uplinks with clients
    this.scene.add(this.hubRings);
    this.buildTierRings();
    this.flowList = new Map();

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.pointerPx = { x: 0, y: 0 };
    this.pointerDirty = false;
    this.bindEvents();

    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
    // Frame cap: full rate while the view moves (drag, zoom, camera flight), the "smoothness"
    // setting (30 fps by default) when idle. Most of the CPU goes into drawing frames, and an
    // idle galaxy only needs its particles and blinks to keep going.
    this.lastFrameAt = 0;
    this.interactUntil = 0;
    this.frameNo = 0;
    r.setAnimationLoop((now) => {
      const active = this.dragging || this.flight || now < this.interactUntil;
      const fps = active ? 60 : Math.max(5, Number(this.settings?.fps) || 30);
      if (now - this.lastFrameAt < 1000 / fps - 2) return;
      this.lastFrameAt = now;
      this.frame();
    });
  }

  // ------------------------------------------------------------------ background

  buildBackground() {
    const rnd = seeded('stars');
    // Distant star sphere.
    const N = 6000;
    const pos = new Float32Array(N * 3), size = new Float32Array(N), phase = new Float32Array(N), col = new Float32Array(N * 3);
    const tints = [new THREE.Color('#ffffff'), new THREE.Color('#cfe0ff'), new THREE.Color('#ffe9c7'), new THREE.Color('#ffd0d0')];
    for (let i = 0; i < N; i++) {
      const u = rnd() * 2 - 1, th = rnd() * Math.PI * 2, rad = 400 + rnd() * 700;
      const s = Math.sqrt(1 - u * u);
      pos.set([rad * s * Math.cos(th), rad * u, rad * s * Math.sin(th)], i * 3);
      size[i] = 0.6 + Math.pow(rnd(), 6) * 4.5;
      phase[i] = rnd();
      const t = tints[Math.floor(rnd() * tints.length)];
      col.set([t.r, t.g, t.b], i * 3);
    }
    this.starUniforms = { uTime: { value: 0 }, uPixel: { value: 1 }, uOpacity: { value: 1 } };
    this.stars = this.points(pos, size, phase, col, this.starUniforms);
    this.scene.add(this.stars);

    // Spiral galaxy dust in the plane where hosts live.
    const M = 9000;
    const p2 = new Float32Array(M * 3), s2 = new Float32Array(M), ph2 = new Float32Array(M), c2 = new Float32Array(M * 3);
    const inner = new THREE.Color('#ffd9a8'), a1 = new THREE.Color('#7f8cff'), a2 = new THREE.Color('#c77dff');
    for (let i = 0; i < M; i++) {
      const arm = i % 3;
      const d = Math.pow(rnd(), 0.7) * 70;
      const ang = arm * (Math.PI * 2 / 3) + d * 0.085 + (rnd() - 0.5) * (0.9 - d / 140);
      const spread = (rnd() - 0.5) * (2 + d * 0.12);
      p2.set([Math.cos(ang) * d + spread, (rnd() - 0.5) * (4 - d * 0.03) * 1.2, Math.sin(ang) * d + spread], i * 3);
      s2[i] = 0.35 + rnd() * 1.1;
      ph2[i] = rnd();
      const c = inner.clone().lerp(arm === 1 ? a2 : a1, Math.min(1, d / 40));
      c2.set([c.r, c.g, c.b], i * 3);
    }
    this.dustUniforms = { uTime: { value: 0 }, uPixel: { value: 1 }, uOpacity: { value: 0.32 } };
    this.dust = this.points(p2, s2, ph2, c2, this.dustUniforms);
    this.scene.add(this.dust);

    // Galactic core.
    const core = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.glow, color: '#ffcf99', blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.55 }));
    core.scale.setScalar(26);
    this.scene.add(core);

    // Nebulae far away.
    const neb = ['#5a3fd6', '#1f6fd1', '#b03a8f', '#2aa39a', '#6d2fb8'];
    neb.forEach((color, i) => {
      const m = new THREE.SpriteMaterial({ map: nebulaTexture('n' + i), color, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.4 });
      const s = new THREE.Sprite(m);
      const a = (i / neb.length) * Math.PI * 2 + 0.4;
      s.position.set(Math.cos(a) * 520, (rnd() - 0.4) * 300, Math.sin(a) * 520);
      s.scale.setScalar(420 + rnd() * 260);
      this.scene.add(s);
    });
  }

  points(pos, size, phase, col, uniforms) {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('aSize', new THREE.BufferAttribute(size, 1));
    g.setAttribute('aPhase', new THREE.BufferAttribute(phase, 1));
    g.setAttribute('aColor', new THREE.BufferAttribute(col, 3));
    const m = new THREE.ShaderMaterial({ uniforms, vertexShader: STAR_VS, fragmentShader: STAR_FS, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true });
    return new THREE.Points(g, m);
  }

  // ------------------------------------------------------------------ layout

  layout(hosts, mode = 'state') {
    // Network dependencies (galaxy.uplink tags): parentOf(id) -> upstream host id.
    const byId = new Map(hosts.map((h) => [h.id, h]));
    const parentOf = (id) => { const u = byId.get(id)?.uplink; return u && byId.has(u) ? u : null; };
    const kids = new Map();
    for (const h of hosts) { const u = parentOf(h.id); if (u) kids.set(u, [...(kids.get(u) || []), h.id]); }
    const bySeed = (tag) => (a, b) => seeded(tag + a)() - seeded(tag + b)();
    for (const list of kids.values()) list.sort(bySeed('slot'));
    this.stationYaw = new Map();
    this.systems = [];
    // Uplinks with clients (MikroTik, ...) become systems of their own outside the galaxy:
    // the uplink far from the core, its clients hanging on the legs of the ports they use.
    const hubs = mode === 'free' ? [] : hosts.filter((h) => !parentOf(h.id) && kids.has(h.id)).map((h) => h.id).sort(bySeed('slot'));
    const inSystem = new Set();
    const mark = (id) => { inSystem.add(id); (kids.get(id) || []).forEach(mark); };
    hubs.forEach(mark);
    const pos = this.layoutGalaxy(hosts.filter((h) => !inSystem.has(h.id)), mode, parentOf, kids, bySeed);
    hubs.forEach((id, i) => {
      const a = ((i + 0.5) / hubs.length) * Math.PI * 2 + 0.9;
      const c = new THREE.Vector3(Math.cos(a) * HUB_R, 0, Math.sin(a) * HUB_R);
      pos.set(id, c);
      this.placeSystem(id, c, a + Math.PI, mode, byId, kids, pos, 0);
    });
    return pos;
  }

  // Clients of `id` around it: each on the extension of its port segment (the station is
  // turned so the gap between segments faces `towards`, i.e. the core or its own uplink).
  // Distance: fixed in the "network" layout, by health in the "state" layout (mini belts).
  placeSystem(id, center, towards, mode, byId, kids, pos, depth) {
    const h = byId.get(id);
    const R = this.planets.get(id)?.targetRadius || 3;
    const ports = h.satellites?.find((s) => s.type === 'ports')?.ports || [];
    const n = ports.length;
    const yaw = n ? Math.PI / 2 - Math.PI / n - towards : 0;
    if (n) this.stationYaw.set(id, yaw);
    const segAngle = (i) => (i / n) * Math.PI * 2 + Math.PI / 2 - yaw; // atan2(z, x) of segment i
    const base = n ? R * POD_R + 2 : R * 1.6 + 2;
    const list = kids.get(id) || [];
    const onPort = new Map(); // segment index -> client ids
    const loose = [];
    for (const c of list) {
      const name = byId.get(c).name;
      const i = ports.findIndex((p) => p.clients?.includes(name));
      if (i >= 0) onPort.set(i, [...(onPort.get(i) || []), c]); else loose.push(c);
    }
    const tierStep = depth ? 0.7 : 1;
    let far = base;
    const put = (c, a) => {
      const ch = byId.get(c);
      const cr = this.planets.get(c)?.targetRadius || 2.5;
      const leg = mode === 'network' ? (depth ? 8 : 12) : MINI_TIERS[healthTier(ch)] * tierStep;
      const d = base + cr + leg;
      far = Math.max(far, d + cr * 3);
      const v = new THREE.Vector3(center.x + Math.cos(a) * d, center.y + (seeded('y' + c)() - 0.5) * 2, center.z + Math.sin(a) * d);
      pos.set(c, v);
      if (kids.has(c)) far = Math.max(far, v.distanceTo(center) + this.placeSystem(c, v, a + Math.PI, mode, byId, kids, pos, depth + 1));
    };
    for (const [i, cs] of onPort) cs.forEach((c, k) => put(c, segAngle(i) + (k - (cs.length - 1) / 2) * 0.3));
    loose.forEach((c, k) => put(c, towards + ((k + 1) / (loose.length + 1)) * Math.PI * 2)); // no ports: spread, gap to the uplink
    this.systems.push({ id, center: center.clone(), depth, base, radius: far });
    return far;
  }

  // The galaxy around the core: health belts ("state" / "network") or free placement.
  layoutGalaxy(hosts, mode, parentOf, kids, bySeed) {
    const byId = new Map(hosts.map((h) => [h.id, h]));
    const byState = mode !== 'free';
    // Every live host gets its own angular slot (order stable per host id), so a planet on an
    // inner belt never sits on the traffic lane of an outer one. Lifeless hosts are spread over
    // the outer belt separately. In the free layout clients are pulled next to their uplink.
    const live = hosts.filter((h) => !lifeless(h)).map((h) => h.id).sort(bySeed('slot'));
    const dead = hosts.filter((h) => lifeless(h)).map((h) => h.id).sort();
    const slotAngle = (id) => {
      const i = live.indexOf(id);
      if (i >= 0) return (i / live.length) * Math.PI * 2 + 0.3;
      return ((dead.indexOf(id) + 0.5) / Math.max(1, dead.length)) * Math.PI * 2 + 1.1;
    };
    const pts = [...hosts].sort((a, b) => (a.id < b.id ? -1 : 1)).map((h) => {
      const rnd = seeded('pos' + h.id);
      const ra = rnd() * Math.PI * 2;
      const a = byState ? slotAngle(h.id) : ra;
      const u = rnd(), y = (rnd() - 0.5) * (byState ? 5 : 10);
      const tier = byState ? HEALTH_TIERS[healthTier(h)] : null;
      const d = byState ? tier.r0 + u * (tier.r1 - tier.r0) : 16 + Math.sqrt(u) * 40;
      return { id: h.id, tier, v: new THREE.Vector3(Math.cos(a) * d, y, Math.sin(a) * d) };
    });
    if (!byState) {
      const at = new Map(pts.map((p) => [p.id, p.v]));
      const place = (id) => {
        const list = (kids.get(id) || []).filter((c) => at.has(c));
        list.forEach((c, i) => {
          const base = at.get(id);
          const a = Math.atan2(base.z, base.x) + (i - (list.length - 1) / 2) * 0.6;
          at.get(c).set(base.x + Math.cos(a) * 12, base.y, base.z + Math.sin(a) * 12);
          place(c);
        });
      };
      hosts.filter((h) => !parentOf(h.id)).forEach((h) => place(h.id));
    }
    const minD = byState ? 16 : 21;
    for (let it = 0; it < (byState ? 0 : 120); it++) { // state layout: slots already keep planets apart
      for (let i = 0; i < pts.length; i++) {
        for (let j = i + 1; j < pts.length; j++) {
          const a = pts[i].v, b = pts[j].v;
          const d = a.distanceTo(b);
          if (d < minD && d > 1e-4) {
            const push = b.clone().sub(a).multiplyScalar((minD - d) / d / 2);
            a.sub(push); b.add(push);
          }
        }
        const p = pts[i].v;
        const r = Math.hypot(p.x, p.z);
        const lo = pts[i].tier ? pts[i].tier.r0 : 14, hi = pts[i].tier ? pts[i].tier.r1 : Infinity;
        const cr = Math.min(hi, Math.max(lo, r));
        if (r > 1e-4 && cr !== r) { p.x *= cr / r; p.z *= cr / r; }
      }
    }
    return new Map(pts.filter((p) => byId.has(p.id)).map((p) => [p.id, p.v]));
  }

  // Orbits around each system: mini health belts ("state") or the ring of port legs ("network").
  buildHubRings() {
    for (const o of [...this.hubRings.children]) { this.hubRings.remove(o); o.geometry.dispose(); o.material.dispose(); }
    const mode = this.settings.layout || 'state';
    for (const sys of this.systems || []) {
      const rings = mode === 'network'
        ? [{ r: sys.base + 2.5 + (sys.depth ? 8 : 12), color: '#7fd0f0' }]
        : HEALTH_TIERS.slice(0, 4).map((t, i) => ({ r: sys.base + 2.5 + MINI_TIERS[i] * (sys.depth ? 0.7 : 1), color: t.color }));
      for (const { r, color } of rings) {
        const g = new THREE.BufferGeometry().setFromPoints(Array.from({ length: 129 }, (_, k) => {
          const a = (k / 128) * Math.PI * 2;
          return new THREE.Vector3(sys.center.x + Math.cos(a) * r, sys.center.y, sys.center.z + Math.sin(a) * r);
        }));
        const line = new THREE.Line(g, new THREE.LineDashedMaterial({ color, dashSize: 0.8, gapSize: 0.8, transparent: true, opacity: 0.35, depthWrite: false }));
        line.computeLineDistances();
        this.hubRings.add(line);
      }
    }
  }

  // ------------------------------------------------------------------ views ("centers")

  // Camera view of a centre: 'all' (everything), 'zabbix' (the galaxy around the core) or a
  // system's uplink id. The camera keeps the default oblique direction and fits the radius.
  viewFor(key) {
    let center = new THREE.Vector3(), radius = 80;
    const pts = [...this.planets.values()].filter((p) => p.visible !== false).map((p) => ({ p, v: p.target || p.group.position }));
    const sys = (this.systems || []).find((x) => x.id === key && !x.depth);
    if (sys) {
      center = sys.center.clone();
      radius = sys.radius + 4;
    } else if (key === 'zabbix') {
      const inSys = new Set((this.systems || []).flatMap((x) => this.clientsOf(x.id)));
      radius = Math.max(40, ...pts.filter(({ p }) => !inSys.has(p.id)).map(({ v }) => v.length() + 6));
    } else if (pts.length) {
      const box = new THREE.Box3();
      for (const { v } of pts) box.expandByPoint(v);
      box.getCenter(center);
      center.y = 0;
      radius = Math.max(40, ...pts.map(({ v }) => v.distanceTo(center) + 6));
    }
    const dir = new THREE.Vector3(0, 96, 104).normalize();
    const dist = Math.min(this.controls.maxDistance, (radius / Math.sin(THREE.MathUtils.degToRad(this.camera.fov / 2))) * (key === 'all' || key === 'zabbix' ? 1.0 : 0.85));
    return { target: center, pos: center.clone().addScaledVector(dir, dist) };
  }

  clientsOf(id) {
    const out = [id];
    for (const h of this.hosts || []) if (h.uplink === id) out.push(...this.clientsOf(h.id));
    return out;
  }

  // Switch the centre the overview is built around; Esc / empty click come back to it.
  setHome(key, instant = false) {
    this.homeKey = key;
    this.home = this.viewFor(key);
    if (instant) {
      this.camera.position.copy(this.home.pos);
      this.controls.target.copy(this.home.target);
      this.controls.update();
    } else if (!this.selected) {
      this.fly(this.home.target.clone(), this.home.pos.clone());
    }
  }

  buildTierRings() {
    this.tierRings = new THREE.Group();
    this.tierLabels = [];
    for (const t of HEALTH_TIERS) {
      const r = (t.r0 + t.r1) / 2;
      const pts = Array.from({ length: 181 }, (_, k) => {
        const a = (k / 180) * Math.PI * 2;
        return new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r);
      });
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
        new THREE.LineDashedMaterial({ color: t.color, dashSize: 1.2, gapSize: 1.4, transparent: true, opacity: 0.28, depthWrite: false }));
      line.computeLineDistances();
      this.tierRings.add(line);
      const el = document.createElement('div');
      el.className = 'tier-label';
      el.style.setProperty('--c', t.color);
      el.innerHTML = `<i></i>${t.short}`;
      el.title = t.name;
      this.tierLabels.push(el);
      const label = new CSS2DObject(el);
      label.userData.r = r;
      this.tierLabelObjs = this.tierLabelObjs || [];
      this.tierLabelObjs.push(label);
      line.add(label);
    }
    this.scene.add(this.tierRings);
  }

  // ------------------------------------------------------------------ planets

  setData(hosts, settings) {
    this.hosts = hosts;
    this.settings = { ...settings };
    const seen = new Set();
    const fresh = new Set();
    for (const h of hosts) {
      seen.add(h.id);
      let p = this.planets.get(h.id);
      if (!p) {
        p = this.createPlanet(h);
        this.planets.set(h.id, p);
        this.world.add(p.group);
        fresh.add(p);
      }
      this.updatePlanet(p, h);
    }
    for (const [id, p] of this.planets) {
      if (!seen.has(id)) {
        this.world.remove(p.group);
        p.label.element.remove();
        this.planets.delete(id);
      }
    }
    this.applySettings(settings); // planet radii first: systems are spaced by them
    const positions = this.layout(hosts, settings.layout);
    // Belt labels go in the gap between two host slots, so they never sit on a planet or its lane.
    const liveCount = Math.max(1, hosts.filter((h) => !lifeless(h)).length);
    const k = Math.floor(liveCount / 2); // a gap on the far side of slot 0, usually nearer the camera
    const gap = 0.3 + Math.PI / liveCount + (2 * Math.PI * k) / liveCount;
    for (const l of this.tierLabelObjs) l.position.set(Math.cos(gap) * l.userData.r, 0, Math.sin(gap) * l.userData.r);
    for (const p of this.planets.values()) {
      const t = positions.get(p.id) || new THREE.Vector3(HUB_R, 0, 0);
      if (fresh.has(p)) {
        p.group.position.copy(t);
        p.radius = p.targetRadius;
      }
      // Animated in frame(): planets glide to a new place. Small shifts (planet sizes follow the
      // traffic on every poll) are ignored so the scene is not rebuilt for nothing.
      if (fresh.has(p) || !p.target || p.target.distanceTo(t) > 0.8) p.target = t.clone();
      if (p.station) p.station.pods.rotation.y = this.stationYaw.get(p.id) || 0;
    }
    this.buildHubRings();
    this.updateFlows();
    if (!this.homeKey) this.setHome('all', true); // first data: show everything
    else this.home = this.viewFor(this.homeKey);
  }

  // ------------------------------------------------------------------ traffic flows

  // [min, max] of positive values over enabled hosts; pick(h) -> number[].
  range(pick) {
    const vals = [...this.planets.values()].filter((p) => p.host.state !== 'disabled')
      .flatMap((p) => pick(p.host)).filter((v) => v > 0);
    return vals.length ? [Math.min(...vals), Math.max(...vals)] : null;
  }

  netRange() {
    return this.range((h) => [(h.net_in || 0) + (h.net_out || 0)]);
  }

  // Two lanes per host between the galactic core (a conventional "outside
  // world") and the planet: blue particles fly in, orange ones fly out.
  // Width, particle count and speed follow a log scale of the bit rate.
  updateFlows() {
    const seen = new Set();
    const lr = this.range((h) => [h.net_in, h.net_out]);
    for (const p of this.planets.values()) {
      const h = p.host;
      if (lifeless(h) || (h.net_in == null && h.net_out == null)) continue;
      seen.add(p.id);
      let f = this.flowList.get(p.id);
      // A client's traffic goes to its uplink (the port segment when galaxy.port names one),
      // not to the core: only the uplink's own WAN flow reaches the core.
      const up = h.uplink ? this.planets.get(h.uplink) : null;
      const start = up ? this.uplinkAnchor(up, h) : new THREE.Vector3();
      const key = [...p.group.position.toArray(), ...start.toArray()].map((v) => v.toFixed(1)).join(',');
      if (!f || f.key !== key) {
        if (f) this.disposeFlow(f);
        f = this.createFlow(p, key, start);
        this.flowList.set(p.id, f);
      }
      for (const lane of f.lanes) {
        const v = lane.dir === 'in' ? h.net_in : h.net_out;
        const lvl = lr ? logRel(v, lr[0], lr[1]) : 0;
        lane.level = lvl;
        lane.speed = 0.03 + lvl * 0.25;
        lane.visibleCount = v ? Math.round(2 + lvl * (lane.max - 2)) : 0;
        const r = 0.02 + lvl * lvl * 0.5;
        if (lane.radius !== r) {
          lane.radius = r;
          lane.tube.geometry.dispose();
          lane.tube.geometry = new THREE.TubeGeometry(lane.curve, 64, r, 6, false);
        }
        lane.pts.geometry.setDrawRange(0, lane.visibleCount);
        lane.pts.material.size = 0.4 + lvl * 1.6;
      }
    }
    for (const [id, f] of this.flowList) {
      if (!seen.has(id)) { this.disposeFlow(f); this.flowList.delete(id); }
    }
    this.updateTethers();
  }

  // Legs from a port segment to the client hanging on it, drawn when there is no traffic
  // lane to show instead: translucent when the client is idle, dashed grey when it is down.
  updateTethers() {
    for (const o of [...this.tethers.children]) { this.tethers.remove(o); o.geometry.dispose(); o.material.dispose(); }
    for (const p of this.planets.values()) {
      const h = p.host;
      const up = h.uplink && this.planets.get(h.uplink);
      if (!up || h.state === 'disabled') continue;
      if (this.flowList.get(p.id)?.lanes.some((l) => l.visibleCount > 0)) continue;
      const a = this.uplinkAnchor(up, h);
      const dir = p.group.position.clone().sub(a);
      const b = a.clone().addScaledVector(dir, Math.max(0, 1 - (p.radius * 1.05) / Math.max(1e-3, dir.length())));
      const g = new THREE.BufferGeometry().setFromPoints([a, b]);
      const dead = lifeless(h);
      const line = new THREE.Line(g, dead
        ? new THREE.LineDashedMaterial({ color: '#8b8a85', dashSize: 0.6, gapSize: 0.5, transparent: true, opacity: 0.5, depthWrite: false })
        : new THREE.LineBasicMaterial({ color: '#a9c4dc', transparent: true, opacity: 0.45, depthWrite: false }));
      if (dead) line.computeLineDistances();
      line.userData.hostId = p.id;
      this.tethers.add(line);
    }
  }

  // Where a client's flow starts on its uplink: the matching port segment, else the
  // uplink's edge facing the client.
  uplinkAnchor(up, h) {
    const seg = up.portList.find((m) => m.data.clients?.includes(h.name));
    if (seg) {
      up.group.updateMatrixWorld(true);
      return this.world.worldToLocal(seg.mesh.getWorldPosition(new THREE.Vector3()));
    }
    const me = this.planets.get(h.id);
    const dir = me ? me.group.position.clone().sub(up.group.position).setY(0).normalize() : new THREE.Vector3(1, 0, 0);
    return up.group.position.clone().addScaledVector(dir, up.radius * 1.2);
  }

  createFlow(p, key, start = new THREE.Vector3()) {
    const end = p.group.position.clone();
    const span = end.clone().sub(start);
    const dist = span.length();
    const side = new THREE.Vector3(-span.z, 0, span.x).normalize();
    const lanes = ['in', 'out'].map((dir, i) => {
      const off = side.clone().multiplyScalar(i ? 0.9 : -0.9);
      const a = start.clone().add(off.clone().multiplyScalar(0.3));
      const b = end.clone().add(off);
      const mid = a.clone().lerp(b, 0.5).add(new THREE.Vector3(0, dist * 0.18, 0)).add(off.clone().multiplyScalar(2));
      const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
      const samples = curve.getSpacedPoints(96);
      const color = new THREE.Color(NET[dir].color);
      const tube = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({
        color, transparent: true, opacity: 0.16, blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      const max = 40;
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(max * 3), 3));
      const pts = new THREE.Points(g, new THREE.PointsMaterial({
        color: color.clone().multiplyScalar(1.8), map: this.glow, size: 1, transparent: true, opacity: 0.95,
        blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
      }));
      pts.frustumCulled = false;
      this.flows.add(tube, pts);
      const rnd = seeded('flow' + p.id + dir);
      return { dir, curve, samples, tube, pts, max, phases: Array.from({ length: max }, () => rnd()), radius: -1, speed: 0.1, level: 0, visibleCount: 0 };
    });
    return { id: p.id, key, lanes };
  }

  disposeFlow(f) {
    for (const lane of f.lanes) {
      this.flows.remove(lane.tube, lane.pts);
      lane.tube.geometry.dispose();
      lane.pts.geometry.dispose();
    }
  }

  animateFlows(t) {
    this.flows.visible = this.settings.flows !== false;
    if (!this.flows.visible) return;
    // Re-anchor lanes to gliding planets a few times a second, not every frame (tube rebuilds are costly).
    if (this.planetsMoving && t - (this.lastFlowAnchor || 0) > 0.2) {
      this.lastFlowAnchor = t;
      this.updateFlows();
    } else if (!this.planetsMoving && this.flowsStale) {
      this.updateFlows(); // final position after the glide
    }
    this.flowsStale = this.planetsMoving;
    const tmp = new THREE.Vector3();
    for (const f of this.flowList.values()) {
      const p = this.planets.get(f.id);
      // With a host focused, other hosts' flows almost vanish so they do not cross the view.
      const dim = !p || !p.visible ? 0 : this.selected && this.selected !== p.id ? p.dim * 0.35 : p.dim;
      for (const lane of f.lanes) {
        lane.tube.material.opacity = (0.05 + lane.level * 0.3) * dim;
        lane.pts.material.opacity = 0.95 * dim;
        lane.tube.visible = lane.pts.visible = dim > 0.02 && lane.visibleCount > 0;
        if (!lane.pts.visible) continue;
        const arr = lane.pts.geometry.attributes.position.array;
        for (let i = 0; i < lane.visibleCount; i++) {
          let u = (lane.phases[i] + t * lane.speed) % 1;
          if (lane.dir === 'out') u = 1 - u; // curve runs core -> planet; outbound travels back
          // Points sampled once per curve (equal arc length): interpolate instead of getPointAt.
          const sp = lane.samples, x = u * (sp.length - 1), k = Math.min(sp.length - 2, Math.floor(x)), w = x - k;
          arr[i * 3] = sp[k].x + (sp[k + 1].x - sp[k].x) * w;
          arr[i * 3 + 1] = sp[k].y + (sp[k + 1].y - sp[k].y) * w;
          arr[i * 3 + 2] = sp[k].z + (sp[k + 1].z - sp[k].z) * w;
        }
        lane.pts.geometry.attributes.position.needsUpdate = true;
      }
    }
  }

  createPlanet(h) {
    const rnd = seeded('look' + h.id + h.name);
    const group = new THREE.Group();
    const body = new THREE.Group(); // scaled by planet radius
    group.add(body);

    const pal = PALETTES[Math.floor(rnd() * PALETTES.length)];
    const uniforms = planetUniforms(pal, rnd() * 100, 4 + rnd() * 10);
    const core = new THREE.Mesh(new THREE.SphereGeometry(1, 64, 48),
      new THREE.ShaderMaterial({ uniforms, vertexShader: PLANET_VS, fragmentShader: PLANET_FS }));
    core.rotation.z = (rnd() - 0.5) * 0.6;
    body.add(core);

    const atmoU = { uColor: { value: new THREE.Color(pal[2]) }, uStrength: { value: 0.5 } };
    const atmo = new THREE.Mesh(new THREE.SphereGeometry(1.22, 48, 32),
      new THREE.ShaderMaterial({ uniforms: atmoU, vertexShader: ATMO_VS, fragmentShader: ATMO_FS, side: THREE.BackSide, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false }));
    body.add(atmo);

    // Status halo sprite (problems / unreachable), pulses.
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.glow, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0 }));
    halo.scale.setScalar(4.2);
    body.add(halo);

    // Event glow: fades over 5 min after a new problem / login, pulses red on High+.
    const alertGlow = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.glow, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0 }));
    alertGlow.scale.setScalar(5);
    body.add(alertGlow);
    const alertRing = new THREE.Mesh(new THREE.PlaneGeometry(4, 4), new THREE.ShaderMaterial({
      uniforms: { uColor: { value: new THREE.Color('#ffffff') }, uFrac: { value: 0 }, uRot: { value: 0 }, uAlpha: { value: 0 }, uTime: { value: 0 } },
      vertexShader: RING_VS, fragmentShader: RING_FS, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, depthTest: false,
    }));
    alertRing.visible = false;
    alertRing.renderOrder = 5;
    body.add(alertRing);

    // Invisible, larger hit target.
    const hit = new THREE.Mesh(new THREE.SphereGeometry(1.7, 16, 12), new THREE.MeshBasicMaterial({ visible: false }));
    hit.userData.hostId = h.id;
    body.add(hit);

    // Metric rings.
    const rings = new THREE.Group();
    rings.rotation.x = -Math.PI / 2 + (rnd() - 0.5) * 0.7;
    rings.rotation.y = (rnd() - 0.5) * 0.5;
    body.add(rings);
    const ringParts = {};
    RING_ORDER.forEach((m, i) => {
      const r0 = RING_R[i], r1 = r0 + RING_W;
      const track = new THREE.Mesh(new THREE.RingGeometry(r0, r1, 128),
        new THREE.MeshBasicMaterial({ color: '#ffffff', transparent: true, opacity: 0.08, side: THREE.DoubleSide, depthWrite: false }));
      const arc = new THREE.Mesh(new THREE.BufferGeometry(),
        new THREE.MeshBasicMaterial({ color: new THREE.Color(METRIC[m].color).multiplyScalar(1.6), transparent: true, opacity: 0.95, side: THREE.DoubleSide, depthWrite: false }));
      rings.add(track, arc);
      ringParts[m] = { track, arc, r0, r1, value: undefined };
    });

    // Router station (built lazily, see updatePlanet).
    const station = null;

    // Satellites container.
    const sats = new THREE.Group();
    body.add(sats);

    // Label.
    const el = document.createElement('div');
    el.className = 'planet-label';
    el.addEventListener('click', (e) => { e.stopPropagation(); this.onSelect(h.id); });
    el.addEventListener('pointerenter', () => this.setHover(h.id));
    el.addEventListener('pointerleave', () => this.setHover(null));
    const label = new CSS2DObject(el);
    label.center.set(0.5, 1);
    group.add(label);

    // Logged-in users badge hanging just below the planet.
    const userEl = document.createElement('div');
    userEl.className = 'user-badge hidden';
    userEl.addEventListener('click', (e) => { e.stopPropagation(); this.onSelect(h.id); });
    const userLabel = new CSS2DObject(userEl);
    userLabel.center.set(0.5, 0);
    group.add(userLabel);

    // Docker moons live in their own tilted belt.
    const moons = new THREE.Group();
    moons.rotation.x = (rnd() - 0.5) * 0.5;
    moons.rotation.z = (rnd() - 0.5) * 0.5;
    body.add(moons);

    return {
      id: h.id, group, body, core, uniforms, atmo, atmoU, halo, hit, rings, ringParts, sats, label, el, moons, moonList: [], portList: [], userEl, userLabel, alertGlow, alertRing, flashes: [], alarm: false, station,
      pal, spin: 0.04 + rnd() * 0.12, radius: 1, targetRadius: 1, satList: [], dim: 1, targetDim: 1, host: h,
    };
  }

  updatePlanet(p, h) {
    p.host = h;
    const dead = lifeless(h);
    const os = osStyle(h.distro?.id);
    const pal = dead ? LIFELESS_PAL : os.pal;
    const u = p.uniforms;
    u.uA.value.set(pal[0]);
    u.uB.value.set(pal[1]);
    u.uC.value.set(pal[2]);
    u.uRim.value.set(pal[2]);
    u.uStyle.value = os.style;
    u.uBands.value = os.bands;
    u.uWarp.value = os.warp;
    u.uEmblem.value = emblemTexture(h.distro?.id);
    u.uEmblemOn.value = os.emblem ? (dead ? 0.35 : 1) : 0;
    u.uEmblemColor.value.set(dead ? '#9a9994' : os.emblem || '#ffffff');
    u.uDesat.value = dead ? 1 : 0;
    u.uBands.value = dead ? 2 : os.bands;

    this.updateStation(p, h, dead);

    const tone = hostTone(h);
    p.tone = tone;
    p.atmoU.uColor.value.set(tone && !dead ? tone : pal[2]);
    p.atmoBase = dead ? 0.04 : tone ? 0.9 : 0.45;
    p.halo.material.color.set(tone || '#ffffff');
    p.haloBase = dead || !tone ? 0 : 0.1 + 0.05 * Math.max(0, h.problems.worst - 2);

    // Rings.
    for (const m of RING_ORDER) {
      const part = p.ringParts[m];
      const v = dead ? null : h[m];
      if (part.value !== v) {
        part.value = v;
        part.arc.geometry.dispose();
        if (v == null) {
          part.arc.geometry = new THREE.BufferGeometry();
        } else {
          const len = Math.max(0.02, Math.min(1, v / 100)) * Math.PI * 2;
          part.arc.geometry = new THREE.RingGeometry(part.r0, part.r1, Math.max(8, Math.ceil(128 * len / (Math.PI * 2))), 1, Math.PI / 2, -len);
        }
      }
      part.track.material.opacity = v == null ? 0.035 : 0.09;
    }

    this.updateSatellites(p, h);

    // Label content.
    p.el.innerHTML = labelHtml(h);
    const users = h.users;
    p.hasUsers = users != null && !dead;
    if (p.hasUsers) {
      const what = h.users_src === 'rdp' ? 'RDP-сессий' : 'залогиненных пользователей';
      p.userEl.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="5" r="3"/><path d="M2 15c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5z"/></svg><b>${Math.round(users)}</b>`;
      p.userEl.title = `${Math.round(users)} ${what}`;
      p.userEl.classList.toggle('zero', !users);
    }
    p.el.classList.toggle('dead', dead);
  }

  updateStation(p, h, dead) {
    const router = isRouter(h);
    if (router && !p.station) {
      p.station = buildRouterStation(p.uniforms, this.glow);
      p.body.add(p.station.group);
    }
    p.core.visible = p.atmo.visible = !router;
    if (!p.station) return;
    const st = p.station;
    st.group.visible = router;
    if (!router) return;
    st.ringMat.color.set(dead ? '#4a4946' : '#6f8196');
    st.ringBase = new THREE.Color(dead ? '#000000' : osStyle(h.distro?.id).pal[2]);
    st.dead = dead;
  }

  updateSatellites(p, h) {
    this.updateMoons(p, h);
    this.updatePortMoons(p, h);
    // Docker containers are moons; a hidden aggregate "services" entry only feeds the detail card.
    const sats = h.satellites.filter((s) => s.type !== 'docker' && !s.hidden);
    const base = p.portList.length ? 3.0 : p.moonList.length ? 2.55 : 2.05;
    const key = sats.map((s) => s.type + ':' + (s.name || '') + (s.important ? '!' : '')).join(',') + '@' + base;
    if (p.satKey !== key) {
      p.satKey = key;
      for (const s of p.satList) {
        s.label.element.remove();
        p.sats.remove(s.pivot);
      }
      p.satList = sats.map((sat, i) => this.createSatellite(p, sat, i, base));
    }
    sats.forEach((sat, i) => {
      const s = p.satList[i];
      s.data = sat;
      const c = SAT_STATUS[sat.status] || SAT_STATUS.nodata;
      s.glow.material.color.set(sat.status === 'ok' ? '#3fd13f' : c.color);
      s.el.innerHTML = `<span class="sat-ic">${SAT_ICON[sat.type] || '•'}</span>${esc(sat.label)}${sat.summary ? ` <b>${esc(sat.summary)}</b>` : ''}`;
      s.el.style.setProperty('--c', c.color);
    });
  }

  updateMoons(p, h) {
    const dock = h.satellites.find((s) => s.type === 'docker');
    const list = dock?.containers || [];
    const key = list.map((c) => c.name).join('|');
    if (p.moonKey !== key) {
      p.moonKey = key;
      for (const m of p.moonList) {
        m.label.element.remove();
        p.moons.remove(m.pivot);
      }
      p.moonList = list.map((c, i) => this.createMoon(p, c, i, list.length));
    }
    list.forEach((c, i) => {
      const m = p.moonList[i];
      m.data = c;
      const pal = c.running ? DOCKER_PAL : DOCKER_OFF;
      const u = m.mesh.material.uniforms;
      u.uA.value.set(pal[0]);
      u.uB.value.set(pal[1]);
      u.uC.value.set(pal[2]);
      u.uRim.value.set(pal[2]);
      u.uDesat.value = c.running ? 0 : 0.4;
      // Size follows memory use, glow follows CPU.
      const mem = c.mem ? Math.min(1, Math.max(0, Math.log2(c.mem / 2 ** 24) / 6)) : 0;
      m.mesh.scale.setScalar(0.07 + mem * 0.07);
      m.glow.material.color.set(c.running ? '#2496ed' : '#d03b3b');
      m.glowBase = c.running ? 0.18 + Math.min(1, (c.cpu || 0) / 40) * 0.7 : 0.45;
      m.speed = c.running ? m.baseSpeed : 0.02;
      m.el.innerHTML = `<span class="sat-ic">🐳</span>${esc(c.name)} <b>${c.running ? (c.cpu == null ? '' : pct(c.cpu, 1)) : esc(c.status || 'stopped')}</b>${c.mem ? ` <span>${bytes(c.mem)}</span>` : ''}`;
      m.el.style.setProperty('--c', c.running ? '#2496ed' : '#d03b3b');
    });
  }

  // LAN ports of a router: moons on their own orbits, linked to the planet by a traffic lane.
  // Router ports: segments around the station. An active port hangs on its traffic lanes
  // (blue in, orange out, as before) and blinks green; an inactive one sits on a translucent
  // strut and blinks red; unknown status keeps the strut and a steady grey light.
  updatePortMoons(p, h) {
    const sat = h.satellites.find((s) => s.type === 'ports');
    const list = p.station ? sat?.ports || [] : [];
    const key = list.map((x) => x.name).join('|');
    if (p.portKey !== key) {
      p.portKey = key;
      for (const m of p.portList) {
        m.label.element.remove();
        m.holder.parent?.remove(m.holder);
        p.body.remove(m.laneIn, m.laneOut, m.pin, m.pout);
      }
      p.portList = list.map((x, i) => this.createPortMoon(p, x, i, list.length));
    }
    const dead = lifeless(h);
    list.forEach((x, i) => {
      const m = p.portList[i];
      m.data = x;
      const tin = x.up ? x.in || 0 : 0, tout = x.up ? x.out || 0 : 0;
      const lvl = portLevel(tin + tout);
      m.level = lvl;
      m.lin = portLevel(tin);
      m.lout = portLevel(tout);
      m.state = dead || x.up == null ? 'none' : x.up ? 'up' : 'down';
      m.active = m.state === 'up' && lvl > 0;
      m.strut.visible = !m.active;
      m.light.material.color.set(PORT_LIGHT[m.state]).multiplyScalar(m.state === 'none' ? 1 : 2);
      m.shellMat.emissive.set(m.active ? '#7fd0f0' : '#000000');
      m.shellMat.emissiveIntensity = m.active ? 0.15 + 0.4 * lvl : 0;
      m.stripMat.emissive.set(m.state === 'up' ? '#7fd0f0' : m.state === 'down' ? '#5a1410' : '#222222');
      m.glowBase = m.active ? 0.1 + 0.5 * lvl : 0;
      m.rin = tin > 0 ? 0.025 + 0.07 * m.lin : 0;   // lane radius per direction (planet units)
      m.rout = tout > 0 ? 0.025 + 0.07 * m.lout : 0;
      m.flowSpeed = 0.2 + 0.7 * lvl;
      m.nin = tin > 0 ? Math.round(2 + m.lin * 12) : 0;
      m.nout = tout > 0 ? Math.round(2 + m.lout * 12) : 0;
      m.pin.geometry.setDrawRange(0, m.nin);
      m.pout.geometry.setDrawRange(0, m.nout);
      m.pin.material.size = 5 + 5 * m.lin;   // screen pixels
      m.pout.material.size = 5 + 5 * m.lout;
      const state = x.up == null ? tr('нет данных', 'no data') : x.up ? '' : tr('отключён', 'down');
      const client = x.clients?.length ? ` → ${esc(x.clients.join(', '))}` : '';
      if (x.virtual) {
        m.el.innerHTML = `<span class="sat-ic">⇄</span>${tr('порт не указан', 'port not set')}${client}`;
      } else {
        const name = `${x.guess ? '≈' : ''}${esc(x.name)}`;
        m.el.innerHTML = `<span class="sat-ic">⇄</span>${name}${client} <b>${state || `↓${bpsShort(x.in)} ↑${bpsShort(x.out)}`}</b>${x.speed ? ` <span>${bps(x.speed)}</span>` : ''}`;
      }
      m.el.style.setProperty('--c', m.state === 'up' ? '#7fd0f0' : m.state === 'down' ? '#ff6b5e' : '#6b6a64');
    });
  }

  createPortMoon(p, x, i, n) {
    const rnd = seeded('port' + p.id + x.name);
    const st = p.station;
    const holder = new THREE.Group(); // one spoke direction per port, evenly spread
    holder.rotation.y = -(i / Math.max(1, n)) * Math.PI * 2 - Math.PI / 2;
    st.pods.add(holder);
    const mats = [];
    const mat = (opts) => {
      const mt = new THREE.MeshStandardMaterial({ transparent: true, ...opts });
      mats.push(mt);
      return mt;
    };
    const shellMat = mat({ color: '#8797ab', metalness: 0.8, roughness: 0.3, emissive: new THREE.Color('#000000') });
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.2, 0.36), shellMat);
    mesh.position.x = POD_R;
    mesh.userData = { hostId: p.id, sat: 'ports', port: x.name, moonKey: p.id + '#' + x.name };
    holder.add(mesh);
    const stripMat = mat({ color: '#1a2230', metalness: 0.3, roughness: 0.6, emissive: new THREE.Color('#7fd0f0'), emissiveIntensity: 0.9 });
    const strip = new THREE.Mesh(new THREE.BoxGeometry(0.31, 0.045, 0.37), stripMat); // window band
    mesh.add(strip);
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.1, 0.03, 16), mat({ color: '#c9d3df', metalness: 0.9, roughness: 0.25 }));
    cap.position.y = 0.115;
    mesh.add(cap);
    const light = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.glow, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0 }));
    light.position.y = 0.2;
    light.scale.setScalar(0.42);
    mesh.add(light);
    const hit = new THREE.Mesh(new THREE.SphereGeometry(0.4, 8, 6), new THREE.MeshBasicMaterial({ visible: false }));
    hit.userData = mesh.userData;
    mesh.add(hit);
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.glow, color: '#7fd0f0', blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0 }));
    glow.scale.setScalar(1.2);
    mesh.add(glow);
    // Translucent strut from the docking ring to the segment (shown when the port carries no traffic).
    const r0 = 1.08, r1 = POD_R - 0.15;
    const strut = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, r1 - r0, 8),
      new THREE.MeshStandardMaterial({ color: '#a9c4dc', metalness: 0.5, roughness: 0.3, transparent: true, opacity: 0.45, depthWrite: false }));
    strut.rotation.z = Math.PI / 2;
    strut.position.x = (r0 + r1) / 2;
    holder.add(strut);
    const el = document.createElement('div');
    el.className = 'sat-label moon-label hidden';
    el.addEventListener('click', (e) => { e.stopPropagation(); this.onSelect(p.id, { section: 'ports' }); });
    const label = new CSS2DObject(el);
    label.center.set(0.5, 1.6);
    mesh.add(label);
    // Two parallel lanes station <-> segment, like the flows to the core: blue = received on the
    // port (towards the router), orange = sent. Bright (above the bloom threshold) unit cylinders
    // re-aimed every frame, plus particles with a fixed on-screen size so they read from afar.
    const mkLane = (color) => new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 1, 10, 1, true),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(color).multiplyScalar(1.6), transparent: true, opacity: 0,
        blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false }));
    const mkPts = (color) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(16 * 3), 3));
      const pts = new THREE.Points(g, new THREE.PointsMaterial({ color: new THREE.Color(color).multiplyScalar(2.2), map: this.glow, size: 6,
        sizeAttenuation: false, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false }));
      pts.frustumCulled = false;
      return pts;
    };
    const laneIn = mkLane(NET.in.color), laneOut = mkLane(NET.out.color);
    const pin = mkPts(NET.in.color), pout = mkPts(NET.out.color);
    laneIn.visible = laneOut.visible = pin.visible = pout.visible = false; // shown once there is traffic
    p.body.add(laneIn, laneOut, pin, pout);
    const phases = Array.from({ length: 16 }, () => rnd());
    return { holder, mesh, hit, glow, light, strut, shellMat, stripMat, mats, el, label, laneIn, laneOut, pin, pout, phases,
      key: p.id + '#' + x.name, blinkPhase: rnd() * 6, state: 'none', active: false,
      level: 0, lin: 0, lout: 0, glowBase: 0, flowSpeed: 0.2, nin: 0, nout: 0, data: x };
  }

  animatePortMoons(p, t, moonLabels) {
    if (!p.portList.length) return;
    const on = p.group.visible && !!p.station?.group.visible;
    const b = new THREE.Vector3(), dir = new THREE.Vector3(), tmp = new THREE.Vector3(), tmp2 = new THREE.Vector3(), side = new THREE.Vector3();
    const up = new THREE.Vector3(0, 1, 0);
    for (const m of p.portList) {
      for (const mt of m.mats) mt.opacity = p.dim;
      m.strut.material.opacity = 0.45 * p.dim;
      m.glow.material.opacity = m.glowBase * p.dim;
      // Green: calm blink; red: fast blink; grey: steady and dim.
      const blink = m.state === 'up' ? (Math.sin(t * 2.4 + m.blinkPhase) > -0.2 ? 1 : 0.2)
        : m.state === 'down' ? (Math.sin(t * 7 + m.blinkPhase) > 0 ? 1 : 0.15) : 0.35;
      m.light.material.opacity = blink * p.dim;
      const show = moonLabels || this.hoverMoon === m.key;
      if (show !== m.shown) { m.shown = show; m.el.classList.toggle('hidden', !show); m.label.visible = show; }
      const active = on && m.active;
      m.laneIn.visible = m.pin.visible = active && m.rin > 0;
      m.laneOut.visible = m.pout.visible = active && m.rout > 0;
      if (!active) continue;
      // Segment position in the planet body's frame; lanes run from the docking ring to the
      // segment, side by side (offset perpendicular to the lane and the vertical axis).
      m.mesh.updateWorldMatrix(true, false);
      p.body.worldToLocal(m.mesh.getWorldPosition(b));
      dir.copy(b).normalize();
      side.crossVectors(dir, up);
      if (side.lengthSq() < 1e-6) side.set(1, 0, 0);
      side.normalize().multiplyScalar(Math.max(m.rin, m.rout) + 0.03);
      const lane = (mesh, r, sgn) => {
        const a0 = tmp.copy(dir).multiplyScalar(1.08).addScaledVector(side, sgn);
        const a1 = tmp2.copy(b).addScaledVector(dir, -0.16).addScaledVector(side, sgn);
        mesh.position.copy(a0).add(a1).multiplyScalar(0.5);
        mesh.quaternion.setFromUnitVectors(up, dir);
        mesh.scale.set(r, a0.distanceTo(a1), r);
        mesh.material.opacity = (0.35 + 0.5 * m.level) * p.dim;
        return [a0.clone(), a1.clone()];
      };
      const [inA, inB] = m.rin > 0 ? lane(m.laneIn, m.rin, 1) : [null, null];
      const [outA, outB] = m.rout > 0 ? lane(m.laneOut, m.rout, -1) : [null, null];
      const fill = (pts, n, a0, a1, inbound) => {
        if (!a0) return;
        const arr = pts.geometry.attributes.position.array;
        for (let i = 0; i < n; i++) {
          let s = (m.phases[i] + t * m.flowSpeed) % 1;
          if (inbound) s = 1 - s; // received on the port: from the segment into the router
          arr[i * 3] = a0.x + (a1.x - a0.x) * s;
          arr[i * 3 + 1] = a0.y + (a1.y - a0.y) * s;
          arr[i * 3 + 2] = a0.z + (a1.z - a0.z) * s;
        }
        pts.geometry.attributes.position.needsUpdate = true;
        pts.material.opacity = p.dim;
      };
      fill(m.pin, m.nin, inA, inB, true);
      fill(m.pout, m.nout, outA, outB, false);
    }
  }

  createMoon(p, c, i, n) {
    const rnd = seeded('moon' + p.id + c.name);
    const pivot = new THREE.Group();
    pivot.rotation.x = (rnd() - 0.5) * 0.35;
    pivot.rotation.z = (rnd() - 0.5) * 0.35;
    const orbitR = 1.5 + (i % 3) * 0.2 + rnd() * 0.08;
    const holder = new THREE.Group();
    pivot.add(holder);
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(1, 20, 14),
      new THREE.ShaderMaterial({ uniforms: planetUniforms(DOCKER_PAL, rnd() * 50, 3), vertexShader: PLANET_VS, fragmentShader: PLANET_FS }));
    mesh.position.x = orbitR;
    mesh.userData = { hostId: p.id, sat: 'docker', container: c.name, moonKey: p.id + '/' + c.name };
    holder.add(mesh);
    // Moons are tiny and moving: pick them with a generous invisible sphere.
    const hit = new THREE.Mesh(new THREE.SphereGeometry(2.6, 8, 6), new THREE.MeshBasicMaterial({ visible: false }));
    hit.userData = mesh.userData;
    mesh.add(hit);
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.glow, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
    glow.scale.setScalar(5.5);
    mesh.add(glow);
    const el = document.createElement('div');
    el.className = 'sat-label moon-label hidden';
    el.addEventListener('click', (e) => { e.stopPropagation(); this.onSelect(p.id, { section: 'docker', container: c.name }); });
    const label = new CSS2DObject(el);
    label.center.set(0.5, 1.3);
    mesh.add(label);
    p.moons.add(pivot);
    const baseSpeed = (0.9 / orbitR) * (0.8 + rnd() * 0.4);
    return { pivot, holder, mesh, hit, glow, el, label, key: p.id + '/' + c.name, baseSpeed, speed: baseSpeed,
      phase: (i / Math.max(1, n)) * Math.PI * 2 + rnd() * 0.4, glowBase: 0.3, data: c };
  }

  createSatellite(p, sat, i, base) {
    const rnd = seeded('sat' + p.id + sat.type + (sat.name || ''));
    const pivot = new THREE.Group();
    pivot.rotation.x = (rnd() - 0.5) * 1.0;
    pivot.rotation.z = (rnd() - 0.5) * 1.0;
    const orbitR = base + i * 0.42;
    const orbit = new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(Array.from({ length: 96 }, (_, k) => {
        const a = (k / 96) * Math.PI * 2;
        return new THREE.Vector3(Math.cos(a) * orbitR, 0, Math.sin(a) * orbitR);
      })),
      new THREE.LineBasicMaterial({ color: '#9fb4ff', transparent: true, opacity: 0.12, depthWrite: false }));
    pivot.add(orbit);
    const holder = new THREE.Group();
    pivot.add(holder);
    // Invisible hit sphere carries the craft, the status beacon and the label.
    const moon = new THREE.Mesh(new THREE.SphereGeometry(0.24, 12, 8), new THREE.MeshBasicMaterial({ visible: false }));
    moon.position.x = orbitR;
    moon.userData.hostId = p.id;
    moon.userData.sat = sat.type;
    holder.add(moon);
    const craft = buildCraft(sat.type, !!sat.important);
    // The host's main service is the flagship; important OS services are slightly larger stations.
    craft.group.scale.setScalar(sat.primary ? 3.1 : sat.important ? 2.6 : 2.1);
    moon.add(craft.group);
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.glow, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
    glow.scale.setScalar(0.22);
    glow.position.set(0, 0.17, -0.04);
    moon.add(glow);
    const el = document.createElement('div');
    el.className = 'sat-label';
    el.addEventListener('click', (e) => { e.stopPropagation(); this.onSelect(p.id, { section: sat.type }); });
    const label = new CSS2DObject(el);
    label.position.set(0, 0.32, 0);
    moon.add(label);
    p.sats.add(pivot);
    return { pivot, holder, moon, glow, orbit, el, label, craft, spin: (rnd() - 0.5) * 0.8,
      speed: (0.25 + rnd() * 0.25) / (1 + i * 0.35), phase: rnd() * Math.PI * 2, data: sat };
  }

  // ------------------------------------------------------------------ settings & filtering

  applySettings(s) {
    this.settings = { ...s };
    this.controls.autoRotate = !!s.autoRotate;
    this.bloom.enabled = s.bloom !== false;
    this.dust.visible = s.dust !== false;
    for (const p of this.planets.values()) {
      const h = p.host;
      p.targetRadius = planetRadius(h, s.sizeBy, { net: this.netRange() });
      p.visible = h.state !== 'disabled' && this.matches(h, s);
      const mode = s.metrics || 'card';
      p.rings.visible = (mode === 'rings' || mode === 'both') && !lifeless(h);
      p.el.classList.toggle('no-card', !(mode === 'card' || mode === 'both'));
      p.sats.visible = s.satellites !== false && !lifeless(h); // nothing orbits a lifeless planet
      p.moons.visible = s.satellites !== false && !lifeless(h);
    }
    this.refreshFocus();
  }

  matches(h, s) {
    if (s.groups && s.groups.length && !h.groups.some((g) => s.groups.includes(g))) return false;
    if (s.onlyProblems && !(h.problems.worst >= 2 || h.state === 'unreachable')) return false;
    if (s.search) {
      const q = s.search.toLowerCase();
      const hay = [h.name, h.host, h.ip, ...h.groups, ...h.satellites.map((x) => x.label)].join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }

  refreshFocus() {
    const sel = this.selected;
    for (const p of this.planets.values()) {
      const focus = !sel || sel === p.id;
      p.targetDim = !p.visible ? 0 : focus ? 1 : 0.28;
      p.group.visible = p.targetDim > 0 || p.dim > 0.01;
      const showLabel = p.visible && (sel === p.id || this.hovered === p.id ||
        (this.settings.labels === 'all' && !lifeless(p.host))); // lifeless planets: label on hover only
      p.el.classList.toggle('hidden', !showLabel);
      p.el.classList.toggle('faded', !!sel && sel !== p.id);
      p.el.classList.toggle('active', sel === p.id);
      p.userEl.classList.toggle('hidden', !p.visible || !p.hasUsers || this.settings.users === false || (!!sel && sel !== p.id));
      const satLabels = p.visible && (sel === p.id || this.hovered === p.id || this.settings.satLabels === 'all');
      for (const s of p.satList) {
        const show = satLabels && !(!!sel && sel !== p.id);
        s.el.classList.toggle('hidden', !show);
        s.label.visible = show; // hidden labels skip the per-frame CSS2D pass
      }
    }
  }

  // ------------------------------------------------------------------ selection & camera

  select(id) {
    this.selected = id;
    this.refreshFocus();
    const c = this.controls;
    if (id && this.planets.has(id)) {
      const p = this.planets.get(id);
      const target = p.group.position.clone();
      const dir = this.camera.position.clone().sub(c.target).normalize();
      if (dir.y < 0.15) { dir.y = 0.25; dir.normalize(); }
      const dist = Math.max(15, p.targetRadius * 11); // whole system (moons, satellites) fits between the card columns
      this.fly(target, target.clone().add(dir.multiplyScalar(dist)));
      c.autoRotateSpeed = 0.6;
    } else {
      this.fly(this.home.target.clone(), this.home.pos.clone());
      c.autoRotateSpeed = 0.35;
    }
  }

  fly(target, pos) {
    this.flight = {
      start: performance.now(), fromT: this.controls.target.clone(), fromP: this.camera.position.clone(), toT: target, toP: pos,
    };
  }

  // ------------------------------------------------------------------ event effects

  flash(id, color, startMs, durMs) {
    const p = this.planets.get(id);
    if (p) p.flashes.push({ color: new THREE.Color(color), start: startMs, dur: durMs });
  }

  setAlarm(id, on) {
    const p = this.planets.get(id);
    if (p) p.alarm = on;
  }

  animateAlerts(p, t, nowMs) {
    p.flashes = p.flashes.filter((f) => nowMs - f.start < f.dur);
    let best = 0, frac = 0, color = null;
    for (const f of p.flashes) {
      const left = 1 - (nowMs - f.start) / f.dur;
      const k = Math.pow(left, 1.2);
      if (k > best) { best = k; frac = left; color = f.color; }
    }
    // Keep alerts readable even when another host is focused.
    const vis = p.visible ? Math.min(1, p.dim + 0.6) : 0;
    const g = p.alertGlow, ring = p.alertRing, ru = ring.material.uniforms;
    ru.uTime.value = t;
    ring.quaternion.copy(this.camera.quaternion); // billboard
    ring.scale.setScalar(Math.max(1.15, 2.6 / p.radius)); // small planets still get a readable ring
    let state = '', css = null;
    if (p.alarm) {
      const pulse = 0.5 + 0.5 * Math.sin(t * 4.2);
      g.material.color.setRGB(1, 0.16, 0.12);
      g.material.opacity = (0.6 + 0.4 * pulse) * vis;
      g.scale.setScalar(6 + pulse * 2.5);
      p.uniforms.uAlarm.value = (0.15 + 0.3 * pulse) * vis;
      ring.visible = vis > 0;
      ru.uColor.value.setRGB(1, 0.2, 0.15);
      ru.uFrac.value = 1;
      ru.uRot.value = t * 1.6;
      ru.uAlpha.value = (0.6 + 0.4 * pulse) * vis;
      state = 'alarming';
      css = '#ff3b30';
    } else if (best > 0.001) {
      g.material.color.copy(color);
      // Strong initial flare (with a short throb in the first seconds), then a slow fade.
      const throb = frac > 0.97 ? 0.3 * Math.sin(t * 10) : 0;
      g.material.opacity = Math.min(1, best * 1.15 + throb) * vis;
      g.scale.setScalar(5 + best * 4.5);
      p.uniforms.uAlarm.value = 0;
      ring.visible = vis > 0;
      ru.uColor.value.copy(color);
      ru.uFrac.value = frac;          // shrinks as the 5 minutes run out
      ru.uRot.value = t * 0.9;        // and keeps rotating
      ru.uAlpha.value = (0.35 + 0.65 * best) * vis;
      state = 'alerting';
      css = `#${color.getHexString()}`;
    } else {
      g.material.opacity = 0;
      p.uniforms.uAlarm.value = 0;
      ring.visible = false;
    }
    if (state !== p.alertState) {
      p.alertState = state;
      p.el.classList.toggle('alerting', state === 'alerting');
      p.el.classList.toggle('alarming', state === 'alarming');
    }
    if (css) {
      p.el.style.setProperty('--ac', css);
      p.el.style.setProperty('--ak', state === 'alarming' ? 1 : best.toFixed(3));
    }
  }

  setViewOffset(px) {
    this.viewOffsetTarget = px;
  }

  // ------------------------------------------------------------------ events

  bindEvents() {
    const el = this.renderer.domElement;
    let down = null;
    el.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY }; });
    el.addEventListener('pointerup', (e) => {
      if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) return;
      const hit = this.pick(e);
      // Moons and satellites move: a click that just misses one still counts as
      // hitting it; a click on truly empty space returns to the whole galaxy.
      const target = hit || this.pickNear(e, 30);
      if (target) this.onSelect(target.hostId, target.sat ? { section: target.sat, container: target.container } : undefined);
      else if (this.selected) this.onSelect(null);
    });
    el.addEventListener('pointermove', (e) => {
      this.pointerPx = { x: e.clientX, y: e.clientY };
      this.pointerEvent = e;
      this.pointerDirty = true;
    });
    el.addEventListener('pointerleave', () => { this.setHover(null); });
  }

  pick(e) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const targets = [];
    for (const p of this.planets.values()) {
      if (!p.visible || !p.group.visible) continue;
      targets.push(p.hit);
      if (p.sats.visible) for (const s of p.satList) targets.push(s.moon);
      if (p.moons.visible) for (const m of p.moonList) targets.push(m.hit);
      for (const m of p.portList) targets.push(m.hit);
    }
    const hits = this.raycaster.intersectObjects(targets, false);
    if (!hits.length) return null;
    // Prefer satellites when they are hit (they sit in front of the hit sphere).
    const sat = hits.find((x) => x.object.userData.sat);
    const o = (sat || hits[0]).object;
    return { hostId: o.userData.hostId, sat: o.userData.sat, container: o.userData.container, moonKey: o.userData.moonKey };
  }

  // Nearest moon / satellite / planet centre within `px` screen pixels.
  pickNear(e, px) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const v = new THREE.Vector3();
    let best = null, bestD = px;
    const test = (obj, data) => {
      obj.getWorldPosition(v).project(this.camera);
      if (v.z > 1) return;
      const x = ((v.x + 1) / 2) * rect.width + rect.left, y = ((1 - v.y) / 2) * rect.height + rect.top;
      const d = Math.hypot(x - e.clientX, y - e.clientY);
      if (d < bestD) { bestD = d; best = data; }
    };
    for (const p of this.planets.values()) {
      if (!p.visible || !p.group.visible) continue;
      if (p.moons.visible) for (const m of p.moonList) test(m.mesh, m.mesh.userData);
      for (const m of p.portList) test(m.mesh, m.mesh.userData);
      if (p.sats.visible) for (const s of p.satList) test(s.moon, s.moon.userData);
    }
    return best;
  }

  setHover(id) {
    if (this.hovered === id) return;
    this.hovered = id;
    this.renderer.domElement.style.cursor = id ? 'pointer' : '';
    this.refreshFocus();
    this.onHover(id ? this.planets.get(id).host : null, this.pointerPx);
  }

  // ------------------------------------------------------------------ loop

  resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    this.width = w; this.height = h;
    this.renderer.setSize(w, h);
    this.composer.setSize(w, h);
    this.labelRenderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.applyViewOffset();
    const px = this.renderer.getPixelRatio();
    this.starUniforms.uPixel.value = px;
    this.dustUniforms.uPixel.value = px;
  }

  applyViewOffset() {
    const w = this.width, h = this.height;
    if (!w) return;
    if (Math.abs(this.viewOffset) < 0.5) this.camera.clearViewOffset();
    else this.camera.setViewOffset(w, h, this.viewOffset, 0, w, h);
    this.camera.updateProjectionMatrix();
  }

  frame() {
    const dt = Math.min(this.clock.getDelta(), 0.2); // low frame caps keep real-time speed
    const t = this.clock.elapsedTime;
    this.starUniforms.uTime.value = t;
    this.dustUniforms.uTime.value = t;
    this.dust.rotation.y += dt * 0.006;
    const nowMs = Date.now();
    this.animateFlows(t);

    if (Math.abs(this.viewOffset - this.viewOffsetTarget) > 0.5) {
      this.viewOffset += (this.viewOffsetTarget - this.viewOffset) * Math.min(1, dt * 6);
      this.applyViewOffset();
    }

    if (this.flight) {
      const f = this.flight;
      f.t = Math.min(1, (performance.now() - f.start) / 1400);
      const k = f.t < 0.5 ? 4 * f.t ** 3 : 1 - (-2 * f.t + 2) ** 3 / 2;
      this.controls.target.lerpVectors(f.fromT, f.toT, k);
      this.camera.position.lerpVectors(f.fromP, f.toP, k);
      if (f.t >= 1) this.flight = null;
    }
    this.controls.update(dt); // dt keeps auto-rotation speed independent of the frame cap

    // Planets glide to new positions (state layout changes); the camera follows the focused one.
    this.planetsMoving = false;
    for (const p of this.planets.values()) {
      if (!p.target) continue;
      const d = p.target.distanceTo(p.group.position);
      if (d < 0.02) continue;
      this.planetsMoving = true;
      const before = p.group.position.clone();
      p.group.position.lerp(p.target, Math.min(1, dt * 1.2));
      if (this.selected === p.id && !this.flight) {
        const delta = p.group.position.clone().sub(before);
        this.camera.position.add(delta);
        this.controls.target.add(delta);
      }
    }
    const belts = this.settings.layout !== 'free' && this.settings.tierRings !== false;
    if (belts !== this.tierRings.visible) {
      this.tierRings.visible = belts;
      for (const el of this.tierLabels) el.classList.toggle('hidden', !belts);
    }

    for (const p of this.planets.values()) {
      p.radius += (p.targetRadius - p.radius) * Math.min(1, dt * 4);
      p.body.scale.setScalar(p.radius);
      p.dim += (p.targetDim - p.dim) * Math.min(1, dt * 5);
      p.group.visible = p.dim > 0.01;
      p.uniforms.uDim.value = p.dim;
      p.uniforms.uTime.value = t;
      p.uniforms.uHover.value += ((this.hovered === p.id ? 1 : 0) - p.uniforms.uHover.value) * Math.min(1, dt * 8);
      p.core.rotation.y += dt * p.spin;
      if (p.station?.group.visible) {
        const st = p.station;
        st.ringPivot.rotation.y = t * 0.25;
        for (const m of st.mats) m.opacity = p.dim;
        // Alarm tints the ring red like the planet shader does for the hull.
        st.ringMat.emissive.copy(st.ringBase).lerp(ALARM_RED, Math.min(1, p.uniforms.uAlarm.value * 2));
        st.beacon.material.opacity = st.dead ? 0 : (Math.sin(t * 3 + p.spin * 40) > 0.6 ? 1 : 0.1) * p.dim;
      }
      p.atmoU.uStrength.value = p.atmoBase * p.dim;
      p.label.position.set(0, p.radius * 1.3 + 0.3, 0);
      p.userLabel.position.set(0, -(p.radius * 1.08 + 0.15), 0);
      const pulse = p.haloBase ? p.haloBase * (0.75 + 0.35 * Math.sin(t * 2.6 + p.spin * 40)) : 0;
      p.halo.material.opacity = pulse * p.dim;
      this.animateAlerts(p, t, nowMs);
      for (const s of p.satList) {
        s.holder.rotation.y = s.phase + t * s.speed;
        s.craft.group.rotation.z = t * s.spin;
        if (s.craft.spinPart) s.craft.spinPart.rotation.z = t * 0.9; // station habitat ring
        for (const m of s.craft.mats) m.opacity = p.dim;
        // Beacon: slow calm blink when OK, fast bright blink on problems.
        const bad = s.data.status !== 'ok';
        const on = Math.sin(t * (bad ? 9 : 2.4) + s.phase) > (bad ? 0 : 0.7);
        s.glow.material.opacity = (on ? (bad ? 1 : 0.8) : 0.12) * p.dim;
        s.glow.scale.setScalar((bad ? 0.32 : 0.22) * (s.data.primary ? 1.5 : 1));
        s.orbit.material.opacity = 0.12 * p.dim;
      }
      for (const m of RING_ORDER) p.ringParts[m].arc.material.opacity = 0.95 * p.dim;
      const moonLabels = p.visible && this.settings.satLabels === 'all' && this.selected === p.id;
      this.animatePortMoons(p, t, moonLabels);
      for (const m of p.moonList) {
        m.holder.rotation.y = m.phase + t * m.speed;
        m.mesh.material.uniforms.uDim.value = p.dim;
        m.mesh.material.uniforms.uTime.value = t;
        m.glow.material.opacity = m.glowBase * p.dim;
        const show = moonLabels || this.hoverMoon === m.key;
        if (show !== m.shown) { m.shown = show; m.el.classList.toggle('hidden', !show); m.label.visible = show; }
        if (show) this.orbitLabels = true;
      }
    }

    if (this.pointerDirty && this.pointerEvent && !this.flight) {
      this.pointerDirty = false;
      const hit = this.pick(this.pointerEvent);
      this.hoverMoon = hit?.moonKey || null;
      this.setHover(hit ? hit.hostId : null);
      if (hit) this.onHover(this.planets.get(hit.hostId).host, this.pointerPx);
    }

    this.composer.render();
    // HTML labels: re-positioned only when the camera or planets moved, or orbiting labels
    // (satellites / moons of the focused host) are shown; otherwise a few times a second.
    this.frameNo += 1;
    const satLabelsShown = [...this.planets.values()].some((p) => p.satList.some((s) => s.label.visible && p.group.visible));
    if (this.cameraMoved || this.planetsMoving || this.orbitLabels || satLabelsShown || this.frameNo % 8 === 0) {
      this.labelRenderer.render(this.scene, this.camera);
    }
    this.cameraMoved = false;
    this.orbitLabels = false;
    if (t - (this.lastDeclutter || 0) > 0.25) {
      this.lastDeclutter = t;
      this.declutter();
    }
  }

  // Screen-space label collision: lower-priority cards first shrink to the
  // name line, then hide if they still overlap a more important one.
  declutter() {
    const camPos = this.camera.position;
    const items = [];
    for (const p of this.planets.values()) {
      const el = p.el;
      el.classList.remove('compact', 'clutter');
      if (!p.visible || el.classList.contains('hidden') || !p.group.visible) continue;
      const h = p.host;
      let prio = h.state === 'disabled' ? 0 : h.state === 'unreachable' ? 7 : 3 + Math.max(0, h.problems.worst);
      if (this.hovered === p.id) prio += 100;
      if (this.selected === p.id) prio += 200;
      items.push({ p, el, prio, dist: p.group.position.distanceTo(camPos) });
    }
    items.sort((a, b) => b.prio - a.prio || a.dist - b.dist);
    const placed = [];
    const hit = (r) => placed.some((q) => r.left < q.right + 4 && r.right > q.left - 4 && r.top < q.bottom + 2 && r.bottom > q.top - 2);
    for (const it of items) {
      let r = it.el.getBoundingClientRect();
      if (hit(r)) {
        it.el.classList.add('compact');
        r = it.el.getBoundingClientRect();
        if (hit(r)) {
          it.el.classList.add('clutter');
          continue;
        }
      }
      placed.push(r);
    }
  }
}
