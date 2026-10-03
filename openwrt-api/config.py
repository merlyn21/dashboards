"""Settings from .env next to the service. Values may be quoted; unknown keys are ignored."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    "router_url": "",           # http://192.168.1.1 - the router as the service (or the proxy) sees it
    "router_user": "",          # rpcd user with the read-only ACL (README.md)
    "router_password": "",
    "proxy": "",                # host:port of an HTTP proxy; empty for a direct connection
    "wan_interface": "wan",     # logical OpenWrt interface that faces the internet
    "poll_interval": "15",      # seconds between polls of metrics and connections
    "config_interval": "300",   # seconds between re-reading interfaces, firewall, listeners and the names file
    "http_host": "127.0.0.1",   # where the map is served; 0.0.0.0 to open it to the LAN
    "http_port": "8787",
    "public_ip": "",            # external address; empty: the WAN address or, if it is private, ask ifconfig.me; off: never ask
    "location": "",             # "lat,lng" of the router when the external address has no place in DB-IP
    "names_file": "names.conf", # address -> name for devices and remote hosts; DHCP names come from the router
}


# Short names accepted in the file. They are not read from the environment: USERNAME there is the login
# name of whoever starts the service.
ALIASES = {"username": "router_user", "password": "router_password"}


def load(path=None):
    path = path or os.path.join(HERE, ".env")
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:  # Notepad on Windows may put a BOM at the start
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                k = k.strip().lower()
                cfg[ALIASES.get(k, k)] = v
    for k in DEFAULTS:  # environment variables override the file, e.g. POLL_INTERVAL=10
        if k.upper() in os.environ:
            cfg[k] = os.environ[k.upper()]
    cfg["poll_interval"] = max(5, int(cfg["poll_interval"]))
    cfg["config_interval"] = max(cfg["poll_interval"], int(cfg["config_interval"]))
    cfg["http_port"] = int(cfg["http_port"])
    cfg["router_url"] = cfg["router_url"].rstrip("/")
    if cfg["router_url"] and "://" not in cfg["router_url"]:
        cfg["router_url"] = "http://" + cfg["router_url"]
    loc = [x.strip() for x in cfg["location"].split(",") if x.strip()]
    cfg["location"] = (float(loc[0]), float(loc[1])) if len(loc) == 2 else None
    if cfg["names_file"] and not os.path.isabs(cfg["names_file"]):
        cfg["names_file"] = os.path.join(HERE, cfg["names_file"])
    if not cfg["router_url"] or not cfg["router_user"]:
        raise SystemExit(f"Set router_url, router_user and router_password in {path}")
    return cfg
