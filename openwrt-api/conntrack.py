"""Parser of conntrack lines, as /proc/net/nf_conntrack and the `conntrack` tool print them:

  ipv4 2 tcp 6 7435 ESTABLISHED src=A dst=B sport=1 dport=2 packets=8 bytes=556 src=B dst=C sport=2 dport=1 ... [ASSURED] mark=0 use=2

The first src/dst/sport/dport group is the original direction, the second one the reply. The number after
the protocol number is the timeout in seconds. Entries are keyed by their original tuple.
"""

PROTOS = {"tcp", "udp", "icmp", "icmpv6", "gre", "sctp", "udplite", "dccp", "unknown"}


def parse(line):
    """One conntrack line -> dict, or None. Event lines also get 'event': NEW, DESTROY, UPDATE."""
    tokens = line.split()
    if not tokens:
        return None
    event = None
    if tokens[0].startswith("[") and tokens[0].endswith("]"):
        event = tokens[0][1:-1]
        tokens = tokens[1:]
    proto = next((t for t in tokens[:4] if t in PROTOS), None)
    if not proto:
        return None
    tuples = [{}, {}]
    n = -1
    state, unreplied, assured = "", False, False
    for t in tokens:
        if "=" in t:
            k, v = t.split("=", 1)
            if k == "src":
                n += 1
            if 0 <= n < 2 and k in ("src", "dst", "sport", "dport", "type", "code", "id", "bytes", "packets"):
                tuples[n].setdefault(k, v)  # icmp tuples have their own id=; the entry id comes later
            if k in ("mark", "use", "zone", "secctx"):
                n = 2  # past the tuples
        elif t == "[UNREPLIED]":
            unreplied = True
        elif t == "[ASSURED]":  # traffic seen both ways (TCP: the handshake completed)
            assured = True
        elif n < 0 and t.isupper():  # ESTABLISHED, SYN_SENT, TIME_WAIT...
            state = t
    orig, repl = tuples
    if "src" not in orig or "dst" not in orig:
        return None
    # Seconds until the entry expires: the number after the protocol number. Every packet resets it,
    # so a timeout that went up between two polls means the connection is alive.
    at = tokens.index(proto)
    timeout = int(tokens[at + 2]) if len(tokens) > at + 2 and tokens[at + 2].isdigit() else None
    if proto.startswith("icmp"):  # the echo id plays the part of the port
        orig["sport"] = orig.get("id", "0")
        orig["dport"] = orig.get("type", "0")
    entry = {
        "proto": "icmp" if proto == "icmpv6" else proto, "state": state, "unreplied": unreplied, "event": event,
        "timeout": timeout,
        "src": orig["src"], "dst": orig["dst"], "sport": int(orig.get("sport", 0) or 0), "dport": int(orig.get("dport", 0) or 0),
        "rsrc": repl.get("src", ""), "rdst": repl.get("dst", ""),
        "orig_bytes": int(orig.get("bytes", 0) or 0), "repl_bytes": int(repl.get("bytes", 0) or 0),
        # packets= only with net.netfilter.nf_conntrack_acct=1
        "orig_packets": int(orig.get("packets", 0) or 0), "repl_packets": int(repl.get("packets", 0) or 0), "assured": assured,
    }
    entry["key"] = f'{entry["proto"]}|{entry["src"]}|{entry["sport"]}|{entry["dst"]}|{entry["dport"]}'
    return entry
