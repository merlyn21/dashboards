"""What the map needs from an OpenWrt router, read over ubus: system, interfaces, neighbours, DHCP names,
Wi-Fi clients, the connection table, listening ports, the firewall verdict for them, and logins.

Everything is read-only. The rpcd user and its ACL are described in README.md.
"""
import collections
import datetime
import ipaddress
import logging
import re
import time

import conntrack
from ubus import UbusError

log = logging.getLogger("router")

TUNNEL = re.compile(r"^(tun|tap|wg|ovpn|ppp|l2tp|pptp)\S*$")
CONNTRACK_HELPER = "/usr/libexec/map-conntrack"


# ---------- system ----------
def board(u):
    b = u.call("system", "board")
    rel = b.get("release", {})
    return {"hostname": b.get("hostname", ""), "machine": b.get("model", ""), "cpu_model": b.get("system", ""),
            "kernel": b.get("kernel", ""), "arch": rel.get("target", ""),
            "os": " ".join(filter(None, [rel.get("distribution"), rel.get("version")])) or "OpenWrt"}


def info(u):
    i = u.call("system", "info")
    mem = i.get("memory", {})
    total = mem.get("total", 0)
    avail = mem.get("available", mem.get("free", 0))
    root = i.get("root", {})   # kilobytes
    out = {"uptime": i.get("uptime"), "load": [round(x / 65536, 2) for x in i.get("load", [])][:3] or None,
           "mem_total": total, "mem_used": 1 - avail / total if total else None, "swap_total": 0, "swap_used": None,
           "localtime": i.get("localtime")}
    if root.get("total"):
        out.update(disk_total=root["total"] * 1024, disk_used=root.get("used", 0) / root["total"])
    return out


class Cpu:
    """CPU load as the busy share between two reads of the router's /proc/stat."""

    def __init__(self):
        self.prev = None
        self.cores = None

    def load(self, u):
        lines = u.read("/proc/stat").splitlines()
        if not lines:
            return None
        self.cores = sum(1 for l in lines if re.match(r"cpu\d", l)) or None
        values = [int(x) for x in lines[0].split()[1:9]]
        idle, total = values[3] + values[4], sum(values)
        pct = None
        if self.prev and total > self.prev[1]:
            pct = round(100 * (1 - (idle - self.prev[0]) / (total - self.prev[1])))
        self.prev = (idle, total)
        return pct


# ---------- network ----------
def interfaces(u):
    """Logical interfaces: [{name, device, up, addrs: [ip_interface], gateway, dns, proto}]."""
    out = []
    for i in u.call("network.interface", "dump").get("interface", []):
        addrs = []
        for key in ("ipv4-address", "ipv6-address"):
            for a in i.get(key, []):
                try:
                    addrs.append(ipaddress.ip_interface(f"{a['address']}/{a['mask']}"))
                except (KeyError, ValueError):
                    pass
        for a in i.get("ipv6-prefix-assignment", []):
            try:
                addrs.append(ipaddress.ip_interface(f"{a['local-address']['address']}/{a['local-address']['mask']}"))
            except (KeyError, ValueError):
                pass
        gw = next((r.get("nexthop") for r in i.get("route", []) if r.get("mask") == 0 and r.get("nexthop") not in (None, "::")), None)
        out.append({"name": i.get("interface"), "device": i.get("l3_device") or i.get("device"), "up": bool(i.get("up")),
                    "addrs": addrs, "gateway": gw, "dns": i.get("dns-server", []), "proto": i.get("proto")})
    return out


def devices(u):
    """Network devices: {name: {up, speed (Mbit/s or None), rx, tx}}."""
    out = {}
    for name, d in u.call("network.device", "status").items():
        speed = re.match(r"(\d+)", str(d.get("speed") or ""))
        st = d.get("statistics", {})
        out[name] = {"up": bool(d.get("up")), "carrier": d.get("carrier", d.get("up")), "speed": int(speed.group(1)) if speed else None,
                     "rx": st.get("rx_bytes", 0), "tx": st.get("tx_bytes", 0), "type": d.get("type", "")}
    return out


def neighbors(u):
    """{ip: (mac, device)} of live ARP and NDP entries."""
    out = {}
    for line in u.run("/sbin/ip", ["neigh"]).splitlines():
        m = re.match(r"(\S+) dev (\S+) lladdr (\S+) (\S+)", line)
        if m and m.group(4) not in ("FAILED", "INCOMPLETE"):
            out[m.group(1)] = (m.group(3).lower(), m.group(2))
    return out


