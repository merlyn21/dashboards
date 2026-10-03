"""The kernel connection tracking table: a full read every poll and a live stream of new and ended entries.

Sources, best first:
  `conntrack` (package conntrack): `conntrack -L` for the table, `conntrack -E` for events.
  /proc/net/nf_conntrack: the table only, no events.
Both print the same line format, so one parser serves the table, the proc file and the event stream:

  [NEW] ipv4 2 tcp 6 120 SYN_SENT src=A dst=B sport=1 dport=2 [UNREPLIED] src=B dst=A sport=2 dport=1 mark=0 use=1

The first src/dst/sport/dport group is the original direction, the second one the reply. With
net.netfilter.nf_conntrack_acct=1 each group also carries packets= and bytes=.
Entries are keyed by their original tuple, which is unique while the entry lives.
"""
import errno
import logging
import os
import shutil
import subprocess
import threading
import time

log = logging.getLogger("conntrack")

PROTOS = {"tcp", "udp", "icmp", "icmpv6", "gre", "sctp", "udplite", "dccp", "unknown"}
PROC = "/proc/net/nf_conntrack"
ACCT = "/proc/sys/net/netfilter/nf_conntrack_acct"


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
    if proto.startswith("icmp"):  # the echo id plays the part of the port
        orig["sport"] = orig.get("id", "0")
        orig["dport"] = orig.get("type", "0")
    entry = {
        "proto": "icmp" if proto == "icmpv6" else proto, "state": state, "unreplied": unreplied, "event": event,
        "src": orig["src"], "dst": orig["dst"], "sport": int(orig.get("sport", 0) or 0), "dport": int(orig.get("dport", 0) or 0),
        "rsrc": repl.get("src", ""), "rdst": repl.get("dst", ""),
        "orig_bytes": int(orig.get("bytes", 0) or 0), "repl_bytes": int(repl.get("bytes", 0) or 0),
        # packets= only with net.netfilter.nf_conntrack_acct=1
        "orig_packets": int(orig.get("packets", 0) or 0), "repl_packets": int(repl.get("packets", 0) or 0), "assured": assured,
    }
    entry["key"] = f'{entry["proto"]}|{entry["src"]}|{entry["sport"]}|{entry["dst"]}|{entry["dport"]}'
    return entry


class Conntrack:
    def __init__(self, ipv6):
        self.ipv6 = ipv6
        self.cli = shutil.which("conntrack")
        self.procs = []
        if self.cli:
            self.source = "conntrack"
        elif os.access(PROC, os.R_OK):
            self.source = "/proc/net/nf_conntrack"
        else:
            self.source = None

    @staticmethod
    def loaded():
        """The table exists only while the nf_conntrack module is loaded (any stateful firewall rule loads it)."""
        return os.path.exists("/proc/sys/net/netfilter/nf_conntrack_count")

    @staticmethod
    def accounting(enable=False):
        """Is per-connection byte accounting on? With enable=True, switch it on first."""
        try:
            with open(ACCT) as f:
                on = f.read().strip() == "1"
            if not on and enable:
                with open(ACCT, "w") as f:
                    f.write("1")
                log.info("net.netfilter.nf_conntrack_acct set to 1: rates appear for connections opened from now on")
                on = True
            return on
        except OSError as e:
            if e.errno == errno.EROFS:  # /proc/sys is read-only in a container without privileged
                log.warning("nf_conntrack_acct cannot be set from here (read-only /proc/sys): run "
                            "`sysctl -w net.netfilter.nf_conntrack_acct=1` on the host, see README.md")
            else:
                log.warning("nf_conntrack_acct: %s", e)
            return False

    def table(self):
        if self.cli:
            out = []
            for family in ["ipv4"] + (["ipv6"] if self.ipv6 else []):
                r = subprocess.run([self.cli, "-L", "-o", "extended", "-f", family], capture_output=True, text=True, timeout=60)
                if r.returncode != 0 and not r.stdout:
                    raise OSError(f"conntrack -L: {r.stderr.strip()}")
                out.extend(r.stdout.splitlines())
        else:
            with open(PROC, encoding="ascii", errors="replace") as f:
                out = f.read().splitlines()
        rows = []
        for line in out:
            e = parse(line)
            if e and (self.ipv6 or ":" not in e["src"]):
                rows.append(e)
        return rows

    def follow(self, callback):
        """Run `conntrack -E` for new and destroyed entries in background threads; restart it if it dies."""
        if not self.cli:
            return False
        threading.Thread(target=self._follow, args=(callback,), name="conntrack-events", daemon=True).start()
        return True

    def _follow(self, callback):
        # One stream for both families: events are not filtered by family unless -f is given, and
        # conntrack before 1.4.6 (Debian 11, Ubuntu 20.04) rejects -f together with -E.
        cmd = [self.cli, "-E", "-e", "NEW,DESTROY", "-o", "extended"]
        if shutil.which("stdbuf"):
            cmd = ["stdbuf", "-oL"] + cmd  # line buffering through the pipe
        while True:
            started = time.time()
            try:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
                self.procs.append(p)
                for line in p.stdout:
                    e = parse(line)
                    if e and e["event"] in ("NEW", "DESTROY") and (self.ipv6 or ":" not in e["src"]):
                        try:
                            callback(e)
                        except Exception:  # a bad event must not stop the stream
                            log.exception("event callback")
                err = p.stderr.read().strip()
                p.wait()
                log.warning("conntrack -E stopped (%s): %s", p.returncode, err[-200:])
            except OSError as e:
                log.warning("conntrack -E: %s", e)
            time.sleep(5 if time.time() - started > 60 else 30)
