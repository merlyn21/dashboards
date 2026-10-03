"""IP lookups in DB-IP Lite databases: city (place) and ASN (organization).

Each CSV (gzip) is parsed once into a compact cache next to it: range starts and ends as sorted
uint32 arrays plus an index into a table of unique values. Lookups are a binary search.
IPv6 is optional (it roughly doubles memory): ranges are kept by the upper 64 bits of the address,
which is finer than any place DB-IP knows, and neighbouring ranges with the same value are merged.
Standard library only.

IP Geolocation by DB-IP (https://db-ip.com), licensed under CC BY 4.0.
"""
import array
import bisect
import csv
import glob
import gzip
import ipaddress
import json
import logging
import os
import re
import time

log = logging.getLogger("geo")


class RangeTable:
    pattern = ""   # file name glob inside the folder
    what = ""      # for log messages

    def __init__(self, folder, ipv6=False):
        self.folder = folder
        self.ipv6 = ipv6
        self.starts = array.array("I")
        self.ends = array.array("I")
        self.idx = array.array("I")
        self.starts6 = array.array("Q")
        self.ends6 = array.array("Q")
        self.idx6 = array.array("I")
        self.values = []
        self.source = None

    def row_value(self, row):
        """Turn a CSV row (without the two address columns) into a JSON-able value."""
        raise NotImplementedError

    def load(self):
        dbs = sorted(glob.glob(os.path.join(self.folder, self.pattern)))
        if not dbs:
            log.warning("no %s in %s: %s unknown", self.pattern, self.folder, self.what)
            return self
        db = dbs[-1]
        self.source = os.path.basename(db)
        stem = os.path.join(self.folder, self.source.replace(".csv.gz", ""))
        if not self._load_cache(stem, db):
            self._build(db)
            self._save_cache(stem)
        log.info("%s: %s, %d IPv4 and %d IPv6 ranges, %d values", self.what, self.source, len(self.starts), len(self.starts6), len(self.values))
        return self

    def _arrays(self):
        arrays = [self.starts, self.ends, self.idx]
        return arrays + [self.starts6, self.ends6, self.idx6] if self.ipv6 else arrays

    def _paths(self, stem):
        # The IPv6 cache has its own values file: a v4-only cache and a v4+v6 cache do not share indexes.
        if self.ipv6:
            return [stem + ext for ext in (".v6.starts", ".v6.ends", ".v6.idx", ".v6.starts6", ".v6.ends6", ".v6.idx6", ".v6.values.json")]
        return [stem + ext for ext in (".starts", ".ends", ".idx", ".values.json")]

    def _load_cache(self, stem, db):
        paths = self._paths(stem)
        if not all(os.path.exists(p) and os.path.getmtime(p) >= os.path.getmtime(db) for p in paths):
            return False
        for arr, p in zip(self._arrays(), paths):
            with open(p, "rb") as f:
                arr.frombytes(f.read())
        with open(paths[-1], encoding="utf-8") as f:
            self.values = json.load(f)
        return True

    def _save_cache(self, stem):
        paths = self._paths(stem)
        for arr, p in zip(self._arrays(), paths):
            with open(p, "wb") as f:
                arr.tofile(f)
        with open(paths[-1], "w", encoding="utf-8") as f:
            json.dump(self.values, f, ensure_ascii=False, separators=(",", ":"))

    def _build(self, db):
        t0 = time.time()
        log.info("%s: building cache from %s, this happens once per database file", self.what, os.path.basename(db))
        seen = {}
        with gzip.open(db, "rt", encoding="utf-8", newline="") as f:
            for row in csv.reader(f):
                v6 = ":" in row[0]
                if v6 and not self.ipv6:
                    break  # the IPv6 part follows the IPv4 part
                value = self.row_value(row[2:])
                key = json.dumps(value, ensure_ascii=False)
                i = seen.get(key)
                if i is None:
                    i = seen[key] = len(self.values)
                    self.values.append(value)
                if not v6:
                    self.starts.append(int(ipaddress.IPv4Address(row[0])))
                    self.ends.append(int(ipaddress.IPv4Address(row[1])))
                    self.idx.append(i)
                    continue
                start, end = int(ipaddress.IPv6Address(row[0])) >> 64, int(ipaddress.IPv6Address(row[1])) >> 64
                if self.idx6 and self.idx6[-1] == i and start <= self.ends6[-1] + 1:
                    self.ends6[-1] = max(end, self.ends6[-1])
                    continue
                self.starts6.append(start)
                self.ends6.append(end)
                self.idx6.append(i)
        log.info("%s: cache built in %.0f s", self.what, time.time() - t0)

    def find(self, ip):
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return None
        if a.version == 4:
            starts, ends, idx, n = self.starts, self.ends, self.idx, int(a)
        else:
            starts, ends, idx, n = self.starts6, self.ends6, self.idx6, int(a) >> 64
        i = bisect.bisect_right(starts, n) - 1
        if i < 0 or n > ends[i]:
            return None
        return self.values[idx[i]]


class Geo(RangeTable):
    """City Lite: continent, country, region, city, latitude, longitude."""
    pattern = "dbip-city-lite-*.csv.gz"
    what = "geo"

    def row_value(self, row):
        _cont, cc, region, city, lat, lng = row
        city = re.sub(r"\s*\(.*\)\s*$", "", city or region)  # "Moscow (Tsentralnyy ... okrug)" -> "Moscow"
        return [round(float(lat), 3), round(float(lng), 3), cc, city]

    def lookup(self, ip):
        """Return {lat, lng, cc, city} or None."""
        v = self.find(ip)
        if not v or v[2] == "ZZ":
            return None
        return {"lat": v[0], "lng": v[1], "cc": v[2], "city": v[3]}


class Asn(RangeTable):
    """ASN Lite: autonomous system number and organization name."""
    pattern = "dbip-asn-lite-*.csv.gz"
    what = "asn"

    def row_value(self, row):
        asn, org = row
        return [int(asn) if asn.isdigit() else 0, org]

    def lookup(self, ip):
        """Return {asn, org} or None."""
        v = self.find(ip)
        if not v:
            return None
        return {"asn": v[0], "org": v[1]}
