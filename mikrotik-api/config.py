"""Settings from .env next to the service. Values may be quoted; unknown keys are ignored."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    "server_api": "",          # router address as seen from the proxy
    "port_api": "8728",
    "username_api": "",
    "password_api": "",
    "proxy": "",               # host:port of an HTTP CONNECT proxy; empty for a direct connection
    "poll_interval": "30",     # seconds between polls of metrics and connections
    "poll_mode": "always",     # always: poll all the time; clients: only while the map is open in a browser
    "idle_timeout": "300",     # poll_mode=clients: seconds without a browser before polling stops
    "config_interval": "300",  # seconds between re-reading services and firewall rules
    "wan_interface": "",       # empty: taken from the DHCP client or the WAN interface list
    "http_host": "127.0.0.1",  # where the map is served; 0.0.0.0 to open it to the LAN
    "http_port": "8787",
}


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
    cfg["poll_interval"] = max(5, int(cfg["poll_interval"]))
    cfg["config_interval"] = max(cfg["poll_interval"], int(cfg["config_interval"]))
    cfg["port_api"] = int(cfg["port_api"])
    cfg["poll_mode"] = cfg["poll_mode"].strip().lower() or "always"
    if cfg["poll_mode"] not in ("always", "clients"):
        raise SystemExit(f"poll_mode must be 'always' or 'clients', not '{cfg['poll_mode']}'")
    cfg["idle_timeout"] = max(0, int(cfg["idle_timeout"]))
    cfg["http_port"] = int(cfg["http_port"])
    if not cfg["server_api"] or not cfg["username_api"]:
        raise SystemExit(f"Set server_api, username_api and password_api in {path}")
    return cfg
