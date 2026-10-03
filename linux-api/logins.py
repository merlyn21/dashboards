"""Who is on this machine: open login sessions (utmp, as `who` prints them) and SSH logins from the log.

Log sources, best first:
  journalctl (systemd journal): sshd messages, read incrementally by cursor;
  /var/log/auth.log (Debian, Ubuntu with rsyslog) or /var/log/secure: read from the last position.
Only logins, logouts and failed attempts are kept, for the last hour.
"""
import collections
import datetime
import ipaddress
import json
import logging
import os
import re
import shutil
import subprocess
import time

from sysinfo import HOST_ROOT, host_path, run

log = logging.getLogger("logins")

PATTERNS = [
    ("login", re.compile(r"Accepted (?P<method>\S+) for (?P<user>\S+) from (?P<addr>\S+) port \d+")),
    ("failure", re.compile(r"Failed (?P<method>\S+) for (?P<invalid>invalid user )?(?P<user>\S*) from (?P<addr>\S+) port \d+")),
    ("invalid", re.compile(r"Invalid user (?P<user>\S*) ?from (?P<addr>\S+)")),
    ("logout", re.compile(r"Disconnected from user (?P<user>\S+) (?P<addr>\S+) port \d+")),
]
SSHD = re.compile(r"\bsshd(?:-session)?\[\d+\]:\s*(.*)$")
FILES = ["/var/log/auth.log", "/var/log/secure"]
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
WHO = re.compile(r"^(?P<user>\S+)\s+(?P<line>\S+)\s+(?P<date>\d{4}-\d\d-\d\d \d\d:\d\d)(?:\s+\((?P<host>[^)]*)\))?")


def _file_time(line, now):
    """Time of a syslog line: ISO ('2026-09-29T12:00:01.123+03:00') or classic ('Sep 29 12:00:01')."""
    try:
        if line[:4].isdigit():
            return datetime.datetime.fromisoformat(line.split()[0]).timestamp()
        mon, day, hms = line.split()[:3]
        d = datetime.datetime(now.year, MONTHS[mon], int(day), *[int(x) for x in hms.split(":")])
        if d > now + datetime.timedelta(days=1):  # December lines read in January
            d = d.replace(year=now.year - 1)
        return d.timestamp()
    except (ValueError, KeyError, IndexError):
        return None


def _remote(host):
    """An address or a host name in `who` output; not ':0', 'local', 'tmux(123).%0'."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return "." in host and not host.startswith((":", "tmux"))


class Logins:
    def __init__(self, where):
        self.where = where          # ip -> {ip, lan, geo, org}
        self.events = collections.deque(maxlen=3000)
        self.cursor = None
        self.file, self.file_pos = None, None
        self.journal = shutil.which("journalctl")
        self.source = None

    # ---------- open sessions ----------
    def sessions(self):
        utmp = next((p for p in (host_path("/run/utmp"), host_path("/var/run/utmp")) if os.path.exists(p)), None)
        out = run(["who", utmp] if utmp and HOST_ROOT else ["who"])
        rows, now = [], time.time()
        for line in out.splitlines():
            m = WHO.match(line)
            if not m:
                continue
            host, tty = m.group("host") or "", m.group("line")
            addr = host if _remote(host) else ""
            try:
                since = datetime.datetime.strptime(m.group("date"), "%Y-%m-%d %H:%M").timestamp()
            except ValueError:
                since = None
            via = "ssh" if addr else ("консоль" if tty.startswith("tty") else "локально")
            rows.append({"user": m.group("user"), "via": via, "tty": tty, "when": m.group("date"),
                         "age": now - since if since else None, "interactive": True,
                         "where": self.where(addr) if addr else {"ip": host or tty, "lan": True}})
        return rows

    # ---------- log events ----------
    def _journal_lines(self):
        cmd = [self.journal, "-o", "json", "--no-pager", "SYSLOG_IDENTIFIER=sshd", "SYSLOG_IDENTIFIER=sshd-session"]
        if HOST_ROOT:
            cmd += ["-D", host_path("/var/log/journal")]
        cmd += ["--after-cursor", self.cursor] if self.cursor else ["--since", "-1h", "-n", "20000"]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("journalctl: %s", e)
            return None
        lines = []
        for raw in out.splitlines():
            try:
                j = json.loads(raw)
            except ValueError:
                continue
            self.cursor = j.get("__CURSOR", self.cursor)
            msg = j.get("MESSAGE")
            if isinstance(msg, str):
                lines.append((int(j.get("__REALTIME_TIMESTAMP", 0)) / 1e6, msg))
        return lines

    def _file_lines(self):
        if not self.file:
            self.file = next((host_path(p) for p in FILES if os.path.exists(host_path(p))), None)
            if not self.file:
                return None
        try:
            st = os.stat(self.file)
            with open(self.file, "rb") as f:
                ino, pos = self.file_pos or (None, None)
                if ino != st.st_ino or pos is None or pos > st.st_size:
                    pos = max(0, st.st_size - (2 << 20)) if pos is None else 0  # first read: the last 2 MB
                f.seek(pos)
                data = f.read(8 << 20)
                self.file_pos = (st.st_ino, pos + len(data))
        except OSError as e:
            log.warning("%s: %s", self.file, e)
            return None
        now = datetime.datetime.now()
        lines = []
        for line in data.decode("utf-8", "replace").splitlines():
            m = SSHD.search(line)
            if m:
                lines.append((_file_time(line, now), m.group(1)))
        return lines

    def read(self):
        lines = None
        if self.journal:
            lines = self._journal_lines()
            if lines is not None and (lines or self.cursor):
                self.source = "journalctl"
            else:
                lines = None  # no sshd in the journal (rsyslog only, or a container without the journal)
        if lines is None:
            lines = self._file_lines()
            self.source = self.file if lines is not None else None
        for t, msg in lines or []:
            for kind, rx in PATTERNS:
                m = rx.search(msg)
                if m:
                    d = m.groupdict()
                    self.events.append({"kind": kind, "user": d.get("user") or "", "addr": d.get("addr"), "at": t,
                                        "method": d.get("method"), "via": "ssh", "invalid": bool(d.get("invalid"))})
                    break

    def summary(self):
        """Events of the last hour, newest first, and counts; failures are folded per address in the browser."""
        now = time.time()
        recent = [e for e in self.events if e["at"] and now - e["at"] <= 3600]
        cache = {}
        out = []
        for e in reversed(recent[-400:]):
            if e["addr"] not in cache:
                cache[e["addr"]] = self.where(e["addr"])
            out.append({"kind": e["kind"], "user": e["user"], "via": e["via"], "method": e["method"],
                        "time": time.strftime("%H:%M:%S", time.localtime(e["at"])), "ago": now - e["at"], "where": cache[e["addr"]]})
        # With password auth one attempt on a missing user logs both "Invalid user" and "Failed ... for invalid user":
        # count such failures once, as "invalid".
        attempts = [e for e in recent if e["kind"] == "invalid" or (e["kind"] == "failure" and not e["invalid"])]
        by_ip = collections.Counter(e["addr"] for e in attempts)
        return {"events": out[:60], "source": self.source,
                "logins": sum(1 for e in recent if e["kind"] == "login"),
                "failures": sum(1 for e in recent if e["kind"] == "failure" and not e["invalid"]),
                "invalid": sum(1 for e in recent if e["kind"] == "invalid"),
                "brute": [{"ip": ip, "n": n} for ip, n in by_ip.most_common(10)]}
