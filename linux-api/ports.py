"""Rows for the ports plate: what listens on this machine and whether the internet can reach it.

A listening socket is judged by two layers:
  1. its bind address: loopback or a LAN address cannot be reached from the internet at all;
  2. the firewall INPUT chain for a new packet from the WAN interface (see firewall.py).

Status: "open" (the firewall lets anyone through), "limited" (only listed sources), "closed" (the firewall
drops it), "local" (bound to loopback or LAN only). DNAT rules (port forwards, Docker) are extra rows.
"""
import ipaddress

import firewall

ORDER = {"open": 0, "limited": 1, "closed": 2, "local": 3}


def _scope(ip, wan_ips):
    """'all', 'wan', 'local' for a bind address."""
    if ip in ("0.0.0.0", "::"):
        return "all"
    if ip in wan_ips:
        return "wan"
    return "local"


def _families(ip):
    if ip == "::":
        return [4, 6]  # dual stack unless net.ipv6.bindv6only=1
    return [6] if ":" in ip else [4]


def judge(rulesets, wan, local, port, proto):
    """Combine every ruleset a packet passes (iptables legacy and nft may both be active)."""
    if not rulesets:
        return "accept", [], True, False, "firewall не прочитан"
    results = [rs.verdict(wan, local, port, proto) for rs in rulesets]
    uncertain = any(r[2] for r in results)
    rate = any(r[3] for r in results)
    drops = [r for r in results if r[0] == "drop"]
    v, sources, _, _, comment = drops[0] if drops else results[0]
    return v, sources, uncertain, rate, comment


def exposure(listen, rulesets, forwards, wan, wan_ips, ipv6):
    """Rows for the plate. `listen` comes from sockets.listeners(), `rulesets` from firewall.load()."""
    local4 = [ipaddress.ip_address(a) for a in wan_ips if ":" not in a]
    local6 = [ipaddress.ip_address(a) for a in wan_ips if ":" in a]
    groups = {}
    for s in listen:
        if s["lport"] == 0:
            continue
        name = s["process"] or "неизвестный процесс"
        g = groups.setdefault(name, {"name": name, "ports": {}})
        g["ports"].setdefault((s["lport"], s["proto"]), set()).add(s["lip"])

    # A port published by Docker shows up twice: as a docker-proxy listener and as a DNAT rule. The DNAT row
    # names the container, so the docker-proxy copy is dropped.
    forwarded = set()
    for f in forwards:
        for proto in (["tcp", "udp"] if f["proto"] == "all" else [f["proto"]]):
            if f["ports"].isdigit():
                forwarded.add((int(f["ports"]), proto))

    rows = []
    for g in groups.values():
        # One process may listen on public and on loopback addresses at once (docker-proxy with ports published
        # on 127.0.0.1 and on 0.0.0.0): only the ports with a public bind are judged, the rest go to a "local" row.
        public = {k: b for k, b in g["ports"].items() if any(_scope(ip, wan_ips) != "local" for ip in b)}
        private = {k: b for k, b in g["ports"].items() if k not in public}
        if g["name"] == "docker-proxy":
            public = {k: b for k, b in public.items() if k not in forwarded}
        if private:
            rows.append({
                "name": g["name"], "ports": [f"{p}/{pr}" for p, pr in sorted(private)], "status": "local",
                "why": "слушает только локальные адреса", "uncertain": False, "access": "",
                "binds": sorted({ip for b in private.values() for ip in b}),
            })
        if not public:
            continue
        worst, why, uncertain, sources_all = "local", "слушает только локальные адреса", False, []
        for (port, proto), binds in sorted(public.items()):
            for ip in binds:
                if _scope(ip, wan_ips) == "local":
                    continue
                for fam in _families(ip):
                    if fam == 6 and (not ipv6 or not local6):
                        continue
                    if fam == 4 and not local4:
                        continue
                    v, sources, u, rate, comment = judge(rulesets.get(fam, []), wan, local4 if fam == 4 else local6, port, proto)
                    uncertain |= u
                    fam_note = " (IPv6)" if fam == 6 else ""
                    if v == "accept":
                        status, text = "open", f"firewall пропускает {port}/{proto}{fam_note}" + (f": {comment}" if comment else "")
                        if rate:
                            text += "; частые подключения ограничены"
                    elif sources:
                        status, text = "limited", f"{port}/{proto}{fam_note}: firewall пропускает только {', '.join(sources)}"
                        sources_all.extend(sources)
                    else:
                        status, text = "closed", f"firewall отбрасывает {port}/{proto}{fam_note} с WAN"
                    if ORDER[status] < ORDER[worst]:
                        worst, why = status, text
        rows.append({
            "name": g["name"], "ports": [f"{p}/{pr}" for p, pr in sorted(public)], "status": worst, "why": why,
            "uncertain": uncertain, "access": ", ".join(sorted(set(sources_all))),
            "binds": sorted({ip for b in public.values() for ip in b}),
        })

    for f in forwards:
        protos = ["tcp", "udp"] if f["proto"] == "all" else [f["proto"]]
        ports = [f"{f['ports'] or 'все'}/{p}" for p in protos]
        limited = bool(f["sources"])
        # name: who opened the port (docker publishes ports with DNAT rules in its DOCKER chain);
        # to: where the packets go; to_name: the container or the LAN device at that address.
        rows.append({
            "name": f.get("opener") or "DNAT", "ports": ports, "status": "limited" if limited else "open",
            "why": (f"DNAT только для {f['sources']}" if limited else "DNAT с WAN") + "; фильтр FORWARD не проверяется"
                   + (f"; {f['comment']}" if f["comment"] else ""),
            "uncertain": f["uncertain"], "access": f["sources"], "binds": [], "forward": True, "to": f["to"],
            "to_name": f.get("to_name"),
        })
    rows.sort(key=lambda r: (ORDER[r["status"]], r["name"]))
    return rows


def load(ipv6):
    return firewall.load(ipv6)