def dhcp_names(u):
    """{ip: host name} from DHCP leases, v4 and v6."""
    names = {}
    try:
        data = u.call("luci-rpc", "getDHCPLeases")
    except UbusError as e:
        log.debug("leases: %s", e)
        return names
    for key, field in (("dhcp_leases", "ipaddr"), ("dhcp6_leases", "ip6addr")):
        for l in data.get(key, []):
            if l.get("hostname") and l.get(field):
                names[l[field]] = l["hostname"]
            for ip in l.get("ip6addrs", []) or []:
                if l.get("hostname"):
                    names[ip.split("/")[0]] = l["hostname"]
    return names


def wifi_clients(u):
    """{mac: {signal, device}} of stations associated with the router's access points."""
    out = {}
    try:
        for dev in u.call("iwinfo", "devices").get("devices", []):
            for sta in u.call("iwinfo", "assoclist", {"device": dev}).get("results", []):
                out[sta["mac"].lower()] = {"signal": sta.get("signal"), "device": dev}
    except UbusError as e:
        log.debug("wifi: %s", e)
    return out


def conntrack_table(u):
    """The whole table through the helper script; rpcd alone gives only the first block of a /proc file."""
    try:
        text = u.run(CONNTRACK_HELPER)
    except UbusError as e:
        if e.code not in (4, 6):
            raise
        log.warning("%s is not installed or not allowed (%s): only the first 4 KB of the table are read, see README.md", CONNTRACK_HELPER, e)
        text = u.read("/proc/net/nf_conntrack")
        text = text[: text.rfind("\n") + 1]   # the last line is cut
    rows = [conntrack.parse(line) for line in text.splitlines()]
    return [r for r in rows if r]


def conntrack_usage(u):
    def num(path):
        try:
            return int(u.read(path).strip())
        except (ValueError, OSError):
            return None
    return num("/proc/sys/net/netfilter/nf_conntrack_count"), num("/proc/sys/net/netfilter/nf_conntrack_max")


# ---------- listening ports and the firewall ----------
def listeners(u):
    """Rows of `netstat -lntup`: [{proto, lip, lport, process}]."""
    rows = []
    for line in u.run("/bin/netstat", ["-lntup"]).splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] not in ("tcp", "udp"):
            continue
        host, _, port = parts[3].rpartition(":")
        if not port.isdigit():
            continue
        host = host.strip("[]")
        if host.startswith("::ffff:") and "." in host:
            host = host[7:]
        prog = parts[-1].split("/", 1)[1] if "/" in parts[-1] and parts[-1].split("/")[0].isdigit() else None
        rows.append({"proto": parts[0], "lip": host or "::", "lport": int(port), "process": prog})
    return rows


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else str(v).split()


def _port_in(spec, port):
    for part in _as_list(spec):
        for piece in part.split(","):
            a, sep, b = piece.replace(":", "-").partition("-")
            if a.isdigit() and (int(a) <= port <= int(b) if sep and b.isdigit() else int(a) == port):
                return True
    return False


class Firewall:
    """fw4 as its uci configuration states it: the zone of the WAN interface, its input policy,
    the rules for packets from that zone to the router, and the port forwards (redirects)."""

    def __init__(self, u, wan_iface, wan_device):
        try:
            values = u.call("uci", "get", {"config": "firewall"}).get("values", {})
        except UbusError as e:
            log.warning("firewall config: %s", e)
            values = {}
        sections = sorted(values.values(), key=lambda s: s.get(".index", 0))
        self.ok = bool(sections)
        self.zone, self.policy = None, "REJECT"
        defaults = next((s for s in sections if s.get(".type") == "defaults"), {})
        for s in sections:
            if s.get(".type") == "zone" and (wan_iface in _as_list(s.get("network")) or wan_device in _as_list(s.get("device"))):
                self.zone = s.get("name")
                self.policy = (s.get("input") or defaults.get("input") or "REJECT").upper()
        self.rules = [s for s in sections if s.get(".type") == "rule" and s.get("enabled", "1") != "0"
                      and s.get("src") in (self.zone, "*") and not s.get("dest")]
        self.redirects = [s for s in sections if s.get(".type") == "redirect" and s.get("enabled", "1") != "0"
                          and s.get("src") == self.zone and s.get("target", "DNAT").upper() == "DNAT"]

    def verdict(self, port, proto, family=4):
        """('accept' | 'drop', sources or '', why) for a new packet from the internet to the router."""
        if not self.zone:
            return "accept", "", "интерфейс WAN не входит ни в одну зону firewall"
        for r in self.rules:
            protos = [p.lower() for p in _as_list(r.get("proto"))] or ["tcp", "udp"]
            if "tcpudp" in protos:
                protos = ["tcp", "udp"]
            if "all" not in protos and proto not in protos:
                continue
            fam = str(r.get("family", "any")).lower()
            if fam not in ("any", "") and fam != f"ipv{family}":
                continue
            if r.get("dest_port") and not _port_in(r["dest_port"], port):
                continue
            if not r.get("dest_port") and proto in ("tcp", "udp") and "all" not in protos and r.get("icmp_type"):
                continue
            target = str(r.get("target", "ACCEPT")).upper()
            if target not in ("ACCEPT", "REJECT", "DROP"):
                continue  # MARK, NOTRACK and friends do not decide
            name = r.get("name") or "без имени"
            src = ", ".join(_as_list(r.get("src_ip")) + _as_list(r.get("src_mac")) + ([f"ipset {r['ipset']}"] if r.get("ipset") else []))
            if target == "ACCEPT":
                return ("drop" if src else "accept"), src, f"правило «{name}»"
            if src:
                continue  # denies only some sources: the rest goes on
            return "drop", "", f"правило «{name}»: {target}"
        if self.policy == "ACCEPT":
            return "accept", "", f"зона {self.zone}: вход ACCEPT"
        return "drop", "", f"зона {self.zone}: вход {self.policy}"

    def forwards(self):
        out = []
        for r in self.redirects:
            protos = [p.lower() for p in _as_list(r.get("proto"))] or ["tcp", "udp"]
            if "tcpudp" in protos or "all" in protos:
                protos = ["tcp", "udp"]
            ext = str(r.get("src_dport", "")).replace(":", "-")
            to = r.get("dest_ip", "?") + (f":{r['dest_port']}" if r.get("dest_port") else (f":{ext}" if ext else ""))
            out.append({"protos": protos, "ports": ext, "to": to, "ip": r.get("dest_ip"), "name": r.get("name", ""),
                        "sources": ", ".join(_as_list(r.get("src_ip")))})
        return out


