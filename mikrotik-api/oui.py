"""Device maker by MAC address, from the IEEE OUI registry (oui/oui.txt, a copy of hwdata's file)."""
import logging
import os
import re

log = logging.getLogger("oui")
LINE = re.compile(r"^([0-9A-F]{6})\s+\(base 16\)\s+(.+?)\s*$")


class Vendors:
    def __init__(self, path):
        self.path = path
        self.table = {}

    def load(self):
        if not os.path.exists(self.path):
            log.warning("no %s: device makers unknown", self.path)
            return self
        with open(self.path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = LINE.match(line)
                if m:
                    self.table[m.group(1)] = m.group(2)
        log.info("oui: %d makers", len(self.table))
        return self

    def lookup(self, mac):
        """Maker for a MAC like 'AC:BA:C0:1E:8C:E8'. Randomized (locally administered) MACs have no maker."""
        hexes = re.sub(r"[^0-9A-Fa-f]", "", mac or "").upper()
        if len(hexes) < 6:
            return None
        if int(hexes[1], 16) & 0x2:
            return "случайный MAC"  # phones and laptops hide their real address this way
        return self.table.get(hexes[:6])


def short(maker):
    """'Tuya Smart Inc.' -> 'Tuya Smart', for labels."""
    if not maker:
        return maker
    s = re.sub(r"[,.]?\s*(Co\.?,?\s*)?(Ltd\.?|Inc\.?|LLC|GmbH|AG|Corp(oration)?\.?|Limited|S\.?A\.?|B\.?V\.?|Technologies|Technology)\s*$", "", maker, flags=re.I)
    s = re.sub(r"[,.]?\s*(Co\.?,?\s*)?(Ltd\.?|Inc\.?|LLC|Limited)\s*$", "", s, flags=re.I)
    return s.strip(" ,.") or maker
