#!/usr/bin/env python3
"""Download the reference databases the service needs, if they are missing.

  geo/dbip-city-lite-YYYY-MM.csv.gz   places by IP        https://db-ip.com (CC BY 4.0)
  geo/dbip-asn-lite-YYYY-MM.csv.gz    organizations by IP https://db-ip.com (CC BY 4.0)
  oui/oui.txt                         device makers by MAC, IEEE registry

DB-IP publishes new files on the 1st of every month; the script takes the current month and
falls back to the previous one. Files are written to a temporary name and renamed only after the
gzip check passes, so an interrupted download never leaves a broken database behind.

Usage:
  python3 fetch_data.py            download what is missing
  python3 fetch_data.py --update   also replace DB-IP files with a newer month and remove the old ones

Internet access goes through the usual HTTP_PROXY / HTTPS_PROXY variables if they are set.
"""
import argparse
import datetime
import glob
import gzip
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (linux-map fetch_data.py)"}
DBIP = [("dbip-city-lite", "места по IP"), ("dbip-asn-lite", "организации по IP")]
OUI_URL = "https://standards-oui.ieee.org/oui/oui.txt"
OUI_SYSTEM = ["/usr/share/hwdata/oui.txt", "/usr/share/misc/oui.txt", "/usr/share/ieee-data/oui.txt"]


def months():
    today = datetime.date.today()
    first = today.replace(day=1)
    prev = (first - datetime.timedelta(days=1)).replace(day=1)
    return [first.strftime("%Y-%m"), prev.strftime("%Y-%m")]


def exists(url):
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200, int(r.headers.get("Content-Length") or 0)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, 0
        raise


ATTEMPTS = 6


def download(url, dest, size_hint=0):
    """Download to dest + '.part' and return that path. A slow or broken connection is retried, and every
    retry (and the next run of the script) continues from the bytes already saved, if the server allows it."""
    tmp = dest + ".part"
    for attempt in range(1, ATTEMPTS + 1):
        try:
            _download_once(url, tmp, size_hint)
            sys.stdout.write("\n")
            return tmp
        except (urllib.error.URLError, OSError) as e:  # socket.timeout is an OSError
            if isinstance(e, urllib.error.HTTPError) and e.code != 416:
                raise
            if attempt == ATTEMPTS:
                raise
            have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            sys.stdout.write(f"\n  обрыв ({e}), повтор {attempt + 1} из {ATTEMPTS} с {have / 1e6:.1f} МБ\n")
            time.sleep(min(30, 5 * attempt))
    return tmp


def _download_once(url, tmp, size_hint):
    have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    headers = dict(UA, Range=f"bytes={have}-") if have else UA
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:  # the saved part is already complete
            return
        raise
    with r:
        resumed = have and r.status == 206
        if have and not resumed:
            have = 0  # the server ignored Range: start over
        total = have + int(r.headers.get("Content-Length") or 0) or size_hint
        done, last = have, -1
        with open(tmp, "ab" if resumed else "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                pct = int(done * 100 / total) if total else -1
                if pct != last and (pct % 5 == 0 or pct < 0):
                    sys.stdout.write(f"\r  {done / 1e6:6.1f} МБ" + (f" из {total / 1e6:.1f} МБ ({pct}%)" if total else ""))
                    sys.stdout.flush()
                    last = pct
        if total and done < total:
            raise OSError(f"получено {done} из {total} байт")


def check_gzip(path):
    with gzip.open(path, "rb") as f:
        while f.read(1 << 22):
            pass


def fetch_dbip(folder, name, label, update):
    have = sorted(glob.glob(os.path.join(folder, f"{name}-*.csv.gz")))
    if have and not update:
        print(f"[ok] {label}: {os.path.basename(have[-1])}")
        return True
    for month in months():
        fname = f"{name}-{month}.csv.gz"
        dest = os.path.join(folder, fname)
        if os.path.exists(dest):
            print(f"[ok] {label}: {fname} уже самая свежая")
            return True
        url = f"https://download.db-ip.com/free/{fname}"
        ok, size = exists(url)
        if not ok:
            continue
        print(f"[..] {label}: {'обновляю до' if have else f'нет базы в {folder}, скачиваю'} {fname}")
        tmp = download(url, dest, size)
        try:
            check_gzip(tmp)
        except (OSError, EOFError):
            os.remove(tmp)  # a broken part must not be continued next time
            raise
        os.replace(tmp, dest)
        for old in have:  # older months and their caches are no longer needed
            stem = old[: -len(".csv.gz")]
            for p in [old] + glob.glob(stem + ".*"):
                if p != dest and os.path.exists(p):
                    os.remove(p)
        print(f"[ok] {label}: {fname}")
        return True
    print(f"[!!] {label}: на download.db-ip.com нет файла за {' и '.join(months())}", file=sys.stderr)
    return bool(have)


def fetch_oui(folder, update):
    dest = os.path.join(folder, "oui.txt")
    if os.path.exists(dest) and not update:
        print("[ok] производители по MAC: oui/oui.txt")
        return True
    try:
        print(f"[..] производители по MAC: {'обновляю' if os.path.exists(dest) else f'нет {dest}, скачиваю'} реестр IEEE")
        tmp = download(OUI_URL, dest)
        with open(tmp, encoding="utf-8", errors="replace") as f:
            if "(base 16)" not in f.read(200000):
                raise ValueError("ответ не похож на реестр OUI")
        os.replace(tmp, dest)
        print("[ok] производители по MAC: oui/oui.txt")
        return True
    except (urllib.error.URLError, OSError, ValueError) as e:
        if os.path.exists(dest + ".part"):
            os.remove(dest + ".part")
        for src in OUI_SYSTEM:
            if os.path.exists(src):
                shutil.copyfile(src, dest)
                print(f"[ok] производители по MAC: IEEE недоступен ({e}), взят системный {src}")
                return True
        if os.path.exists(dest):
            print(f"[ok] производители по MAC: IEEE недоступен ({e}), остаётся прежний oui/oui.txt")
            return True
        print(f"[!!] производители по MAC: не удалось получить реестр ({e}); устройства будут без производителя", file=sys.stderr)
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true", help="заменить базы на более свежие")
    ap.add_argument("--dir", default=HERE, help=argparse.SUPPRESS)  # for tests
    ap.add_argument("--only", default="city,asn,oui", help=argparse.SUPPRESS)
    args = ap.parse_args()
    only = set(args.only.split(","))
    geo_dir, oui_dir = os.path.join(args.dir, "geo"), os.path.join(args.dir, "oui")
    os.makedirs(geo_dir, exist_ok=True)
    os.makedirs(oui_dir, exist_ok=True)
    ok = True
    for (name, label), key in zip(DBIP, ("city", "asn")):
        if key in only:
            try:
                ok &= fetch_dbip(geo_dir, name, label, args.update)
            except (urllib.error.URLError, OSError, EOFError) as e:
                print(f"\n[!!] {label}: {e}", file=sys.stderr)
                ok = False
    if "oui" in only:
        ok &= fetch_oui(oui_dir, args.update)
    if not ok:
        sys.exit("Не все базы получены. Сервис запустится, но без них места, организации или производители будут неизвестны.")


if __name__ == "__main__":
    main()
