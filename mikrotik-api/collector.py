"""Polls the router over the API, keeps the current picture in memory and publishes changes.

Every poll: system resources, WAN counters, DHCP leases, tunnels and the full conntrack table.
Connections are tracked by their conntrack id: a new id is a "start", a vanished id an "end",
and the byte counters between two polls give the current rate of each connection.
Every config_interval: WAN detection, LAN networks, services and firewall (ports plate).
"""
import collections
import datetime
import ipaddress
import logging
import queue
import re
import threading
import time

import classify
import oui
import ports
from routeros import RouterOS, RouterOSError

log = logging.getLogger("collector")

CONN_FIELDS = [".id", "src-address", "dst-address", "reply-src-address", "protocol", "tcp-state", "orig-bytes", "repl-bytes",
               "orig-packets", "repl-packets", "seen-reply", "assured"]
# Organizations that scan the whole internet for research or security products.
RESEARCH = re.compile(r"censys|shodan|palo alto|xpanse|shadowserver|rapid7|binaryedge|stretchoid|onyphe|driftnet|leakix|"
                      r"criminal ?ip|internet measurement|recyber|modat|bitsight|alphastrike|netscout|intrinsec|hadrian|"
                      r"security ?research|academic|university", re.I)


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
AUTH_PATTERNS = [
    ("login", re.compile(r"user (?P<user>\S+) logged in from (?P<addr>\S+) via (?P<via>\S+)")),
    ("logout", re.compile(r"user (?P<user>\S+) logged out from (?P<addr>\S+) via (?P<via>\S+)")),
    ("failure", re.compile(r"login failure for user (?P<user>\S+) from (?P<addr>\S+) via (?P<via>\S+)")),
    ("denied", re.compile(r"denied (?P<via>\S+) connect from (?P<addr>\S+)")),  # address not in "Available From"
]
INTERACTIVE = {"web", "winbox", "ssh", "telnet", "ftp", "winbox/dude", "console"}


def router_time(value, now):
    """RouterOS log time ('13:05:01', 'sep/28 11:26:31', 'sep/28/2025 11:26:31') as a datetime in router local time."""
    parts = value.split()
    try:
        hms = [int(x) for x in parts[-1].split(":")]
        if len(parts) == 1:
            d = now.date()
        else:
            md = parts[0].split("/")
            year = int(md[2]) if len(md) > 2 else now.year
            d = datetime.date(year, MONTHS[md[0].lower()], int(md[1]))
            if len(md) == 2 and d > now.date():
                d = d.replace(year=year - 1)
        return datetime.datetime.combine(d, datetime.time(*hms))
    except (ValueError, KeyError, IndexError):
        return None


def split_addr(value):
    """'1.2.3.4:80' -> ('1.2.3.4', 80); '1.2.3.4' -> ('1.2.3.4', 0)."""
    if value.count(":") == 1:
        ip, port = value.split(":")
        return ip, int(port)
    return value, 0


def is_public(ip):
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


