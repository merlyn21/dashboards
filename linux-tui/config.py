"""Settings from .env next to the service. Values may be quoted; unknown keys are ignored."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    "mode": "router",           # router: WAN + LAN behind NAT; host: a single server, usually in a cloud
    "wan_interface": "",        # empty: the interface of the default route
    "lan_interfaces": "",       # router mode, comma separated; empty: every other interface except lo, docker, veth
    "poll_interval": "10",      # seconds between polls of metrics and connections
    "config_interval": "300",   # seconds between re-reading addresses, firewall, listeners and the names file
    "public_ip": "",            # external address; empty: the WAN address or, if it is private, ask ifconfig.me; off: never ask
    "location": "",             # "lat,lng" of this machine when the external address has no place in DB-IP
    "names_file": "names.conf", # address -> name for devices and remote hosts
    "conntrack_acct": "off",    # on: set net.netfilter.nf_conntrack_acct=1 at start, needed for per-connection rates
    "conntrack_events": "on",   # off: only poll the table, short connections between polls are missed
    "openvpn_status": "",       # comma separated status files; empty: the usual places are searched
    "ipv6": "auto",             # auto: IPv6 places and firewall only if the WAN has a global IPv6 address; on, off
}


def _split(value):
    return [x.strip() for x in value.split(",") if x.strip()]


def load(path=None):
    path = path or os.path.join(HERE, ".env")
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                cfg[k.strip().lower()] = v
    for k in DEFAULTS:  # environment variables override the file, e.g. POLL_INTERVAL=10
        if k.upper() in os.environ:
            cfg[k] = os.environ[k.upper()]
    cfg["mode"] = cfg["mode"].strip().lower()
    if cfg["mode"] not in ("router", "host"):
        raise SystemExit(f"mode must be router or host, got {cfg['mode']!r} in {path}")
    cfg["poll_interval"] = max(5, int(cfg["poll_interval"]))
    cfg["config_interval"] = max(cfg["poll_interval"], int(cfg["config_interval"]))
    cfg["lan_interfaces"] = _split(cfg["lan_interfaces"])
    cfg["openvpn_status"] = _split(cfg["openvpn_status"])
    for k in ("conntrack_acct", "conntrack_events"):
        cfg[k] = cfg[k].strip().lower() in ("on", "yes", "true", "1")
    cfg["ipv6"] = cfg["ipv6"].strip().lower()
    loc = _split(cfg["location"])
    cfg["location"] = (float(loc[0]), float(loc[1])) if len(loc) == 2 else None
    if cfg["names_file"] and not os.path.isabs(cfg["names_file"]):
        cfg["names_file"] = os.path.join(HERE, cfg["names_file"])
    return cfg
