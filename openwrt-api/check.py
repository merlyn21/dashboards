#!/usr/bin/env python3
"""Check the router's setup for the map: log in with the settings from .env and try every call the service makes.

  python3 check.py        (Linux, macOS)
  py -3 check.py          (Windows)

Prints one line per check. Nothing is changed on the router. See ROUTER-SETUP.md for what to fix.
"""
import sys

import config
import router
from ubus import Ubus, UbusError


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    cfg = config.load()
    u = Ubus(cfg["router_url"], cfg["router_user"], cfg["router_password"], cfg["proxy"] or None)
    print(f"Роутер {cfg['router_url']}" + (f" через прокси {cfg['proxy']}" if cfg["proxy"] else " напрямую") + f", пользователь {cfg['router_user']}")
    try:
        u.login()
    except OSError as e:
        print(f"[!!] вход: {e}")
        print("     Проверьте адрес роутера, прокси, имя и пароль в .env и шаг 3 в ROUTER-SETUP.md.")
        return 1
    print("[ok] вход выполнен")
    failed = 0

    def check(title, fn, hint):
        nonlocal failed
        try:
            print(f"[ok] {title}: {fn()}")
        except (OSError, KeyError, ValueError, StopIteration) as e:
            failed += 1
            print(f"[!!] {title}: {e}\n     {hint}")

    acl = "проверьте файл прав /usr/share/rpcd/acl.d/map-readonly.json (шаг 2) и перезапустите rpcd"
    check("модель и версия", lambda: "{machine}, {os}".format(**router.board(u)), acl)
    check("нагрузка и память", lambda: f"работает {router.info(u)['uptime'] // 86400} дн", acl)
    check("процессор", lambda: f"{len(u.read('/proc/stat'))} байт из /proc/stat", acl)

    def wan():
        ifs = router.interfaces(u)
        w = next((i for i in ifs if i["name"] == cfg["wan_interface"]), None)
        if not w:
            raise ValueError(f"нет интерфейса {cfg['wan_interface']}; есть: {', '.join(i['name'] for i in ifs)}")
        return f"{w['device']} {', '.join(str(a.ip) for a in w['addrs']) or 'без адреса'}, шлюз {w['gateway']}"
    check(f"интерфейс {cfg['wan_interface']}", wan, "задайте wan_interface в .env: имя интерфейса из списка")
    check("счётчики устройств", lambda: f"{len(router.devices(u))} устройств", acl)
    check("соседи (ip neigh)", lambda: f"{len(router.neighbors(u))} адресов", acl)
    check("аренды DHCP", lambda: f"{len(router.dhcp_names(u))} имён", acl)
    check("клиенты Wi-Fi", lambda: f"{len(router.wifi_clients(u))} клиентов", acl)

    def table():
        text = u.run(router.CONNTRACK_HELPER)
        count = u.read("/proc/sys/net/netfilter/nf_conntrack_count").strip()
        return f"{len(text.splitlines())} записей, {len(text) // 1024} КБ (в ядре сейчас {count})"
    check("таблица соединений", table, "установите скрипт /usr/libexec/map-conntrack (шаг 1) и разрешите его в файле прав (шаг 2)")
    check("слушающие порты (netstat)", lambda: f"{len(router.listeners(u))} сокетов", acl)

    def fw():
        ifs = router.interfaces(u)
        w = next(i for i in ifs if i["name"] == cfg["wan_interface"])
        f = router.Firewall(u, cfg["wan_interface"], w["device"])
        if not f.ok:
            raise ValueError("конфигурация firewall не прочитана")
        return f"зона {f.zone}, вход {f.policy}, правил для WAN {len(f.rules)}, пробросов {len(f.redirects)}"
    check("firewall", fw, acl)
    check("журнал (logread)", lambda: f"{u.run('/sbin/logread').count(chr(10))} строк", acl)

    def secrets():
        try:
            u.call("uci", "get", {"config": "network"})
        except UbusError as e:
            if e.code == 6:
                return "закрыт, как и должно быть"
            raise
        raise ValueError("конфигурация network читается: в ней пароли. В списке uci файла прав должны быть только firewall и dhcp")
    check("доступ к конфигурации network", secrets, "исправьте файл прав по шагу 2")
    print("\nВсё готово: запускайте карту." if not failed else f"\nНе прошло проверок: {failed}. Исправьте по подсказкам выше и запустите check.py ещё раз.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
