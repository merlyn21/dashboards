#!/usr/bin/env python3
"""Zabbix Galaxy: local backend.

Serves the static frontend from ./web and a compact JSON API built on top of
the Zabbix JSON-RPC API. Credentials are read from .env and never leave this
process. Standard library only.

    python3 server.py [--port 8080] [--host 127.0.0.1]
"""
import argparse
import atexit
import json
import math
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
OVERVIEW_TTL = 25  # seconds
STALE_AFTER = 600  # item without new data for this long counts as "no data"
# Web users online are not exposed by the API to a non-super-admin role. If you
# add an item that counts them (e.g. a DB monitor item on the Zabbix server host:
# SELECT count(*) FROM sessions WHERE status=0 AND lastaccess > extract(epoch from now())-300),
# give it this key and the ticker will show it.
WEB_USERS_KEY = os.environ.get("GALAXY_WEB_USERS_KEY", "zabbix.web.users.online")


# --------------------------------------------------------------------------- config

ENV_KEYS = ("ZABBIX_URL", "ZABBIX_USER", "ZABBIX_PASSWORD", "ZABBIX_TOKEN", "ZABBIX_PROXY",
            "GALAXY_HOST", "GALAXY_PORT", "GALAXY_LANG", "GALAXY_SERVICES")

# Interface language (GALAXY_LANG=ru|en); set in main() from the settings file.
LANG = "ru"


# Important OS services (GALAXY_SERVICES="postgres,1C,nginx"): case-insensitive substrings of the
# service name or description. Empty = default behaviour (one aggregate "services" satellite).
SERVICE_PATTERNS = []
MAX_OTHER_SERVICES = 5  # other services still get the aggregate satellite only when there are this few


def L(ru, en):
    """Pick the string for the configured interface language."""
    return en if LANG == "en" else ru


def load_env(path):
    """Read ZABBIX_* / GALAXY_* values from .env. Accepts KEY=value lines or a
    JSON-like fragment ("KEY": "value"). Real environment variables take precedence."""
    cfg = {}
    try:
        text = open(path, encoding="utf-8").read()
    except FileNotFoundError:
        text = ""
    for key in ENV_KEYS:
        m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % key, text)
        if m:
            cfg[key] = json.loads('"%s"' % m.group(1))
            continue
        # [ \t]* (not \s*) so an empty value such as "ZABBIX_PROXY=" never swallows the next line
        m = re.search(r"^[ \t]*(?:export[ \t]+)?%s[ \t]*=[ \t]*(.*)$" % key, text, re.M)
        if m:
            cfg[key] = m.group(1).strip().strip("'\"")
    for key in ENV_KEYS:
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    if not cfg.get("ZABBIX_URL"):
        sys.exit("ZABBIX_URL is not set (.env or environment)")
    return cfg


# --------------------------------------------------------------------------- zabbix client

class ZabbixError(Exception):
    pass


