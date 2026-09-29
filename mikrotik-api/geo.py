"""IPv4 lookups in DB-IP Lite databases: city (place) and ASN (organization).

Each CSV (gzip) is parsed once into a compact cache next to it: range starts and ends as sorted
uint32 arrays plus an index into a table of unique values. Lookups are a binary search.
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

    def __init__(self, folder):
        self.folder = folder
        self.starts = array.array("I")
        self.ends = array.array("I")
        self.idx = array.array("I")
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
        log.info("%s: %s, %d ranges, %d values", self.what, self.source, len(self.starts), len(self.values))
        return self

    def _paths(self, stem):
        return [stem + ext for ext in (".starts", ".ends", ".idx", ".values.json")]

    def _load_cache(self, stem, db):
        paths = self._paths(stem)
        if not all(os.path.exists(p) and os.path.getmtime(p) >= os.path.getmtime(db) for p in paths):
            return False
        for arr, p in zip((self.starts, self.ends, self.idx), paths):
            with open(p, "rb") as f:
                arr.frombytes(f.read())
        with open(paths[3], encoding="utf-8") as f:
            self.values = json.load(f)
        return True

    def _save_cache(self, stem):
        paths = self._paths(stem)
        for arr, p in zip((self.starts, self.ends, self.idx), paths):
            with open(p, "wb") as f:
                arr.tofile(f)
        with open(paths[3], "w", encoding="utf-8") as f:
            json.dump(self.values, f, ensure_ascii=False, separators=(",", ":"))

    def _build(self, db):
        t0 = time.time()
        log.info("%s: building cache from %s, this happens once per database file", self.what, os.path.basename(db))
        seen = {}
        with gzip.open(db, "rt", encoding="utf-8", newline="") as f:
            for row in csv.reader(f):
                if ":" in row[0]:
                    break  # the IPv6 part follows the IPv4 part
                value = self.row_value(row[2:])
                key = json.dumps(value, ensure_ascii=False)
                i = seen.get(key)
                if i is None:
                    i = seen[key] = len(self.values)
                    self.values.append(value)
                self.starts.append(int(ipaddress.IPv4Address(row[0])))
                self.ends.append(int(ipaddress.IPv4Address(row[1])))
                self.idx.append(i)
        log.info("%s: cache built in %.0f s", self.what, time.time() - t0)

    def find(self, ip):
        try:
            n = int(ipaddress.IPv4Address(ip))
        except ValueError:
            return None
        i = bisect.bisect_right(self.starts, n) - 1
        if i < 0 or n > self.ends[i]:
            return None
        return self.values[self.idx[i]]


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
