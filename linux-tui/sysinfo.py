"""Facts about this machine from /proc, /sys and `ip -j`: OS, CPU, memory, disk, interfaces, routes, DNS."""
import glob
import ipaddress
import json
import logging
import os
import re
import subprocess
import urllib.request

log = logging.getLogger("sysinfo")

# In a container the host's file system is mounted read-only at HOST_ROOT (see docker-compose.yml).
# /proc and /sys need no prefix: with --pid host and --network host they already show the host.
HOST_ROOT = os.environ.get("HOST_ROOT", "").rstrip("/")


def host_path(path):
    """A host file path as seen from here: /etc/os-release -> /host/etc/os-release in a container."""
    return HOST_ROOT + path if HOST_ROOT and path.startswith("/") else path


VIRTUAL = re.compile(r"^(lo|docker\d*|br-[0-9a-f]+|veth.*|virbr\d*|cni\d*|flannel.*|cali.*|vxlan.*|kube.*)$")
TUNNEL = re.compile(r"^(tun|tap|wg|ovpn)\S*$")


def run(cmd, timeout=10):
    """Output of a command, or '' if it is missing or fails."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        log.debug("%s: %s", cmd[0], e)
        return ""


def read(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


def read_int(path, default=None):
    try:
        return int(read(path))
    except ValueError:
        return default


def os_release():
    info = {}
    for line in read(host_path("/etc/os-release")).splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k] = v.strip().strip('"')
    return info.get("PRETTY_NAME") or info.get("NAME") or "Linux"


def machine():
    """Hardware or cloud platform, e.g. 'Standard PC (Q35 + ICH9, 2009)' or 'Google Compute Engine'."""
    vendor = read("/sys/class/dmi/id/sys_vendor")
    product = read("/sys/class/dmi/id/product_name")
    if product and vendor and vendor.split()[0].lower() not in product.lower():
        product = f"{vendor} {product}"
    cpu = ""
    for line in read("/proc/cpuinfo").splitlines():
        if line.startswith(("model name", "Hardware", "Model")):
            cpu = re.sub(r"\s+", " ", line.split(":", 1)[1]).strip()
            break
    return product or vendor, cpu


class Cpu:
    """CPU load as the busy share between two reads of /proc/stat."""

    def __init__(self):
        self.prev = None

    def load(self):
        parts = read("/proc/stat").splitlines()[0].split()[1:]
        values = [int(x) for x in parts[:8]]
        idle, total = values[3] + values[4], sum(values)
        pct = None
        if self.prev and total > self.prev[1]:
            pct = round(100 * (1 - (idle - self.prev[0]) / (total - self.prev[1])))
        self.prev = (idle, total)
        return pct


def memory():
    m = {}
    for line in read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        m[k] = int(v.split()[0]) * 1024 if v.split() else 0
    total, avail = m.get("MemTotal", 0), m.get("MemAvailable", m.get("MemFree", 0))
    swap_total, swap_free = m.get("SwapTotal", 0), m.get("SwapFree", 0)
    return {
        "mem_total": total, "mem_used": 1 - avail / total if total else None,
        "swap_total": swap_total, "swap_used": 1 - swap_free / swap_total if swap_total else None,
    }


def disk(path="/"):
    try:
        st = os.statvfs(host_path(path))
    except OSError:
        return {}
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    return {"disk_total": total, "disk_used": 1 - free / total if total else None}


def temperature():
    """Hottest thermal zone in °C, or None (usual for cloud machines)."""
    temps = [read_int(p) for p in glob.glob("/sys/class/thermal/thermal_zone*/temp")]
    temps = [t / 1000 for t in temps if t and 0 < t < 150000]
    return round(max(temps)) if temps else None


def uptime():
    try:
        return int(float(read("/proc/uptime").split()[0]))
    except (IndexError, ValueError):
        return None


def loadavg():
    try:
        return [float(x) for x in read("/proc/loadavg").split()[:3]]
    except ValueError:
        return None


def iface_counters(name):
    base = f"/sys/class/net/{name}"
    speed = read_int(f"{base}/speed")  # Mbit/s; -1 or missing on virtual interfaces
    return {
        "rx": read_int(f"{base}/statistics/rx_bytes", 0), "tx": read_int(f"{base}/statistics/tx_bytes", 0),
        "up": read(f"{base}/operstate") in ("up", "unknown"), "speed": speed if speed and speed > 0 else None,
        "carrier_changes": read_int(f"{base}/carrier_changes"),
    }


def addresses():
    """{interface: [ipaddress.ip_interface, ...]} from `ip -j addr`, with the link type per interface."""
    try:
        data = json.loads(run(["ip", "-j", "addr", "show"]) or "[]")
    except ValueError:
        data = []
    result = {}
    for link in data:
        name = link.get("ifname")
        addrs = []
        for a in link.get("addr_info", []):
            try:
                addrs.append(ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}"))
            except (KeyError, ValueError):
                pass
        result[name] = {"addrs": addrs, "up": "UP" in link.get("flags", []), "type": link.get("link_type", "")}
    return result


def default_route(family="-4"):
    """(interface, gateway) of the default route or (None, None)."""
    try:
        routes = json.loads(run(["ip", "-j", family, "route", "show", "default"]) or "[]")
    except ValueError:
        routes = []
    routes.sort(key=lambda r: r.get("metric", 0))
    for r in routes:
        if r.get("dev"):
            return r["dev"], r.get("gateway")
    return None, None


def dns_servers():
    """Upstream resolvers; behind systemd-resolved the stub 127.0.0.53 is replaced with the real ones."""
    def parse(text):
        return [l.split()[1] for l in text.splitlines() if l.startswith("nameserver") and len(l.split()) > 1]
    servers = parse(read(host_path("/etc/resolv.conf")))
    if all(s.startswith("127.0.0.5") for s in servers):
        real = parse(read(host_path("/run/systemd/resolve/resolv.conf")))
        servers = real or servers
    return servers[:3]


def docker_containers():
    """{container address: container name} from the docker CLI; empty without Docker or without the right to ask it."""
    ids = run(["docker", "ps", "-q"], timeout=5).split()
    if not ids:
        return {}
    fmt = "{{.Name}}|{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"
    names = {}
    for line in run(["docker", "inspect", "--format", fmt] + ids, timeout=10).splitlines():
        name, _, ips = line.partition("|")
        for ip in ips.split():
            names[ip] = name.lstrip("/")
    return names


def conntrack_usage():
    return read_int("/proc/sys/net/netfilter/nf_conntrack_count"), read_int("/proc/sys/net/netfilter/nf_conntrack_max")


def ip_forward():
    return read("/proc/sys/net/ipv4/ip_forward") == "1"


IP_SERVICES = ["https://ifconfig.me/ip", "https://api.ipify.org"]


def external_ip():
    """Ask a public service what our IPv4 address is, like `curl ifconfig.me`. None if nobody answers."""
    for url in IP_SERVICES:
        try:
            # ifconfig.me answers a browser with a page and curl with the bare address
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8.5.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                ip = r.read(64).decode().strip()
            if ipaddress.ip_address(ip).version == 4:
                return ip
            log.info("%s answered with IPv6 %s, trying the next service", url, ip)
        except (OSError, ValueError) as e:
            log.warning("external address from %s: %s", url, e)
    return None
