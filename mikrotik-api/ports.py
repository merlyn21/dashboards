"""Which router services can be reached from the internet, judged from the router's own config.

For every listening service the verdict combines two layers:
  1. the firewall `input` chain: is a new packet from the WAN interface to this port dropped?
  2. the service's own access list ("Available From" in /ip service, SNMP community addresses).

Status: "closed" (firewall drops it or the service is off), "limited" (firewall lets it through,
the service itself only answers listed networks), "open" (reachable and not limited).
"""
import ipaddress

from routeros import RouterOSError


def _ports_match(spec, port):
    if not spec:
        return True
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            if int(a) <= port <= int(b):
                return True
        elif part and int(part) == port:
            return True
    return False


def _iface_match(value, wan):
    """RouterOS interface matchers allow a leading '!' for negation."""
    if not value:
        return True
    neg = value.startswith("!")
    name = value.lstrip("!")
    hit = name == wan
    return hit != neg


def _list_match(value, lists_of_wan):
    if not value:
        return True
    neg = value.startswith("!")
    hit = value.lstrip("!") in lists_of_wan or value.lstrip("!") == "all"
    return hit != neg


# Matchers we evaluate. A rule with any other matcher is conditional: we cannot know if it applies.
_KNOWN = {".id", "chain", "action", "protocol", "dst-port", "in-interface", "in-interface-list", "connection-state",
          "disabled", "comment", "dynamic", "invalid", "log", "log-prefix", "bytes", "packets", "place-before"}


def firewall_verdict(rules, wan, lists_of_wan, port, proto):
    """Walk the input chain for a new packet from WAN. Returns (verdict, rule_comment, uncertain)."""
    uncertain = False
    for r in rules:
        if r.get("chain") != "input" or r.get("disabled") == "true" or r.get("invalid") == "true":
            continue
        state = r.get("connection-state", "")
        if state:
            states = state.lstrip("!").split(",")
            hit = "new" in states
            if hit == state.startswith("!"):
                continue
        if r.get("protocol") and r["protocol"] != proto:
            continue
        if not _ports_match(r.get("dst-port", ""), port):
            continue
        if not _iface_match(r.get("in-interface", ""), wan):
            continue
        if not _list_match(r.get("in-interface-list", ""), lists_of_wan):
            continue
        extra = [k for k in r if k not in _KNOWN and r[k] not in ("", "false")]
        if extra:
            uncertain = True  # e.g. src-address: applies only to some senders
            continue
        action = r.get("action", "accept")
        if action in ("drop", "reject", "tarpit"):
            return "drop", r.get("comment", ""), uncertain
        if action == "accept":
            return "accept", r.get("comment", ""), uncertain
        # passthrough, log, jump and friends do not decide
    return "accept", "нет правила, по умолчанию пропускается", uncertain


def _limited(addresses):
    nets = [a.strip() for a in (addresses or "").split(",") if a.strip()]
    if not nets:
        return False, ""
    for n in nets:
        try:
            if ipaddress.ip_network(n, strict=False).prefixlen == 0:
                return False, n
        except ValueError:
            pass
    return True, ", ".join(nets)


def exposure(api, wan):
    """Read the config over the API and return a list of rows for the ports plate."""
    rules = api.print("/ip/firewall/filter")
    members = api.print("/interface/list/member", proplist=["list", "interface"])
    lists_of_wan = {m["list"] for m in members if m.get("interface") == wan}

    items = []  # (name, [(port, proto)], enabled, access_list, note)
    for s in api.print("/ip/service"):
        port = int(s.get("port", 0) or 0)
        items.append((s.get("name"), [(port, "tcp")], s.get("disabled") != "true" and s.get("invalid") != "true",
                      s.get("address", ""), "Available From"))

    def optional(path):
        try:
            return api.print(path)
        except RouterOSError:
            return []

    bt = optional("/tool/bandwidth-server")
    if bt:
        items.append(("bandwidth-server", [(2000, "tcp"), (2000, "udp")], bt[0].get("enabled") == "true", "",
                      "вход по пользователям роутера" if bt[0].get("authenticate") == "true" else "без пароля"))
    dns = optional("/ip/dns")
    if dns:
        items.append(("dns", [(53, "udp"), (53, "tcp")], dns[0].get("allow-remote-requests") == "true", "", "резолвер для запросов"))
    snmp = optional("/snmp")
    if snmp:
        comm = [c for c in optional("/snmp/community") if c.get("disabled") != "true"]
        addrs = ",".join(c.get("addresses", "") for c in comm)
        items.append(("snmp", [(161, "udp")], snmp[0].get("enabled") == "true", addrs, "адреса community"))
    for path, name, default_port in (("/ip/proxy", "web-proxy", 8080), ("/ip/socks", "socks", 1080)):
        cfg = optional(path)
        if cfg:
            items.append((name, [(int(cfg[0].get("port", default_port) or default_port), "tcp")], cfg[0].get("enabled") == "true", "", ""))

    rows = []
    for name, ports, enabled, access, note in items:
        verdicts = [firewall_verdict(rules, wan, lists_of_wan, p, pr) for p, pr in ports]
        fw_open = any(v[0] == "accept" for v in verdicts)
        uncertain = any(v[2] for v in verdicts)
        limited, nets = _limited(access)
        if not enabled:
            status, why = "off", "сервис выключен"
        elif not fw_open:
            status, why = "closed", "firewall отбрасывает с WAN"
        elif limited:
            status, why = "limited", f"firewall пропускает; сервис отвечает только {nets}"
        else:
            status, why = "open", "firewall пропускает, ограничений по адресу нет" + (f"; {note}" if note else "")
        rows.append({
            "name": name, "ports": [f"{p}/{pr}" for p, pr in ports], "enabled": enabled, "status": status,
            "why": why, "uncertain": uncertain, "access": nets,
        })
    order = {"open": 0, "limited": 1, "closed": 2, "off": 3}
    rows.sort(key=lambda r: (order[r["status"]], r["name"]))
    return rows
