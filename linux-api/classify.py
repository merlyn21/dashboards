"""Labels for connections: device types, well-known services, map categories, WAN gates."""
import re

SERVICE_PREFIX = [
    ("Яндекс", ["213.180.", "93.158.", "87.250.", "5.255.", "77.88.", "5.45.", "95.108.", "178.154."]),
    ("Google", ["8.8.8.8", "8.8.4.4", "142.250.", "142.251.", "172.217.", "216.58.", "74.125.", "209.85.", "108.177.", "173.194.", "64.233.", "34.", "35."]),
    ("Cloudflare", ["188.114.", "104.16.", "104.17.", "104.18.", "104.19.", "104.20.", "104.21.", "104.22.", "104.26.", "104.27.",
                    "172.64.", "172.65.", "172.66.", "172.67.", "162.158.", "162.159.", "1.1.1.1", "1.0.0.1"]),
    ("Amazon AWS", ["3.", "13.", "18.", "52.", "54.", "99.83.", "99.86.", "15.", "44."]),
    ("Microsoft", ["4.", "20.", "40.", "51."]),
    ("Apple", ["17."]),
    ("Telegram", ["149.154.", "91.108."]),
    ("VK", ["87.240.", "95.142.", "217.20.", "94.100.", "93.186."]),
]


def service_of(ip):
    for name, prefixes in SERVICE_PREFIX:
        for p in prefixes:
            if ip == p or (p.endswith(".") and ip.startswith(p)):
                return name
    return None


# Device type from the DHCP host name: generic words and brands only, no names of particular devices.
NAME_RULES = [
    ("camera", r"\bcam|camera|ipc|tapo|ezviz|hikvision|dahua|reolink|^c\d{3}\b"),
    ("vacuum", r"vacuum|robot|roborock|dreame|irobot|roomba|ecovacs"),
    ("speaker", r"speaker|echo|alexa|station|yandex|alice|homepod|sonos|mini\d?"),
    ("tv", r"\btv\b|-tv|bravia|smarttv|chromecast|firetv|androidtv|webos"),
    ("iot", r"\bhub\b|plug|bulb|lamp|socket|rozetka|switch|sensor|tuya|aqara|shelly|sonoff|esp[-_]?\d|smart"),
    ("phone", r"iphone|ipad|android|phone|galaxy|pixel|redmi|poco|honor|huawei|oneplus|oppo|vivo|realme"),
    ("pc", r"desktop|laptop|notebook|macbook|imac|\bpc\b|workstation|thinkpad"),
    ("server", r"\bnas\b|server|\bsrv|raspberry|\brpi|proxmox|docker|synology|qnap"),
    ("ap", r"router|\bap\b|archer|keenetic|mikrotik|unifi|ubnt|openwrt|\bax\d{3,4}|deco|mesh"),
]
# Device type and a readable label from the maker of the network card (IEEE OUI), for devices
# that do not send a host name. First match wins, so specific makers come before broad ones.
MAKER_RULES = [
    ("iot", "умное устройство", r"tuya|espressif|shelly|allterco|sonoff|itead|lumi united|aqara|broadlink|wiz connected"),
    ("camera", "камера", r"hikvision|dahua|ezviz|reolink|axis comm|uniview|imou|hanwha"),
    ("vacuum", "пылесос", r"roborock|ecovacs|irobot|dreame"),
    ("speaker", "колонка", r"sonos|bose"),
    ("tv", "телевизор", r"lg electronics|tcl|hisense|vizio|roku"),
    ("console", "приставка", r"nintendo|sony interactive|microsoft.*xbox"),
    ("server", "Raspberry Pi", r"raspberry"),
    ("server", "NAS", r"synology|qnap|western digital"),
    ("ap", "роутер или точка доступа", r"ubiquiti|mikrotik|routerboard|zyxel|keenetic|netgear|tp-link|tp link|cudy|mercusys"),
    ("phone", "телефон или планшет", r"apple|samsung|huawei|honor|oneplus|oppo|vivo|motorola|xiaomi communications|realme|zte"),
    ("pc", "компьютер", r"intel|realtek|azurewave|liteon|lite-on|hon hai|foxconn|qualcomm|atheros|dell|lenovo|hewlett|\bhp\b|asustek|micro-star|gigabyte|elitegroup|pegatron|wistron|quanta|chicony"),
]
TYPE_LABEL = {"iot": "умное устройство", "camera": "камера", "vacuum": "пылесос", "speaker": "колонка", "tv": "телевизор",
              "console": "приставка", "server": "сервер", "ap": "роутер или точка доступа", "phone": "телефон или планшет", "pc": "компьютер"}


def _maker_rule(maker):
    m = (maker or "").lower()
    for t, label, rx in MAKER_RULES:
        if re.search(rx, m):
            return t, label
    return None


def device_type(name, maker=None):
    """Type by the DHCP name first; if the name says nothing, by the maker of the network card."""
    n = (name or "").lower()
    for t, rx in NAME_RULES:
        if re.search(rx, n):
            return t
    r = _maker_rule(maker)
    return r[0] if r else "unknown"


