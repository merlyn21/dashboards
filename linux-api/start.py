#!/usr/bin/env python3
"""Start the map on any system: fetch missing databases, then run the service in this window.

  sudo ./start.sh             run in this window
  ./start.sh --update         also refresh the databases to the latest month first
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    # Russian messages must survive any console: Windows cmd, redirection to a file, old code pages.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    os.chdir(HERE)
    sys.path.insert(0, HERE)

    if not os.path.exists(os.path.join(HERE, ".env")):
        print("Нет файла .env: работаю с настройками по умолчанию (mode=router, WAN по маршруту по умолчанию).\n"
              "Шаблон: .env.example, см. README.md.", file=sys.stderr)

    import fetch_data
    argv = sys.argv
    try:
        sys.argv = [argv[0]] + [a for a in argv[1:] if a == "--update"]
        fetch_data.main()
    except SystemExit as e:  # a failed download is not fatal
        if e.code not in (None, 0):
            print(f"{e.code}\nПродолжаю без недостающих баз.", file=sys.stderr)
    finally:
        sys.argv = argv

    import server
    return server.main()


if __name__ == "__main__":
    sys.exit(main() or 0)
