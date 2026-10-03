"""Reads this machine, keeps the current picture in memory and publishes changes.

Every poll: CPU, memory, disk, WAN counters, neighbours (router mode), OpenVPN clients, sockets and the
full conntrack table. Connections are keyed by their original tuple: a new key is a "start", a vanished
key an "end", and the byte counters between two polls give the current rate of each connection.
Between polls the `conntrack -E` stream reports starts and ends as they happen; they are sent to the
browser in small batches once a second.
Every config_interval: interfaces and addresses, external address, names file, listeners and firewall.

Without a conntrack table (nf_conntrack not loaded) connections come from `ss`: only this machine's own
sockets, TCP rates from the socket byte counters.
"""
import collections
import ipaddress
import logging
import os
import queue
import socket
import subprocess
import threading
import time

import classify
import logins
import openvpn
import oui
import ports
import sockets
import sysinfo
from conntrack import Conntrack

log = logging.getLogger("collector")


def is_public(ip):
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


class Collector:
    def __init__(self, cfg, geo, asn, names):
        self.cfg, self.geo, self.asn, self.names = cfg, geo, asn, names
        self.router_mode = cfg["mode"] == "router"
        self.lock = threading.Lock()
        self.subscribers = []
        self.conns = {}          # tuple key -> connection dict
        self.prev_bytes = {}     # tuple key -> (orig, repl, t)
        self.destroyed = {}      # tuple key -> time of its DESTROY event, so a poll does not bring it back
        self.pending_added, self.pending_removed = [], []
        # Inbound connections announced by `conntrack -E` wait here until they can be judged: a new TCP connection
        # always starts unanswered, so at NEW a real session and a scan look the same. They are decided by the
        # next poll (the table has the current state) or by their DESTROY event (it has the final [ASSURED] flag).
        self.pending_in = {}
        self.wan = None
        self.wan_ips, self.local_ips, self.lan_nets, self.lan_ifaces, self.tun_ifaces = [], set(), [], [], []
        self.ipv6 = False
        self.public_ip, self.public_auto_at = None, 0
        self.home = None
        self.router = {"mode": cfg["mode"], "hostname": socket.gethostname()}
        self.ports, self.fw_notes = [], []
        self.tunnels, self.vpn_clients, self.vpn_real = [], [], {}
        self.proc_tuple, self.proc_port, self.listen_ports = {}, {}, set()
        self.cpu = sysinfo.Cpu()
        self.wan_prev, self.wan_first = None, None
        self.inbound = collections.deque()   # (t, ip, gate, port/proto) for the last hour
        self.scan_ips = {}                   # outside address -> last time it looked like a scan
        self.tried_now = {}                  # outside address -> ports it touched during the last hour
        self.logins = logins.Logins(self.where)
        self.sessions, self.auth = [], {"events": [], "logins": 0, "failures": 0, "invalid": 0, "brute": []}
        self.started = time.time()
        self.status = {"ok": False, "error": "ещё не опрашивали", "at": None}
        self.next_poll = time.time()
        self.last_config = 0
        self.polls = 0
        self.ct = None
        self.acct = False
        self.events = False
        self.ct_error = None

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
                "vpn_clients": self.vpn_clients, "home": self.home, "conns": list(self.conns.values()),
                "inbound": self._inbound_summary(), "sessions": self.sessions, "auth": self.auth,
                "status": self.status, "poll_interval": self.cfg["poll_interval"],
                "next_poll": self.next_poll, "started": self.started, "geo_source": self.geo.source, "asn_source": self.asn.source,
            }

    # ---------- main loop ----------
    def run(self):
        self.read_config()
        self.ct = Conntrack(self.ipv6)
        self.acct = Conntrack.loaded() and Conntrack.accounting(self.cfg["conntrack_acct"])
        if self.ct.source and Conntrack.loaded():
            log.info("connections from %s, byte accounting %s", self.ct.source, "on" if self.acct else "off (no rates, see conntrack_acct)")
            if self.cfg["conntrack_events"]:
                self.events = self.ct.follow(self.on_event)
                if self.events:
                    threading.Thread(target=self._flush_loop, name="flush", daemon=True).start()
        else:
            log.warning("no conntrack table (%s): connections of this machine only, from ss",
                        "nf_conntrack module is not loaded" if not Conntrack.loaded() else "no conntrack tool and no /proc/net/nf_conntrack")
        backoff = 5
        while True:
            self.next_poll = time.time() + self.cfg["poll_interval"]
            try:
                if time.time() - self.last_config >= self.cfg["config_interval"]:
                    self.read_config()
                msg = self.poll()
                self.status = {"ok": True, "error": None, "at": time.time()}
                msg["status"] = self.status
                msg["next_poll"] = self.next_poll
                self.publish(msg)
                backoff = 5
            except Exception as e:  # keep serving the map whatever breaks in one poll
                log.exception("poll failed")
                self.status = {"ok": False, "error": str(e), "at": time.time()}
                self.next_poll = time.time() + backoff
                self.publish({"type": "status", "status": self.status, "next_poll": self.next_poll})
                time.sleep(backoff)
                backoff = min(300, backoff * 2)
                continue
            time.sleep(max(0.0, self.next_poll - time.time()))

    # ---------- config: interfaces, addresses, names, firewall ----------
    def read_config(self):
        cfg = self.cfg
        links = sysinfo.addresses()
        gw_if, gw = sysinfo.default_route("-4")
        gw6_if, gw6 = sysinfo.default_route("-6")
        wan = cfg["wan_interface"] or gw_if or gw6_if
        if wan not in links:
            log.warning("WAN interface %r not found; interfaces: %s", wan, ", ".join(links))
        self.wan = wan
        wan_addrs = [a for a in links.get(wan, {}).get("addrs", []) if not a.ip.is_link_local]
        self.wan_ips = [str(a.ip) for a in wan_addrs]
        self.local_ips = {str(a.ip) for l in links.values() for a in l["addrs"]}

        self.tun_ifaces = [n for n, l in links.items() if n != wan and (sysinfo.TUNNEL.match(n) or l["type"] == "none") and n != "lo"]
        if self.router_mode:
            lan = cfg["lan_interfaces"] or [n for n in links if n != wan and not sysinfo.VIRTUAL.match(n) and n not in self.tun_ifaces]
        else:
            lan = list(cfg["lan_interfaces"])
        self.lan_ifaces = [n for n in lan if n in links] + [n for n in self.tun_ifaces if n not in lan]
        self.lan_nets = [a.network for n in self.lan_ifaces for a in links[n]["addrs"]
                         if not a.ip.is_link_local and not a.ip.is_loopback and a.network.prefixlen < a.max_prefixlen]

        self.ipv6 = cfg["ipv6"] == "on" or (cfg["ipv6"] == "auto" and any(a.version == 6 and a.ip.is_global for a in wan_addrs))

        # External address: from settings, or on the WAN interface, or asked from a public service
        # when the WAN address is private (cloud 1:1 NAT, provider NAT). public_ip=off never asks.
        wan4 = next((a for a in self.wan_ips if ":" not in a), None)
        public, source = None, None
        setting = cfg["public_ip"].strip().lower()
        if setting not in ("", "auto", "off"):
            public, source = cfg["public_ip"].strip(), "config"
        elif wan4 and is_public(wan4):
            public, source = wan4, "wan"
        elif setting != "off":
            if self.public_auto_at == 0 or time.time() - self.public_auto_at > cfg["config_interval"] - 1 or self.public_ip is None:
                self.public_ip, self.public_auto_at = sysinfo.external_ip(), time.time()
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
        listen = sockets.listeners()
        self.listen_ports = {(s["proto"], s["lport"]) for s in listen}
        rulesets, notes = ports.load(self.ipv6)
        local_for_fw = [ip for ip in self.wan_ips if not ipaddress.ip_address(ip).is_link_local]
        forwards = []
        for rs in rulesets.get(4, []) + rulesets.get(6, []):
            forwards.extend(rs.forwards(wan, [ipaddress.ip_address(ip) for ip in local_for_fw]))
        # Who is behind a port forward: a Docker container by its address, or a named LAN device.
        containers = sysinfo.docker_containers() if forwards else {}
        for f in forwards:
            ip, port = sockets.split_hostport(f["to"]) if ":" in f["to"] and not f["to"].count(":") > 1 or f["to"].startswith("[") else (f["to"], 0)
            name = containers.get(ip) or (self.names.of(ip, reverse=False)[0] if ip else None)
            f["to_name"] = name if name and name != ip else None
            f["opener"] = "docker" if f.get("chain", "").upper().startswith("DOCKER") or ip in containers else "DNAT"
        rows = ports.exposure(listen, rulesets, forwards, wan, local_for_fw, self.ipv6)

        product, cpu_model = sysinfo.machine()
        u = os.uname()
        with self.lock:
            self.ports, self.fw_notes = rows, notes
            self.router.update({
                "hostname": socket.gethostname(), "os": sysinfo.os_release(), "kernel": u.release, "arch": u.machine,
                "machine": product, "cpu_model": cpu_model, "cores": os.cpu_count(),
                "wan": wan, "wan_ips": self.wan_ips, "public_ip": public, "public_source": source,
                "double_nat": bool(public and wan4 and not is_public(wan4)), "gateway": gw or gw6, "dns": sysinfo.dns_servers(),
                "lan": [str(n) for n in self.lan_nets], "ipv6": self.ipv6, "forwarding": sysinfo.ip_forward(),
            })
        self.last_config = time.time()
        log.info("config: %s mode, WAN %s %s, public %s, LAN %s, %d listeners, %d rows on the ports plate%s",
                 self.cfg["mode"], wan, ", ".join(self.wan_ips) or "-", public or "unknown",
                 ", ".join(map(str, self.lan_nets)) or "-", len(listen), len(rows), "; " + "; ".join(notes) if notes else "")

    def in_lan(self, ip):
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(a.version == n.version and a in n for n in self.lan_nets)

    # ---------- live events from conntrack -E ----------
    def on_event(self, e):
        now = time.time()
        with self.lock:
            key = e["key"]
            if e["event"] == "NEW":
                if key in self.conns or key in self.pending_in:
                    return
                conn = self._build(e, now)
                if conn is None:
                    return
                if conn["kind"] == "in":
                    self.pending_in[key] = conn
                    return
                self.conns[key] = conn
                self.prev_bytes[key] = (0, 0, now)
                self.pending_added.append(conn)
            else:
                self.destroyed[key] = now
                conn = self.pending_in.pop(key, None)
                if conn is not None:
                    # Opened and closed between two polls: judge it by its final state and show it once.
                    conn["replied"] = not e["unreplied"]
                    conn["assured"], conn["packets"] = e["assured"], e["orig_packets"]
                    self._count_inbound(conn, now)
                    self._judge(conn, final=True)
                    self.pending_added.append(conn)
                    self.pending_removed.append(key)
                elif self.conns.pop(key, None) is not None:
                    self.prev_bytes.pop(key, None)
                    self.pending_removed.append(key)

    def _flush_loop(self):
        while True:
            time.sleep(1)
            with self.lock:
                added, removed = self.pending_added, self.pending_removed
                self.pending_added, self.pending_removed = [], []
            if added or removed:
                self.publish({"type": "delta", "t": time.time(), "added": added, "removed": removed})

    # ---------- one poll ----------
    def poll(self):
        now = time.time()
        r = {"cpu": self.cpu.load(), "load": sysinfo.loadavg(), "temp": sysinfo.temperature(), "uptime": sysinfo.uptime()}
        r.update(sysinfo.memory())
        r.update(sysinfo.disk("/"))

        # WAN rate from interface counters.
        wc = sysinfo.iface_counters(self.wan) if self.wan else {"rx": 0, "tx": 0, "up": False, "speed": None, "carrier_changes": None}
        rate_in = rate_out = 0.0
        if self.wan_prev and wc["rx"] >= self.wan_prev[0] and wc["tx"] >= self.wan_prev[1]:
            dt = max(1.0, now - self.wan_prev[2])
            rate_in, rate_out = (wc["rx"] - self.wan_prev[0]) * 8 / dt, (wc["tx"] - self.wan_prev[1]) * 8 / dt
        self.wan_prev = (wc["rx"], wc["tx"], now)
        if not self.wan_first or wc["rx"] < self.wan_first[0]:
            self.wan_first = (wc["rx"], wc["tx"])

        devices = self.names.read_neighbors(set(self.lan_ifaces)) if self.lan_ifaces else None

        # OpenVPN clients: their tunnel addresses get names. The tunnel itself is the connection from the client's
        # address *and port*: several clients behind one NAT, or an SSH session from the same address, stay apart.
        clients = openvpn.clients(self.cfg["openvpn_status"])
        self.names.vpn = {v.split("/")[0]: c["name"] for c in clients for v in c["virtual"]}
        self.vpn_real = {(c["real"], c["real_port"]): c["name"] for c in clients}
        for c in clients:
            g = self.geo.lookup(c["real"])
            c["place"] = {"city": g["city"], "cc": g["cc"]} if g else None
        links = sysinfo.addresses()
        tunnels = [{"name": n, "up": links[n]["up"], "addrs": [str(a) for a in links[n]["addrs"]]} for n in self.tun_ifaces if n in links]

        # Logins: open sessions and sshd messages since the last poll.
        sessions = self.logins.sessions()
        self.logins.read()
        auth = self.logins.summary()

        # Sockets: process names, and the connections themselves when there is no conntrack table.
        socks = sockets.connections()
        self.proc_tuple, self.proc_port = sockets.process_index(socks)
        use_ct = self.ct and self.ct.source and Conntrack.loaded()
        snap_t = time.time()
        table = None
        if use_ct:
            try:
                table = self.ct.table()
            except (OSError, subprocess.SubprocessError) as e:  # e.g. not root: fall back to this machine's sockets
                if not self.ct_error:
                    log.warning("%s; connections from ss instead", e)
                self.ct_error, use_ct = str(e), False
        if table is None:
            table = sockets.as_conntrack(socks, self.listen_ports)

        with self.lock:
            seen, added, updated = set(), [], {}
            for row in table:
                key = row["key"]
                seen.add(key)
                conn = self.conns.get(key)
                if conn is None:
                    if self.destroyed.get(key, 0) >= snap_t:
                        continue  # ended after the table was read
                    conn = self.pending_in.pop(key, None) or self._build(row, now)
                    if conn is None:
                        continue
                    self.conns[key] = conn
                    added.append(conn)
                    self._count_inbound(conn, now)
                    prev = None
                else:
                    prev = self.prev_bytes.get(key)
                orig, repl = row["orig_bytes"], row["repl_bytes"]
                dt = max(1.0, now - prev[2]) if prev else float(self.cfg["poll_interval"])
                # A connection that is new since the last poll moved all its bytes within the interval.
                # On the very first poll we do not know when existing connections started, so no rate yet.
                fresh = self.polls > 0
                d_orig = orig - prev[0] if prev else (orig if fresh else 0)
                d_repl = repl - prev[1] if prev else (repl if fresh else 0)
                self.prev_bytes[key] = (orig, repl, now)
                # For a connection opened from inside, "orig" is upload and "repl" is download.
                up, down = (d_orig, d_repl) if conn["kind"] in ("out", "router") else (d_repl, d_orig)
                conn["up"], conn["down"] = max(0, up) * 8 / dt, max(0, down) * 8 / dt
                conn["bytes"] = orig + repl
                conn["state"] = row["state"]
                # Active: bytes moved since the last poll. Unknown (None) without byte accounting.
                conn["active"] = (d_orig + d_repl > 0) if (self.acct or not use_ct) else None
                if conn["kind"] == "in":
                    conn["replied"] = classify.replied(row["proto"], row["state"], row["unreplied"])
                    conn["assured"] = row["assured"]
                    conn["packets"] = row["orig_packets"]
                renamed = self._refresh_name(conn)
                if prev or renamed or conn["active"]:
                    updated[key] = self._update_row(conn)
            while self.inbound and self.inbound[0][0] < now - 3600:
                self.inbound.popleft()
            self._classify_inbound(now, updated, packets_known=bool(self.acct and use_ct))
            self.polls += 1
            # Entries that appeared through events after the table was read are not "gone".
            removed = [k for k, c in self.conns.items() if k not in seen and c["first"] < snap_t]
            # Announced inbound connections that no table ever showed and no DESTROY closed: forget them.
            self.pending_in = {k: c for k, c in self.pending_in.items() if c["first"] >= now - 3 * self.cfg["poll_interval"]}
            for k in removed:
                self.conns.pop(k, None)
                self.prev_bytes.pop(k, None)
            self.destroyed = {k: t for k, t in self.destroyed.items() if t > now - 5}

            ct_count, ct_max = sysinfo.conntrack_usage()
            self.tunnels, self.vpn_clients = tunnels, clients
            self.sessions, self.auth = sessions, auth
            self.router.update(r)
            self.router.update({
                "wan_in": rate_in, "wan_out": rate_out, "wan_up": wc["up"], "wan_speed": wc["speed"],
                "wan_rx": wc["rx"] - self.wan_first[0], "wan_tx": wc["tx"] - self.wan_first[1], "carrier_changes": wc["carrier_changes"],
                "conntrack": ct_count if use_ct else len(table), "conntrack_max": ct_max if use_ct else None,
                "conn_source": self.ct.source if use_ct else "ss", "acct": self.acct if use_ct else True, "events": self.events and use_ct,
                "devices": devices, "shown": len(self.conns), "at": now,
            })
            msg = {"type": "poll", "t": now, "router": self.router, "tunnels": tunnels, "vpn_clients": clients,
                   "added": added, "updated": updated, "removed": removed, "inbound": self._inbound_summary(),
                   "sessions": sessions, "auth": auth, "ports": self.ports, "fw_notes": self.fw_notes, "home": self.home,
                   "fresh": self.polls == 1}  # the first poll brings the table, not new connections
        log.info("poll: %d connections (+%d, -%d), WAN in %.0f kbit/s out %.0f kbit/s", len(self.conns), len(added), len(removed), rate_in / 1e3, rate_out / 1e3)
        return msg

    # ---------- one connection ----------
    def _process(self, proto, lip, lport, pip, pport):
        return self.proc_tuple.get((proto, lip, lport, pip, pport)) or self.proc_port.get((proto, lport))

    def _self_label(self, process):
        name = process or "система"
        return f"{name} (роутер)" if self.router_mode else name

    def _refresh_name(self, conn):
        """Names come later than connections: reverse DNS answers, a process shows up in ss. True if changed."""
        before = (conn["device"], conn.get("process"))
        if conn["kind"] == "out":
            name, kind, maker = self.names.of(conn["src"])
            conn["device"] = name
            if kind:
                conn["dev_type"] = kind
        elif conn["kind"] == "router" and not conn.get("process"):
            conn["process"] = self.proc_tuple.get((conn["proto"], conn["src"], conn["sport"], conn["dst"], conn["dport"]))
            conn["device"] = self._self_label(conn["process"])
        elif conn["kind"] == "in" and not conn.get("process") and not conn.get("forwarded"):
            conn["process"] = self._process(conn["proto"], conn["dst"], conn["dport"], conn["src"], conn["sport"])
        return (conn["device"], conn.get("process")) != before

    def _build(self, e, now):
        src, dst, rsrc = e["src"], e["dst"], e["rsrc"]
        sport, dport, proto = e["sport"], e["dport"], e["proto"]
        process, target, forwarded = None, None, False
        if is_public(dst) and self.in_lan(src) and src not in self.local_ips:
            kind, remote = "out", dst
            device, dev_kind, maker = self.names.of(src)
        elif is_public(src) and dst in self.local_ips:
            kind, remote, device, dev_kind, maker = "in", src, None, None, None
            forwarded = bool(rsrc) and rsrc != dst and rsrc not in self.local_ips
            if forwarded:
                target = self.names.of(rsrc)[0]
            else:
                process = e.get("process") or self._process(proto, dst, dport, src, sport)
        elif is_public(src) and self.in_lan(dst):
            # Routed without NAT, usually IPv6: the internet talks to a LAN device directly.
            kind, remote, device, dev_kind, maker = "in", src, None, None, None
            forwarded, target = True, self.names.of(dst)[0]
        elif is_public(dst) and src in self.local_ips:
            kind, remote, dev_kind, maker = "router", dst, "server", None
            process = e.get("process") or self.proc_tuple.get((proto, src, sport, dst, dport))
            device = self._self_label(process)
        else:
            return None  # local traffic is not shown on the map
        dev_type = dev_kind or (classify.device_type(device, maker) if kind == "out" else dev_kind)
        service = classify.service_of(remote)
        a = self.asn.lookup(remote)
        vpn_client = kind == "in" and (remote, sport) in self.vpn_real
        answered = classify.replied(proto, e["state"], e["unreplied"])
        conn = {
            "id": e["key"], "kind": kind, "src": src, "sport": sport, "dst": dst, "dport": dport, "proto": proto,
            "remote": remote, "remote_name": self.names.remote(remote), "device": device, "dev_type": dev_type,
            "service": service, "process": process, "geo": self.geo.lookup(remote), "maker": oui.short(maker) if maker else None,
            "org": {"asn": a["asn"], "name": classify.org_short(a["org"]), "full": a["org"]} if a else None,
            "first": now, "up": 0.0, "down": 0.0, "bytes": 0, "state": e["state"], "replied": answered, "active": None,
            "assured": e.get("assured", False), "packets": e.get("orig_packets", 0),
        }
        if kind == "in":
            conn["gate"] = classify.gate_of(dport, proto)
            conn["forwarded"] = forwarded
            conn["target"] = target
            if vpn_client:
                conn["vpn_client"] = self.vpn_real[(remote, sport)]
        conn["cat"] = classify.category(kind, dport, proto, dev_type, service, forwarded, vpn_client, answered, self.router_mode)
        if kind == "in":
            conn["ports_tried"] = len(self.tried_now.get(remote, ())) or 1
            if conn["cat"] == "scan" and conn["org"] and classify.RESEARCH.search(conn["org"]["full"] or ""):
                conn["cat"] = "research"
        return conn

    @staticmethod
    def _update_row(conn):
        """What the browser needs to refresh an existing connection."""
        return [round(conn["down"]), round(conn["up"]), conn["state"], conn["device"], conn.get("process"),
                conn["cat"], conn["active"], conn.get("ports_tried"), conn.get("vpn_client")]

    def where(self, ip):
        """An address for the logins plate: local or not, place and organization."""
        try:
            lan = not ipaddress.ip_address(ip).is_global or self.in_lan(ip)
        except ValueError:
            return {"ip": ip, "lan": True}
        if lan:
            return {"ip": ip, "lan": True}
        a = self.asn.lookup(ip)
        return {"ip": ip, "lan": False, "geo": self.geo.lookup(ip), "org": classify.org_short(a["org"]) if a else None}

    # ---------- scans versus real connections to this machine ----------
    def _classify_inbound(self, now, updated, packets_known):
        """Judge every inbound connection in the table again: its state and the ports its source tried change."""
        tried = {}
        for _, ip, _, key in self.inbound:
            tried.setdefault(ip, set()).add(key)
        self.tried_now = tried
        self.packets_known = packets_known
        for key, c in self.conns.items():
            if c["kind"] != "in":
                continue
            before = (c["cat"], c.get("ports_tried"), c.get("vpn_client"))
            self._judge(c)
            if (c["cat"], c.get("ports_tried"), c.get("vpn_client")) != before:
                updated[key] = self._update_row(c)
        self.scan_ips = {ip: t for ip, t in self.scan_ips.items() if t >= now - 3600}

    def _judge(self, c, final=False):
        """A connection from the internet is a scan when nobody answered, the TCP handshake never completed,
        a ping sweep sent a packet or two, or the source touched several ports within the hour without a solid
        session. Packet counts are used only with byte accounting on (nf_conntrack_acct=1). final: the connection
        is over (DESTROY), and a TCP connection that never got [ASSURED] was only knocking.
        Addresses named in names.conf (your own monitoring, offices) are never scanners."""
        ports = len(self.tried_now.get(c["remote"], ())) or 1
        packets_known = getattr(self, "packets_known", False)
        # The status file is re-read every poll: a tunnel may appear after its connection, or go away.
        vpn = self.vpn_real.get((c["remote"], c["sport"]))
        c["vpn_client"] = vpn
        if vpn:
            cat = "vpn"
        elif c.get("remote_name"):
            cat = "session"
        else:
            answered = c.get("replied", True)
            if c["proto"] == "icmp":
                probe = not answered or (packets_known and c.get("packets", 0) <= 3)
                solid = not probe
            elif c["proto"] == "tcp":
                probe = not answered or (final and not c.get("assured")) \
                    or (packets_known and not c.get("assured") and c.get("packets", 0) <= 6)
                solid = answered and c.get("assured", False)
            else:
                probe = not answered
                solid = answered and c.get("assured", False)
            if probe or (ports >= 3 and not solid):
                org = (c.get("org") or {}).get("full") or ""
                cat = "research" if classify.RESEARCH.search(org) else "scan"
            else:
                cat = "session"
        c["cat"], c["ports_tried"] = cat, ports
        if cat in ("scan", "research"):
            self.scan_ips[c["remote"]] = time.time()

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