def device_label(maker, mac, short_maker):
    """Name for a device without a DHCP host name: '<type> <maker> …<end of MAC>'."""
    tail = f"…{mac[-5:]}" if mac else ""
    if maker == "случайный MAC":
        return f"скрытый MAC {tail}".strip()  # phones and laptops hide their real address
    r = _maker_rule(maker)
    if r:
        return f"{r[1]} {short_maker} {tail}".strip()
    if maker:
        return f"{short_maker} {tail}".strip()
    return f"устройство {tail}".strip()


IOT = {"camera", "vacuum", "iot", "speaker"}

_ORG_PREFIX = re.compile(r'^\s*(JSC|PJSC|OJSC|CJSC|OOO|LLC|ООО|АО|ПАО|ЗАО)\s+', re.I)
_ORG_SUFFIX = re.compile(r"[,.]?\s*\b(LLC|L\.L\.C\.|Inc\.?|Ltd\.?|Limited|GmbH|AG|S\.?A\.?|B\.?V\.?|JSC|PJSC|OJSC|CJSC|OOO|Co\.?|Corp(oration)?\.?|Company|plc|SAS|SRL|AB)\s*$", re.I)


def org_short(org):
    """'YANDEX LLC' -> 'Yandex', 'Amazon.com, Inc.' -> 'Amazon.com'."""
    if not org:
        return org
    s = _ORG_PREFIX.sub("", org)  # 'JSC "TIMEWEB"' -> 'TIMEWEB'
    for _ in range(3):
        s2 = _ORG_SUFFIX.sub("", s).strip(" ,.")
        if s2 == s:
            break
        s = s2
    s = s.strip(' "\'«»-') or org  # 'Sesameware FZ-LLC' leaves a trailing dash
    return s.title() if s.isupper() and len(s) > 4 else s


# Organizations that scan the whole internet for research or security products.
RESEARCH = re.compile(r"censys|shodan|palo alto|xpanse|shadowserver|rapid7|binaryedge|stretchoid|onyphe|driftnet|leakix|"
                      r"criminal ?ip|internet measurement|recyber|modat|bitsight|alphastrike|netscout|intrinsec|hadrian|"
                      r"security ?research|academic|university", re.I)

VPN_PORTS = {500, 4500, 1194, 1701, 1723, 51820}


# TCP states of a connection that got past the handshake; SYN_RECV alone is what a SYN scan leaves.
TCP_SESSION = {"ESTABLISHED", "FIN_WAIT", "CLOSE_WAIT", "LAST_ACK", "TIME_WAIT", "CLOSE"}


def replied(proto, state, unreplied):
    """Did the other side answer? For TCP only a finished handshake counts."""
    if unreplied:
        return False
    return proto != "tcp" or not state or state in TCP_SESSION


def category(kind, port, proto, dev_type, service, forwarded=False, vpn_client=False, answered=True, router_mode=False):
    if kind == "in":
        # Inbound: a session once there is an answer, a scan before that; the collector refines scans
        # every poll (packets, ports tried by the source, research scanners).
        if vpn_client:
            return "vpn"
        return "session" if answered else "scan"
    if kind == "router" and router_mode:
        return "router"  # the router's own traffic: DNS and NTP upstream, tunnels it dials
    if port in VPN_PORTS or proto == "gre":
        return "vpn"
    if dev_type in IOT:
        return "iot"
    if service:
        return "service"
    if proto == "udp" and port > 1024:
        return "p2p"
    return "other"


# What a connection to this machine is aiming at.
GATES = [
    ("ssh", "SSH и Telnet", lambda p, pr: p in (22, 23, 2222)),
    ("web", "Веб", lambda p, pr: p in (80, 443, 8080, 8081, 8000, 8443, 8888, 3000, 5000, 9000)),
    ("mail", "Почта", lambda p, pr: p in (25, 110, 143, 465, 587, 993, 995)),
    ("db", "Базы данных", lambda p, pr: p in (1433, 1521, 3306, 5432, 5984, 6379, 9042, 9200, 11211, 27017)),
    ("remote", "RDP и VNC", lambda p, pr: p == 3389 or 5900 <= p <= 5910),
    ("sip", "SIP", lambda p, pr: 5060 <= p <= 5099),
    ("dns", "DNS", lambda p, pr: p == 53),
    ("snmp", "SNMP", lambda p, pr: p == 161),
    ("amp", "Усилители", lambda p, pr: p in (123, 1900, 389, 1434, 19, 3702, 5353, 111, 137, 520, 623, 69, 427, 47808, 5683)),
    ("vpn", "VPN", lambda p, pr: p in VPN_PORTS or pr in ("gre", "ipencap", "sctp")),
    ("icmp", "ICMP", lambda p, pr: pr == "icmp"),
    ("other", "Прочие порты", lambda p, pr: True),
]


def gate_of(port, proto):
    for gid, _, test in GATES:
        if test(port, proto):
            return gid
    return "other"