def exposure(listen, fw, wan_ips, name_of):
    """Rows of the ports plate, in the shape the map expects (see linux-api): the port, who opened it,
    whether the internet side of the router can reach it."""
    order = {"open": 0, "limited": 1, "closed": 2, "local": 3}
    groups = {}
    for s in listen:
        if s["proto"] == "udp" and s["lport"] in (68, 546):
            continue  # DHCP clients of the router itself: the firewall lets the provider's answers in, it is not a service
        g = groups.setdefault(s["process"] or "неизвестный процесс", {})
        g.setdefault((s["lport"], s["proto"]), set()).add(s["lip"])
    rows = []
    for name, ports in groups.items():
        public = {k: b for k, b in ports.items() if any(ip in ("0.0.0.0", "::") or ip in wan_ips for ip in b)}
        private = {k: b for k, b in ports.items() if k not in public}
        if private:
            rows.append({"name": name, "ports": [f"{p}/{pr}" for p, pr in sorted(private)], "status": "local",
                         "why": "слушает только локальные адреса", "uncertain": False, "access": "",
                         "binds": sorted({ip for b in private.values() for ip in b})})
        if not public:
            continue
        worst, why, access = "closed", "", []
        for (port, proto), binds in sorted(public.items()):
            v, src, text = fw.verdict(port, proto, 6 if all(":" in ip for ip in binds) else 4)
            status = "open" if v == "accept" else "limited" if src else "closed"
            if src:
                access.append(src)
            if order[status] < order[worst] or not why:
                worst, why = status, (f"firewall пропускает {port}/{proto}: {text}" if status == "open"
                                      else f"{port}/{proto}: firewall пропускает только {src} ({text})" if status == "limited"
                                      else f"firewall не пускает с WAN: {text}")
        rows.append({"name": name, "ports": [f"{p}/{pr}" for p, pr in sorted(public)], "status": worst, "why": why,
                     "uncertain": not fw.ok, "access": ", ".join(sorted(set(access))),
                     "binds": sorted({ip for b in public.values() for ip in b})})
    for f in fw.forwards():
        target = name_of(f["ip"]) if f["ip"] else None
        rows.append({"name": "проброс", "ports": [f"{f['ports'] or 'все'}/{p}" for p in f["protos"]],
                     "status": "limited" if f["sources"] else "open",
                     "why": (f"проброс только для {f['sources']}" if f["sources"] else "проброс с WAN") + (f": «{f['name']}»" if f["name"] else ""),
                     "uncertain": False, "access": f["sources"], "binds": [], "forward": True, "to": f["to"],
                     "to_name": target if target and target != f["ip"] else None})
    rows.sort(key=lambda r: (order[r["status"]], r["name"]))
    return rows


