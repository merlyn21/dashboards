"""Sockets of this machine from `ss` (package iproute2): listening ports and open connections with their process.

Listeners feed the ports plate. Connections name the process behind a conntrack entry of the machine itself,
and they are the whole picture when the conntrack table is missing (no stateful firewall, nf_conntrack not loaded).
"""
import ipaddress
import re

from sysinfo import run

USERS = re.compile(r'users:\(\("([^"]+)",pid=(\d+)')
COUNTER = re.compile(r"\b(bytes_sent|bytes_acked|bytes_received):(\d+)")


def split_hostport(value):
    """'1.2.3.4:80', '[2001:db8::1]:22', '*:22', '127.0.0.53%lo:53' -> ('1.2.3.4', 80) and so on."""
    host, _, port = value.rpartition(":")
    host = host.strip("[]")
    if "%" in host:
        host = host.split("%", 1)[0].strip("[]")
    if host.startswith("::ffff:") and "." in host:
        host = host[7:]
    if host == "*":
        host = "0.0.0.0"
    try:
        return host, int(port)
    except ValueError:
        return host, 0


def _rows(args):
    """Parse `ss -H -n -p <args>`. With -i the extra info comes on the next, indented line."""
    rows = []
    for line in run(["ss", "-H", "-n", "-p"] + args).splitlines():
        if line[:1] in (" ", "\t") and rows:
            for k, v in COUNTER.findall(line):
                rows[-1][k] = int(v)
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        netid, state, local, peer = parts[0], parts[1], parts[4], parts[5]
        m = USERS.search(line)
        lip, lport = split_hostport(local)
        pip, pport = split_hostport(peer)
        rows.append({"proto": netid, "state": state, "lip": lip, "lport": lport, "pip": pip, "pport": pport,
                     "process": m.group(1) if m else None, "pid": int(m.group(2)) if m else None})
    return rows


def _ephemeral():
    try:
        with open("/proc/sys/net/ipv4/ip_local_port_range") as f:
            lo, hi = (int(x) for x in f.read().split())
        return lo, hi
    except (OSError, ValueError):
        return 32768, 60999


def listeners():
    """TCP sockets in LISTEN and bound UDP sockets.

    An unconnected UDP socket on an ephemeral port belongs to a client (a browser, a resolver) waiting for
    replies, not to a service: such sockets are left out.
    """
    lo, hi = _ephemeral()
    return [r for r in _rows(["-t", "-u", "-l"])
            if r["proto"] == "tcp" or (r["proto"] == "udp" and not lo <= r["lport"] <= hi)]


def connections():
    """Established TCP and connected UDP sockets, with byte counters for TCP."""
    rows = []
    for r in _rows(["-t", "-u", "-i"]):
        if r["proto"] not in ("tcp", "udp") or r["pport"] == 0:
            continue
        r["sent"] = r.get("bytes_sent", r.get("bytes_acked", 0))
        r["recv"] = r.get("bytes_received", 0)
        rows.append(r)
    return rows


def process_index(conns):
    """(proto, local ip, local port, peer ip, peer port) -> process name, and (proto, local port) -> name."""
    by_tuple, by_port = {}, {}
    for r in conns:
        if r["process"]:
            by_tuple[(r["proto"], r["lip"], r["lport"], r["pip"], r["pport"])] = r["process"]
    for r in listeners():
        if r["process"]:
            by_port.setdefault((r["proto"], r["lport"]), r["process"])
    return by_tuple, by_port


def as_conntrack(conns, listen_ports):
    """Connections in the conntrack entry shape, for machines without a conntrack table.

    Direction comes from the listening ports: a socket on a listening port was opened from outside.
    """
    rows = []
    for r in conns:
        inbound = (r["proto"], r["lport"]) in listen_ports
        if inbound:
            src, sport, dst, dport, ob, rb = r["pip"], r["pport"], r["lip"], r["lport"], r["recv"], r["sent"]
        else:
            src, sport, dst, dport, ob, rb = r["lip"], r["lport"], r["pip"], r["pport"], r["sent"], r["recv"]
        try:
            ipaddress.ip_address(src), ipaddress.ip_address(dst)
        except ValueError:
            continue
        rows.append({
            "proto": r["proto"], "state": r["state"].replace("ESTAB", "ESTABLISHED"), "unreplied": False, "event": None,
            "src": src, "dst": dst, "sport": sport, "dport": dport, "rsrc": dst, "rdst": src,
            "orig_bytes": ob, "repl_bytes": rb, "process": r["process"],
            # a socket in ss is past its handshake; packet counts are not known
            "orig_packets": 0, "repl_packets": 0, "assured": True,
            "key": f'{r["proto"]}|{src}|{sport}|{dst}|{dport}',
        })
    return rows
