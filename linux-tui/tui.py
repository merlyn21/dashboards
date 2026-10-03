#!/usr/bin/env python3
"""Connections of a Linux router or server in the terminal: who talks to whom, scans, ports, logins.

  sudo ./tui.py                             collect on this machine (root: conntrack, iptables, process names)
  ./tui.py --url http://127.0.0.1:8787      show what a running linux-api map service collects

Standard library only (curses). Keys: left/right or 1-6 - views (connections, services, logins, scans, VPN, countries); g grouping; s sorting; d direction; c categories;
/ filter; f ticker filter; Enter details; p pause; q quit. Settings come from .env (see README.md).
"""
import argparse
import collections
import curses
import json
import locale
import logging
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

# Category: label, 256-color, 8-color fallback. Same categories and colors as the web map.
CATS = {
    "service": ("Сервисы", 75, curses.COLOR_BLUE),
    "p2p": ("P2P и торрент", 42, curses.COLOR_GREEN),
    "iot": ("Облака умного дома", 214, curses.COLOR_YELLOW),
    "vpn": ("VPN", 205, curses.COLOR_MAGENTA),
    "router": ("Сам роутер", 141, curses.COLOR_MAGENTA),
    "session": ("Подключения снаружи", 209, curses.COLOR_RED),
    "scan": ("Сканы", 227, curses.COLOR_YELLOW),
    "research": ("Исследовательские сканеры", 250, curses.COLOR_WHITE),
    "other": ("Прочее", 246, curses.COLOR_WHITE),
}
UI_COLORS = {"good": (78, curses.COLOR_GREEN), "warn": (214, curses.COLOR_YELLOW), "bad": (203, curses.COLOR_RED),
             "muted": (244, curses.COLOR_WHITE), "head": (81, curses.COLOR_CYAN), "in": (75, curses.COLOR_BLUE),
             "out": (209, curses.COLOR_YELLOW), "sel": (231, curses.COLOR_WHITE)}
GATE_LABEL = {"ssh": "SSH и Telnet", "web": "Веб", "mail": "Почта", "db": "Базы данных", "remote": "RDP и VNC", "sip": "SIP",
              "dns": "DNS", "snmp": "SNMP", "amp": "Усилители", "vpn": "VPN", "icmp": "ICMP", "other": "Прочие порты"}
GROUPS = [("conn", "соединения"), ("dest", "направления"), ("proc", "процессы"), ("country", "страны"), ("source", "источники входящих")]
SORTS = [("rate", "скорость"), ("count", "число"), ("age", "возраст"), ("name", "имя")]
DIRS = [("all", "все"), ("in", "входящие"), ("out", "исходящие"), ("scan", "сканы")]
SPARK = "▁▂▃▄▅▆▇█"
VIEWS = ["conn", "services", "logins", "scans", "vpn", "countries"]
# Country names for the usual suspects; any other country is shown by its two-letter code.
COUNTRIES = {
    "RU": "Россия", "US": "США", "DE": "Германия", "NL": "Нидерланды", "FR": "Франция", "GB": "Великобритания", "CN": "Китай",
    "HK": "Гонконг", "SG": "Сингапур", "JP": "Япония", "KR": "Южная Корея", "IN": "Индия", "BR": "Бразилия", "CA": "Канада",
    "UA": "Украина", "BY": "Беларусь", "KZ": "Казахстан", "AM": "Армения", "GE": "Грузия", "AZ": "Азербайджан", "TR": "Турция",
    "IR": "Иран", "PL": "Польша", "FI": "Финляндия", "SE": "Швеция", "NO": "Норвегия", "DK": "Дания", "CH": "Швейцария",
    "AT": "Австрия", "IT": "Италия", "ES": "Испания", "PT": "Португалия", "IE": "Ирландия", "BE": "Бельгия", "CZ": "Чехия",
    "RO": "Румыния", "BG": "Болгария", "HU": "Венгрия", "RS": "Сербия", "GR": "Греция", "LT": "Литва", "LV": "Латвия",
    "EE": "Эстония", "MD": "Молдова", "IL": "Израиль", "AE": "ОАЭ", "SA": "Саудовская Аравия", "EG": "Египет", "ZA": "ЮАР",
    "NG": "Нигерия", "ET": "Эфиопия", "VN": "Вьетнам", "TH": "Таиланд", "ID": "Индонезия", "MY": "Малайзия", "PH": "Филиппины",
    "TW": "Тайвань", "PK": "Пакистан", "BD": "Бангладеш", "AU": "Австралия", "NZ": "Новая Зеландия", "MX": "Мексика",
    "AR": "Аргентина", "CL": "Чили", "CO": "Колумбия", "UZ": "Узбекистан", "KG": "Киргизия", "SC": "Сейшелы", "LU": "Люксембург",
    "IS": "Исландия", "SK": "Словакия", "SI": "Словения", "HR": "Хорватия", "CY": "Кипр", "PA": "Панама", "MN": "Монголия",
}
HEAT = " ░▒▓█"


# ---------- formatting ----------
def fmt_rate(bps):
    bps = bps or 0
    if bps < 1e3:
        return f"{round(bps)} бит/с"
    if bps < 1e6:
        return f"{round(bps / 1e3)} кбит/с"
    return f"{bps / 1e6:.1f} Мбит/с".replace(".", ",")


def fmt_bytes(b):
    b = b or 0
    if b >= 1e12:
        return f"{b / 1e12:.1f} ТБ".replace(".", ",")
    if b >= 1e9:
        return f"{b / 1e9:.1f} ГБ".replace(".", ",")
    if b >= 1e6:
        return f"{round(b / 1e6)} МБ"
    return f"{round(b / 1e3)} КБ"


def fmt_age(sec):
    if sec is None:
        return ""
    sec = max(0, sec)
    if sec < 90:
        return f"{int(sec)} с"
    if sec < 5400:
        return f"{int(sec // 60)} мин"
    if sec < 172800:
        return f"{int(sec // 3600)} ч"
    return f"{int(sec // 86400)} дн"


