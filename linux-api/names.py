"""Names for addresses: the names file, OpenVPN clients, /etc/hosts, reverse DNS, device maker by MAC.

Names file (names_file in .env), one entry per line, '#' starts a comment line:

    # address             name              type
    aa:bb:cc:11:22:33     "NAS в кладовке"  nas
    192.168.1.20          Принтер           printer
    10.8.0.0/24           VPN               pc
    203.0.113.10          "Офис Москва"

The address is a MAC, an IP (v4 or v6) or a network. For a network the name gets the host part:
"VPN .5". Order of preference: MAC, exact IP, the narrowest network, OpenVPN client, /etc/hosts,
reverse DNS, device maker by MAC, the address itself. The file is re-read when it changes.
"""
import ipaddress
import json
import logging
import os
import queue
import re
import shlex
import socket
import threading

import classify
import oui
from sysinfo import host_path, run

log = logging.getLogger("names")
MAC = re.compile(r"^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$", re.I)
TYPES = {"pc", "phone", "nas", "server", "camera", "printer", "tv", "iot", "ap", "speaker", "vacuum"}


def norm_mac(mac):
    return (mac or "").lower().replace("-", ":")


class Names:
    def __init__(self, path, vendors):
        self.path, self.vendors = path, vendors
        self.mtime = None
        self.by_mac, self.by_ip, self.nets = {}, {}, []
        self.hosts = {}
        self.vpn = {}          # virtual address -> OpenVPN common name
        self.neigh = {}        # ip -> mac
        self.rdns = {}         # ip -> name or None (looked up, nothing found)
        self.rdns_queue = queue.Queue(maxsize=512)
        threading.Thread(target=self._rdns_worker, name="rdns", daemon=True).start()

    # ---------- sources ----------
    def reload(self):
        """Re-read the names file if it changed, and /etc/hosts."""
        self.hosts = {}
        try:
            with open(host_path("/etc/hosts"), encoding="utf-8", errors="replace") as f:
                for line in f:
                    parts = line.split("#", 1)[0].split()
                    if len(parts) >= 2 and not parts[1].startswith(("localhost", "ip6-")):
                        self.hosts.setdefault(parts[0], parts[1])
        except OSError:
            pass
        # isfile: a Docker bind mount of a missing file shows up as an empty directory
        if not self.path or not os.path.isfile(self.path):
            self.by_mac, self.by_ip, self.nets, self.mtime = {}, {}, [], None
            return
        mtime = os.path.getmtime(self.path)
        if mtime == self.mtime:
            return
        by_mac, by_ip, nets = {}, {}, []
        with open(self.path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    parts = shlex.split(line)
                except ValueError as e:
                    log.warning("%s:%d: %s", self.path, n, e)
                    continue
                if len(parts) < 2:
                    log.warning("%s:%d: need an address and a name", self.path, n)
                    continue
                addr, name = parts[0], parts[1]
                kind = parts[2].lower() if len(parts) > 2 and parts[2].lower() in TYPES else None
                if MAC.match(addr):
                    by_mac[norm_mac(addr)] = (name, kind)
                    continue
                try:
                    if "/" in addr:
                        nets.append((ipaddress.ip_network(addr, strict=False), name, kind))
                    else:
                        by_ip[str(ipaddress.ip_address(addr))] = (name, kind)
                except ValueError:
                    log.warning("%s:%d: %r is not a MAC, IP or network", self.path, n, addr)
        nets.sort(key=lambda x: -x[0].prefixlen)
        self.by_mac, self.by_ip, self.nets, self.mtime = by_mac, by_ip, nets, mtime
        log.info("names: %d MAC, %d IP, %d networks from %s", len(by_mac), len(by_ip), len(nets), self.path)

    def read_neighbors(self, ifaces):
        """IP -> MAC of devices seen on these interfaces (ARP and NDP). Returns the number of live ones."""
        try:
            data = json.loads(run(["ip", "-j", "neigh", "show"]) or "[]")
        except ValueError:
            data = []
        neigh, live = {}, 0
        for n in data:
            if n.get("dev") not in ifaces or not n.get("lladdr"):
                continue
            state = n.get("state", [])
            if "FAILED" in state or "INCOMPLETE" in state:
                continue
            neigh[n["dst"]] = norm_mac(n["lladdr"])
            if ":" not in n["dst"]:
                live += 1
        self.neigh = neigh
        return live

    # ---------- lookups ----------
    def of(self, ip, reverse=True):
        """(name, type, maker) for an address. Without a known name the address itself is returned."""
        mac = self.neigh.get(ip)
        maker = self.vendors.lookup(mac) if mac else None
        hit = self.by_mac.get(mac) if mac else None
        hit = hit or self.by_ip.get(ip)
        if not hit and self.nets:
            try:
                a = ipaddress.ip_address(ip)
                for net, name, kind in self.nets:
                    if a.version == net.version and a in net:
                        host = int(a) - int(net.network_address)
                        hit = (f"{name} .{host}" if a.version == 4 else f"{name} {ip}", kind)
                        break
            except ValueError:
                pass
        if hit:
            return hit[0], hit[1], maker
        if ip in self.vpn:
            return self.vpn[ip], "pc", maker
        if ip in self.hosts:
            return self.hosts[ip], None, maker
        if reverse:
            name = self._reverse(ip)
            if name:
                return name, None, maker
        if maker:
            # 'камера Hikvision …C6:EE', 'скрытый MAC …44:55'
            return classify.device_label(maker, mac.upper(), oui.short(maker)), None, maker
        return ip, None, maker

    def remote(self, ip):
        """A name for an internet address, only from the names file."""
        hit = self.by_ip.get(ip)
        if hit:
            return hit[0]
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return None
        for net, name, _ in self.nets:
            if a.version == net.version and a in net:
                return name
        return None

    def _reverse(self, ip):
        if ip in self.rdns:
            return self.rdns[ip]
        self.rdns[ip] = None
        try:
            self.rdns_queue.put_nowait(ip)
        except queue.Full:
            del self.rdns[ip]
        return None

    def _rdns_worker(self):
        while True:
            ip = self.rdns_queue.get()
            try:
                name = socket.gethostbyaddr(ip)[0]
                name = name.split(".")[0] if not name.replace(".", "").isdigit() else None
            except (OSError, UnicodeError):
                name = None
            self.rdns[ip] = name
