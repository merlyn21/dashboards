"""What the firewall does with a new packet from the internet, judged from `iptables-save` and `ip6tables-save`.

ufw and Docker keep their rules in iptables too, so one reader covers them. The walk follows the filter
INPUT chain for a new packet that arrives on the WAN interface for a given port, jumping into user chains
(ufw-before-input, ufw-user-input and so on) and back, until ACCEPT, DROP or REJECT decides, or the chain
policy does. Conditions the walk does not model make a rule "conditional": it is skipped and the answer is
marked as approximate. Source address conditions (-s, ipset src) on an ACCEPT rule are collected: the port
is then open only for those sources.

Ports published by Docker and other DNAT rules in nat PREROUTING reach their target through FORWARD, not
INPUT; they are listed separately.
"""
import ipaddress
import logging
import os
import re
import shlex
import shutil

from sysinfo import run

log = logging.getLogger("firewall")

DENY = {"DROP", "REJECT"}
BUILTIN = {"ACCEPT", "DROP", "REJECT", "RETURN"}
RATE = {"limit", "hashlimit", "recent", "connlimit"}
IPTABLES_TABLES = {"filter", "nat", "mangle", "raw", "security"}


def parse_save(text):
    """iptables-save output -> {table: {"policy": {chain: policy}, "rules": {chain: [rule]}}}."""
    tables, cur = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("*"):
            cur = tables.setdefault(line[1:], {"policy": {}, "rules": {}})
        elif line.startswith(":") and cur is not None:
            parts = line[1:].split()
            cur["policy"][parts[0]] = parts[1] if len(parts) > 1 else "-"
            cur["rules"].setdefault(parts[0], [])
        elif line.startswith("-A ") and cur is not None:
            try:
                rule = parse_rule(shlex.split(line))
            except ValueError:
                continue
            cur["rules"].setdefault(rule["chain"], []).append(rule)
    return tables


def parse_rule(tokens):
    """['-A', 'INPUT', '-p', 'tcp', '--dport', '22', '-j', 'ACCEPT'] -> {chain, opts: [(neg, name, args)], target, ...}."""
    rule = {"chain": tokens[1], "opts": [], "modules": set(), "target": None, "goto": False, "target_opts": {}, "comment": ""}
    i, neg = 2, False
    while i < len(tokens):
        t = tokens[i]
        if t == "!":
            neg = True
            i += 1
            continue
        j = i + 1
        while j < len(tokens) and not (tokens[j].startswith("-") and len(tokens[j]) > 1 and not tokens[j][1].isdigit()) and tokens[j] != "!":
            j += 1
        args = tokens[i + 1:j]
        if t in ("-j", "--jump", "-g", "--goto"):
            rule["target"] = args[0] if args else None
            rule["goto"] = t in ("-g", "--goto")
            k = j
            while k < len(tokens):  # target options follow the target
                if tokens[k].startswith("--"):
                    m = k + 1
                    while m < len(tokens) and not tokens[m].startswith("--"):
                        m += 1
                    rule["target_opts"][tokens[k]] = tokens[k + 1:m]
                    k = m
                else:
                    k += 1
            break
        if t in ("-m", "--match"):
            rule["modules"].add(args[0] if args else "")
        elif t == "--comment":
            rule["comment"] = " ".join(args).strip("'\" ")
        else:
            rule["opts"].append((neg, t, args))
        neg = False
        i = j
    return rule


def _port_in(spec, port):
    for part in spec.split(","):
        if ":" in part:
            a, b = part.split(":", 1)
            if int(a or 0) <= port <= int(b or 65535):
                return True
        elif part and int(part) == port:
            return True
    return False


def _iface_match(pattern, name):
    if pattern.endswith("+"):
        return name.startswith(pattern[:-1])
    return pattern == name


class Packet:
    """A new packet from the internet: arrives on `wan` for one of `local` addresses, to `port`/`proto`."""

    def __init__(self, wan, local, port, proto):
        self.wan, self.local, self.port, self.proto = wan, local, port, proto