class Collector:
    def __init__(self, cfg, geo, asn, vendors):
        self.cfg, self.geo, self.asn, self.vendors = cfg, geo, asn, vendors
        self.api = RouterOS(cfg["server_api"], cfg["port_api"], cfg["username_api"], cfg["password_api"], cfg["proxy"] or None)
        self.lock = threading.Lock()
        self.subscribers = []
        self.conns = {}          # conntrack id -> connection dict
        self.prev_bytes = {}     # conntrack id -> (orig, repl, t)
        self.names = {}          # LAN ip -> host name
        self.makers = {}         # LAN ip -> device maker by MAC
        self.lan_nets = []
        self.wan = self.cfg["wan_interface"] or None
        self.wan_ip = None
        self.home = None
        self.router = {}
        self.ports = []
        self.tunnels = []
        self.wan_prev = None
        self.inbound = collections.deque()   # (t, ip, gate, port/proto) for the last hour
        self.sessions = []                   # /user/active, enriched
        self.auth = collections.deque(maxlen=300)  # login events from /log, newest last
        self.log_seen = set()
        self.scan_ips = {}      # outside address -> last time it looked like a scan
        self.tried_now = {}
        self.started = time.time()
        self.status = {"ok": False, "error": "ещё не опрашивали", "at": None}
        self.next_poll = time.time()
        self.last_config = 0
        self.polls = 0
        # poll_mode=clients: poll only while a browser is connected, sleep after idle_timeout without one.
        self.on_demand = cfg["poll_mode"] == "clients"
        self.wake = threading.Event()
        self.last_client = time.time()
        self.paused = False

    # ---------- subscribers (server-sent events) ----------
    def subscribe(self):
        q = queue.Queue(maxsize=20)
        with self.lock:
            self.subscribers.append(q)
            self.last_client = time.time()
        self.wake.set()  # a sleeping collector starts polling right away
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)
            self.last_client = time.time()

    def _idle(self):
        """True when polling should stop: on-demand mode, no browser for longer than idle_timeout."""
        with self.lock:
            nobody = not self.subscribers
        return self.on_demand and nobody and time.time() - self.last_client >= self.cfg["idle_timeout"]

    def _sleep_until_client(self):
        log.info("no browser for %d s: polling paused, API session closed", self.cfg["idle_timeout"])
        self.api.close()
        with self.lock:
            # What we knew is stale by the time someone comes back: start from a clean table.
            self.conns.clear()
            self.prev_bytes.clear()
            self.wan_prev = None
            self.polls = 0
            self.paused = True
            self.status = {"ok": None, "error": None, "at": time.time(), "paused": True}
        self.wake.clear()
        while self._idle():
            self.wake.wait(timeout=60)
            self.wake.clear()
        with self.lock:
            self.paused = False
            self.status = {"ok": None, "error": None, "at": time.time(), "paused": False}
        log.info("browser connected: polling resumed")

    def publish(self, msg):
        with self.lock:
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:  # a slow browser: drop its oldest message
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except queue.Empty:
                    pass

    # ---------- snapshot for a newly opened page ----------
    def snapshot(self):
        with self.lock:
            return {
                "router": self.router, "ports": self.ports, "tunnels": self.tunnels, "home": self.home,
                "conns": list(self.conns.values()), "inbound": self._inbound_summary(), "status": self.status,
                "sessions": self.sessions, "auth": self._auth_summary(),
                "poll_interval": self.cfg["poll_interval"], "next_poll": self.next_poll, "started": self.started,
                "poll_mode": self.cfg["poll_mode"], "paused": self.paused,
                "geo_source": self.geo.source, "asn_source": self.asn.source,
            }

    # ---------- main loop ----------
    def run(self):
        backoff = 5
        while True:
            if self._idle():
                self._sleep_until_client()
            # Rates need two polls: right after a (re)start the second poll comes after 5 s, not a full interval.
            gap = min(5, self.cfg["poll_interval"]) if self.polls == 0 else self.cfg["poll_interval"]
            self.next_poll = time.time() + gap
            try:
                if time.time() - self.last_config >= self.cfg["config_interval"] or not self.wan_ip:
                    self.read_config()
                msg = self.poll()
                self.status = {"ok": True, "error": None, "at": time.time()}
                msg["status"] = self.status
                msg["next_poll"] = self.next_poll
                msg["fresh"] = self.polls == 1  # everything is "new" on the first poll: no start animations
                self.publish(msg)
                backoff = 5
            except (RouterOSError, OSError) as e:
                log.warning("poll failed: %s", e)
                self.api.close()
                self.status = {"ok": False, "error": str(e), "at": time.time()}
                self.next_poll = time.time() + backoff
                self.publish({"type": "status", "status": self.status, "next_poll": self.next_poll})
                time.sleep(backoff)
                backoff = min(300, backoff * 2)
                continue
            self.wake.wait(timeout=max(0.0, self.next_poll - time.time()))
            self.wake.clear()

    # ---------- config: WAN, LAN, services ----------
    def read_config(self):
        api = self.api
        if not self.cfg["wan_interface"]:
            dhcp = [d for d in api.print("/ip/dhcp-client", proplist=["interface", "status", "disabled"]) if d.get("disabled") != "true"]
            members = api.print("/interface/list/member", proplist=["list", "interface"])
            wan_list = [m["interface"] for m in members if m.get("list", "").upper() == "WAN"]
            self.wan = (dhcp[0]["interface"] if dhcp else None) or (wan_list[0] if wan_list else "ether1")
        addrs = api.print("/ip/address", proplist=["address", "interface", "disabled"])
        self.lan_nets, self.wan_ip = [], None
        for a in addrs:
            if a.get("disabled") == "true":
                continue
            iface = ipaddress.ip_interface(a["address"])
            if a.get("interface") == self.wan:
                self.wan_ip = str(iface.ip)
            else:
                self.lan_nets.append(iface.network)
        g = self.geo.lookup(self.wan_ip) if self.wan_ip else None
        # No fixed fallback: without a location for the WAN address the map simply has no home point.
        self.home = {"lat": g["lat"], "lng": g["lng"], "city": g["city"], "cc": g["cc"]} if g else None
        ident = api.print("/system/identity")
        speed = ""
        try:
            mon = api.command(["/interface/ethernet/monitor", f"=numbers={self.wan}", "=once="])
            speed = mon[0].get("rate", "") if mon else ""
        except RouterOSError:
            pass
        rows = ports.exposure(api, self.wan)
        with self.lock:
            self.ports = rows
            self.router.update({"identity": ident[0].get("name", "") if ident else "", "wan": self.wan, "wan_ip": self.wan_ip, "wan_speed": speed})
        self.last_config = time.time()
        log.info("config: WAN %s %s, LAN %s, %d services checked", self.wan, self.wan_ip, ", ".join(map(str, self.lan_nets)), len(rows))

    def in_lan(self, ip):
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(a in n for n in self.lan_nets)

    # ---------- one poll ----------
    def poll(self):
        api, now = self.api, time.time()
        # No .proplist here: on RouterOS 6.49 a proplist on /system/resource makes the router drop the session.
        res = api.print("/system/resource")[0]
        wan_if = api.print("/interface", proplist=["name", "rx-byte", "tx-byte", "running", "link-downs"], name=self.wan)
        leases = api.print("/ip/dhcp-server/lease", proplist=["address", "host-name", "mac-address", "status", "comment"])
        tunnels = []
        for path in ("/interface/ovpn-client", "/interface/l2tp-client", "/interface/pptp-client", "/interface/sstp-client"):
            try:
                for t in api.print(path, proplist=["name", "disabled", "running"]):
                    tunnels.append({"name": t.get("name"), "type": path.rsplit("/", 1)[1], "enabled": t.get("disabled") != "true", "up": t.get("running") == "true"})
            except RouterOSError:
                pass
        table = api.print("/ip/firewall/connection", proplist=CONN_FIELDS)
        self._read_auth(api)

        # WAN rate from interface counters.
        wan = wan_if[0] if wan_if else {}
        rx, tx = int(wan.get("rx-byte", 0) or 0), int(wan.get("tx-byte", 0) or 0)
        rate_in = rate_out = 0.0
        if self.wan_prev and rx >= self.wan_prev[0] and tx >= self.wan_prev[1]:
            dt = max(1.0, now - self.wan_prev[2])
            rate_in, rate_out = (rx - self.wan_prev[0]) * 8 / dt, (tx - self.wan_prev[1]) * 8 / dt
        self.wan_prev = (rx, tx, now)

        self.names, self.makers = {}, {}
        for l in leases:
            ip, mac = l.get("address"), l.get("mac-address", "")
            if not ip:
                continue
            maker = self.vendors.lookup(mac)
            self.makers[ip] = maker
            # A device without a DHCP host name or a lease comment is named after its maker.
            label = l.get("host-name") or l.get("comment")
            if not label:
                label = classify.device_label(maker, mac, oui.short(maker) if maker else "") if mac else ip
            self.names[ip] = label
        bound = sum(1 for l in leases if l.get("status") == "bound")

        # Connections.
        seen, added, updated = set(), [], {}
        for row in table:
            cid = row.get(".id")
            if not cid:
                continue
            seen.add(cid)
            orig, repl = int(row.get("orig-bytes", 0) or 0), int(row.get("repl-bytes", 0) or 0)
            conn = self.conns.get(cid)
            if conn is None:
                conn = self._build(row, now)
                if conn is None:
                    continue
                self.conns[cid] = conn
                added.append(conn)
                prev = None
            else:
                prev = self.prev_bytes.get(cid)
            dt = max(1.0, now - prev[2]) if prev else float(self.cfg["poll_interval"])
            # A connection that is new since the last poll moved all its bytes within the interval.
            # On the very first poll we do not know when existing connections started, so no rate yet.
            fresh = self.polls > 0
            d_orig = orig - prev[0] if prev else (orig if fresh else 0)
            d_repl = repl - prev[1] if prev else (repl if fresh else 0)
            self.prev_bytes[cid] = (orig, repl, now)
            # For a connection opened from inside, "orig" is upload and "repl" is download.
            up, down = (d_orig, d_repl) if conn["kind"] in ("out", "router") else (d_repl, d_orig)
            conn["up"], conn["down"] = max(0, up) * 8 / dt, max(0, down) * 8 / dt
            conn["bytes"] = orig + repl
            conn["state"] = row.get("tcp-state", "")
            conn["seen_reply"] = row.get("seen-reply") == "true"
            conn["assured"] = row.get("assured") == "true"
            conn["packets"] = int(row.get("orig-packets", 0) or 0) + int(row.get("repl-packets", 0) or 0)
            if prev:
                updated[cid] = [round(conn["down"]), round(conn["up"]), conn["state"], conn["cat"]]
        self.polls += 1
        removed = [cid for cid in list(self.conns) if cid not in seen]
        for cid in removed:
            self.conns.pop(cid, None)
            self.prev_bytes.pop(cid, None)
        for c in added:
            if c["kind"] == "in":
                self.inbound.append((now, c["remote"], c["gate"], f'{c["dport"]}/{c["proto"]}'))
        while self.inbound and self.inbound[0][0] < now - 3600:
            self.inbound.popleft()
        self._classify_inbound(now, updated)

        mem_total = int(res.get("total-memory", 0) or 0)
        hdd_total = int(res.get("total-hdd-space", 0) or 0)
        with self.lock:
            self.tunnels = tunnels
            self.router.update({
                "board": res.get("board-name", ""), "cpu_name": res.get("cpu", ""), "version": res.get("version", ""),
                "uptime": res.get("uptime", ""), "cpu": int(res.get("cpu-load", 0) or 0),
                "mem_used": 1 - int(res.get("free-memory", 0) or 0) / mem_total if mem_total else None, "mem_total": mem_total,
                "hdd_used": 1 - int(res.get("free-hdd-space", 0) or 0) / hdd_total if hdd_total else None,
                "wan_in": rate_in, "wan_out": rate_out, "wan_running": wan.get("running") == "true", "link_downs": wan.get("link-downs"),
                "conntrack": len(table), "dhcp_bound": bound, "at": now,
            })
            summary = self._inbound_summary()
            msg = {"type": "poll", "t": now, "router": self.router, "tunnels": tunnels, "added": added, "updated": updated,
                   "removed": removed, "inbound": summary, "ports": self.ports, "home": self.home,
                   "sessions": self.sessions, "auth": self._auth_summary()}
        log.info("poll: %d connections (+%d, -%d), WAN in %.0f kbit/s out %.0f kbit/s", len(self.conns), len(added), len(removed), rate_in / 1e3, rate_out / 1e3)
        return msg

    def _build(self, row, now):
        src, sport = split_addr(row.get("src-address", ""))
        dst, dport = split_addr(row.get("dst-address", ""))
        rsrc, _ = split_addr(row.get("reply-src-address", ""))
        proto = row.get("protocol", "")
        if self.in_lan(src) and is_public(dst):
            kind, remote, device = "out", dst, self.names.get(src, src)
        elif dst == self.wan_ip and is_public(src):
            kind, remote, device = "in", src, None
        elif src == self.wan_ip and is_public(dst):
            kind, remote, device = "router", dst, "Router"
        else:
            return None  # local traffic is not shown on the map
        maker = self.makers.get(src) if kind == "out" else None
        dev_type = classify.device_type(device, maker) if device else None
        service = classify.service_of(remote)
        a = self.asn.lookup(remote)
        forwarded = kind == "in" and rsrc not in ("", self.wan_ip)
        conn = {
            "id": row[".id"], "kind": kind, "src": src, "sport": sport, "dst": dst, "dport": dport,
            "proto": proto, "remote": remote, "device": device, "dev_type": dev_type, "service": service,
            "geo": self.geo.lookup(remote), "maker": oui.short(maker) if maker else None,
            "org": {"asn": a["asn"], "name": classify.org_short(a["org"]), "full": a["org"]} if a else None, "first": now, "up": 0.0, "down": 0.0, "bytes": 0, "state": "", "seen_reply": False,
        }
        if kind == "in":
            conn["gate"] = classify.gate_of(dport, proto)
            conn["forwarded"] = forwarded
            conn["target"] = self.names.get(rsrc, rsrc) if forwarded else None
        conn["cat"] = classify.category(kind, dport, proto, dev_type, service, forwarded)
        return conn

    # ---------- who is on the router ----------
    def _where(self, addr):
        """Describe an address: our LAN, or country, city and organization from DB-IP."""
        ip = (addr or "").split(":")[0]
        if self.in_lan(ip) or not is_public(ip):
            return {"ip": ip, "lan": True}
        a = self.asn.lookup(ip)
        return {"ip": ip, "lan": False, "geo": self.geo.lookup(ip), "org": classify.org_short(a["org"]) if a else None}

    def _read_auth(self, api):
        clock = api.print("/system/clock")
        c = clock[0] if clock else {}
        now = router_time(f'{c.get("date", "")} {c.get("time", "")}', datetime.datetime.now()) if c else None
        now = now or datetime.datetime.now()
        sessions = []
        for u in api.print("/user/active", proplist=["name", "address", "via", "when", "group"]):
            since = router_time(u.get("when", ""), now)
            via = u.get("via", "")
            sessions.append({"user": u.get("name", ""), "via": via, "group": u.get("group", ""), "when": u.get("when", ""),
                             "age": (now - since).total_seconds() if since else None, "interactive": via in INTERACTIVE,
                             "where": self._where(u.get("address", ""))})
        self.sessions = sessions
        for row in api.print("/log", proplist=[".id", "time", "topics", "message"]):
            lid = row.get(".id")
            if not lid or lid in self.log_seen:
                continue
            self.log_seen.add(lid)
            msg = row.get("message", "")
            for kind, rx in AUTH_PATTERNS:
                m = rx.search(msg)
                if not m:
                    continue
                t = router_time(row.get("time", ""), now)
                d = m.groupdict()
                self.auth.append({"id": lid, "kind": kind, "user": d.get("user"), "via": d.get("via"), "time": row.get("time", ""),
                                  "at": t, "where": self._where(d.get("addr", ""))})
                break
        if len(self.log_seen) > 5000:  # the router keeps 1000 lines; forget ids that are long gone
            self.log_seen = {e["id"] for e in self.auth}
        self.router_now = now

    def _auth_summary(self):
        """Login events of the last hour by router time, newest first, without routine API logins and logouts."""
        now = getattr(self, "router_now", None) or datetime.datetime.now()
        recent = [e for e in self.auth if e["at"] and (now - e["at"]).total_seconds() <= 3600]
        shown = [e for e in recent if not (e["kind"] in ("login", "logout") and e["via"] == "api")]
        out = [{k: v for k, v in e.items() if k != "at"} | {"ago": (now - e["at"]).total_seconds()} for e in reversed(shown)][:40]
        return {"events": out, "failures": sum(1 for e in recent if e["kind"] == "failure"),
                "denied": sum(1 for e in recent if e["kind"] == "denied"),
                "logins": sum(1 for e in recent if e["kind"] == "login" and e["via"] != "api")}

    # ---------- scans versus real connections to the router ----------
    def _tried(self, now):
        """Ports each outside address touched on the WAN address during the last hour (conntrack and log refusals)."""
        tried = {}
        for _, ip, _, key in self.inbound:
            tried.setdefault(ip, set()).add(key)
        rnow = getattr(self, "router_now", None)
        for e in self.auth:
            if e["kind"] == "denied" and e["at"] and rnow and (rnow - e["at"]).total_seconds() <= 3600:
                tried.setdefault(e["where"]["ip"], set()).add("8291/tcp" if "winbox" in (e["via"] or "") else f'{e["via"]}')
        return tried

    def _classify_inbound(self, now, updated):
        """A connection to the router is a scan when nobody answered, the handshake never completed,
        a ping sweep sent a packet or two, or the source tried several ports within the hour."""
        tried = self._tried(now)
        for cid, c in self.conns.items():
            if c["kind"] != "in":
                continue
            ports = len(tried.get(c["remote"], ()))
            c["ports_tried"] = ports
            if c["proto"] == "icmp":
                probe = not c["seen_reply"] or c["packets"] <= 3
            elif c["proto"] == "tcp":
                probe = not c["seen_reply"] or (not c["assured"] and c["packets"] <= 6) or c["state"] in ("syn-recv", "syn-received", "syn-sent")
            else:
                probe = not c["seen_reply"]
            org = (c.get("org") or {}).get("full") or ""
            if c.get("forwarded"):
                cat = "p2p"
            elif probe or ports >= 3:
                cat = "research" if RESEARCH.search(org) else "scan"
            else:
                cat = "attack"
            if cat != c["cat"]:
                c["cat"] = cat
                if cid in updated:
                    updated[cid][3] = cat
                else:  # new connections carry their category in "added"; old ones need an update
                    updated[cid] = [round(c["down"]), round(c["up"]), c["state"], cat]
            if cat in ("scan", "research"):
                self.scan_ips[c["remote"]] = now
        cut = now - 3600
        self.scan_ips = {ip: t for ip, t in self.scan_ips.items() if t >= cut}
        self.tried_now = tried

    def _inbound_summary(self):
        """Connections to the WAN address during the last hour: by gate, by port, scans apart from real connections."""
        by_gate, by_port, scan_port, conn_port, sources = {}, {}, {}, {}, set()
        scanners = getattr(self, "scan_ips", {})
        for _, ip, gate, key in self.inbound:
            by_gate.setdefault(gate, set()).add(ip)
            by_port.setdefault(key, set()).add(ip)
            (scan_port if ip in scanners else conn_port).setdefault(key, set()).add(ip)
            sources.add(ip)
        tried = getattr(self, "tried_now", {})
        top = []
        for ip in sorted(scanners, key=lambda i: (-len(tried.get(i, ())), -scanners[i]))[:10]:
            a = self.asn.lookup(ip)
            org = a["org"] if a else ""
            top.append({"ip": ip, "ports": len(tried.get(ip, ())), "sample": sorted(tried.get(ip, ()))[:6],
                        "org": classify.org_short(org) if org else None, "research": bool(RESEARCH.search(org or "")),
                        "geo": self.geo.lookup(ip), "ago": time.time() - scanners[ip]})
        return {"total": len(self.inbound), "sources": len(sources),
                "gates": {g: len(s) for g, s in by_gate.items()}, "ports": {k: len(s) for k, s in by_port.items()},
                "scan_ports": {k: len(s) for k, s in scan_port.items()}, "connect_ports": {k: len(s) for k, s in conn_port.items()},
                "scan_sources": len(scanners), "scanners": top,
                "since": max(self.started, time.time() - 3600)}
