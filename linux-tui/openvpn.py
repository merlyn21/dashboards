"""OpenVPN server clients from its status file.

The status file is written by the server every few seconds when it runs with --status. Debian and Ubuntu units:
  openvpn-server@NAME   /run/openvpn-server/status-NAME.log  (--status-version 2)
  openvpn@NAME          /run/openvpn/NAME.status              (version 1)
Versions 1, 2 and 3 (tab separated) are understood. An OpenVPN client has no clients to list: its tunnel
shows up as a tun interface only.
"""
import glob
import logging
import os
import time

from sysinfo import host_path

log = logging.getLogger("openvpn")

SEARCH = ["/run/openvpn-server/*.log", "/run/openvpn/*.status", "/run/openvpn/*.log", "/var/log/openvpn/*status*",
          "/etc/openvpn/*status*", "/etc/openvpn/server/*status*", "/var/run/openvpn*.status"]


def find(paths):
    """Status files: the configured paths (host paths, also in a container) or the usual places."""
    if paths:
        return [host_path(p) for p in paths if os.path.exists(host_path(p))]
    found = []
    for pattern in SEARCH:
        found.extend(p for p in glob.glob(host_path(pattern)) if os.path.isfile(p))
    return sorted(set(found))


def split_real(addr):
    """'udp4:1.2.3.4:51234', '1.2.3.4:51234', '[2001:db8::1]:51234', '2001:db8::1' -> (address, port or 0)."""
    for prefix in ("udp4:", "udp6:", "tcp4-server:", "tcp6-server:", "tcp4:", "tcp6:", "udp:", "tcp:"):
        if addr.startswith(prefix):
            addr = addr[len(prefix):]
    if addr.startswith("["):
        host, _, port = addr[1:].partition("]")
        port = port.lstrip(":")
    elif addr.count(":") == 1:
        host, port = addr.split(":")
    else:
        host, port = addr, ""
    return host, int(port) if port.isdigit() else 0


def parse(text, server):
    """Clients from one status file."""
    clients, virtual = {}, {}
    lines = text.splitlines()
    sep = "\t" if any(l.startswith("CLIENT_LIST\t") for l in lines) else ","
    section = None
    for line in lines:
        f = line.split(sep)
        if f[0] == "CLIENT_LIST" and len(f) >= 8:
            # CLIENT_LIST,CN,Real,Virtual,Virtual6,BytesRecv,BytesSent,Since,Since(time_t),...
            cn, real = f[1], f[2]
            since = int(f[8]) if len(f) > 8 and f[8].isdigit() else None
            host, port = split_real(real)
            clients[(cn, real)] = {"name": cn, "real": host, "real_port": port, "virtual": [v for v in (f[3], f[4]) if v],
                                   "rx": int(f[5] or 0), "tx": int(f[6] or 0), "since": since, "server": server}
        elif f[0] in ("OpenVPN CLIENT LIST", "ROUTING TABLE", "GLOBAL STATS"):
            section = f[0]
        elif section == "OpenVPN CLIENT LIST" and len(f) == 5 and f[0] != "Common Name":
            cn, real = f[0], f[1]
            host, port = split_real(real)
            clients[(cn, real)] = {"name": cn, "real": host, "real_port": port, "virtual": [], "rx": int(f[2] or 0),
                                   "tx": int(f[3] or 0), "since": _time(f[4]), "server": server}
        elif section == "ROUTING TABLE" and len(f) >= 3 and f[0] != "Virtual Address":
            virtual.setdefault((f[1], f[2]), []).append(f[0])
    for key, addrs in virtual.items():  # version 1 keeps virtual addresses in a separate table
        if key in clients:
            clients[key]["virtual"].extend(a for a in addrs if not a.endswith("C"))  # 'C' marks iroute cache entries
    return list(clients.values())


def _time(value):
    try:
        return int(time.mktime(time.strptime(value.strip(), "%a %b %d %H:%M:%S %Y")))
    except ValueError:
        return None


def clients(paths):
    result = []
    for p in find(paths):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            log.warning("%s: %s", p, e)
            continue
        # A status file of a dead server stops changing: ignore it after 10 minutes.
        if time.time() - os.path.getmtime(p) > 600:
            continue
        name = os.path.basename(p).replace("status-", "").rsplit(".", 1)[0]
        result.extend(parse(text, name))
    return result