def match(rule, pkt, table="filter"):
    """'yes', 'no', 'maybe' (a condition we do not model) or ('src', label) when only some sources match."""
    src_only = None
    for neg, name, args in rule["opts"]:
        a = args[0] if args else ""
        if name in ("-p", "--protocol"):
            hit = a in ("all", "0") or a == pkt.proto or (a, pkt.proto) in (("6", "tcp"), ("17", "udp"))
        elif name in ("-i", "--in-interface"):
            hit = _iface_match(a, pkt.wan)
        elif name in ("-o", "--out-interface"):
            if table == "filter":
                return "no"  # INPUT has no output interface
            continue
        elif name in ("-s", "--source"):
            nets = a.split(",")
            if any(n.endswith("/0") for n in nets):
                hit = True
            elif neg:
                return "maybe"
            else:
                src_only = a
                continue
        elif name in ("-d", "--destination"):
            try:
                hit = any(ip in ipaddress.ip_network(n, strict=False) for n in a.split(",") for ip in pkt.local)
            except (ValueError, TypeError):
                return "maybe"
        elif name in ("--dport", "--destination-port", "--dports", "--destination-ports", "--ports"):
            hit = pkt.proto in ("tcp", "udp") and _port_in(a, pkt.port)
        elif name in ("--sport", "--source-port", "--sports", "--source-ports"):
            hit = False  # the client's port is not a fixed number
        elif name in ("--ctstate", "--state"):
            hit = "NEW" in a.split(",")
        elif name == "--syn":
            hit = pkt.proto == "tcp"
        elif name == "--tcp-flags":
            hit = pkt.proto == "tcp" and len(args) == 2 and args[1] == "SYN" and "SYN" in args[0].split(",")
        elif name == "--dst-type":
            hit = "LOCAL" in a.split(",")
        elif name == "--src-type":
            hit = False if "LOCAL" in a.split(",") else True
        elif name == "--pkt-type":
            hit = a == "unicast"
        elif name == "--match-set" and len(args) >= 2:
            if neg:
                return "maybe"
            if args[1].startswith("src"):
                src_only = f"ipset {args[0]}"
                continue
            return "maybe"
        elif name in ("--icmp-type", "--icmpv6-type"):
            hit = pkt.proto.startswith("icmp")
        elif name == "--limit-iface-in":
            continue
        elif rule["modules"] & RATE:
            continue  # rate options: handled by the caller
        else:
            return "maybe"
        if hit == neg:
            return "no"
    return ("src", src_only) if src_only else "yes"


class Walk:
    def __init__(self, table, pkt):
        self.table, self.pkt = table, pkt
        self.sources, self.uncertain, self.rate = [], False, False

    def chain(self, name, depth=0, sources_only=None):
        if depth > 40:
            self.uncertain = True
            return None, ""
        for r in self.table["rules"].get(name, []):
            m = match(r, self.pkt)
            if m == "no":
                continue
            if m == "maybe":
                self.uncertain = True
                continue
            tgt = r["target"]
            if not tgt:
                continue
            limited = bool(r["modules"] & RATE)
            src = m[1] if isinstance(m, tuple) else sources_only
            if limited and tgt != "ACCEPT":
                # Rate rules that deny or jump catch only too frequent packets (ufw limit, connlimit-above);
                # the first packet from a new source passes them.
                self.rate = self.rate or tgt in DENY or tgt in self.table["rules"]
                continue
            if src:
                if tgt == "ACCEPT":
                    self.sources.append(src)
                elif tgt not in BUILTIN and tgt in self.table["rules"]:
                    self.chain(tgt, depth + 1, src)
                continue
            if tgt == "ACCEPT":
                return "accept", r["comment"]
            if tgt in DENY:
                return "drop", r["comment"]
            if tgt == "RETURN":
                return None, ""
            if tgt in self.table["rules"]:
                v = self.chain(tgt, depth + 1, sources_only)
                if v[0] or r["goto"]:
                    return v
            # LOG, NFLOG, MARK and other non-terminal targets fall through
        return None, ""