class _PostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """urllib refuses to follow 307/308 for POST; resend the same body instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code in (307, 308) and req.get_method() == "POST":
            return urllib.request.Request(newurl, req.data, dict(req.header_items()),
                                          origin_req_host=req.origin_req_host, unverifiable=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Zabbix:
    def __init__(self, cfg):
        url = cfg["ZABBIX_URL"].rstrip("/")
        self.url = url if url.endswith("api_jsonrpc.php") else url + "/api_jsonrpc.php"
        self.user = cfg.get("ZABBIX_USER")
        self.password = cfg.get("ZABBIX_PASSWORD")
        self.api_token = cfg.get("ZABBIX_TOKEN")
        self.token = self.api_token
        self.lock = threading.Lock()
        # ZABBIX_PROXY (http://[user:pass@]host:port) routes every API call through a proxy.
        # Unset: urllib's default, which still honours system HTTP(S)_PROXY variables.
        self.proxy = cfg.get("ZABBIX_PROXY") or None
        handlers = [urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})] if self.proxy else []
        # The nginx in front of Zabbix may answer with a 307 to the same URL plus a
        # challenge cookie (bp_chl); keep cookies and repeat the POST to get through.
        handlers += [urllib.request.HTTPCookieProcessor(), _PostRedirectHandler()]
        self.opener = urllib.request.build_opener(*handlers)

    def proxy_display(self):
        """Proxy address with any credentials masked, for logs."""
        return re.sub(r"//[^@/]*@", "//***@", self.proxy) if self.proxy else None

    def _raw(self, method, params, auth=True):
        headers = {"Content-Type": "application/json-rpc"}
        if auth and self.token:
            headers["Authorization"] = "Bearer " + self.token
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
        req = urllib.request.Request(self.url, body, headers)
        with self.opener.open(req, timeout=60) as r:
            data = json.load(r)
        if "error" in data:
            e = data["error"]
            raise ZabbixError("%s: %s" % (e.get("message"), e.get("data")))
        return data["result"]

    def version(self):
        try:
            return self._raw("apiinfo.version", {}, auth=False)
        except Exception:
            return None

    def login(self):
        with self.lock:
            if self.api_token:
                self.token = self.api_token
                return
            self.token = None
            self.token = self._raw("user.login", {"username": self.user, "password": self.password}, auth=False)

    def logout(self):
        if self.token and not self.api_token:
            try:
                self._raw("user.logout", [])
            except Exception:
                pass
            self.token = None

    def call(self, method, params):
        if not self.token:
            self.login()
        try:
            return self._raw(method, params)
        except ZabbixError as e:
            msg = str(e).lower()
            if "session" in msg or "not authorized" in msg or "re-login" in msg:
                self.login()
                return self._raw(method, params)
            raise


# --------------------------------------------------------------------------- helpers

def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


KEY_ARGS = re.compile(r"^([^\[]+)(?:\[(.*)\])?$")


def split_key(key):
    """'net.if.in["eth0",dropped]' -> ('net.if.in', ['eth0', 'dropped'])."""
    m = KEY_ARGS.match(key)
    if not m:
        return key, []
    base, args = m.group(1), m.group(2)
    if args is None:
        return base, []
    out, cur, quoted, i = [], "", False, 0
    while i < len(args):
        c = args[i]
        if quoted:
            if c == "\\" and i + 1 < len(args) and args[i + 1] == '"':
                cur += '"'
                i += 1
            elif c == '"':
                quoted = False
            else:
                cur += c
        elif c == '"' and cur == "":
            quoted = True
        elif c == ",":
            out.append(cur.strip())
            cur = ""
        else:
            cur += c
        i += 1
    out.append(cur.strip())
    return base, out


# Windows hosts report hundreds of services; only these matter for the dashboard
# (PostgreSQL and 1C). Override with GALAXY_WIN_SERVICES="regex".
IMPORTANT_WIN_SERVICES = re.compile(
    os.environ.get("GALAXY_WIN_SERVICES") or r"postgres|pgsql|\b1[cс]\b|1[cс]:|1[cс]link|1[cс] ras", re.I)

VIRTUAL_IF = re.compile(r"^(lo|tun|tap|virbr|vnet|veth|docker|br-|wg|cni|flannel|kube|bridge|gre|erspan|ip6|sit\d|ifb)", re.I)
WAN_IF = re.compile(r"gateway|\bwan|wan\d|pppoe|uplink|internet|\bisp|lte", re.I)
VIRTUAL_IF_WIN = re.compile(r"(TAP-Windows|OpenVPN|Loopback|Hyper-V|Virtual|WireGuard|Wintun|Bluetooth)", re.I)
# Raw flash / loop devices (OpenWrt exposes ~10 mtdblock partitions via vfs.dev.*).
FLASH_DEV = re.compile(r"^(mtdblock\d+|ubiblock\d+_\d+|mmcblk\d+boot\d+|loop\d+|ram\d+)$")
# Linux operstate as the agent template maps it (IF-MIB like): 6 up; 2 down, 3 lowerlayerdown, 7 notpresent.
OPERSTATE_UP, OPERSTATE_DOWN = 6, (2, 3, 7)
SEVERITY = ["not_classified", "information", "warning", "average", "high", "disaster"]


class Host:
    """Item index for one host with typed accessors."""

    def __init__(self, items, now):
        self.now = now
        self.by_key = {}
        self.items = items
        for it in items:
            self.by_key[it["key_"]] = it

    def item(self, *keys):
        for k in keys:
            it = self.by_key.get(k)
            if it is not None:
                return it
        return None

    def fresh(self, it):
        return it is not None and int(it.get("lastclock") or 0) > self.now - STALE_AFTER

    def val(self, *keys, fresh=True):
        it = self.item(*keys)
        if it is None or (fresh and not self.fresh(it)):
            return None
        return num(it["lastvalue"])

    def first(self, *prefixes, fresh=True):
        """Item whose key is `p` or starts with `p[` (SNMP templates put OIDs in the key)."""
        for p in prefixes:
            for it in self.items:
                k = it["key_"]
                if (k == p or k.startswith(p + "[")) and (not fresh or self.fresh(it)):
                    return it
        return None

    def fval(self, *prefixes, fresh=True):
        it = self.first(*prefixes, fresh=fresh)
        return num(it["lastvalue"]) if it else None

    def text(self, *keys):
        it = self.item(*keys)
        return it["lastvalue"] if it is not None and it["lastvalue"] != "" else None


DEBIAN_BY_GCC = {"8": "10", "10": "11", "12": "12", "14": "13"}


def detect_distro(os_text, uname, family, groups):
    """Best-effort distribution from system.sw.os / system.uname strings.
    Returns {"id": ..., "name": ...}; id drives the planet surface style.
    `groups` holds the host name followed by its group names."""
    t = " ".join(x for x in (os_text, uname) if x)
    tl = t.lower()
    if "windows" in tl or family == "windows":
        m = re.search(r"Build (\d+)", t) or re.search(r"10\.0\.(\d+)", t)
        build = int(m.group(1)) if m else 0
        m = re.search(r"Windows Server (\d{4})", t)
        if m:
            name = "Windows Server " + m.group(1)
        elif "windows 11" in tl or build >= 22000:
            name = "Windows 11"
        elif "windows 10" in tl:
            name = "Windows 10"
        else:
            name = "Windows"
        return {"id": "windows", "name": name}
    # OpenWrt before Debian: its kernel string reads "(user@debian) (aarch64-openwrt-linux-musl-gcc ...)".
    if "openwrt" in tl:
        return {"id": "openwrt", "name": "OpenWrt"}
    if "routeros" in tl or "mikrotik" in tl:
        m = re.search(r"RouterOS\s+(\S+)", t)
        return {"id": "routeros", "name": "RouterOS" + (" " + m.group(1) if m else "")}
    if "raspbian" in tl or "raspberrypi" in tl or "rpt-rpi" in tl:
        return {"id": "raspbian", "name": "Raspberry Pi OS"}
    if "ubuntu" in tl:
        m = re.search(r"~(\d{2}\.\d{2})", t)
        return {"id": "ubuntu", "name": "Ubuntu" + (" " + m.group(1) if m else "")}
    if "debian" in tl:
        m = re.search(r"deb(\d+)u", t)
        ver = m.group(1) if m else None
        if not ver:
            m = re.search(r"gcc-(\d+)", t)
            ver = DEBIAN_BY_GCC.get(m.group(1)) if m else None
        return {"id": "debian", "name": "Debian" + (" " + ver if ver else "")}
    for key, dist, name in (("manjaro", "manjaro", "Manjaro"), (".el", "rhel", "RHEL-like"), ("fedora", "fedora", "Fedora"),
                            ("arch", "arch", "Arch Linux"), ("alpine", "alpine", "Alpine")):
        if key in tl:
            return {"id": dist, "name": name}
    if family == "linux" or "linux" in tl:
        return {"id": "linux", "name": "Linux"}
    # No OS data (disabled or never polled): guess from host name and groups.
    gl = " ".join(groups).lower()
    if "rasp" in gl:
        return {"id": "raspbian", "name": "Raspberry Pi OS"}
    if "ubuntu" in gl:
        return {"id": "ubuntu", "name": "Ubuntu"}
    if "debian" in gl:
        return {"id": "debian", "name": "Debian"}
    if "windows" in gl:
        return {"id": "windows", "name": "Windows"}
    if "linux" in gl:
        return {"id": "linux", "name": "Linux"}
    return {"id": "unknown", "name": L("ОС неизвестна", "Unknown OS")}


def infobase_name(item_name, fallback):
    """'utdemo: Активные сессии' -> 'utdemo'."""
    return item_name.split(":", 1)[0].strip() if ":" in item_name else fallback


def iface_label(item_name, ifname):
    m = re.match(r"Interface (.*?):", item_name)
    # SNMP templates append the interface alias in brackets: "ether1-gateway()" when it is empty
    return re.sub(r"\(\)$", "", m.group(1)).strip() if m else ifname


# --------------------------------------------------------------------------- data model

class Model:
    def __init__(self, zbx):
        self.zbx = zbx
        self.cache = None
        self.cache_at = 0
        self.lock = threading.Lock()
        self.refreshing = False

    # ---- overview -------------------------------------------------------

    def overview(self):
        """Stale-while-revalidate: once built, callers always get the last snapshot
        immediately; an expired snapshot is rebuilt in a background thread so
        detail/history/service requests never wait for the full rebuild."""
        if self.cache is None:
            with self.lock:
                if self.cache is None:
                    self._refresh()
        elif time.time() - self.cache_at >= OVERVIEW_TTL and not self.refreshing:
            self.refreshing = True
            threading.Thread(target=self._refresh_bg, daemon=True).start()
        return self.cache

    def _refresh(self):
        self.cache = self._build()
        self.cache_at = time.time()

    def _refresh_bg(self):
        try:
            with self.lock:
                self._refresh()
        except Exception as e:  # keep serving the previous snapshot
            print("overview refresh failed: %s" % e, file=sys.stderr)
        finally:
            self.refreshing = False

    def _build(self):
        z = self.zbx
        now = int(time.time())
        hosts = z.call("host.get", {
            "output": ["hostid", "host", "name", "status", "description",
                       "maintenance_status", "active_available"],
            "selectInterfaces": ["available", "ip", "dns", "type", "main", "error"],
            "selectHostGroups": ["name"],
            "selectTags": ["tag", "value"],
        })
        items = z.call("item.get", {
            "output": ["itemid", "hostid", "key_", "name", "lastvalue", "lastclock",
                       "units", "value_type"],
            "monitored": True,
        })
        triggers = z.call("trigger.get", {
            "output": ["triggerid", "description", "priority", "lastchange"],
            "filter": {"value": 1},
            "monitored": True,
            "skipDependent": True,
            "expandDescription": True,
            "selectHosts": ["hostid"],
            "selectLastEvent": ["eventid", "acknowledged", "clock"],
        })

        items_by_host = {}
        for it in items:
            items_by_host.setdefault(it["hostid"], []).append(it)
        problems_by_host = {}
        for t in triggers:
            ev = t.get("lastEvent") or {}
            p = {
                "id": t["triggerid"],
                "name": t["description"],
                "severity": int(t["priority"]),
                "since": int(ev.get("clock") or t["lastchange"] or 0),
                "acknowledged": ev.get("acknowledged") == "1",
            }
            for h in t.get("hosts", []):
                problems_by_host.setdefault(h["hostid"], []).append(p)

        out = []
        detail = {}
        for h in hosts:
            hid = h["hostid"]
            hx = Host(items_by_host.get(hid, []), now)
            probs = sorted(problems_by_host.get(hid, []), key=lambda p: (-p["severity"], -p["since"]))
            summary, extra = self._host(h, hx, probs, now)
            out.append(summary)
            detail[hid] = extra

        out.sort(key=lambda x: x["name"].lower())
        warnings = self._topology(hosts, out, detail)
        groups = sorted({g for h in out for g in h["groups"]})
        info = self._zabbix_info(hosts, items, out, now)
        info["topology_warnings"] = warnings
        return {"generated": now, "hosts": out, "groups": groups, "_detail": detail, "zabbix": info}

    @staticmethod
    def _topology(hosts, out, detail):
        """Network dependencies from host tags: galaxy.uplink=<host name of the upstream device>
        and optionally galaxy.port=<its port>. Sets uplink / uplink_port / clients / blocked_by on
        the summaries and the client name on the matching port. Returns warnings for bad tags."""
        tags = {h["hostid"]: {t["tag"]: t["value"].strip() for t in h.get("tags", [])} for h in hosts}

        def attach(parent, port, client, guess):
            """Name the client on the port, both in the summary and in the port card."""
            for x in [port] + [x for x in (detail.get(parent["id"], {}).get("satellites", {}).get("ports") or {}).get("ports", [])
                               if x["name"] == port["name"]]:
                x.setdefault("clients", []).append(client)
                if guess:
                    x["guess"] = True
        live = [s for s in out if s["state"] != "disabled"]
        by_host = {s["host"]: s for s in live}
        by_name = {s["name"]: s for s in live}
        warnings = []
        for s in live:
            s["uplink"] = None
            s["clients"] = []
        for s in live:
            ref = tags.get(s["id"], {}).get("galaxy.uplink")
            if not ref:
                continue
            parent = by_host.get(ref) or by_name.get(ref)
            if parent is None:
                warnings.append(L("%s: galaxy.uplink=%s — хост не найден", "%s: galaxy.uplink=%s — host not found") % (s["name"], ref))
                continue
            if parent is s:
                warnings.append(L("%s: galaxy.uplink ссылается на себя", "%s: galaxy.uplink points to itself") % s["name"])
                continue
            s["uplink"] = parent["id"]
            s["uplink_name"] = parent["name"]
            s["uplink_port"] = tags[s["id"]].get("galaxy.port") or None
        # Break cycles: walk up from every host; a host met twice closes a loop.
        ids = {s["id"]: s for s in live}
        for s in live:
            seen, cur = [], s
            while cur is not None and cur["uplink"]:
                if cur["id"] in seen:
                    warnings.append(L("Цикл в galaxy.uplink: %s", "Loop in galaxy.uplink: %s") % " → ".join(ids[i]["name"] for i in seen))
                    cur["uplink"] = None
                    break
                seen.append(cur["id"])
                cur = ids.get(cur["uplink"])
        for s in live:
            if not s["uplink"]:
                s.pop("uplink_name", None)
                s.pop("uplink_port", None)
                continue
            parent = ids[s["uplink"]]
            parent["clients"].append(s["id"])
            parent.setdefault("client_names", []).append(s["name"])
            port = s.get("uplink_port")
            if port:
                sat = next((x for x in parent["satellites"] if x["type"] == "ports"), None)
                hit = next((x for x in (sat or {}).get("ports", []) if x["name"] == port), None)
                if hit is None:
                    warnings.append(L("%s: galaxy.port=%s — у %s нет такого порта", "%s: galaxy.port=%s — %s has no such port")
                                    % (s["name"], port, parent["name"]))
                    s["uplink_port"] = None
                else:
                    attach(parent, hit, s["name"], False)
        # Clients without galaxy.port: guess the port by traffic. The port receives what the client
        # sends and sends what it receives, so compare (client out, client in) with (port in, port out)
        # on a log scale and pair greedily; clients left over hang on a "port not set" segment.
        lg = lambda v: math.log10(max(v or 0, 1000))
        for parent in live:
            sat = next((x for x in parent["satellites"] if x["type"] == "ports"), None)
            if not sat or not parent["clients"]:
                continue
            free = [p for p in sat.get("ports", []) if p.get("up") and not p.get("clients") and not p.get("virtual")]
            todo = [ids[c] for c in parent["clients"] if not ids[c].get("uplink_port")]
            pairs = sorted((abs(lg(c["net_out"]) - lg(p.get("in"))) + abs(lg(c["net_in"]) - lg(p.get("out"))), i, j)
                           for i, c in enumerate(todo) if (c["net_in"] or 0) + (c["net_out"] or 0) > 0
                           for j, p in enumerate(free))
            used_c, used_p = set(), set()
            for score, i, j in pairs:
                if score > 1.3 or i in used_c or j in used_p:
                    continue  # more than ~4-5x off in each direction: not a believable match
                used_c.add(i)
                used_p.add(j)
                c, p = todo[i], free[j]
                c["uplink_port"], c["uplink_port_guess"] = p["name"], True
                attach(parent, p, c["name"], True)
            for i, c in enumerate(todo):
                if i not in used_c:
                    sat["ports"].append({"name": "?" + c["name"], "virtual": True, "up": None, "in": None, "out": None,
                                         "speed": None, "uplink": False, "clients": [c["name"]]})
            missing = [c["name"] for i, c in enumerate(todo) if i not in used_c]
            if missing:
                warnings.append(L("Порт не указан и не угадан (galaxy.port): %s", "Port not set and not guessed (galaxy.port): %s")
                                % ", ".join(missing))
        # A host that is down while its uplink is down too is unreachable because of the uplink.
        down = lambda x: x["state"] in ("unreachable", "unknown")
        for s in live:
            s["blocked_by"] = None
            cur = ids.get(s["uplink"]) if s["uplink"] else None
            while down(s) and cur is not None:
                if down(cur):
                    s["blocked_by"] = cur["id"]
                    s["blocked_by_name"] = cur["name"]
                cur = ids.get(cur["uplink"]) if cur["uplink"] else None
        return warnings

    def _zabbix_info(self, hosts, items, summaries, now):
        """Facts about the Zabbix installation itself, for the ticker."""
        if not hasattr(self, "_version"):
            self._version = self.zbx.version()
        enabled = [h for h in summaries if h["state"] != "disabled"]
        sev = [0] * 6
        for h in enabled:
            for i, n in enumerate(h["problems"]["by_severity"]):
                sev[i] += n
        web_users = next((num(it["lastvalue"]) for it in items if it["key_"] == WEB_USERS_KEY and int(it.get("lastclock") or 0)), None)
        queue = next((num(it["lastvalue"]) for it in items if it["key_"] == "zabbix[queue,10m]"), None)
        try:
            unsupported = int(self.zbx.call("item.get", {"countOutput": True, "monitored": True, "filter": {"state": 1}}))
        except Exception:
            unsupported = None
        return {
            "version": self._version,
            "server": urlparse(self.zbx.url).hostname,
            "hosts_enabled": sum(1 for h in hosts if h["status"] == "0"),
            "hosts_disabled": sum(1 for h in hosts if h["status"] == "1"),
            "hosts_online": sum(1 for h in summaries if h["state"] == "online"),
            "items": len(items),
            "unsupported": unsupported,
            "problems": sev,
            "queue_10m": queue,
            "web_users": web_users,
        }

    def _host(self, h, hx, probs, now):
        enabled = h["status"] == "0"
        names = " ".join(it["name"] for it in hx.items[:80])
        os_family = "windows" if ("Windows:" in names or "perf_counter" in " ".join(hx.by_key)) else (
            "linux" if "Linux:" in names else "unknown")

        # SNMP devices describe themselves in sysDescr ("RouterOS RB951G-2HnD")
        descr_it = hx.first("system.descr", fresh=False)
        sysdescr = (descr_it["lastvalue"] or None) if descr_it else None
        if os_family == "unknown" and (sysdescr or any(it["key_"].startswith("zabbix[host,snmp") for it in hx.items)):
            os_family = "network"

        # availability
        ifaces = h.get("interfaces", [])
        iface_ok = any(i["available"] == "1" for i in ifaces)
        iface_bad = any(i["available"] == "2" for i in ifaces)
        active_ok = h.get("active_available") == "1"
        cpu_item = hx.item("system.cpu.util")
        data_fresh = hx.fresh(cpu_item) or hx.fresh(hx.item("agent.ping"))
        # ICMP checks (template "ICMP Ping"): for devices without an agent (cameras, printers,
        # switches) this is the only data, and a fresh ping says whether they are alive.
        ping_it = hx.item("icmpping")
        ping = hx.val("icmpping")
        probe = bool(hx.items) and all(k.startswith(("icmpping", "zabbix[host")) for k in hx.by_key)
        if not enabled:
            state = "disabled"
        elif iface_ok or active_ok or data_fresh or ping == 1:
            state = "online"
        elif ping == 0:
            state = "unreachable"
        elif iface_bad or hx.items:
            state = "unreachable"
        else:
            state = "unknown"

        # core metrics
        cpu = hx.val("system.cpu.util")
        if cpu is None:
            idle = hx.val("system.cpu.util[,idle]")
            cpu = None if idle is None else max(0.0, 100 - idle)
        if cpu is None:  # SNMP: one item per processor, system.cpu.util[hrProcessorLoad.N]
            loads = [num(it["lastvalue"]) for it in hx.items
                     if it["key_"].startswith("system.cpu.util[hrProcessorLoad") and hx.fresh(it)]
            cpu = sum(loads) / len(loads) if loads else None
        mem = hx.val("vm.memory.utilization", "vm.memory.util")
        if mem is None:  # SNMP: vm.memory.util[memoryUsedPercentage.Memory]
            mem = hx.fval("vm.memory.util", "vm.memory.utilization")
        if mem is None:
            pav = hx.val("vm.memory.size[pavailable]")
            mem = None if pav is None else 100 - pav
        mem_total = hx.val("vm.memory.size[total]", fresh=False) or hx.fval("vm.memory.total", fresh=False)
        cores = hx.val("system.cpu.num", fresh=False) or hx.val(
            'wmi.get[root/cimv2,"Select NumberOfLogicalProcessors from Win32_ComputerSystem"]', fresh=False)
        load1 = hx.val("system.cpu.load[all,avg1]", "system.cpu.load[,avg1]")
        # Logged-in users: Linux agent item, or RDP sessions on Windows (custom "rdps").
        users = hx.val("system.users.num", "rdps")
        users_src = ("system" if hx.item("system.users.num") else "rdp") if hx.item("system.users.num", "rdps") else None
        uptime = hx.val("system.uptime")
        if uptime is None:  # SNMP devices: hrSystemUptime / sysUpTime
            uptime = hx.fval("system.hw.uptime", "system.net.uptime")
        swap_free_pct = hx.val("system.swap.size[,pfree]", "system.swap.pfree")

        if state == "online" and cpu is None and mem is None and iface_bad:
            state = "unreachable"

        # filesystems
        fs = {}
        for it in hx.items:
            base, args = split_key(it["key_"])
            if base in ("vfs.fs.dependent.size", "vfs.fs.size") and len(args) >= 2 and args[1] in ("pused", "total", "used"):
                d = fs.setdefault(args[0], {"mount": args[0]})
                d[args[1]] = num(it["lastvalue"]) if hx.fresh(it) else None
            elif base in ("vfs.fs.pused", "vfs.fs.total", "vfs.fs.used") and args:
                label = it["name"].split(":", 1)[0].strip() if ":" in it["name"] else args[0]
                d = fs.setdefault(label, {"mount": label})
                d[base.rsplit(".", 1)[1]] = num(it["lastvalue"]) if hx.fresh(it) else None
        filesystems = sorted(fs.values(), key=lambda f: f["mount"])
        main_fs = fs.get("/") or fs.get("C:")
        if main_fs is None and filesystems:
            main_fs = max(filesystems, key=lambda f: f.get("total") or 0)
        disk = main_fs.get("pused") if main_fs else None

        # network
        ifs = {}
        snmp_field = {"net.if.status": "status", "net.if.speed": "speed", "net.if.type": "iftype",
                      "net.if.in.errors": "in_errors", "net.if.out.errors": "out_errors",
                      "net.if.in.discards": "in_discards", "net.if.out.discards": "out_discards"}
        for it in hx.items:
            base, args = split_key(it["key_"])
            if len(args) != 1:
                continue
            # SNMP keys carry the OID (ifHCInOctets.3 / ifHCOutOctets.3): group by the ifIndex.
            m = re.match(r"^if\w+\.(\d+)$", args[0])
            ifid = "if%s" % m.group(1) if m else args[0]
            if base in ("net.if.in", "net.if.out"):
                d = ifs.setdefault(ifid, {"id": ifid})
                d.setdefault("name", iface_label(it["name"], args[0]))
                d["in" if base.endswith("in") else "out"] = num(it["lastvalue"]) if hx.fresh(it) else None
                d["item_" + ("in" if base.endswith("in") else "out")] = (it["itemid"], int(it["value_type"]))
            elif m and base in snmp_field:
                d = ifs.setdefault(ifid, {"id": ifid, "name": iface_label(it["name"], args[0])})
                d[snmp_field[base]] = num(it["lastvalue"]) if hx.fresh(it) or base in ("net.if.speed", "net.if.type") else None
        interfaces = sorted(ifs.values(), key=lambda i: -((i.get("in") or 0) + (i.get("out") or 0)))
        for i in interfaces:
            # ifType: 6 ethernet, 71 wifi, 117 gigabit; bridges, tunnels and "other" are virtual.
            virt_type = i.get("iftype") is not None and int(i["iftype"]) not in (6, 62, 69, 71, 117)
            i["virtual"] = bool(VIRTUAL_IF.match(i["name"]) or VIRTUAL_IF_WIN.search(i["name"]) or virt_type)
        phys = [i for i in interfaces if not i["virtual"]] or interfaces
        # A router forwards LAN traffic between its own ports: summing them would count it
        # twice, so when an uplink is recognisable by name, host traffic is the uplink alone.
        uplinks = [i for i in phys if WAN_IF.search(i["name"])]
        if uplinks:
            phys = uplinks + [i for i in phys if i not in uplinks]
            traffic_ifs = uplinks
        else:
            traffic_ifs = phys
        net_in = sum(i.get("in") or 0 for i in traffic_ifs) if any(i.get("in") is not None for i in traffic_ifs) else None
        net_out = sum(i.get("out") or 0 for i in traffic_ifs) if any(i.get("out") is not None for i in traffic_ifs) else None

        satellites, sat_detail = self._satellites(hx, probs)

        worst = max((p["severity"] for p in probs), default=-1)
        ip = next((i["ip"] or i["dns"] for i in ifaces if i["main"] == "1"), None)

        summary = {
            "id": h["hostid"],
            "name": h["name"],
            "host": h["host"],
            "groups": [g["name"] for g in h.get("hostgroups", [])],
            "state": state,
            "maintenance": h.get("maintenance_status") == "1",
            "os": os_family,
            "distro": detect_distro(hx.text("system.sw.os") or sysdescr, hx.text("system.uname"), os_family,
                                    [h["name"]] + [g["name"] for g in h.get("hostgroups", [])]),
            "ip": ip,
            "cpu": cpu,
            "mem": mem,
            "mem_total": mem_total,
            "disk": disk,
            "disk_mount": main_fs["mount"] if main_fs else None,
            "disk_total": main_fs.get("total") if main_fs else None,
            "net_in": net_in,
            "net_out": net_out,
            "cores": cores,
            "load1": load1,
            "users": users,
            "users_src": users_src,
            "uptime": uptime,
            "items": len(hx.items),
            "problems": {"count": len(probs), "worst": worst,
                         "by_severity": [sum(1 for p in probs if p["severity"] == s) for s in range(6)],
                         # compact list for client-side alerting (new / high-severity problems)
                         "list": [{"id": p["id"], "name": p["name"], "severity": p["severity"],
                                   "since": p["since"], "ack": p["acknowledged"]} for p in probs]},
            "satellites": satellites,
            # Ping data (any host with ICMP items); probe = ping is all there is.
            "ping": {"ok": ping, "rtt": hx.val("icmppingsec"), "loss": hx.val("icmppingloss")} if ping_it else None,
            "probe": probe,
        }
        if probe:
            summary["distro"] = {"id": "device", "name": L("Устройство · только ping", "Device · ping only")}
        disks = self._disks(hx)
        series = self._series_items(hx, main_fs, phys)
        for d in disks:  # per-device history for the disk card sparklines
            for field, ref in d.pop("_ids").items():
                series["dev:%s:%s" % (d["name"], field)] = ref
        extra = {
            "os_name": hx.text("system.sw.os", "system.uname") or sysdescr,
            "arch": hx.text("system.sw.arch"),
            "hostname": hx.text("system.hostname", "agent.hostname"),
            "agent_version": hx.text("agent.version"),
            "description": h.get("description") or "",
            "swap_free_pct": swap_free_pct,
            "procs": hx.val("proc.num"),
            "users": hx.val("system.users.num"),
            "filesystems": filesystems,
            "disks": disks,
            "interfaces": [{k: v for k, v in i.items()} for i in interfaces],
            "iface_errors": [i["error"] for i in ifaces if i.get("error")],
            "problems": probs,
            "satellites": sat_detail,
            "series_items": series,
        }
        return summary, extra

    def _disks(self, hx):
        """Block devices: I/O load (vfs.dev.* or Windows PhysicalDisk counters)
        merged with S.M.A.R.T. data (SATA attributes, NVMe health)."""
        devs = {}

        def dev(name):
            return devs.setdefault(name, {"name": name, "io": {}, "smart": None, "_ids": {},
                                          # vda/vdb (virtio), xvd*, "0 C:" on VMs: no SMART behind them
                                          "virtual": bool(re.match(r"^(x?vd[a-z]+|dm-\d+)$", name))})

        for it in hx.items:
            base, args = split_key(it["key_"])
            v = num(it["lastvalue"]) if hx.fresh(it) else None
            if base.startswith("vfs.dev.") and args:
                field = {"vfs.dev.util": "util", "vfs.dev.read.rate": "r_iops", "vfs.dev.write.rate": "w_iops",
                         "vfs.dev.read.await": "r_await", "vfs.dev.write.await": "w_await",
                         "vfs.dev.queue_size": "queue"}.get(base)
                if field:
                    d = dev(args[0])
                    d["io"][field] = v
                    if field in ("util", "r_iops", "w_iops"):
                        d["_ids"][field] = (it["itemid"], int(it["value_type"]))
            elif base == "perf_counter_en" and args and "PhysicalDisk(" in args[0] and "_Total" not in args[0]:
                m = re.search(r"PhysicalDisk\((.*?)\)\\(.*)$", args[0])
                if not m:
                    continue
                d, counter = dev(m.group(1)), m.group(2)
                ref = (it["itemid"], int(it["value_type"]))
                if counter == "% Idle Time":
                    d["io"]["util"] = None if v is None else max(0.0, 100 - v)
                    d["_ids"]["idle"] = ref  # history is idle %, the client inverts it
                elif counter == "Disk Reads/sec":
                    d["io"]["r_iops"] = v
                    d["_ids"]["r_iops"] = ref
                elif counter == "Disk Writes/sec":
                    d["io"]["w_iops"] = v
                    d["_ids"]["w_iops"] = ref
                elif counter == "Avg. Disk sec/Read":
                    d["io"]["r_await"] = None if v is None else v * 1000
                elif counter == "Avg. Disk sec/Write":
                    d["io"]["w_await"] = None if v is None else v * 1000
                elif counter == "Current Disk Queue Length":
                    d["io"]["queue"] = v
            elif base.startswith("smart.disk.") and args and base != "smart.disk.get":
                raw = args[-1] if base == "smart.disk.get" else args[0]
                short = raw.split()[0]  # "sda sat" -> "sda"
                name = short + "n1" if re.fullmatch(r"nvme\d+", short) and (short + "n1") in devs else short
                d = dev(name)
                sm = d["smart"] = d["smart"] or {"id": raw, "type": "nvme" if short.startswith("nvme") else "sata", "attrs": {}}
                field = base[len("smart.disk."):]
                val = it["lastvalue"]
                if field.startswith("attribute."):
                    sm["attrs"][field[len("attribute."):]] = num(val)
                elif field in ("model", "sn", "test", "error"):
                    sm[field] = val or None
                elif field in ("temperature", "hours", "percentage_used", "media_errors", "critical_warning", "es"):
                    # SMART is polled rarely (often hourly): accept values up to 3 h old.
                    recent = int(it.get("lastclock") or 0) > hx.now - 3 * 3600
                    sm[field] = num(val) if (recent or (field == "hours" and num(val))) else None
        # Fold "nvme0" SMART entries into "nvme0n1" when both exist.
        for name in [n for n in devs if re.fullmatch(r"nvme\d+", n)]:
            full = name + "n1"
            if full in devs and devs[name]["smart"] and not devs[full]["smart"]:
                devs[full]["smart"] = devs.pop(name)["smart"]
        out = []
        for d in devs.values():
            sm = d["smart"]
            status = "ok"
            if sm:
                a = sm["attrs"]
                if (sm.get("test") == "false" or (sm.get("critical_warning") or 0) > 0 or (sm.get("media_errors") or 0) > 0
                        or (a.get("reallocated_sector_ct") or 0) > 0 or (a.get("reported_uncorrect") or 0) > 0):
                    status = "bad"
                elif (sm.get("temperature") or 0) >= 55 or (sm.get("percentage_used") or 0) >= 80:
                    status = "warn"
            if (d["io"].get("util") or 0) >= 90 and status == "ok":
                status = "warn"
            d["status"] = status
            if not d["io"] and not sm:
                continue
            if FLASH_DEV.match(d["name"]) and not sm:  # router flash partitions: always idle, pure noise
                continue
            out.append(d)
        return sorted(out, key=lambda d: (d["smart"] is None, d["name"]))

    def _series_items(self, hx, main_fs, phys):
        """Item ids used for the history charts of the detail panel."""
        def iid(*keys):
            it = hx.item(*keys)
            return (it["itemid"], int(it["value_type"])) if it else None
        def pref(*prefixes):
            it = hx.first(*prefixes, fresh=False)
            return (it["itemid"], int(it["value_type"])) if it else None
        s = {
            "cpu": iid("system.cpu.util") or pref("system.cpu.util"),
            "mem": iid("vm.memory.utilization", "vm.memory.util") or pref("vm.memory.util", "vm.memory.utilization"),
            "load": iid("system.cpu.load[all,avg1]"),
            "rtt": iid("icmppingsec"),
            "loss": iid("icmppingloss"),
        }
        if main_fs:
            m = main_fs["mount"]
            s["disk"] = iid("vfs.fs.dependent.size[%s,pused]" % m, "vfs.fs.size[%s,pused]" % m)
            if not s["disk"]:  # SNMP storage: the mount is the item-name label
                it = next((i for i in hx.items if i["key_"].startswith("vfs.fs.pused[") and i["name"].startswith(m + ":")), None)
                s["disk"] = (it["itemid"], int(it["value_type"])) if it else None
        if phys:
            top = phys[0]
            s["net_in"] = top.get("item_in")
            s["net_out"] = top.get("item_out")
            s["net_iface"] = top["name"]
        return s

    def _satellites(self, hx, probs):
        """Derive 'templates' from item keys: the API role cannot read templates."""
        sats, detail = [], {}

        def sat_problems(*prefixes):
            return [p for p in probs if p["name"].startswith(prefixes)]

        def add(kind, label, status, summary, info, prefixes=(), primary=False):
            ps = sat_problems(*prefixes) if prefixes else []
            if ps and status == "ok":
                status = "warn"
            sat = {"type": kind, "label": label, "status": status, "summary": summary, "problems": len(ps)}
            if primary:
                sat["primary"] = True  # the host's main service: bigger craft on the innermost orbit
            if kind == "1c":
                sat["bases"], sat["sessions"] = info.get("bases"), info.get("sessions")
            if kind == "docker":
                sat["containers"] = [{"name": c["name"], "running": bool(c.get("running")), "cpu": c.get("cpu"),
                                      "mem": c.get("mem"), "status": c.get("status")} for c in info["containers"]]
            sats.append(sat)
            if info is not None:
                detail[kind] = info
            return sat

        keys = hx.by_key

        # Docker ------------------------------------------------------------
        if any(k.startswith(("docker.ping", "docker.info", "docker.containers")) for k in keys):  # template items
            conts = {}
            for it in hx.items:
                base, args = split_key(it["key_"])
                if not args or not base.startswith("docker.container"):
                    continue
                name = args[0]
                c = conts.setdefault(name, {"name": name.lstrip("/")})
                v = it["lastvalue"]
                if base == "docker.container_info.state.status":
                    c["status"] = v or None
                elif base == "docker.container_info.state.running":
                    c["running"] = v == "1"
                elif base == "docker.container_info.image":
                    c["image"] = v
                elif base == "docker.container_info.restart_count":
                    c["restarts"] = num(v)
                elif base == "docker.container_info.state.health":
                    c["health"] = num(v)
                elif base == "docker.container_info.started":
                    c["started"] = num(v)
                elif base == "docker.container_stats.cpu_pct_usage":
                    c["cpu"] = num(v) if hx.fresh(it) else None
                    c["item_cpu"] = it["itemid"]
                elif base == "docker.container_stats.memory.usage":
                    c["mem"] = num(v) if hx.fresh(it) else None
                elif base == "docker.networks.rx_bytes":
                    c["rx"] = num(v) if hx.fresh(it) else None
                elif base == "docker.networks.tx_bytes":
                    c["tx"] = num(v) if hx.fresh(it) else None
            containers = sorted(conts.values(), key=lambda c: (not c.get("running"), c["name"]))
            running = sum(1 for c in containers if c.get("running"))
            # docker.ping uses "discard unchanged with heartbeat": a steady 1 is stored rarely,
            # so allow up to 3 h like pgsql.ping instead of the usual 10-minute freshness.
            ping_it = hx.item("docker.ping")
            ping = num(ping_it["lastvalue"]) if ping_it and int(ping_it.get("lastclock") or 0) > hx.now - 3 * 3600 else None
            status = "down" if ping == 0 else ("warn" if running < len(containers) else "ok")
            if ping is None:
                status = "nodata"
            add("docker", "Docker", status, "%d/%d" % (running, len(containers)), {
                "version": hx.text("docker.server_version"),
                "images": hx.val("docker.images.total", fresh=False),
                "running": running,
                "total": len(containers),
                "containers": containers,
            }, ("Docker", "Container"))

        # MySQL (template "MySQL by Zabbix agent 2") --------------------------
        # Only the template's own items count: a lone custom key such as
        # mysql.is_master (a UserParameter) does not make MySQL a monitored service.
        if any(k.startswith(("mysql.ping[", "mysql.get_status_variables[")) for k in keys):
            def mval(prefix, fresh=True):
                it = next((i for i in hx.items if i["key_"] == prefix or i["key_"].startswith(prefix + "[")), None)
                if it is None or (fresh and int(it.get("lastclock") or 0) < hx.now - 3 * 3600):
                    return None
                return num(it["lastvalue"])
            dbs = []
            for it in hx.items:
                base, args = split_key(it["key_"])
                if base == "mysql.db.size" and len(args) >= 4:
                    dbs.append({"name": args[3], "size": num(it["lastvalue"])})
            ping = mval("mysql.ping")
            conns, qps = mval("mysql.threads_connected"), mval("mysql.queries.rate")
            status = "nodata" if ping is None else ("ok" if ping == 1 else "down")
            summary = " · ".join(x for x in (
                "%d conn" % conns if conns is not None else "", "%.0f q/s" % qps if qps is not None else "") if x)
            version_it = next((i for i in hx.items if i["key_"].startswith("mysql.version")), None)
            add("mysql", "MySQL", status, summary, {
                "ping": ping,
                "version": version_it["lastvalue"] if version_it else None,
                "uptime": mval("mysql.uptime"),
                "connections": conns,
                "running": mval("mysql.threads_running"),
                "max_used": mval("mysql.max_used_connections", fresh=False),
                "qps": qps,
                "slow": mval("mysql.slow_queries.rate"),
                "buffer_pool": mval("mysql.buffer_pool_utilization"),
                "databases": sorted(dbs, key=lambda d: -(d["size"] or 0)),
            }, ("MySQL",), primary=True)

        # PostgreSQL --------------------------------------------------------
        if any(k.startswith(("pgsql.ping[", "pgsql.connections[", "pgsql.dbstat.sum[")) for k in keys):  # template items
            dbs = []
            for it in hx.items:
                base, args = split_key(it["key_"])
                if base == "pgsql.db.size" and len(args) >= 4:
                    dbs.append({"name": args[3], "size": num(it["lastvalue"])})
            ping = next((num(it["lastvalue"]) for it in hx.items if it["key_"].startswith("pgsql.ping")
                         and int(it.get("lastclock") or 0) > hx.now - 3 * 3600), None)  # ping is polled ~hourly
            conns = hx.val("pgsql.connections.sum.total")
            status = "nodata" if ping is None else ("ok" if ping == 1 else "down")
            add("postgresql", "PostgreSQL", status, "%d conn" % conns if conns is not None else "", {
                "ping": ping,
                "connections": conns,
                "connections_pct": hx.val("pgsql.connections.sum.total_pct"),
                "active": hx.val("pgsql.connections.sum.active"),
                "cache_hit": hx.val("pgsql.cache.hit"),
                "tps": hx.val("pgsql.dbstat.sum.xact_commit.rate"),
                "uptime": next((num(it["lastvalue"]) for it in hx.items if it["key_"].startswith("pgsql.uptime") and hx.fresh(it)), None),
                "databases": sorted(dbs, key=lambda d: -(d["size"] or 0)),
            }, ("PostgreSQL", "DB [", "Connections", "Dbstat", "WAL", "Archive", "Checkpoint", "Bgwriter"))

        # 1C ------------------------------------------------------------------
        if any(k.startswith("1c.") for k in keys):
            bases = {}
            legacy = legacy_fresh = 0
            for it in hx.items:
                base, args = split_key(it["key_"])
                if not base.startswith("1c."):
                    continue
                if base.startswith("1c.sessions.") and args:
                    ib = bases.setdefault(args[0], {"name": infobase_name(it["name"], args[0])})
                    ib[base[len("1c.sessions."):]] = num(it["lastvalue"]) if hx.fresh(it) else None
                elif not base.startswith("1c.sessions"):
                    legacy += 1
                    legacy_fresh += hx.fresh(it)
            ibs = sorted(bases.values(), key=lambda b: b["name"].lower())
            fresh_ibs = [b for b in ibs if b.get("total") is not None]
            sessions = sum(b.get("total") or 0 for b in fresh_ibs)
            status = "ok" if fresh_ibs or legacy_fresh else "nodata"
            summary = (L("%d баз · %d сеанс.", "%d bases · %d sess.") % (len(ibs), sessions)) if ibs else ""
            add("1c", L("1С", "1C"), status, summary, {
                "bases": len(ibs),
                "sessions": sessions,
                "infobases": [{"name": b["name"], "total": b.get("total"), "users": b.get("users"),
                               "active": b.get("active"), "cpu": b.get("cpu_5min"),
                               "blocked": (b.get("blocked_dbms") or 0) + (b.get("blocked_ls") or 0)} for b in ibs],
                "legacy_items": legacy,
                "legacy_fresh": legacy_fresh,
            }, ("[1С", "[1C", "1C", "1С") + tuple(b["name"] + ":" for b in ibs))

        # SMART -------------------------------------------------------------
        if any(k.startswith("smart.disk") for k in keys):
            disks = {}
            for it in hx.items:
                base, args = split_key(it["key_"])
                if not base.startswith("smart.disk.") or not args or base == "smart.disk.get":
                    continue
                d = disks.setdefault(args[0], {"name": args[0]})
                field = base.split(".", 2)[2]
                if field in ("temperature", "hours", "percentage_used", "media_errors", "critical_warning"):
                    d[field] = num(it["lastvalue"]) if hx.fresh(it) or field == "hours" else None
                elif field in ("model", "test"):
                    d[field] = it["lastvalue"]
            disks = sorted(disks.values(), key=lambda d: d["name"])
            bad = any(d.get("test") == "false" or (d.get("critical_warning") or 0) > 0 or (d.get("media_errors") or 0) > 0 for d in disks)
            temps = [d["temperature"] for d in disks if d.get("temperature") is not None]
            add("smart", "S.M.A.R.T.", "down" if bad else ("ok" if temps else "nodata"),
                ("%d°C" % max(temps)) if temps else L("%d диск.", "%d disks") % len(disks), {"disks": disks}, ("SMART",))

        # Services ----------------------------------------------------------
        svcs = []
        for it in hx.items:
            base, args = split_key(it["key_"])
            if base == "service.info" and len(args) >= 2 and args[1] == "state":
                # With GALAXY_SERVICES every Windows service is read (the list decides what is
                # important); without it only the built-in PostgreSQL / 1C pattern is kept.
                if not SERVICE_PATTERNS and not IMPORTANT_WIN_SERVICES.search(args[0] + " " + it["name"]):
                    continue
                v = num(it["lastvalue"]) if hx.fresh(it) else None
                svcs.append({"name": args[0], "ok": v == 0 if v is not None else None, "state": v, "label": it["name"]})
            elif base == "systemd.unit.info" and args:
                svcs.append({"name": args[0], "ok": it["lastvalue"] == "active", "state": it["lastvalue"]})
            elif base == "service.status" and args:
                svcs.append({"name": args[0], "ok": it["lastvalue"] in ("active", "1", "running"), "state": it["lastvalue"]})
        if svcs:
            def svc_prefixes(lst):
                return tuple('"%s"' % x["name"] for x in lst) + tuple('State of service "%s"' % x["name"] for x in lst)

            important = []
            if SERVICE_PATTERNS:
                for x in svcs:
                    hay = (x["name"] + " " + x.get("label", "")).lower()
                    x["important"] = any(p in hay for p in SERVICE_PATTERNS)
                important = [x for x in svcs if x["important"]]
                # Each important service is its own satellite ("station" craft).
                for x in important:
                    st = "nodata" if x["ok"] is None else ("ok" if x["ok"] else "down")
                    sat = add("svc", x["name"], st, L("работает", "running") if x["ok"] else
                              (L("остановлена", "stopped") if x["ok"] is False else ""), None, svc_prefixes([x]))
                    sat["name"] = x["name"]
                    sat["important"] = True
            others = [x for x in svcs if not x.get("important")]
            down = [x for x in svcs if x["ok"] is False]
            known = [x for x in svcs if x["ok"] is not None]
            status = "nodata" if not known else ("warn" if down else "ok")
            sat = add("services", L("Службы", "Services"), status,
                      "%d/%d" % (len(known) - len(down), len(svcs)) if known else "",
                      {"services": sorted(svcs, key=lambda x: (not x.get("important"), x["ok"] is not False, x["name"].lower()))},
                      svc_prefixes(svcs))
            # The aggregate satellite stays for the detail card; it is not drawn when the
            # important ones are already shown and the rest are too many (Windows) or none.
            if SERVICE_PATTERNS and (not others or len(others) > MAX_OTHER_SERVICES):
                sat["hidden"] = True

        # Network device ports (SNMP IF-MIB: ifOperStatus / ifHighSpeed / traffic) ----
        ports = {}
        for it in hx.items:
            base, args = split_key(it["key_"])
            m = re.match(r"^if\w+\.(\d+)$", args[0]) if len(args) == 1 else None
            if not m or not base.startswith("net.if."):
                continue
            p = ports.setdefault(m.group(1), {"name": iface_label(it["name"], args[0])})
            field = {"net.if.status": "status", "net.if.speed": "speed", "net.if.type": "iftype",
                     "net.if.in": "in", "net.if.out": "out", "net.if.in.errors": "in_errors",
                     "net.if.out.errors": "out_errors"}.get(base)
            if field:
                p[field] = num(it["lastvalue"]) if hx.fresh(it) or field in ("speed", "iftype") else None
        agent_ports = not ports and self._is_openwrt(hx)
        if agent_ports:
            ports = self._agent_ports(hx)
        if ports and any("status" in p for p in ports.values()):
            plist = sorted(ports.values(), key=lambda p: p["name"])
            for p in plist:
                if agent_ports:  # /sys/class/net/*/operstate: 6 up, unknown (0) on tunnels
                    p["up"] = None if p.get("status") in (None, 0) else p["status"] == OPERSTATE_UP
                else:  # ifOperStatus: 1 up, 2 down
                    p["up"] = None if p.get("status") is None else p["status"] == 1
                p["uplink"] = bool(WAN_IF.search(p["name"]))
            phys_ports = [p for p in plist if p.get("iftype") in (6, 62, 69, 71, 117)] or plist
            up = [p for p in phys_ports if p["up"]]
            uplink_down = any(p["uplink"] and p["up"] is False for p in plist)
            ping = hx.val("agent.ping") if agent_ports else hx.val("icmpping")
            status = "down" if uplink_down or ping == 0 else ("nodata" if not any(p["up"] is not None for p in plist) else "ok")
            psat = add("ports", L("Порты", "Ports"), status, L("%d/%d подключено", "%d/%d up") % (len(up), len(phys_ports)), {
                "ports": plist,
                "ping": ping,
                "ping_src": "agent" if agent_ports else "icmp",
                "loss": hx.val("icmppingloss"),
                "rtt": hx.val("icmppingsec"),
                "model": ((hx.first("system.descr", fresh=False) or {}).get("lastvalue") or None) if not agent_ports else
                         " · ".join(x for x in ("OpenWrt", hx.text("system.hostname")) if x),
                "name": hx.text("system.name"),
            }, ("Interface", "Mikrotik", "SNMP", "OpenWrt"), primary=True)
            # LAN ports are drawn as moons linked to the router by traffic lanes (the uplink is the
            # flow to the core), so the aggregate satellite itself is not drawn, only its card.
            psat["hidden"] = True
            psat["ports"] = [{"name": p["name"], "up": p["up"], "in": p.get("in"), "out": p.get("out"),
                              "speed": p.get("speed"), "uplink": p["uplink"]}
                             for p in phys_ports if not p["uplink"]]

        # Zabbix server -----------------------------------------------------
        if any(k.startswith("zabbix[") for k in keys) and any(it["name"].startswith("Zabbix server:") for it in hx.items):
            nvps = hx.val("zabbix[requiredperformance]")
            queue = hx.val("zabbix[queue,10m]")
            add("zabbix", "Zabbix", "ok", "queue %d" % queue if queue is not None else "", {
                "nvps": nvps,
                "queue_10m": queue,
                "items": hx.val("zabbix[items]", fresh=False),
                "hosts": hx.val("zabbix[hosts]", fresh=False),
            }, ("Zabbix server",))
        # GALAXY_SERVICES also names template services (mysql, postgresql, 1c, docker, zabbix):
        # a match makes that satellite "important" too, drawn as a station in its own colour.
        if SERVICE_PATTERNS:
            for sat in sats:
                if sat["type"] in ("svc", "services"):
                    continue
                aliases = {"postgresql": "pgsql postgres", "mysql": "mariadb", "1c": "1с", "zabbix": "zabbix_server"}
                hay = (sat["type"] + " " + sat["label"] + " " + aliases.get(sat["type"], "")).lower()
                if any(p in hay for p in SERVICE_PATTERNS):
                    sat["important"] = True
        sats.sort(key=lambda s: (not s.get("primary"), not s.get("important")))
        return sats, detail

    @staticmethod
    def _is_openwrt(hx):
        return "openwrt" in " ".join(x for x in (hx.text("system.sw.os"), hx.text("system.uname")) if x).lower()

    @staticmethod
    def _agent_ports(hx):
        """Router ports from the Linux agent template (OpenWrt): net.if.in/out["lan1"] plus
        vfs.file.contents["/sys/class/net/<if>/operstate|speed"]. Kept: switch ports (lanN, wan),
        other eth* only when there is no DSA switch (eth0 is then the CPU conduit) and Wi-Fi APs."""
        ifs = {}
        for it in hx.items:
            base, args = split_key(it["key_"])
            if base in ("net.if.in", "net.if.out") and len(args) == 1:
                ifs.setdefault(args[0], {})[base[7:]] = num(it["lastvalue"]) if hx.fresh(it) else None
            elif base == "vfs.file.contents" and args:
                m = re.match(r"^/sys/class/net/([^/]+)/(operstate|speed)$", args[0])
                if m:
                    field = "status" if m.group(2) == "operstate" else "speed"
                    ifs.setdefault(m.group(1), {})[field] = num(it["lastvalue"]) if hx.fresh(it) or field == "speed" else None
        switch = [n for n in ifs if re.match(r"^(lan\d+|wan\d*)$", n)]
        wifi = [n for n in ifs if re.match(r"^(phy\d+-ap\d+|wlan\d+(-\d+)?)$", n)]
        wired = switch or [n for n in ifs if re.match(r"^eth\d+$", n)]
        ports = {}
        for n in sorted(wired) + sorted(wifi):
            d = ifs[n]
            ports[n] = {"name": n, "status": d.get("status"), "speed": d.get("speed") or None,
                        "iftype": 71 if n in wifi else 6, "in": d.get("in"), "out": d.get("out")}
        return ports

    # ---- detail & history -----------------------------------------------

    def host(self, hid):
        ov = self.overview()
        h = next((x for x in ov["hosts"] if x["id"] == hid), None)
        if not h:
            return None
        d = dict(ov["_detail"][hid])
        d.pop("series_items", None)
        return {"host": h, "detail": d, "generated": ov["generated"]}

    def history(self, hid, period, container=None):
        ov = self.overview()
        extra = ov["_detail"].get(hid)
        if not extra:
            return None
        series = dict(extra["series_items"])
        net_iface = series.pop("net_iface", None)
        if container:
            dock = extra["satellites"].get("docker", {})
            c = next((c for c in dock.get("containers", []) if c["name"] == container), None)
            series = {"cpu": (c["item_cpu"], 0)} if c and c.get("item_cpu") else {}
        since, now, bucket, out = self.fetch_series(series, period)
        return {"from": since, "till": now, "bucket": bucket, "series": out, "net_iface": net_iface}

    def fetch_series(self, refs, period, points=240):
        """refs: {name: (itemid, value_type)} -> bucketed [[clock, avg], ...] per name.
        One API call per value type (history) or a single trend.get for long periods."""
        now = int(time.time())
        since = now - period
        bucket = max(60, period // points)
        wanted = {name: ref for name, ref in refs.items() if ref and ref[1] in (0, 3)}
        raw = {}
        if period > 2 * 86400:
            ids = sorted({ref[0] for ref in wanted.values()})
            if ids:
                for r in self.zbx.call("trend.get", {"output": ["itemid", "clock", "value_avg"], "itemids": ids,
                                                     "time_from": since, "time_till": now}):
                    raw.setdefault(r["itemid"], []).append((int(r["clock"]), float(r["value_avg"])))
        else:
            by_type = {}
            for itemid, vtype in wanted.values():
                by_type.setdefault(vtype, set()).add(itemid)
            for vtype, ids in by_type.items():
                for r in self.zbx.call("history.get", {"output": ["itemid", "clock", "value"], "history": vtype,
                                                       "itemids": sorted(ids), "time_from": since, "time_till": now,
                                                       "sortfield": "clock", "sortorder": "ASC"}):
                    raw.setdefault(r["itemid"], []).append((int(r["clock"]), float(r["value"])))
        out = {}
        for name, (itemid, _) in wanted.items():
            agg = {}
            for t, v in raw.get(itemid, []):
                b = t - t % bucket
                s = agg.setdefault(b, [0.0, 0])
                s[0] += v
                s[1] += 1
            out[name] = [[b, round(s / n, 4)] for b, (s, n) in sorted(agg.items())]
        return since, now, bucket, out

    # ---- service windows (PostgreSQL, 1C) ------------------------------

    def service(self, hid, kind, period):
        host = next((h for h in self.overview()["hosts"] if h["id"] == hid), None)
        if not host:
            return None
        prefix = {"postgresql": "pgsql.", "1c": "1c.", "mysql": "mysql."}[kind]
        items = self.zbx.call("item.get", {
            "output": ["itemid", "key_", "name", "lastvalue", "lastclock", "units", "value_type", "state", "error"],
            "hostids": [hid], "search": {"key_": prefix}, "startSearch": True, "monitored": True,
            "selectTags": ["tag", "value"],
        })
        now = int(time.time())
        build = {"postgresql": self._service_pg, "1c": self._service_1c, "mysql": self._service_mysql}[kind]
        data, refs = build(items, now)
        since, till, bucket, series = self.fetch_series(refs, period)
        data.update({"kind": kind, "host": {"id": hid, "name": host["name"]}, "from": since, "till": till,
                     "bucket": bucket, "series": series, "generated": now})
        return data

    @staticmethod
    def _metric(it, now, stale_after=STALE_AFTER):
        """Compact value record for the frontend."""
        clock = int(it.get("lastclock") or 0)
        numeric = int(it["value_type"]) in (0, 3)
        v = (num(it["lastvalue"]) if numeric else it["lastvalue"]) if clock else None
        return {"v": v, "u": it.get("units") or "", "n": it["name"],
                "age": now - clock if clock else None, "stale": not clock or clock < now - stale_after}

    def _service_pg(self, items, now):
        summary, dbs, unsupported, refs = {}, {}, [], {}
        for it in items:
            if it.get("state") == "1":
                unsupported.append({"name": it["name"], "error": it.get("error") or ""})
                continue
            base, args = split_key(it["key_"])
            if int(it["value_type"]) not in (0, 3):
                continue  # raw JSON master items
            field = base[len("pgsql."):]
            field = field[:-5] if field.endswith(".rate") else field
            stale = 3 * 3600 if field in ("ping", "uptime") else 15 * 60
            m = re.match(r"^DB \[(.+?)\]:", it["name"])
            if m:
                db = dbs.setdefault(m.group(1), {"name": m.group(1), "m": {}})
                db["m"][field] = self._metric(it, now, stale)
                if field in ("dbstat.xact_commit", "db.size"):
                    refs["db:%s:%s" % (m.group(1), field)] = (it["itemid"], int(it["value_type"]))
            else:
                summary[field] = self._metric(it, now, stale)
                refs[field] = (it["itemid"], int(it["value_type"]))
        keep = ("connections.sum.active", "connections.sum.idle", "connections.sum.idle_in_transaction",
                "connections.sum.waiting", "connections.sum.total", "dbstat.sum.xact_commit",
                "dbstat.sum.xact_rollback", "cache.hit", "dbstat.sum.tup_returned", "dbstat.sum.tup_fetched",
                "dbstat.sum.tup_inserted", "dbstat.sum.tup_updated", "dbstat.sum.tup_deleted",
                "dbstat.sum.blks_hit", "dbstat.sum.blks_read", "dbstat.sum.deadlocks", "dbstat.sum.temp_bytes",
                "bgwriter.buffers_checkpoint", "bgwriter.buffers_clean", "bgwriter.buffers_alloc",
                "bgwriter.checkpoints_timed", "bgwriter.checkpoints_req", "wal.write", "replication.lag.sec")
        refs = {k: v for k, v in refs.items() if k in keep or k.startswith("db:")}
        databases = sorted(dbs.values(), key=lambda d: -((d["m"].get("db.size") or {}).get("v") or 0))
        return {"summary": summary, "databases": databases, "unsupported": unsupported}, refs

    def _service_mysql(self, items, now):
        """MySQL by Zabbix agent 2: status variables as a flat map, databases by size."""
        summary, dbs, unsupported, refs = {}, [], [], {}
        for it in items:
            if it.get("state") == "1":
                unsupported.append({"name": it["name"], "error": it.get("error") or ""})
                continue
            base, args = split_key(it["key_"])
            if base == "mysql.db.size" and len(args) >= 4:
                rec = self._metric(it, now, 3 * 3600)
                rec["name"] = args[3]
                dbs.append(rec)
                refs["db:%s" % args[3]] = (it["itemid"], int(it["value_type"]))
                continue
            if base == "mysql.version":
                summary["version"] = {"v": it["lastvalue"] or None, "u": "", "n": it["name"]}
                continue
            if int(it["value_type"]) not in (0, 3):
                continue
            field = base[len("mysql."):]
            field = field[:-5] if field.endswith(".rate") else field
            summary[field] = self._metric(it, now, 3 * 3600 if field in ("ping", "uptime") else 45 * 60)
            if field == "innodb_log_file_size":
                summary[field]["u"] = "B"  # the template leaves this one without units
            refs[field] = (it["itemid"], int(it["value_type"]))
        keep = ("queries", "questions", "slow_queries", "com_select", "com_insert", "com_update", "com_delete",
                "threads_connected", "threads_running", "connections", "aborted_connects", "aborted_clients",
                "bytes_received", "bytes_sent", "innodb_buffer_pool_read_requests", "innodb_buffer_pool_reads",
                "buffer_pool_utilization", "created_tmp_tables", "created_tmp_disk_tables", "innodb_row_lock_waits")
        refs = {k: v for k, v in refs.items() if k in keep or k.startswith("db:")}
        dbs.sort(key=lambda d: -(d["v"] or 0))
        return {"summary": summary, "databases": dbs, "unsupported": unsupported}, refs

    def _service_1c(self, items, now):
        bases, legacy, refs = {}, [], {}
        for it in items:
            base, args = split_key(it["key_"])
            tags = {t["tag"]: t["value"] for t in it.get("tags", [])}
            if base.startswith("1c.sessions.") and args:
                name = tags.get("infobase") or infobase_name(it["name"], args[0])
                ib = bases.setdefault(args[0], {"name": name, "uuid": args[0], "m": {}})
                field = base[len("1c.sessions."):]
                if it.get("state") == "1":
                    ib.setdefault("errors", []).append(it.get("error") or "")
                    continue
                ib["m"][field] = self._metric(it, now, 15 * 60)
                if field in ("total", "active", "users", "calls_5min", "cpu_5min", "duration_5min",
                             "duration_dbms_5min", "memory_5min", "bytes_5min", "dbms_bytes_5min",
                             "blocked_dbms", "blocked_ls"):
                    refs["%s:%s" % (args[0], field)] = (it["itemid"], int(it["value_type"]))
            elif base.startswith("1c.sessions"):
                continue  # raw rac output master items
            else:
                rec = self._metric(it, now, 15 * 60) if int(it["value_type"]) in (0, 3) else {"v": None, "u": "", "n": it["name"]}
                rec.update({"key": it["key_"], "supported": it.get("state") != "1", "error": it.get("error") or ""})
                legacy.append(rec)
        infobases = sorted(bases.values(), key=lambda b: b["name"].lower())
        return {"infobases": infobases, "legacy": sorted(legacy, key=lambda r: (r["supported"], r["n"]))}, refs


# --------------------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    model = None
    server_version = "ZabbixGalaxy/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("GALAXY_DEBUG"):
            super().log_message(fmt, *args)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/api/config":
                return self._json({"lang": LANG})
            if u.path == "/api/overview":
                ov = self.model.overview()
                return self._json({k: v for k, v in ov.items() if not k.startswith("_")})
            m = re.fullmatch(r"/api/host/(\d+)", u.path)
            if m:
                d = self.model.host(m.group(1))
                return self._json(d) if d else self._json({"error": "host not found"}, 404)
            m = re.fullmatch(r"/api/history/(\d+)", u.path)
            if m:
                period = int(q.get("period", ["3600"])[0])
                period = max(600, min(period, 30 * 86400))
                d = self.model.history(m.group(1), period, q.get("container", [None])[0])
                return self._json(d) if d else self._json({"error": "host not found"}, 404)
            m = re.fullmatch(r"/api/service/(\d+)/(postgresql|1c|mysql)", u.path)
            if m:
                period = int(q.get("period", ["3600"])[0])
                period = max(600, min(period, 30 * 86400))
                d = self.model.service(m.group(1), m.group(2), period)
                return self._json(d) if d else self._json({"error": "host not found"}, 404)
            if u.path.startswith("/api/"):
                return self._json({"error": "not found"}, 404)
            return self._static(u.path)
        except (ZabbixError, urllib.error.URLError, TimeoutError) as e:
            return self._json({"error": "zabbix: %s" % e}, 502)

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.realpath(os.path.join(WEB, path.lstrip("/")))
        if not full.startswith(WEB + os.sep) or not os.path.isfile(full):
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def resolve_env(name):
    """Settings file by short name or path: "env_uk", ".env_uk" and "path/to/file"
    all work; relative names are looked up in the current directory, then next
    to server.py. Exits with a clear message when nothing matches."""
    names = [name] if name.startswith(".") or os.sep in name else [name, "." + name]
    tried = []
    for n in names:
        for base in ("", ROOT):
            path = os.path.abspath(os.path.join(base, n))
            if path in tried:
                continue
            tried.append(path)
            if os.path.isfile(path):
                return path
    sys.exit("settings file %r not found (tried: %s)" % (name, ", ".join(tried)))


def main():
    ap = argparse.ArgumentParser(
        description="Zabbix Galaxy dashboard",
        epilog="examples: python3 server.py            (uses .env)\n"
               "          python3 server.py env_uk     (uses .env_uk)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("envfile", nargs="?", help="settings file to use instead of .env, e.g. env_uk or .env_uk")
    ap.add_argument("--env", dest="env_opt", help="same as the positional settings file")
    ap.add_argument("--host", help="listen address (default: GALAXY_HOST from the settings file, else 127.0.0.1)")
    ap.add_argument("--port", type=int, help="listen port (default: GALAXY_PORT from the settings file, else 8080)")
    args = ap.parse_args()

    chosen = args.envfile or args.env_opt
    # Default .env may be absent (settings can come from the environment); an explicit file must exist.
    env_path = resolve_env(chosen) if chosen else os.path.join(ROOT, ".env")
    print("Settings: %s" % env_path)
    cfg = load_env(env_path)
    global LANG, SERVICE_PATTERNS
    SERVICE_PATTERNS = [p.strip().lower() for p in re.split(r"[,;]", cfg.get("GALAXY_SERVICES") or "") if p.strip()]
    LANG = (cfg.get("GALAXY_LANG") or "ru").strip().lower()
    if LANG not in ("ru", "en"):
        sys.exit("GALAXY_LANG must be ru or en, got %r" % cfg.get("GALAXY_LANG"))
    # Command-line flags win over .env, .env wins over the built-in defaults.
    args.host = args.host or cfg.get("GALAXY_HOST") or "127.0.0.1"
    try:
        args.port = args.port or int(cfg.get("GALAXY_PORT") or 8080)
    except ValueError:
        sys.exit("GALAXY_PORT must be a number, got %r" % cfg.get("GALAXY_PORT"))
    zbx = Zabbix(cfg)
    zbx.login()
    atexit.register(zbx.logout)
    Handler.model = Model(zbx)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    via = zbx.proxy_display()
    print("Zabbix Galaxy: http://%s:%d  (Zabbix %s%s)" % (args.host, args.port, zbx.url, ", via proxy %s" % via if via else ""))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