# ---------- logins: dropbear and LuCI in the system log ----------
PATTERNS = [
    ("login", "ssh", re.compile(r"(?P<method>Password|Pubkey) auth succeeded for '(?P<user>[^']*)'.* from (?P<addr>[0-9a-fA-F.:]+):\d+")),
    ("failure", "ssh", re.compile(r"Bad password attempt for '(?P<user>[^']*)' from (?P<addr>[0-9a-fA-F.:]+):\d+")),
    ("failure", "ssh", re.compile(r"Max auth tries reached - user '(?P<user>[^']*)' from <?(?P<addr>[0-9a-fA-F.:]+):\d+")),
    ("invalid", "ssh", re.compile(r"Login attempt for nonexistent user(?: '(?P<user>[^']*)')? from (?P<addr>[0-9a-fA-F.:]+):\d+")),
    ("logout", "ssh", re.compile(r"Exit \((?P<user>[^)]*)\) from <(?P<addr>[0-9a-fA-F.:]+):\d+>")),
    ("login", "luci", re.compile(r"luci: accepted login on \S+ for (?P<user>\S+) from (?P<addr>[0-9a-fA-F.:]+)")),
    ("failure", "luci", re.compile(r"luci: failed login on \S+ for (?P<user>\S+) from (?P<addr>[0-9a-fA-F.:]+)")),
]
LOGLINE = re.compile(r"^(\w{3} \w{3}\s+\d+ \d\d:\d\d:\d\d \d{4}) \S+ (\w[\w.-]*)(?:\[(\d+)\])?: (.*)$")


class Logins:
    """Logins, failed attempts and open SSH sessions from the router's log (logread keeps the last lines only)."""

    def __init__(self, where):
        self.where = where

    def read(self, u, router_now):
        """(sessions, summary). router_now: the router's local clock as seconds, from `system info`."""
        try:
            text = u.run("/sbin/logread")
        except UbusError as e:
            log.debug("logread: %s", e)
            return [], {"events": [], "source": None, "logins": 0, "failures": 0, "invalid": 0, "brute": []}
        events, open_ssh = [], {}
        for line in text.splitlines():
            m = LOGLINE.match(line)
            if not m or m.group(2) not in ("dropbear", "luci", "uhttpd"):
                continue
            stamp, prog, pid, msg = m.groups()
            if prog != "dropbear" and "luci:" not in msg and prog != "luci":
                continue
            if prog == "luci" and not msg.startswith("luci:"):
                msg = "luci: " + msg
            try:
                at = datetime.datetime.strptime(re.sub(r"\s+", " ", stamp), "%a %b %d %H:%M:%S %Y")
                at = (at - datetime.datetime(1970, 1, 1)).total_seconds()
            except ValueError:
                continue
            for kind, via, rx in PATTERNS:
                mm = rx.search(msg)
                if not mm:
                    continue
                d = mm.groupdict()
                e = {"kind": kind, "via": via, "user": d.get("user") or "", "addr": d["addr"], "at": at,
                     "method": {"Password": "пароль", "Pubkey": "ключ"}.get(d.get("method"), via)}
                events.append(e)
                if via == "ssh" and kind == "login":
                    open_ssh[pid] = e
                elif via == "ssh" and kind == "logout":
                    open_ssh.pop(pid, None)
                break
        sessions = [{"user": e["user"], "via": "ssh", "tty": "", "when": time.strftime("%Y-%m-%d %H:%M", time.gmtime(e["at"])),
                     "age": max(0, router_now - e["at"]), "interactive": True, "where": self.where(e["addr"])} for e in open_ssh.values()]
        recent = [e for e in events if router_now - e["at"] <= 3600]
        cache = {}
        out = []
        for e in reversed(recent[-400:]):
            if e["addr"] not in cache:
                cache[e["addr"]] = self.where(e["addr"])
            out.append({"kind": e["kind"], "user": e["user"], "via": e["via"], "method": e["method"],
                        "time": time.strftime("%H:%M:%S", time.gmtime(e["at"])), "ago": router_now - e["at"], "where": cache[e["addr"]]})
        brute = collections.Counter(e["addr"] for e in recent if e["kind"] in ("failure", "invalid"))
        return sessions, {"events": out[:60], "source": "logread",
                          "logins": sum(1 for e in recent if e["kind"] == "login"),
                          "failures": sum(1 for e in recent if e["kind"] == "failure"),
                          "invalid": sum(1 for e in recent if e["kind"] == "invalid"),
                          "brute": [{"ip": ip, "n": n} for ip, n in brute.most_common(10)]}


def external_ip(opener):
    """Ask a public service for our address, through the same proxy as the router: the proxy sits in
    the router's network, so the answer is the external address of that network."""
    for url in ("http://ifconfig.me/ip", "http://api.ipify.org"):
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8.5.0"})
            with opener.open(req, timeout=8) as r:
                ip = r.read(64).decode().strip()
            if ipaddress.ip_address(ip).version == 4:
                return ip
        except (OSError, ValueError) as e:
            log.warning("external address from %s: %s", url, e)
    return None