class Ruleset:
    def __init__(self, label, text):
        self.label = label
        self.tables = parse_save(text)
        self.count = sum(len(rules) for t in self.tables.values() for rules in t["rules"].values())

    def verdict(self, wan, local, port, proto):
        """(verdict, sources, uncertain, rate_limited, comment) for a new packet from the internet."""
        filt = self.tables.get("filter")
        if not filt:
            return "accept", [], False, False, "правил нет"
        w = Walk(filt, Packet(wan, local, port, proto))
        v, comment = w.chain("INPUT")
        if not v:
            policy = filt["policy"].get("INPUT", "ACCEPT")
            v, comment = ("drop" if policy in DENY else "accept"), f"политика INPUT {policy}"
        return v, w.sources, w.uncertain, w.rate, comment

    def forwards(self, wan, local):
        """DNAT rules a packet from the internet can hit: [{proto, ports, to, sources}]."""
        nat = self.tables.get("nat")
        if not nat:
            return []
        found, seen = [], set()

        def walk(chain, depth):
            if depth > 20 or chain in seen:
                return
            seen.add(chain)
            for r in nat["rules"].get(chain, []):
                proto = next((a[0] for n, nm, a in r["opts"] if nm in ("-p", "--protocol") and not n and a), "all")
                ports = next((a[0] for n, nm, a in r["opts"] if nm in ("--dport", "--dports", "--destination-port") and not n and a), "")
                probe = Packet(wan, local, int(re.split(r"[,:]", ports)[0]) if ports else 0, proto if proto != "all" else "tcp")
                m = match({**r, "opts": [o for o in r["opts"] if o[1] not in ("--dport", "--dports", "--destination-port")]}, probe, "nat")
                if m in ("no",):
                    continue
                if r["target"] == "DNAT":
                    to = " ".join(r["target_opts"].get("--to-destination", []))
                    found.append({"proto": proto, "ports": ports.replace(":", "-"), "to": to, "chain": chain,
                                  "sources": m[1] if isinstance(m, tuple) else "", "uncertain": m == "maybe", "comment": r["comment"]})
                elif r["target"] in nat["rules"]:
                    walk(r["target"], depth + 1)
                    seen.discard(r["target"])

        walk("PREROUTING", 0)
        return found


def _variant(cmd):
    """'nf_tables' or 'legacy' from `iptables-save --version`."""
    out = run([cmd, "--version"])
    return "nf_tables" if "nf_tables" in out else "legacy"


def load(ipv6):
    """Rulesets for IPv4 (and IPv6): {4: [Ruleset], 6: [Ruleset]} plus notes about what is not understood."""
    result, notes = {4: [], 6: []}, []
    for fam, base in ((4, "iptables"), (6, "ip6tables")):
        if fam == 6 and not ipv6:
            continue
        main = f"{base}-save"
        if not shutil.which(main):
            if fam == 4:
                notes.append("нет iptables-save: firewall не проверяется")
            continue
        variant = _variant(main)
        text = run([main])
        if not text.strip() and os.geteuid() != 0:
            notes.append(f"{main} не прочитан: нужен root")
            continue
        result[fam].append(Ruleset(f"{base} ({variant})", text))
        other = f"{base}-legacy-save" if variant == "nf_tables" else f"{base}-nft-save"
        if shutil.which(other):
            rs = Ruleset(other, run([other]))
            if rs.count and not result[fam][0].count:
                result[fam] = [rs]  # e.g. a container with iptables-nft on a host that uses legacy
            elif rs.count:
                result[fam].append(rs)
                notes.append(f"правила есть и в {result[fam][0].label}, и в {other}: пакет проходит оба набора")
    if shutil.which("nft"):
        foreign = []
        for line in run(["nft", "list", "tables"]).splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0] == "table" and not (parts[1] in ("ip", "ip6") and parts[2] in IPTABLES_TABLES):
                foreign.append(f"{parts[1]} {parts[2]}")
        if foreign:
            notes.append(f"есть таблицы nftables, которые не разбираются ({', '.join(foreign)}): вывод приблизительный")
    return result, notes