def fmt_uptime(sec):
    if sec is None:
        return "—"
    d, h, m = int(sec // 86400), int(sec % 86400 // 3600), int(sec % 3600 // 60)
    return f"{d}д {h}ч" if d else f"{h}ч {m}мин"


def place(g):
    return f"{g['cc']} {g['city']}".strip() if g else "место неизвестно"


def spark(values, width):
    vals = list(values)[-width:]
    top = max(vals) if vals else 0
    if not top:
        return "▁" * len(vals)
    return "".join(SPARK[min(7, int(v / top * 7.999))] for v in vals)


def bar(frac, width):
    frac = max(0.0, min(1.0, frac or 0))
    full = int(frac * width)
    return "█" * full + "░" * (width - full)


def fit(text, width):
    """Cut or pad to exactly `width` characters."""
    text = str(text or "")
    if width <= 0:
        return ""
    if len(text) > width:
        return text[: max(0, width - 1)] + "…"
    return text + " " * (width - len(text))


# ---------- connection labels, as on the web map ----------
def is_scan(c):
    return c["kind"] == "in" and c.get("cat") in ("scan", "research")


def dir_of(c):
    return "out" if c["kind"] != "in" else "scan" if is_scan(c) else "in"


def rate_of(c):
    return (c.get("down") or 0) + (c.get("up") or 0)


def org_of(c):
    return c.get("remote_name") or c.get("service") or (c.get("org") or {}).get("name") or c["remote"]


# ---------- services: listening ports and port forwards ----------
def port_match(spec, port, proto):
    """'2222/tcp', '1000-2000/udp', 'все/tcp' against a connection's destination port and protocol."""
    num, _, pr = spec.partition("/")
    if pr and pr != proto:
        return False
    if num == "все":
        return True
    if "-" in num:
        a, b = num.split("-", 1)
        return a.isdigit() and b.isdigit() and int(a) <= port <= int(b)
    return num.isdigit() and int(num) == port


def ports_label(specs, limit=6):
    """['80/tcp', '443/tcp', '53/udp'] -> '80, 443/tcp · 53/udp': the port comes first, it is what the internet sees."""
    by = collections.OrderedDict()
    for spec in specs:
        num, _, proto = spec.partition("/")
        by.setdefault(proto, []).append(num)
    parts = []
    for proto, nums in by.items():
        parts.append(", ".join(nums[:limit]) + ("…" if len(nums) > limit else "") + (f"/{proto}" if proto else ""))
    return " · ".join(parts)


def owner_label(row):
    """Who opened the port and where it leads: 'sshd', 'docker → zabbix-server (172.18.0.3:10051)'."""
    if not row.get("forward"):
        return row["name"]
    to, name = row.get("to") or "?", row.get("to_name")
    target = f"{name} ({to})" if name else to
    if row["name"].startswith("проброс"):  # a linux-api service: the target is already in the name
        return row["name"]
    return f"{row['name']} → {'контейнер ' if row['name'] == 'docker' else ''}{target}"


def local_label(row):
    """'docker-proxy 1080, 3000, 8088' for a service that listens on loopback only."""
    nums = sorted({spec.split("/")[0] for spec in row["ports"]}, key=lambda x: int(x) if x.isdigit() else 0)
    return f"{row['name']} {', '.join(nums[:8])}{'…' if len(nums) > 8 else ''}"


def service_id(row):
    return row["name"] + "|" + ",".join(row["ports"])


def service_of(rows, c):
    """The ports plate row a connection from the internet came to; 'ping' for ICMP, None if no service listens."""
    if c["proto"] == "icmp":
        return "ping"
    for row in rows:
        if row["status"] == "local":
            continue
        if any(port_match(spec, c["dport"], c["proto"]) for spec in row["ports"]):
            return service_id(row)
    return None


class Labels:
    def __init__(self, model):
        self.m = model

    def self_word(self):
        return "сервер" if self.m.router.get("mode") == "host" else "роутер"

    def source(self, c):
        return f"VPN {c['vpn_client']}" if c.get("vpn_client") else c.get("remote_name") or c["remote"]

    def target(self, c):
        if c.get("forwarded"):
            return c.get("target") or "?"
        return c.get("process") or ("ядро" if c["proto"] == "icmp" else self.self_word())

    def port(self, c):
        return "ping" if c["proto"] == "icmp" else f"{c['dport']}/{c['proto']}"

    def who(self, c):
        return self.source(c) if c["kind"] == "in" else (c.get("device") or "?")

    def what(self, c):
        """Inbound: who takes it on this machine; outbound: the remote end (the organization has its own column)."""
        if c["kind"] == "in":
            return self.target(c)
        return c.get("remote_name") or c.get("service") or c["remote"]

    def tick(self, c, still=False):
        if c["kind"] == "in":
            text = f"{self.source(c)} → {self.target(c)}: {self.port(c)}"
            if is_scan(c):
                text += ", скан" + (f" ({c['ports_tried']} портов за час)" if (c.get("ports_tried") or 1) > 1 else "")
            if c.get("org") and not c.get("vpn_client"):
                text += f" · {c['org']['name']}"
        else:
            text = f"{c.get('device')} → {org_of(c)}{':' + str(c['dport']) if c['dport'] else ''} {c['proto']}"
        if c.get("geo"):
            text += f" · {place(c['geo'])}"
        if still:
            text += f" · {fmt_rate(rate_of(c))}" if rate_of(c) else " · активно"
        return text


# ---------- data sources ----------
class LocalSource:
    """Collect on this machine with the same collector as the map service."""

    def __init__(self, out):
        self.out = out

    def start(self):
        import config
        from collector import Collector
        from geo import Asn, Geo
        from names import Names
        from oui import Vendors
        import sysinfo
        cfg = config.load()
        wan = cfg["wan_interface"] or sysinfo.default_route("-4")[0] or sysinfo.default_route("-6")[0]
        v6 = cfg["ipv6"] == "on" or (cfg["ipv6"] == "auto" and any(
            a.version == 6 and a.ip.is_global for a in sysinfo.addresses().get(wan, {}).get("addrs", [])))
        geo = Geo(os.path.join(HERE, "geo"), v6).load()
        asn = Asn(os.path.join(HERE, "geo"), v6).load()
        names = Names(cfg["names_file"], Vendors(os.path.join(HERE, "oui", "oui.txt")).load())
        self.collector = Collector(cfg, geo, asn, names)
        q = self.collector.subscribe()
        threading.Thread(target=self.collector.run, name="collector", daemon=True).start()
        self.out.put({"type": "state", **self.collector.snapshot()})

        def forward():
            while True:
                self.out.put(q.get())
        threading.Thread(target=forward, name="forward", daemon=True).start()
        return self


class RemoteSource:
    """Read a running linux-api service: a snapshot, then the server-sent events stream."""

    def __init__(self, out, url):
        self.out, self.url = out, url.rstrip("/")

    def start(self):
        threading.Thread(target=self.run, name="remote", daemon=True).start()
        return self

    def run(self):
        backoff = 2
        while True:
            try:
                with urllib.request.urlopen(self.url + "/api/state", timeout=30) as r:
                    self.out.put({"type": "state", **json.load(r)})
                with urllib.request.urlopen(self.url + "/api/events", timeout=90) as r:
                    backoff = 2
                    for raw in r:
                        line = raw.decode("utf-8", "replace").rstrip("\r\n")
                        if line.startswith("data: "):
                            self.out.put(json.loads(line[6:]))
            except (OSError, ValueError, urllib.error.URLError) as e:
                self.out.put({"type": "status", "status": {"ok": False, "error": f"нет связи с {self.url}: {e}"},
                              "next_poll": time.time() + backoff})
            time.sleep(backoff)
            backoff = min(60, backoff * 2)


class LogRing(logging.Handler):
    """Warnings of the collector, for the status line: stderr belongs to curses."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.last = None

    def emit(self, record):
        self.last = (time.time(), record.getMessage())


# ---------- state, as the browser keeps it ----------
class Model:
    def __init__(self):
        self.links = {}
        self.router, self.ports, self.fw_notes, self.tunnels, self.vpn_clients = {}, [], [], [], []
        self.inbound, self.sessions, self.auth = {}, [], {}
        self.status, self.next_poll, self.poll_interval = {}, 0, 30
        self.home = None
        self.geo_source = True
        self.hist = collections.defaultdict(lambda: collections.deque(maxlen=120))
        self.ticks = collections.deque(maxlen=300)
        self.later = []           # (due, conn id, still) ticker lines spread over the poll interval
        self.svc_hist = collections.defaultdict(lambda: collections.deque(maxlen=60))  # service -> traffic per poll
        self.labels = Labels(self)
        self.paused = False
        self.polls = 0

    def common(self, m):
        for key, attr in (("router", "router"), ("ports", "ports"), ("fw_notes", "fw_notes"), ("tunnels", "tunnels"),
                          ("vpn_clients", "vpn_clients"), ("inbound", "inbound"), ("sessions", "sessions"), ("auth", "auth"),
                          ("status", "status"), ("next_poll", "next_poll"), ("poll_interval", "poll_interval"), ("home", "home")):
            if m.get(key) is not None:
                setattr(self, attr, m[key])

    def apply(self, m):
        t = m.get("type")
        if t == "state":
            self.links = {c["id"]: c for c in m.get("conns", [])}
            self.geo_source = m.get("geo_source")
            self.common(m)
        elif t == "poll":
            self.common(m)
            for cid in m.get("removed", []):
                self.links.pop(cid, None)
            for cid, row in m.get("updated", {}).items():
                c = self.links.get(cid)
                if not c:
                    continue
                row = list(row) + [None] * (9 - len(row))
                down, up, state, device, proc, cat, active, tried, vpn = row[:9]
                c.update(down=down, up=up, state=state, device=device, process=proc)
                if cat:
                    c["cat"] = cat
                if len(m["updated"][cid]) > 6:
                    c["active"] = active
                if tried is not None:
                    c["ports_tried"] = tried
                if len(m["updated"][cid]) > 8:
                    c["vpn_client"] = vpn
            added = {c["id"] for c in m.get("added", [])}
            if self.router.get("acct") is not False:
                for cid, c in self.links.items():
                    if cid not in m.get("updated", {}) and cid not in added:
                        c["active"] = False
            now = time.time()
            spread = max(1, self.poll_interval - 2)
            for c in m.get("added", []):
                self.links[c["id"]] = c
                if not m.get("fresh"):
                    self.later.append((now + spread * (hash(c["id"]) % 1000) / 1000, c["id"], False))
            if not m.get("fresh"):
                still = [c for c in self.links.values() if c.get("active") is True and c["id"] not in added and not is_scan(c)]
                still.sort(key=rate_of, reverse=True)
                for c in still[:12]:
                    self.later.append((now + spread * (hash(c["id"] + "s") % 1000) / 1000, c["id"], True))
            self.record_services()
            r = self.router
            self.hist["wan_in"].append(r.get("wan_in") or 0)
            self.hist["wan_out"].append(r.get("wan_out") or 0)
            self.hist["cpu"].append(r.get("cpu") or 0)
            self.polls += 1
        elif t == "delta":
            for c in m.get("added", []):
                self.links[c["id"]] = c
                self.tick(c, False)
            for cid in m.get("removed", []):
                self.links.pop(cid, None)
        else:
            self.common(m)

    def record_services(self):
        """Traffic of real (not scan) inbound connections per service, one point per poll, for the sparklines."""
        rates = collections.Counter()
        for c in self.links.values():
            if c["kind"] == "in" and not is_scan(c):
                rates[service_of(self.ports, c) or "other"] += rate_of(c)
        for sid in {service_id(r) for r in self.ports if r["status"] != "local"} | {"ping", "other"} | set(rates):
            self.svc_hist[sid].append(rates.get(sid, 0))

    def tick(self, c, still):
        self.ticks.appendleft((time.strftime("%H:%M:%S"), dir_of(c), still, c.get("cat", "other"), self.labels.tick(c, still)))

    def due(self):
        now = time.time()
        ready = [x for x in self.later if x[0] <= now]
        if ready:
            self.later = [x for x in self.later if x[0] > now]
            for _, cid, still in sorted(ready):
                c = self.links.get(cid)
                if c:
                    self.tick(c, still)
        return bool(ready)


# ---------- the screen ----------
class App:
    def __init__(self, scr, model, source_q, log_ring, remote):
        self.scr, self.m, self.q, self.log, self.remote = scr, model, source_q, log_ring, remote
        self.view = "conn"
        self.group, self.sort, self.dir, self.tick_dir = 0, 0, 0, 0
        self.hidden = set()
        self.filter = ""
        self.sel, self.top, self.scroll = 0, 0, 0
        self.popup = None          # list of (text, attr) lines
        self.svc_sel, self.svc_blocks = 0, []
        self.c_sel, self.c_top, self.c_rows = 0, 0, []
        self.rows = []
        self.colors()

    # ----- colors -----
    def colors(self):
        self.pairs = {}
        if not curses.has_colors():
            return
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK
        rich = curses.COLORS >= 256
        n = 1
        for name, (_, c256, c8) in CATS.items():
            curses.init_pair(n, c256 if rich else c8, bg)
            self.pairs[name] = n
            n += 1
        for name, (c256, c8) in UI_COLORS.items():
            curses.init_pair(n, c256 if rich else c8, bg)
            self.pairs[name] = n
            n += 1
        curses.init_pair(n, curses.COLOR_BLACK, 81 if rich else curses.COLOR_CYAN)
        self.pairs["cursor"] = n

    def a(self, name, extra=0):
        p = self.pairs.get(name)
        return (curses.color_pair(p) if p else 0) | extra

    def put(self, y, x, text, attr=0, width=None):
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return x
        width = min(width if width is not None else len(text), w - x)
        if width <= 0:
            return x
        try:
            self.scr.addnstr(y, x, text, width, attr)
        except curses.error:
            pass  # the bottom-right cell
        return x + min(len(text), width)

    # ----- main loop -----
    def run(self):
        curses.curs_set(0)
        self.scr.timeout(200)
        last = 0
        while True:
            changed = False
            while True:
                try:
                    msg = self.q.get_nowait()
                except queue.Empty:
                    break
                if not self.m.paused:
                    self.m.apply(msg)
                    changed = True
            changed |= self.m.due()
            if changed or time.time() - last >= 1:
                self.draw()
                last = time.time()
            key = self.scr.getch()
            if key == -1:
                continue
            if self.key(key) is False:
                return

    # ----- keys -----
    def key(self, k):
        if self.popup is not None:
            if k in (27, 10, 13, curses.KEY_ENTER, ord("q"), ord(" ")):
                self.popup = None
            elif k == curses.KEY_DOWN:
                self.popup_top += 1
            elif k == curses.KEY_UP:
                self.popup_top = max(0, self.popup_top - 1)
            self.draw()
            return True
        if k in (ord("q"), ord("Q")):
            return False
        if ord("1") <= k <= ord(str(len(VIEWS))):
            self.view = VIEWS[k - ord("1")]
            self.scroll = 0
        elif k in (curses.KEY_RIGHT, ord("l")):
            self.view = VIEWS[(VIEWS.index(self.view) + 1) % len(VIEWS)]
            self.scroll = 0
        elif k in (curses.KEY_LEFT, ord("h")):
            self.view = VIEWS[(VIEWS.index(self.view) - 1) % len(VIEWS)]
            self.scroll = 0
        elif k == ord("g"):
            self.group = (self.group + 1) % len(GROUPS)
            self.sel = self.top = 0
        elif k == ord("s"):
            self.sort = (self.sort + 1) % len(SORTS)
        elif k == ord("d"):
            self.dir = (self.dir + 1) % len(DIRS)
            self.sel = self.top = 0
        elif k == ord("f"):
            self.tick_dir = (self.tick_dir + 1) % len(DIRS)
        elif k == ord("p"):
            self.m.paused = not self.m.paused
        elif k == ord("c"):
            self.categories()
        elif k == ord("/"):
            self.ask_filter()
        elif k == 27:
            self.filter = ""
        elif k in (curses.KEY_DOWN, ord("j")):
            self.move(1)
        elif k in (curses.KEY_UP, ord("k")):
            self.move(-1)
        elif k == curses.KEY_NPAGE:
            self.move(10)
        elif k == curses.KEY_PPAGE:
            self.move(-10)
        elif k == curses.KEY_HOME:
            self.sel = self.scroll = self.svc_sel = self.c_sel = 0
        elif k in (10, 13, curses.KEY_ENTER) and self.view == "conn":
            self.details()
        elif k in (10, 13, curses.KEY_ENTER) and self.view == "services":
            self.service_details()
        elif k in (10, 13, curses.KEY_ENTER) and self.view == "countries" and self.c_rows:
            self.group_details(self.c_rows[self.c_sel])
        elif k == curses.KEY_RESIZE:
            pass
        self.draw()
        return True

    def move(self, n):
        if self.view == "conn":
            self.sel = max(0, min(len(self.rows) - 1, self.sel + n))
        elif self.view == "services":
            self.svc_sel = max(0, min(len(self.svc_blocks) - 1, self.svc_sel + n))
        elif self.view == "countries":
            self.c_sel = max(0, min(len(self.c_rows) - 1, self.c_sel + n))
        else:
            self.scroll = max(0, self.scroll + n)

    def ask_filter(self):
        h, w = self.scr.getmaxyx()
        curses.curs_set(1)
        self.put(h - 1, 0, " " * (w - 1))
        self.put(h - 1, 0, "Фильтр (адрес, процесс, организация, страна; Enter — применить, Esc — сбросить): ", self.a("head"))
        curses.echo()
        self.scr.timeout(-1)
        try:
            text = self.scr.getstr(h - 1, min(w - 20, 84), 60).decode("utf-8", "replace")
        except curses.error:
            text = ""
        curses.noecho()
        curses.curs_set(0)
        self.scr.timeout(200)
        self.filter = text.strip().lower()
        self.sel = self.top = 0

    def categories(self):
        keys = list(CATS)
        while True:
            lines = [("Категории: цифра — показать или скрыть, Enter — готово", self.a("head", curses.A_BOLD)), ("", 0)]
            counts = collections.Counter(c.get("cat") for c in self.m.links.values())
            for i, k in enumerate(keys, 1):
                on = k not in self.hidden
                lines.append((f" {i}  [{'x' if on else ' '}] {CATS[k][0]}  {counts.get(k, 0)}", self.a(k) if on else self.a("muted")))
            self.popup, self.popup_top = lines, 0
            self.draw()
            self.scr.timeout(-1)
            k = self.scr.getch()
            self.scr.timeout(200)
            if ord("1") <= k <= ord(str(min(9, len(keys)))):
                cat = keys[k - ord("1")]
                self.hidden ^= {cat}
                continue
            break
        self.popup = None

    # ----- data for the table -----
    def visible(self):
        want = DIRS[self.dir][0]
        out = []
        for c in self.m.links.values():
            if c.get("cat") in self.hidden:
                continue
            if want != "all" and dir_of(c) != want:
                continue
            if self.filter:
                hay = " ".join(str(x) for x in (c["remote"], c.get("remote_name"), c.get("device"), c.get("process"), c.get("target"),
                                                (c.get("org") or {}).get("full"), place(c.get("geo")), c["dport"], c.get("vpn_client"))).lower()
                if self.filter not in hay:
                    continue
            out.append(c)
        return out

    def table_rows(self):
        conns = self.visible()
        L = self.m.labels
        mode = GROUPS[self.group][0]
        now = time.time()
        if mode == "conn":
            rows = [{"c": c, "cat": c.get("cat", "other"), "rate": rate_of(c), "n": 1, "age": now - (c.get("first") or now),
                     "name": L.who(c)} for c in conns]
        else:
            groups = {}
            for c in conns:
                if mode == "dest":
                    if c["kind"] == "in":
                        continue
                    k = org_of(c)
                elif mode == "proc":
                    if c["kind"] == "in":
                        continue
                    k = c.get("device") or "?"
                elif mode == "country":
                    k = (c.get("geo") or {}).get("cc") or "??"
                else:
                    if c["kind"] != "in":
                        continue
                    k = L.source(c)
                g = groups.setdefault(k, {"name": k, "conns": [], "rate": 0, "down": 0, "up": 0, "n": 0, "age": 0, "cats": collections.Counter()})
                g["conns"].append(c)
                g["n"] += 1
                g["rate"] += rate_of(c)
                g["down"] += c.get("down") or 0
                g["up"] += c.get("up") or 0
                g["age"] = max(g["age"], now - (c.get("first") or now))
                g["cats"][c.get("cat", "other")] += 1
            rows = list(groups.values())
            for g in rows:
                g["cat"] = g["cats"].most_common(1)[0][0]
        key = SORTS[self.sort][0]
        if key == "rate":
            rows.sort(key=lambda r: (r["rate"], r["n"]), reverse=True)
        elif key == "count":
            rows.sort(key=lambda r: (r["n"], r["rate"]), reverse=True)
        elif key == "age":
            rows.sort(key=lambda r: r["age"], reverse=True)
        else:
            rows.sort(key=lambda r: str(r["name"]).lower())
        return rows

    # ----- drawing -----
    def draw(self):
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        if h < 12 or w < 60:
            self.put(0, 0, "Окно слишком маленькое: нужно хотя бы 60×12", self.a("warn"))
            scr.refresh()
            return
        self.draw_top(w)
        self.draw_tabs(2, w)
        tick_h = 5 if h >= 30 else 3 if h >= 20 else 0
        body_top, body_bottom = 3, h - 1 - tick_h
        if self.view == "conn":
            self.draw_table(body_top, body_bottom, w)
        elif self.view == "services":
            self.draw_services(body_top, body_bottom, w)
        elif self.view == "logins":
            self.draw_lists(body_top, body_bottom, w, *self.lines_logins())
        elif self.view == "scans":
            self.draw_lists(body_top, body_bottom, w, *self.lines_scans((w - 3) // 2 if w >= 110 else w - 2))
        elif self.view == "countries":
            self.draw_countries(body_top, body_bottom, w)
        else:
            self.draw_lists(body_top, body_bottom, w, *self.lines_vpn())
        if tick_h:
            self.draw_ticker(h - 1 - tick_h, tick_h, w)
        self.draw_footer(h - 1, w)
        if self.popup is not None:
            self.draw_popup(h, w)
        scr.refresh()

    def draw_top(self, w):
        """Two status lines. Each is a list of segments with a priority and a short form: when the terminal is
        narrow, the least important segments are shortened first and then dropped, so nothing is cut mid-word."""
        r, m = self.m.router, self.m
        B, MU = curses.A_BOLD, self.a("muted")
        sep = ("  │ ", MU)

        def gauge(label, value, warn, bad, width):
            if value is None:
                return [sep, (f"{label} …", MU)], [sep, (f"{label} …", MU)]
            p = value * 100
            lvl = self.a("bad" if p >= bad else "warn" if p >= warn else "good")
            return ([sep, (f"{label} ", MU), (f"{round(p)}% ", B), (bar(value, width), lvl)],
                    [sep, (f"{label} ", MU), (f"{round(p)}%", lvl | B)])

        host = "сервер" if r.get("mode") == "host" else "роутер"
        dot = (" ● ", (self.a("bad") if r.get("wan_up") is False else self.a("good")) | B)
        name = r.get("hostname") or "…"
        cpu = r.get("cpu")
        cpu_long, cpu_short = gauge("CPU", None if cpu is None else cpu / 100, 60, 85, 8)
        if r.get("temp") is not None:
            cpu_long = cpu_long + [(f" {r['temp']}°C", MU)]
        mem_long, mem_short = gauge("MEM", r.get("mem_used"), 75, 90, 6)
        disk_long, disk_short = gauge("DISK", r.get("disk_used"), 80, 92, 6)
        ip = r.get("public_ip") or (r.get("wan_ips") or ["?"])[0]
        nat = [(" двойной NAT", self.a("warn"))] if r.get("double_nat") else []
        line1 = [
            {"prio": 10, "long": [dot, (name, B)], "short": [dot, (fit(name, 18).rstrip() if len(name) > 18 else name, B)]},
            {"prio": 1, "long": [(f"  {host} · {r.get('os') or '—'}", MU)], "short": None},
            {"prio": 3, "long": [(f" · работает {fmt_uptime(r.get('uptime'))}", MU)], "short": [(f" · {fmt_uptime(r.get('uptime'))}", MU)]},
            {"prio": 9, "long": cpu_long, "short": cpu_short},
            {"prio": 8, "long": mem_long, "short": mem_short},
            {"prio": 7, "long": disk_long, "short": disk_short},
            {"prio": 6, "long": [sep, ("IP ", MU), (ip, B)] + nat, "short": [sep, (ip, B)]},
        ]
        self.put_segments(0, line1, w)

        IN, OUT = self.a("in"), self.a("out")
        ct, cmax = r.get("conntrack"), r.get("conntrack_max")
        ct_long = [sep, ("соединения ", MU), (f"{ct if ct is not None else '…'}" + (f"/{cmax}" if cmax else ""), B)]
        if cmax and ct:
            p = ct / cmax * 100
            ct_long.append((f" {round(p)}%", self.a("bad" if p >= 90 else "warn" if p >= 70 else "muted")))
        ct_short = [sep, ("соед. ", MU), (f"{ct if ct is not None else '…'}", B)]
        left = max(0, round(m.next_poll - time.time()))
        st = m.status or {}
        if m.paused:
            poll_long = poll_short = [sep, ("ПАУЗА (p)", self.a("warn", B))]
        elif st.get("ok") is False:
            poll_long = [sep, (f"ошибка: {st.get('error')}, повтор через {left} с", self.a("bad"))]
            poll_short = [sep, (f"ошибка, повтор через {left} с", self.a("bad"))]
        elif st.get("ok"):
            poll_long = [sep, (f"опрос {m.poll_interval} с{' + события' if r.get('events') else ''}, следующий через {left} с", MU)]
            poll_short = [sep, (f"опрос через {left} с", MU)]
        else:
            poll_long = poll_short = [sep, ("первый опрос…", MU)]
        line2 = [
            {"prio": 10, "long": [(f" WAN {r.get('wan') or '?'}", MU), (f"  ↓ {fmt_rate(r.get('wan_in'))} ", IN | B), (spark(m.hist["wan_in"], 16), IN),
                                  (f"  ↑ {fmt_rate(r.get('wan_out'))} ", OUT | B), (spark(m.hist["wan_out"], 16), OUT)],
             "short": [(" WAN", MU), (f" ↓ {fmt_rate(r.get('wan_in'))}", IN | B), (f" ↑ {fmt_rate(r.get('wan_out'))}", OUT | B)]},
            {"prio": 7, "long": ct_long, "short": ct_short},
        ]
        if m.tunnels or m.vpn_clients:
            tun = " ".join(t["name"] for t in m.tunnels)
            vpn = f"VPN {len(m.vpn_clients)}" if m.vpn_clients else ""
            line2.append({"prio": 5, "long": [sep, ("туннели ", MU), (" · ".join(filter(None, [tun, vpn])), self.a("vpn"))],
                          "short": [sep, (vpn or tun, self.a("vpn"))]})
        line2.append({"prio": 6, "long": poll_long, "short": poll_short})
        self.put_segments(1, line2, w)

    def put_segments(self, y, segs, w):
        width = lambda pieces: sum(len(t) for t, _ in pieces)
        form = {id(s): "long" for s in segs}
        total = lambda: sum(width(s[form[id(s)]]) for s in segs if form[id(s)])
        order = sorted(segs, key=lambda s: s["prio"])
        for step in ("short", None):          # first shorten, least important first; then drop
            for s in order:
                if total() <= w - 1:
                    break
                if step == "short" and s["short"] is not None and form[id(s)] == "long":
                    form[id(s)] = "short"
                elif step is None and s["prio"] < 10:
                    form[id(s)] = None
                elif step == "short" and s["short"] is None:
                    form[id(s)] = None
        x = 0
        for s in segs:
            if form[id(s)]:
                for text, attr in s[form[id(s)]]:
                    x = self.put(y, x, text, attr)

    def draw_tabs(self, y, w):
        m = self.m
        a, ib = m.auth or {}, m.inbound or {}
        n_open = sum(1 for p in m.ports if p["status"] == "open")
        failed = (a.get("failures") or 0) + (a.get("invalid") or 0)
        # A counter next to the name: what deserves a look without opening the tab.
        # Each badge is a list of (text, color), numbers only: open ports in red; sessions in green and,
        # after a dot, failed logins of the last hour in yellow.
        logins = [(str(len(m.sessions or [])), "good")] + ([(" · ", "muted"), (str(failed), "warn")] if failed else [])
        tabs = (("Соединения", "conn", [(str(len(m.links)), "head")]),
                ("Сервисы", "services", [(str(n_open), "bad")] if n_open else []),
                ("Входы", "logins", logins),
                ("Сканы", "scans", [(str(ib["scan_sources"]), "scan")] if ib.get("scan_sources") else []),
                ("VPN", "vpn", [(str(len(m.vpn_clients)), "vpn")] if m.vpn_clients else []),
                ("Страны", "countries", [(str(len({(c.get("geo") or {}).get("cc") for c in m.links.values() if c.get("geo")})), "service")]))
        x = 1
        for n, (name, view, badge) in enumerate(tabs, 1):
            on = self.view == view
            x = self.put(y, x, f" {n} {name} ", self.a("cursor", curses.A_BOLD) if on else self.a("muted"))
            for text, color in badge:
                x = self.put(y, x, text, self.a(color, curses.A_BOLD))
            x += 2 if badge else 1
        if self.view == "countries":
            sort = SORTS[self.sort][1] if SORTS[self.sort][0] != "age" else "скорость"
            self.put(y, x, f"  сортировка: {sort} (s) · Enter — соединения страны" + (f" · скрыто категорий: {len(self.hidden)} (c)" if self.hidden else ""),
                     self.a("muted"))
        if self.view == "conn":
            info = f"  группировка: {GROUPS[self.group][1]} (g) · сортировка: {SORTS[self.sort][1]} (s) · показ: {DIRS[self.dir][1]} (d)"
            if self.hidden:
                info += f" · скрыто категорий: {len(self.hidden)} (c)"
            if self.filter:
                info += f" · фильтр «{self.filter}» (Esc)"
            self.put(y, x, info, self.a("muted"))
        if not self.m.geo_source:
            self.put(y, w - 30, " нет базы мест DB-IP в geo/ ", self.a("warn", curses.A_BOLD))

    def draw_table(self, top, bottom, w):
        self.rows = rows = self.table_rows()
        L = self.m.labels
        mode = GROUPS[self.group][0]
        height = bottom - top - 1
        if height < 1:
            return
        self.sel = max(0, min(self.sel, len(rows) - 1))
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + height:
            self.top = self.sel - height + 1
        rate_w = 12
        if mode == "conn":
            flex = max(10, w - 2 - 3 - 10 - 2 * rate_w - 7 - 6)
            cols = [("", 2), ("кто", int(flex * 0.26)), ("куда / кому", int(flex * 0.24)), ("порт", 10), ("место", int(flex * 0.22)),
                    ("организация", flex - int(flex * 0.26) - int(flex * 0.24) - int(flex * 0.22)), ("↓ вход", rate_w), ("↑ выход", rate_w), ("возраст", 7)]
        else:
            first = {"dest": "организация", "proc": "процесс или устройство", "country": "страна", "source": "источник"}[mode]
            flex = max(10, w - 2 - 6 - 2 * rate_w - 14)
            cols = [("", 2), (first, int(flex * 0.4)), ("соед.", 6), ("↓ вход", rate_w), ("↑ выход", rate_w), ("", 14), ("подробнее", flex - int(flex * 0.4))]
        x = 1
        for name, width in cols:
            x = self.put(top, x, fit(name, width) + " ", self.a("head", curses.A_BOLD))
        max_rate = max([r["rate"] for r in rows] + [1])
        max_n = max([r["n"] for r in rows] + [1])
        for i, row in enumerate(rows[self.top:self.top + height]):
            y = top + 1 + i
            idx = self.top + i
            cur = idx == self.sel
            base = self.a("cursor") if cur else 0
            col = self.a("cursor") if cur else self.a(row["cat"])
            if cur:
                self.put(y, 0, " " * (w - 1), base)
            if mode == "conn":
                c = row["c"]
                sym = {"out": "→", "in": "←", "scan": "◆"}[dir_of(c)]
                dim = 0 if c.get("active") is not False or cur else curses.A_DIM
                vals = [sym, L.who(c), L.what(c), L.port(c), place(c.get("geo")), (c.get("org") or {}).get("name") or "",
                        fmt_rate(c.get("down")) if rate_of(c) else "", fmt_rate(c.get("up")) if rate_of(c) else "", fmt_age(row["age"])]
                x = 1
                for j, ((_, width), v) in enumerate(zip(cols, vals)):
                    attr = col | dim if j in (0, 1, 2) else (self.a("in") if j == 6 else self.a("out") if j == 7 else base or self.a("muted")) | dim
                    if cur:
                        attr = base
                    x = self.put(y, x, fit(v, width) + " ", attr)
            else:
                g = row
                sub = self.group_sub(mode, g)
                frac = g["rate"] / max_rate if max_rate > 1 and g["rate"] else g["n"] / max_n
                vals = ["●", g["name"], str(g["n"]), fmt_rate(g["down"]), fmt_rate(g["up"]), bar(frac, 13), sub]
                x = 1
                for j, ((_, width), v) in enumerate(zip(cols, vals)):
                    attr = base if cur else col if j in (0, 1, 5) else self.a("in") if j == 3 else self.a("out") if j == 4 else self.a("muted") if j == 6 else 0
                    x = self.put(y, x, fit(v, width) + " ", attr)
        if not rows:
            self.put(top + 2, 3, "Пока пусто" + (" — проверьте фильтр (Esc) и скрытые категории (c)" if self.filter or self.hidden or self.dir else ""), self.a("muted"))
        elif len(rows) > height:
            self.put(bottom - 1, w - 22, f" {self.top + 1}–{min(len(rows), self.top + height)} из {len(rows)} ", self.a("muted"))

    def group_sub(self, mode, g):
        conns = g["conns"]
        if mode in ("dest", "country"):
            places = collections.Counter((c.get("geo") or {}).get("city") or "?" for c in conns)
            who = collections.Counter(c.get("device") or self.m.labels.source(c) for c in conns)
            return " · ".join(filter(None, [", ".join(p for p, _ in places.most_common(2)), ", ".join(p for p, _ in who.most_common(3))]))
        if mode == "proc":
            orgs = collections.Counter(org_of(c) for c in conns)
            return ", ".join(o for o, _ in orgs.most_common(4))
        c = conns[0]
        ports = sorted({self.m.labels.port(x) for x in conns})
        targets = sorted({self.m.labels.target(x) for x in conns})
        scan = sum(1 for x in conns if is_scan(x))
        return " · ".join(filter(None, [place(c.get("geo")), (c.get("org") or {}).get("name"), ", ".join(ports[:4]), "→ " + ", ".join(targets[:3]),
                                        f"сканов {scan}" if scan else ""]))

    # ----- logins, scans, VPN: lists in one or two columns -----
    def lines_logins(self):
        """Left: who is logged in now and the hour in numbers. Right: SSH events, repeats folded."""
        m = self.m
        head = self.a("head", curses.A_BOLD)
        a, ss = m.auth or {}, m.sessions or []
        host = m.router.get("mode") == "host"
        left = [(f"Кто на {'сервере' if host else 'роутере'} сейчас: {len(ss)}", head)]
        if not ss:
            left.append(("Открытых сессий нет", self.a("muted")))
        for s in ss:
            w_ = s["where"]
            where = s.get("tty", "") if w_.get("lan") else f"{w_['ip']} · {place(w_.get('geo'))} · {w_.get('org') or ''}"
            color = "good" if w_.get("lan") else "warn" if host else "bad"
            left.append((f"● {s['user']} ({s['via']}) · {where} · вошёл {fmt_age(s.get('age'))} назад", self.a(color)))
        left.append(("", 0))
        failed = (a.get("failures") or 0) + (a.get("invalid") or 0)
        left.append(("SSH за последний час", head))
        left.append((f"входов: {a.get('logins', 0)}", self.a("good") if a.get("logins") else self.a("muted")))
        left.append((f"неверных паролей или ключей: {a.get('failures', 0)}", self.a("warn") if a.get("failures") else self.a("muted")))
        left.append((f"попыток несуществующих пользователей: {a.get('invalid', 0)}", self.a("warn") if a.get("invalid") else self.a("muted")))
        if not a.get("source"):
            left.append(("журнал sshd не найден (journalctl, /var/log/auth.log, /var/log/secure)", self.a("muted")))
        if a.get("brute"):
            left.append(("", 0))
            left.append(("Кто перебирает: неудачных попыток за час", head))
            top = a["brute"][0]["n"] or 1
            geo = {e["where"]["ip"]: e["where"] for e in a.get("events", [])}
            for b in a["brute"]:
                w_ = geo.get(b["ip"], {})
                extra = f" · {place(w_.get('geo'))} · {w_.get('org') or ''}" if w_ and not w_.get("lan") else ""
                left.append((fit(b["ip"], 16) + " " + bar(b["n"] / top, 12) + f" {b['n']}{extra}", self.a("session")))
        kinds = {"login": "вход", "failure": "неверный пароль или ключ", "invalid": "нет такого пользователя", "logout": "выход"}
        folded = []
        for e in a.get("events", []):
            f = next((x for x in folded if x["kind"] == e["kind"] and x["user"] == e["user"] and x["where"]["ip"] == e["where"]["ip"]), None)
            if f:
                f["n"] += 1
            else:
                folded.append({**e, "n": 1})
        right = [(f"События SSH за час, свежие сверху{' (последние 60)' if len(a.get('events', [])) >= 60 else ''}", head)]
        if not folded:
            right.append(("Ни входов, ни неудачных попыток", self.a("muted")))
        for e in folded:
            color = "good" if e["kind"] == "login" else "muted" if e["kind"] == "logout" else "session"
            w_ = e["where"]
            where = w_["ip"] + ("" if w_.get("lan") else f" · {place(w_.get('geo'))} · {w_.get('org') or ''}")
            right.append((f"{e['time']} {e['user'] or '?'} · {kinds[e['kind']]}{' ×' + str(e['n']) if e['n'] > 1 else ''} · {where}", self.a(color)))
        return left, right

    def lines_scans(self, width):
        """Left: scanners of the last hour folded by organization. Right: what they were after."""
        m = self.m
        head = self.a("head", curses.A_BOLD)
        ib = m.inbound or {}
        now_scans = [c for c in m.links.values() if is_scan(c)]
        left = [(f"Сканеры за час: {ib.get('scan_sources', 0)} адресов · сейчас сканов: {len(now_scans)} · "
                 f"новых входящих за час: {ib.get('total', 0)} от {ib.get('sources', 0)} адресов", head)]
        by_org = {}
        for x in ib.get("scanners", []):
            k = x.get("org") or x["ip"]
            g = by_org.setdefault(k, {**x, "ips": [], "sample": set()})
            g["ips"].append(x["ip"])
            g["ports"] = max(g["ports"], x["ports"])
            g["sample"] |= set(x.get("sample", []))
        for g in sorted(by_org.values(), key=lambda g: (-g["ports"], -len(g["ips"]))):
            who = g.get("org") or g["ip"]
            ips = f"{len(g['ips'])} адресов" if len(g["ips"]) > 1 else g["ips"][0]
            left.append((f"◆ {who} · {g['ports']} портов · {ips} · {place(g.get('geo'))}" + (" · исследовательский" if g.get("research") else ""),
                         self.a("research" if g.get("research") else "scan")))
            left.append((f"   {', '.join(sorted(g['sample'])[:8])} · {fmt_age(g.get('ago'))} назад", self.a("muted")))
        if not by_org:
            left.append(("сканеров не было", self.a("muted")))
        elif ib.get("scan_sources", 0) > len(ib.get("scanners", [])):
            left.append((f"показаны {len(ib.get('scanners', []))} самых настойчивых из {ib.get('scan_sources')}", self.a("muted")))

        right = []
        gates = sorted((ib.get("gates") or {}).items(), key=lambda x: -x[1])
        if gates:
            right.append(("На что шли: источников за час по видам портов", head))
            top = gates[0][1] or 1
            for g, n in gates[:8]:
                right.append((fit(GATE_LABEL.get(g, g), 16) + " " + bar(n / top, 20) + f" {n}", self.a("bad" if g in ("ssh", "db", "remote") else "session")))
            right.append(("", 0))
        tl = ib.get("timeline")
        right.append(("Сканы по портам, по минутам за час (сейчас справа)", head))
        if not tl:
            ports = sorted((ib.get("scan_ports") or {}).items(), key=lambda x: -x[1])[:12]
            for k, n in ports:
                right.append((f"{fit(k, 11)} {n} адресов", self.a("scan")))
            if not ports:
                right.append(("сканов не было", self.a("muted")))
        else:
            rows = sorted(((k, tl["scan"][k]) for k in tl["ports"] if sum(tl["scan"][k])), key=lambda x: -sum(x[1]))[:14]
            cells = min(60, max(10, width - 20))
            for k, series in rows:
                strip = [(HEAT[0 if n == 0 else 1 if n == 1 else 2 if n <= 3 else 3 if n <= 9 else 4] if n else "·",
                          self.a("scan") if n else self.a("muted", curses.A_DIM)) for n in series[-cells:]]
                right.append(([(fit(k, 11) + " ", self.a("muted"))] + strip + [(f" {sum(series)}", self.a("scan"))], None))
            if not rows:
                right.append(("сканов не было", self.a("muted")))
            else:
                right.append((f"{' ' * 12}· нет  ░ 1  ▒ 2–3  ▓ 4–9  █ 10+", self.a("muted", curses.A_DIM)))
        return left, right

    def lines_vpn(self):
        m = self.m
        head = self.a("head", curses.A_BOLD)
        now = time.time()
        left = [(f"Клиенты OpenVPN: {len(m.vpn_clients)}", head)]
        if not m.vpn_clients:
            left.append(("Клиентов нет: сервер OpenVPN не найден, или его статус-файл не читается (openvpn_status в .env)", self.a("muted")))
        tunnel = collections.defaultdict(lambda: [0, 0.0])     # the tunnel connection itself, by client name
        inside = collections.defaultdict(lambda: [0, 0.0])     # what the client does through the tunnel
        virt = {v.split("/")[0]: c["name"] for c in m.vpn_clients for v in c.get("virtual", [])}
        for c in m.links.values():
            if c.get("vpn_client"):
                t = tunnel[c["vpn_client"]]
                t[0] += 1
                t[1] += rate_of(c)
            elif c["kind"] == "out" and c.get("src") in virt:
                t = inside[virt[c["src"]]]
                t[0] += 1
                t[1] += rate_of(c)
        for c in sorted(m.vpn_clients, key=lambda c: -tunnel[c["name"]][1]):
            since = f"подключён {fmt_age(now - c['since'])}" if c.get("since") else ""
            pl = c.get("place")
            left.append((f"● {c['name']}  {', '.join(c.get('virtual', []))}", self.a("vpn", curses.A_BOLD)))
            left.append((f"   с адреса {c['real']}:{c.get('real_port') or '?'}" + (f" · {pl['cc']} {pl['city']}" if pl else "") + f" · {since}", self.a("muted")))
            t, n = tunnel[c["name"]], inside[c["name"]]
            left.append((f"   туннель: {fmt_rate(t[1]) if t[1] else 'тихо'} · всего ↓ {fmt_bytes(c.get('tx'))} ↑ {fmt_bytes(c.get('rx'))}"
                         + (f" · через туннель наружу: {n[0]} соединений, {fmt_rate(n[1])}" if n[0] else ""), self.a("vpn")))
        right = [("Туннельные интерфейсы", head)]
        if not m.tunnels:
            right.append(("нет", self.a("muted")))
        for t in m.tunnels:
            right.append((f"{'●' if t.get('up') else '○'} {t['name']}  {', '.join(t.get('addrs', []))}", self.a("good" if t.get("up") else "muted")))
        return left, right

    def draw_lists(self, top, bottom, w, left, right):
        """Two columns side by side on a wide terminal, one under the other on a narrow one; ↑↓ scroll if needed."""
        height = bottom - top
        two = w >= 110 and right
        lw = (w - 3) // 2 if two else w - 2
        lines_l, lines_r = (left, right) if two else (left + ([("", 0)] + right if right else []), [])
        total = max(len(lines_l), len(lines_r))
        self.scroll = max(0, min(self.scroll, max(0, total - height)))
        for col_x, lines, width in ((1, lines_l, lw), (lw + 3, lines_r, w - lw - 4)):
            for i, item in enumerate(lines[self.scroll:self.scroll + height]):
                text, attr = item
                y = top + i
                if isinstance(text, list):  # a heat strip: pieces with their own colors
                    x = col_x
                    for piece, pattr in text:
                        x = self.put(y, x, piece, pattr, max(0, col_x + width - x))
                else:
                    self.put(y, col_x, fit(text, width), attr)
        if two:
            for y in range(top, bottom):
                self.put(y, lw + 1, "│", self.a("muted", curses.A_DIM))
        if total > height:
            self.put(bottom - 1, w - 28, f" ↑↓ прокрутка {self.scroll + 1}–{min(total, self.scroll + height)} из {total} ", self.a("muted"))

    # ----- countries -----
    def countries(self):
        groups = {}
        for c in self.m.links.values():
            if c.get("cat") in self.hidden:
                continue
            g = c.get("geo")
            cc = g["cc"] if g else "??"
            r = groups.get(cc)
            if r is None:
                r = groups[cc] = {"cc": cc, "name": COUNTRIES.get(cc, "место неизвестно" if cc == "??" else cc), "conns": [], "n": 0, "out": 0,
                                  "in": 0, "scan": 0, "down": 0, "up": 0, "rate": 0, "cities": collections.Counter(),
                                  "orgs": collections.Counter(), "cats": collections.Counter()}
            r["conns"].append(c)
            r["n"] += 1
            r[dir_of(c)] += 1
            r["down"] += c.get("down") or 0
            r["up"] += c.get("up") or 0
            r["rate"] += rate_of(c)
            if g and g.get("city"):
                r["cities"][g["city"]] += 1
            r["orgs"][org_of(c) if c["kind"] != "in" else (c.get("org") or {}).get("name") or c["remote"]] += rate_of(c) + 1
            r["cats"][c.get("cat", "other")] += 1
        rows = list(groups.values())
        key = SORTS[self.sort][0]
        if key == "count":
            rows.sort(key=lambda r: (-r["n"], -r["rate"]))
        elif key == "name":
            rows.sort(key=lambda r: r["name"].lower())
        else:  # by traffic; "age" has no meaning for a country
            rows.sort(key=lambda r: (-r["rate"], -r["n"]))
        return rows

    def draw_countries(self, top, bottom, w):
        self.c_rows = rows = self.countries()
        height = bottom - top - 1
        if height < 1:
            return
        self.c_sel = max(0, min(self.c_sel, len(rows) - 1))
        if self.c_sel < self.c_top:
            self.c_top = self.c_sel
        elif self.c_sel >= self.c_top + height:
            self.c_top = self.c_sel - height + 1
        rate_w = 12
        flex = max(10, w - 2 - 3 - 22 - 3 * 9 - 2 * rate_w - 14 - 9)
        cols = [("", 3), ("страна", 22), ("наружу", 8), ("снаружи", 8), ("сканы", 8), ("↓ вход", rate_w), ("↑ выход", rate_w), ("доля", 14),
                ("города · организации", flex)]
        x = 1
        for name, width in cols:
            x = self.put(top, x, fit(name, width) + " ", self.a("head", curses.A_BOLD))
        max_rate = max([r["rate"] for r in rows] + [1])
        max_n = max([r["n"] for r in rows] + [1])
        for i, r in enumerate(rows[self.c_top:self.c_top + height]):
            y = top + 1 + i
            cur = self.c_top + i == self.c_sel
            base = self.a("cursor") if cur else 0
            if cur:
                self.put(y, 0, " " * (w - 1), base)
            cat = r["cats"].most_common(1)[0][0]
            frac = r["rate"] / max_rate if max_rate > 1 and r["rate"] else r["n"] / max_n
            sub = " · ".join(filter(None, [", ".join(c for c, _ in r["cities"].most_common(3)), ", ".join(o for o, _ in r["orgs"].most_common(3))]))
            vals = [(r["cc"], self.a("muted")), (r["name"], self.a(cat, curses.A_BOLD)),
                    (str(r["out"]) if r["out"] else "·", 0 if r["out"] else self.a("muted", curses.A_DIM)),
                    (str(r["in"]) if r["in"] else "·", self.a("session") if r["in"] else self.a("muted", curses.A_DIM)),
                    (str(r["scan"]) if r["scan"] else "·", self.a("scan") if r["scan"] else self.a("muted", curses.A_DIM)),
                    (fmt_rate(r["down"]) if r["rate"] else "", self.a("in")), (fmt_rate(r["up"]) if r["rate"] else "", self.a("out")),
                    (bar(frac, 13), self.a(cat)), (sub, self.a("muted"))]
            x = 1
            for (_, width), (v, attr) in zip(cols, vals):
                x = self.put(y, x, fit(v, width) + " ", base if cur else attr)
        if not rows:
            self.put(top + 2, 3, "Пока пусто", self.a("muted"))
        elif len(rows) > height:
            self.put(bottom - 1, w - 22, f" {self.c_top + 1}–{min(len(rows), self.c_top + height)} из {len(rows)} ", self.a("muted"))

    # ----- services -----
    def services(self):
        """One block per service reachable from the internet, busiest first; ping and unmatched ports at the end."""
        m, L = self.m, self.m.labels
        ib = m.inbound or {}
        tl = ib.get("timeline") or {}
        conn_hour, scan_hour = ib.get("connect_ports") or {}, ib.get("scan_ports") or {}
        by = collections.defaultdict(list)
        for c in m.links.values():
            if c["kind"] == "in":
                by[service_of(m.ports, c) or "other"].append(c)
        blocks = []
        rows = [r for r in m.ports if r["status"] != "local"]
        rows.append({"name": "ping", "ports": ["icmp"], "status": "open", "why": "эхо-запросы, отвечает ядро", "sid": "ping"})
        rows.append({"name": "прочие порты", "ports": [], "status": "closed", "why": "никто не слушает: сканы и отказы", "sid": "other"})
        for r in rows:
            sid = r.get("sid") or service_id(r)
            conns = by.get(sid, [])
            if sid in ("ping", "other") and not conns and not any(m.svc_hist.get(sid, [])):
                continue
            real = [c for c in conns if not is_scan(c)]
            scans = [c for c in conns if is_scan(c)]
            if sid == "ping":
                keys = [k for k in tl.get("ports", []) if k.endswith("/icmp")]
            elif sid == "other":  # ports nothing listens on
                keys = [k for k in tl.get("ports", []) if not k.endswith("/icmp") and not any(
                    port_match(spec, int(k.split("/")[0]), k.split("/")[1]) for x in m.ports if x["status"] != "local" for spec in x["ports"])]
            else:
                keys = [k for k in tl.get("ports", []) if any(port_match(spec, int(k.split("/")[0]), k.split("/")[1]) for spec in r["ports"])]
            heat_conn = [sum(tl["conn"][k][i] for k in keys) for i in range(60)] if keys else None
            heat_scan = [sum(tl["scan"][k][i] for k in keys) for i in range(60)] if keys else None
            def hour(d):
                if sid in ("ping", "other"):
                    return sum(n for k, n in d.items() if k in keys)
                return sum(n for k, n in d.items() if k.count("/") == 1 and k.split("/")[0].isdigit()
                           and any(port_match(spec, int(k.split("/")[0]), k.split("/")[1]) for spec in r["ports"]))
            clients = collections.Counter()
            where = {}
            for c in real:
                who = L.source(c)
                clients[who] += 1
                where[who] = " · ".join(filter(None, [place(c.get("geo")), None if c.get("vpn_client") else (c.get("org") or {}).get("name")]))
            blocks.append({"row": r, "sid": sid, "conns": conns, "real": real, "scans": scans,
                           "down": sum(c.get("down") or 0 for c in real), "up": sum(c.get("up") or 0 for c in real),
                           "clients": clients, "where": where, "heat": (heat_conn, heat_scan),
                           "hour_conn": hour(conn_hour), "hour_scan": hour(scan_hour), "hist": m.svc_hist.get(sid, [])})
        tail = {"ping": 1, "other": 2}
        # Most dangerous first: open to everyone, then limited by address, then closed by the firewall;
        # inside a status the ones with more connections and traffic come first.
        danger = {"open": 0, "limited": 1, "closed": 2}
        blocks.sort(key=lambda b: (tail.get(b["sid"], 0), danger.get(b["row"]["status"], 3), -len(b["real"]), -(b["down"] + b["up"]),
                                   -(b["hour_conn"] + b["hour_scan"])))
        return blocks

    def draw_services(self, top, bottom, w):
        self.svc_blocks = blocks = self.services()
        L = self.m.labels
        if not blocks:
            self.put(top + 1, 3, "Из интернета ничего не слушается" if self.m.ports else "Ждём первого опроса…", self.a("muted"))
            return
        self.svc_sel = max(0, min(self.svc_sel, len(blocks) - 1))
        colors = {"open": "bad", "limited": "warn", "closed": "good"}
        words = {"open": "открыт", "limited": "ограничен", "closed": "закрыт firewall"}
        lines = []          # (block index, pieces) where pieces = [(text, attr), ...]
        for i, b in enumerate(blocks):
            r = b["row"]
            sel = i == self.svc_sel
            st = r["status"]
            name_attr = self.a("cursor", curses.A_BOLD) if sel else curses.A_BOLD
            now = f"сейчас {len(b['real'])}" + (f" · сканов {len(b['scans'])}" if b["scans"] else "")
            rate = f"↓ {fmt_rate(b['down'])} ↑ {fmt_rate(b['up'])}" if b["down"] + b["up"] else ""
            ports = ports_label(r["ports"]) if r["ports"] and b["sid"] != "ping" else r["name"]
            owner = owner_label(r) if r["ports"] and b["sid"] != "ping" else ""
            left = [("▶ " if sel else "  ", self.a("head", curses.A_BOLD)), (f" {ports} ", name_attr), ("  " + owner, 0),
                    (f"  ● {words.get(st, st)}" if b["sid"] not in ("ping", "other") else "", self.a(colors.get(st, "muted")))]
            right = [(now, curses.A_BOLD if b["real"] else self.a("muted")), ("  " + rate, self.a("in")) if rate else ("", 0),
                     ("  " + spark(b["hist"], 20), self.a("session")) if b["hist"] else ("", 0)]
            lines.append((i, left, right))
            if b["clients"]:
                who = ", ".join(f"{k}{' ×' + str(n) if n > 1 else ''}" + (f" ({b['where'][k]})" if b["where"].get(k) else "")
                                for k, n in b["clients"].most_common(4))
                more = len(b["clients"]) - 4
                lines.append((i, [("     кто: ", self.a("muted")), (who + (f" и ещё {more}" if more > 0 else ""), self.a("session"))], []))
            elif b["sid"] != "other":
                lines.append((i, [("     сейчас никого", self.a("muted", curses.A_DIM))], []))
            hc, hs = b["heat"]
            # Events per minute come from the time line, distinct addresses from the hourly summary.
            if hc is None:
                label = f"     за час: подключались {b['hour_conn']} адресов, сканировали {b['hour_scan']}  "
            else:
                label = (f"     за час: {sum(hc)} подключений (от {b['hour_conn']} адресов), "
                         f"{sum(hs)} сканов (от {b['hour_scan']})  ")
            if hc is None:
                lines.append((i, [(label, self.a("muted"))], []))
            else:
                cells = max(10, min(60, w - len(label) - 4))
                strip = []
                for n_c, n_s in list(zip(hc, hs))[-cells:]:
                    n = n_c + n_s
                    ch = HEAT[0 if n == 0 else 1 if n == 1 else 2 if n <= 3 else 3 if n <= 9 else 4]
                    strip.append((ch if n else "·", self.a("scan" if n_s > n_c else "session") if n else self.a("muted", curses.A_DIM)))
                lines.append((i, [(label, self.a("muted"))] + strip, []))
        local = [local_label(r) for r in self.m.ports if r["status"] == "local"]
        if local:
            lines.append((-1, [("  только локально (127.0.0.1 или LAN), из интернета не видны:", self.a("muted"))], []))
            for item in local:
                lines.append((-1, [("     " + item, self.a("muted", curses.A_DIM))], []))
        for note in self.m.fw_notes:
            lines.append((-1, [("  ! " + note, self.a("warn"))], []))
        lines.append((-1, [("  полоса — новые подключения по минутам за час, сейчас справа: · нет  ░ 1  ▒ 2–3  ▓ 4–9  █ 10+, жёлтым — сканы", self.a("muted", curses.A_DIM))], []))
        # Keep the selected block on screen.
        height = bottom - top
        first = next(n for n, (i, _, _) in enumerate(lines) if i == self.svc_sel)
        last = max(n for n, (i, _, _) in enumerate(lines) if i == self.svc_sel)
        if first < self.scroll:
            self.scroll = first
        elif last >= self.scroll + height:
            self.scroll = last - height + 1
        self.scroll = max(0, min(self.scroll, max(0, len(lines) - height)))
        for n, (_, left, right) in enumerate(lines[self.scroll:self.scroll + height]):
            y = top + n
            x = 1
            rw = sum(len(t) for t, _ in right)
            for text, attr in left:
                x = self.put(y, x, text, attr, max(0, w - rw - 2 - x))
            x = max(x + 2, w - rw - 2)
            for text, attr in right:
                x = self.put(y, x, text, attr)

    def service_details(self):
        if not self.svc_blocks:
            return
        b = self.svc_blocks[self.svc_sel]
        L = self.m.labels
        r = b["row"]
        title = f"{ports_label(r['ports'], 20)} · {owner_label(r)}" if r["ports"] and b["sid"] != "ping" else r["name"]
        lines = [(title, self.a("head", curses.A_BOLD)), (r.get("why", ""), self.a("muted"))]
        if r.get("binds"):
            lines.append((f"слушает: {', '.join(r['binds'])}", self.a("muted")))
        hc, hs = b["heat"]
        hour = (f"за час {sum(hc)} подключений от {b['hour_conn']} адресов, {sum(hs)} сканов от {b['hour_scan']}" if hc is not None
                else f"за час подключались {b['hour_conn']} адресов, сканировали {b['hour_scan']}")
        lines.append((f"сейчас {len(b['real'])} подключений, {len(b['scans'])} сканов · {hour}", 0))
        lines.append(("", 0))
        now = time.time()
        for c in sorted(b["conns"], key=lambda c: (is_scan(c), -rate_of(c))):
            text = (f"{'◆' if is_scan(c) else '←'} {L.source(c)} → {L.target(c)} {L.port(c)} · {place(c.get('geo'))} · "
                    f"{(c.get('org') or {}).get('name') or ''} · {c.get('state') or ''} · {fmt_rate(rate_of(c))} · {fmt_age(now - (c.get('first') or now))}")
            lines.append((text, self.a(c.get("cat", "session"))))
        if not b["conns"]:
            lines.append(("сейчас никого", self.a("muted")))
        lines.append(("", 0))
        lines.append(("Esc или Enter — закрыть, ↑↓ — прокрутка", self.a("muted")))
        self.popup, self.popup_top = lines, 0

    # ----- bottom -----
    def draw_ticker(self, y, height, w):
        want = DIRS[self.tick_dir][0]
        self.put(y, 0, "─" * (w - 1), self.a("muted", curses.A_DIM))
        self.put(y, 2, f" Лента: {DIRS[self.tick_dir][1]} (f) ", self.a("head", curses.A_BOLD))
        rows = [t for t in self.m.ticks if want == "all" or t[1] == want][: height - 1]
        if not rows:
            self.put(y + 1, 2, "Новых соединений пока нет", self.a("muted"))
        for i, (tm, d, still, cat, text) in enumerate(rows):
            self.put(y + 1 + i, 1, tm + " ", self.a("muted"))
            attr = self.a(cat) | (curses.A_DIM if still else 0)
            self.put(y + 1 + i, 10, ("◆ " if d == "scan" else "← " if d == "in" else "→ ") + text, attr, w - 11)

    def draw_footer(self, y, w):
        keys = "← → или 1–6 вкладки · ↑↓ выбор · Enter подробно · g группы · s сортировка · d направление · c категории · / фильтр · f лента · p пауза · q выход"
        self.put(y, 0, fit(" " + keys, w - 1), self.a("muted", curses.A_REVERSE))
        note = None
        if self.log and self.log.last and time.time() - self.log.last[0] < 120:
            note = self.log.last[1]
        if not self.remote and os.geteuid() != 0:
            note = note or "не root: conntrack, iptables и имена процессов недоступны — запустите через sudo"
        if note and w > 100:
            self.put(y, max(0, w - min(len(note), w // 2) - 2), fit(" " + note, min(len(note) + 1, w // 2)), self.a("warn", curses.A_REVERSE))

    # ----- details -----
    def details(self):
        if not self.rows:
            return
        row = self.rows[self.sel]
        L = self.m.labels
        head = self.a("head", curses.A_BOLD)
        lines = []
        if "c" in row:
            c = row["c"]
            lines.append((f"{CATS.get(c.get('cat'), ('?',))[0]} · {'входящее' if c['kind'] == 'in' else 'исходящее'}", self.a(c.get("cat", "other"), curses.A_BOLD)))
            lines.append(("", 0))
            org = c.get("org") or {}
            fields = [
                ("Откуда", f"{c['src']}:{c['sport']}"), ("Куда", f"{c['dst']}:{c['dport']} {c['proto']}"),
                ("Состояние", c.get("state") or "—"),
                ("Кто", L.who(c)), ("Кому", L.what(c) if c["kind"] != "in" else L.target(c)),
                ("Процесс", c.get("process") or "—"), ("Имя из names.conf", c.get("remote_name") or "—"),
                ("Организация", f"AS{org.get('asn')} {org.get('full')}" if org else "—"),
                ("Место", place(c.get("geo"))), ("Производитель", c.get("maker") or "—"),
                ("Скорость", f"↓ {fmt_rate(c.get('down'))}  ↑ {fmt_rate(c.get('up'))}"),
                ("Передано", fmt_bytes(c.get("bytes"))), ("Возраст", fmt_age(time.time() - (c.get("first") or time.time()))),
            ]
            if c["kind"] == "in":
                fields += [("Портов за час от источника", str(c.get("ports_tried") or 1)),
                           ("Ответ", "да" if c.get("replied", True) else "нет"), ("[ASSURED]", "да" if c.get("assured") else "нет"),
                           ("Клиент VPN", c.get("vpn_client") or "—"), ("Проброс", c.get("target") if c.get("forwarded") else "—")]
                auth = [e for e in (self.m.auth or {}).get("events", []) if e["where"]["ip"] == c["remote"]]
                if auth:
                    fields.append(("SSH с этого адреса", f"{len(auth)} событий за час, последнее: {auth[0]['kind']} {auth[0]['user']}"))
            for k, v in fields:
                lines.append((f"{k + ':':<28}{v}", 0))
            lines.append(("", 0))
            lines.append(("Esc или Enter — закрыть, ↑↓ — прокрутка", self.a("muted")))
            self.popup, self.popup_top = lines, 0
        else:
            self.group_details(row)

    def group_details(self, row):
        """Every connection of a group (an organization, a process, a country), the busiest first."""
        L = self.m.labels
        lines = [(f"{row['name']} · {row['n']} соединений · ↓ {fmt_rate(row['down'])} ↑ {fmt_rate(row['up'])}", self.a("head", curses.A_BOLD)), ("", 0)]
        for c in sorted(row["conns"], key=lambda c: (is_scan(c), -rate_of(c))):
            sym = "◆" if is_scan(c) else "←" if c["kind"] == "in" else "→"
            lines.append((f"{sym} {L.who(c)} → {L.what(c)} {L.port(c)} · {place(c.get('geo'))} · "
                          f"{(c.get('org') or {}).get('name') or ''} · {fmt_rate(rate_of(c))}", self.a(c.get("cat", "other"))))
        lines.append(("", 0))
        lines.append(("Esc или Enter — закрыть, ↑↓ — прокрутка", self.a("muted")))
        self.popup, self.popup_top = lines, 0

    def draw_popup(self, h, w):
        lines = self.popup
        pw = min(w - 4, max(50, max(len(t) for t, _ in lines) + 4))
        ph = min(h - 4, len(lines) + 2)
        y0, x0 = (h - ph) // 2, (w - pw) // 2
        self.popup_top = max(0, min(self.popup_top, max(0, len(lines) - (ph - 2))))
        for y in range(ph):
            self.put(y0 + y, x0, " " * pw, curses.A_REVERSE if y in (0, ph - 1) else 0)
        for i, (text, attr) in enumerate(lines[self.popup_top:self.popup_top + ph - 2]):
            self.put(y0 + 1 + i, x0 + 2, fit(text, pw - 4), attr)
        for y in range(ph):
            self.put(y0 + y, x0, "│", self.a("head"))
            self.put(y0 + y, x0 + pw - 1, "│", self.a("head"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="адрес работающего сервиса linux-api, например http://127.0.0.1:8787")
    ap.add_argument("--log", help="писать журнал сборщика в этот файл")
    args = ap.parse_args()
    locale.setlocale(locale.LC_ALL, "")
    ring = LogRing()
    handlers = [ring] + ([logging.FileHandler(args.log, encoding="utf-8")] if args.log else [])
    logging.basicConfig(level=logging.INFO if args.log else logging.WARNING, handlers=handlers,
                        format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    q = queue.Queue()
    model = Model()
    if args.url:
        RemoteSource(q, args.url).start()
    else:
        sys.path.insert(0, HERE)
        print("Загружаю базы и запускаю сбор…", flush=True)
        LocalSource(q).start()
    try:
        curses.wrapper(lambda scr: App(scr, model, q, ring, bool(args.url)).run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
