"""Polls an OpenWrt router over ubus, keeps the current picture in memory and publishes changes.

Every poll: system load and memory, WAN counters, neighbours, DHCP names, Wi-Fi clients, logins and the
full conntrack table. Connections are keyed by their original tuple: a new key is a "start", a vanished
key an "end".
Byte counters of conntrack are not used: on a router they are often reset by a traffic monitor (nlbwmon)
or stay at zero, so a per-connection rate would be wrong. Activity comes from the entry's timeout instead:
every packet resets it, so a timeout that went up since the previous poll means the connection carried
traffic in between.
Every config_interval: interfaces and addresses, external address, names file, listening ports, firewall.
"""
import collections
import ipaddress
import logging
import queue
import threading
import time

import classify
import oui
import router
from ubus import Ubus

log = logging.getLogger("collector")


def is_public(ip):
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


class Collector:
    def __init__(self, cfg, geo, asn, names):
        self.cfg, self.geo, self.asn, self.names = cfg, geo, asn, names
        self.u = Ubus(cfg["router_url"], cfg["router_user"], cfg["router_password"], cfg["proxy"] or None)
        self.lock = threading.Lock()
        self.subscribers = []
        self.conns = {}          # tuple key -> connection dict
        self.prev = {}           # tuple key -> (timeout, t) at the previous poll
        self.wan, self.wan_dev = cfg["wan_interface"], None
        self.wan_ips, self.local_ips, self.lan_nets, self.lan_devs, self.tunnels = [], set(), [], set(), []
        self.public_ip, self.public_at = None, 0
        self.home = None
        self.router = {"mode": "router", "hostname": ""}
        self.ports, self.fw_notes = [], []
        self.proc_port = {}
        self.cpu = router.Cpu()
        self.wan_prev, self.wan_first = None, None
        self.inbound = collections.deque()   # (t, ip, gate, port/proto) for the last hour
        self.scan_ips, self.tried_now = {}, {}
        self.logins = router.Logins(self.where)
        self.sessions, self.auth = [], {"events": [], "logins": 0, "failures": 0, "invalid": 0, "brute": []}
        self.started = time.time()
        self.status = {"ok": None, "error": None, "at": None}
        self.next_poll = time.time()
        self.last_config = 0
        self.polls = 0

    # ---------- subscribers (server-sent events) ----------
    def subscribe(self):
        q = queue.Queue(maxsize=50)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

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
                "router": self.router, "ports": self.ports, "fw_notes": self.fw_notes, "tunnels": self.tunnels,
                "vpn_clients": [], "home": self.home, "conns": list(self.conns.values()),
                "inbound": self._inbound_summary(), "sessions": self.sessions, "auth": self.auth,
                "status": self.status, "poll_interval": self.cfg["poll_interval"],
                "next_poll": self.next_poll, "started": self.started, "geo_source": self.geo.source, "asn_source": self.asn.source,
            }

    # ---------- main loop ----------
    def run(self):
        backoff = 5
        while True:
            self.next_poll = time.time() + self.cfg["poll_interval"]
            try:
                if time.time() - self.last_config >= self.cfg["config_interval"] or not self.wan_dev:
                    self.read_config()
                msg = self.poll()
                self.status = {"ok": True, "error": None, "at": time.time()}
                msg["status"] = self.status
                msg["next_poll"] = self.next_poll
                self.publish(msg)
                backoff = 5
            except Exception as e:  # the router is away, the proxy is down, a reply is malformed: keep serving the map
                if isinstance(e, OSError):
                    log.warning("poll failed: %s", e)
                else:
                    log.exception("poll failed")
                self.u.close()
                self.status = {"ok": False, "error": str(e), "at": time.time()}
                self.next_poll = time.time() + backoff
                self.publish({"type": "status", "status": self.status, "next_poll": self.next_poll})
                time.sleep(backoff)
                backoff = min(300, backoff * 2)
                continue
            time.sleep(max(0.0, self.next_poll - time.time()))

    # ---------- config: interfaces, addresses, names, firewall ----------
    def read_config(self):
        u, cfg = self.u, self.cfg
        board = router.board(u)
        ifaces = router.interfaces(u)
        wan = next((i for i in ifaces if i["name"] == self.wan), None)
        if wan is None:
            raise OSError(f"wan_interface={self.wan}: no such interface on the router; there are {', '.join(i['name'] for i in ifaces)}")
        self.wan_dev = wan["device"]
        # wan and wan6 share one device: every address on it is a WAN address
        wan_addrs = [a for i in ifaces if i["device"] == self.wan_dev for a in i["addrs"] if not a.ip.is_link_local]
        self.wan_ips = [str(a.ip) for a in wan_addrs]
        self.local_ips = {str(a.ip) for i in ifaces for a in i["addrs"]}
        lan = [i for i in ifaces if i["device"] != self.wan_dev and i["name"] != "loopback" and i["up"]]
        self.lan_devs = {i["device"] for i in lan}
        self.lan_nets = [a.network for i in lan for a in i["addrs"] if not a.ip.is_link_local and a.network.prefixlen < a.max_prefixlen]
        self.tunnels = [{"name": i["device"], "up": i["up"], "addrs": [str(a) for a in i["addrs"]]}
                        for i in ifaces if i["device"] and router.TUNNEL.match(i["device"])]

        # External address: from settings, or on the WAN interface, or asked from a public service through
        # the same proxy when the WAN address is private (the router sits behind another router).
        wan4 = next((a for a in self.wan_ips if ":" not in a), None)
        public, source = None, None
        setting = cfg["public_ip"].strip().lower()
        if setting not in ("", "auto", "off"):
            public, source = cfg["public_ip"].strip(), "config"
        elif wan4 and is_public(wan4):
            public, source = wan4, "wan"
        elif setting != "off":
            if self.public_at == 0 or time.time() - self.public_at > cfg["config_interval"] - 1 or self.public_ip is None:
                self.public_ip, self.public_at = router.external_ip(u.opener), time.time()
            public, source = self.public_ip, "auto"
        self.public_ip = public
        if public:
            self.local_ips.add(public)
        wan6 = next((a for a in self.wan_ips if ":" in a and is_public(a)), None)
        g = self.geo.lookup(public or wan6) if (public or wan6) else None
        if cfg["location"]:
            lat, lng = cfg["location"]
            self.home = {"lat": lat, "lng": lng, "city": g["city"] if g else "", "cc": g["cc"] if g else ""}
        elif g:
            self.home = {"lat": g["lat"], "lng": g["lng"], "city": g["city"], "cc": g["cc"]}
        else:
            self.home = {"lat": 55.75, "lng": 37.62, "city": "", "cc": ""}

        self.names.reload()
        try:
            self.names.set_hosts(u.read("/etc/hosts"))
        except OSError as e:
            log.debug("hosts: %s", e)
        self._read_devices()
        listen = router.listeners(u)
        self.proc_port = {}
        for s in listen:
            if s["process"]:
                self.proc_port.setdefault((s["proto"], s["lport"]), s["process"])
        fw = router.Firewall(u, self.wan, self.wan_dev)
        notes = [] if fw.ok else ["конфигурация firewall не прочитана: вывод по портам приблизительный"]
        rows = router.exposure(listen, fw, self.wan_ips, lambda ip: self.names.of(ip)[0])

        with self.lock:
            self.ports, self.fw_notes = rows, notes
            self.router.update(board)
            self.router.update({
                "mode": "router", "cores": self.cpu.cores, "wan": self.wan_dev, "wan_ips": self.wan_ips, "public_ip": public,
                "public_source": source, "double_nat": bool(public and wan4 and not is_public(wan4)), "gateway": wan["gateway"],
                "dns": wan["dns"][:3], "lan": [str(n) for n in self.lan_nets], "ipv6": bool(wan6), "forwarding": True,
            })
        self.last_config = time.time()
        log.info("config: %s %s, WAN %s %s, public %s, LAN %s, %d listeners, firewall zone %s (input %s)",
                 board["machine"], board["os"], self.wan_dev, ", ".join(self.wan_ips) or "-", public or "unknown",
                 ", ".join(map(str, self.lan_nets)) or "-", len(listen), fw.zone, fw.policy)

    def _read_devices(self):
        """Neighbours, DHCP names and Wi-Fi clients: who is in the LAN and what they are called."""
        u = self.u
        neigh = router.neighbors(u)
        self.names.neigh = {ip: mac for ip, (mac, _) in neigh.items()}
        self.names.dhcp = router.dhcp_names(u)
        wifi = router.wifi_clients(u)
        lan = {mac for ip, (mac, dev) in neigh.items() if dev in self.lan_devs and ":" not in ip}
        return len(lan), len(wifi)

    def in_lan(self, ip):
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(a.version == n.version and a in n for n in self.lan_nets)

    # ---------- one poll ----------
    def poll(self):
        u = self.u
        now = time.time()
        r = router.info(u)
        router_now = r.pop("localtime", None) or now
        r["cpu"] = self.cpu.load(u)
        r["cores"] = self.cpu.cores
        r["temp"] = None

        # WAN rate from the device counters: these are exact, unlike the per-connection ones.
        devs = router.devices(u)
        wd = devs.get(self.wan_dev, {"rx": 0, "tx": 0, "up": False, "speed": None})
        rate_in = rate_out = 0.0
        if self.wan_prev and wd["rx"] >= self.wan_prev[0] and wd["tx"] >= self.wan_prev[1]:
            dt = max(1.0, now - self.wan_prev[2])
            rate_in, rate_out = (wd["rx"] - self.wan_prev[0]) * 8 / dt, (wd["tx"] - self.wan_prev[1]) * 8 / dt
        self.wan_prev = (wd["rx"], wd["tx"], now)
        if not self.wan_first or wd["rx"] < self.wan_first[0]:
            self.wan_first = (wd["rx"], wd["tx"])

        n_lan, n_wifi = self._read_devices()
        sessions, auth = self.logins.read(u, router_now)
        table = router.conntrack_table(u)
        ct_count, ct_max = router.conntrack_usage(u)

        with self.lock:
            seen, added, updated = set(), [], {}
            for row in table:
                key = row["key"]
                seen.add(key)
                conn = self.conns.get(key)
                prev = self.prev.get(key)
                if conn is None:
                    conn = self._build(row, now)
                    if conn is None:
                        continue
                    self.conns[key] = conn
                    added.append(conn)
                    self._count_inbound(conn, now)
                timeout = row.get("timeout")
                # Alive: the timeout went up, or fell by less than the time that passed (a packet reset it meanwhile).
                if prev is None or timeout is None or prev[0] is None:
                    active = self.polls > 0 and prev is None
                else:
                    active = timeout > prev[0] - (now - prev[1]) + 2
                self.prev[key] = (timeout, now)
                conn["active"] = bool(active)
                conn["state"] = row["state"]
                if conn["kind"] == "in":
                    conn["replied"] = classify.replied(row["proto"], row["state"], row["unreplied"])
                    conn["assured"] = row["assured"]
                renamed = self._refresh_name(conn)
                if prev is not None or renamed:
                    updated[key] = self._update_row(conn)
            while self.inbound and self.inbound[0][0] < now - 3600:
                self.inbound.popleft()
            self._classify_inbound(now, updated)
            self.polls += 1
            removed = [k for k in self.conns if k not in seen]
            for k in removed:
                self.conns.pop(k, None)
                self.prev.pop(k, None)

            self.sessions, self.auth = sessions, auth
            self.router.update(r)
            self.router.update({
                "wan_in": rate_in, "wan_out": rate_out, "wan_up": wd["up"], "wan_speed": wd["speed"],
                "wan_rx": wd["rx"] - self.wan_first[0], "wan_tx": wd["tx"] - self.wan_first[1], "carrier_changes": None,
                "conntrack": ct_count if ct_count is not None else len(table), "conntrack_max": ct_max,
                "conn_source": "ubus", "acct": True, "rates": False, "events": False,
                "devices": n_lan, "wifi_clients": n_wifi, "shown": len(self.conns), "at": now,
            })
            msg = {"type": "poll", "t": now, "router": self.router, "tunnels": self.tunnels, "vpn_clients": [],
                   "added": added, "updated": updated, "removed": removed, "inbound": self._inbound_summary(),
                   "sessions": sessions, "auth": auth, "ports": self.ports, "fw_notes": self.fw_notes, "home": self.home,
                   "fresh": self.polls == 1}  # the first poll brings the table, not new connections
        log.info("poll: %d connections (+%d, -%d), %d active, WAN in %.0f kbit/s out %.0f kbit/s", len(self.conns), len(added), len(removed),
                 sum(1 for c in self.conns.values() if c["active"]), rate_in / 1e3, rate_out / 1e3)
        return msg

    # ---------- one connection ----------
    def _refresh_name(self, conn):
        """Names come later than connections: a DHCP lease appears, a line is added to names.conf. True if changed."""
        if conn["kind"] != "out":
            return False
        before = conn["device"]
        name, kind, maker = self.names.of(conn["src"])
        conn["device"] = name
        if kind:
            conn["dev_type"] = kind
        return conn["device"] != before

    def _build(self, e, now):
        src, dst, rsrc = e["src"], e["dst"], e["rsrc"]
        sport, dport, proto = e["sport"], e["dport"], e["proto"]
        process, target, forwarded, maker, dev_kind = None, None, False, None, None
        if is_public(dst) and self.in_lan(src) and src not in self.local_ips:
            kind, remote = "out", dst
            device, dev_kind, maker = self.names.of(src)
        elif is_public(src) and dst in self.local_ips:
            kind, remote, device = "in", src, None
            forwarded = bool(rsrc) and rsrc != dst and rsrc not in self.local_ips
            if forwarded:
                target = self.names.of(rsrc)[0]
            else:
                process = self.proc_port.get((proto, dport))
        elif is_public(src) and self.in_lan(dst):
            kind, remote, device = "in", src, None   # routed without NAT, usually IPv6
            forwarded, target = True, self.names.of(dst)[0]
        elif is_public(dst) and src in self.local_ips:
            kind, remote, device, dev_kind = "router", dst, "роутер", "ap"
        else:
            return None  # local traffic is not shown on the map
        dev_type = dev_kind or (classify.device_type(device, maker) if kind == "out" else dev_kind)
        service = classify.service_of(remote)
        a = self.asn.lookup(remote)
        answered = classify.replied(proto, e["state"], e["unreplied"])
        conn = {
            "id": e["key"], "kind": kind, "src": src, "sport": sport, "dst": dst, "dport": dport, "proto": proto,
            "remote": remote, "remote_name": self.names.remote(remote), "device": device, "dev_type": dev_type,
            "service": service, "process": process, "geo": self.geo.lookup(remote), "maker": oui.short(maker) if maker else None,
            "org": {"asn": a["asn"], "name": classify.org_short(a["org"]), "full": a["org"]} if a else None,
            "first": now, "up": 0.0, "down": 0.0, "bytes": 0, "state": e["state"], "replied": answered, "active": None,
            "assured": e.get("assured", False),
        }
        if kind == "in":
            conn["gate"] = classify.gate_of(dport, proto)
            conn["forwarded"] = forwarded
            conn["target"] = target
            conn["ports_tried"] = len(self.tried_now.get(remote, ())) or 1
        conn["cat"] = classify.category(kind, dport, proto, dev_type, service, forwarded, False, answered, True)
        return conn

    @staticmethod
    def _update_row(conn):
        """What the browser needs to refresh an existing connection (rates are not known here: zeros)."""
        return [0, 0, conn["state"], conn["device"], conn.get("process"), conn["cat"], conn["active"], conn.get("ports_tried"), None]

    def where(self, ip):
        """An address for the logins plate: local or not, place and organization."""
        try:
            lan = not ipaddress.ip_address(ip).is_global
        except ValueError:
            return {"ip": ip, "lan": True}
        if lan:
            return {"ip": f"{ip} · {self.names.of(ip)[0]}" if self.names.of(ip)[0] != ip else ip, "lan": True}
        a = self.asn.lookup(ip)
        return {"ip": ip, "lan": False, "geo": self.geo.lookup(ip), "org": classify.org_short(a["org"]) if a else None}

    # ---------- scans versus real connections to the router ----------
    def _classify_inbound(self, now, updated):
        """A connection from the internet is a scan when nobody answered, the TCP handshake never completed,
        or the source touched several ports within the hour without a solid session. Addresses named in
        names.conf are never scanners. Packet counts are not used: the router's counters cannot be trusted."""
        tried = {}
        for _, ip, _, key in self.inbound:
            tried.setdefault(ip, set()).add(key)
        self.tried_now = tried
        for key, c in self.conns.items():
            if c["kind"] != "in":
                continue
            ports = len(tried.get(c["remote"], ())) or 1
            if c.get("remote_name"):
                cat = "session"
            else:
                answered = c.get("replied", True)
                solid = answered and (c.get("assured", False) or c["proto"] == "icmp")
                if not answered or (ports >= 3 and not solid):
                    org = (c.get("org") or {}).get("full") or ""
                    cat = "research" if classify.RESEARCH.search(org) else "scan"
                else:
                    cat = "session"
            if (cat, ports) != (c["cat"], c.get("ports_tried")):
                c["cat"], c["ports_tried"] = cat, ports
                updated[key] = self._update_row(c)
            if cat in ("scan", "research"):
                self.scan_ips[c["remote"]] = now
        self.scan_ips = {ip: t for ip, t in self.scan_ips.items() if t >= now - 3600}

    def _count_inbound(self, conn, now):
        if conn["kind"] == "in":
            self.inbound.append((now, conn["remote"], conn["gate"], f'{conn["dport"]}/{conn["proto"]}'))

    def _inbound_summary(self):
        """Connections from the internet during the last hour: by gate, by port, scans apart from real connections."""
        by_gate, by_port, scan_port, conn_port, sources = {}, {}, {}, {}, set()
        for _, ip, gate, key in self.inbound:
            by_gate.setdefault(gate, set()).add(ip)
            by_port.setdefault(key, set()).add(ip)
            (scan_port if ip in self.scan_ips else conn_port).setdefault(key, set()).add(ip)
            sources.add(ip)
        top = []
        for ip in sorted(self.scan_ips, key=lambda i: (-len(self.tried_now.get(i, ())), -self.scan_ips[i]))[:10]:
            a = self.asn.lookup(ip)
            org = a["org"] if a else ""
            top.append({"ip": ip, "ports": len(self.tried_now.get(ip, ())), "sample": sorted(self.tried_now.get(ip, ()))[:6],
                        "org": classify.org_short(org) if org else None, "research": bool(classify.RESEARCH.search(org or "")),
                        "geo": self.geo.lookup(ip), "ago": time.time() - self.scan_ips[ip]})
        return {"total": len(self.inbound), "sources": len(sources),
                "gates": {g: len(s) for g, s in by_gate.items()}, "ports": {k: len(s) for k, s in by_port.items()},
                "scan_ports": {k: len(s) for k, s in scan_port.items()}, "connect_ports": {k: len(s) for k, s in conn_port.items()},
                "scan_sources": len(self.scan_ips), "scanners": top,
                "since": max(self.started, time.time() - 3600)}
