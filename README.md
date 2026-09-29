# Dashboards

Два самостоятельных дашборда для домашней и небольшой офисной инфраструктуры.
Оба написаны на Python 3.9+ только со стандартной библиотекой, отдают веб-интерфейс из
того же процесса и запускаются без Docker или в Docker.

| Проект | Что показывает | Источник данных | Порт по умолчанию |
| --- | --- | --- | --- |
| [`zabbix/`](zabbix/) — **Zabbix Galaxy** | 3D-галактика хостов: планеты, состояние, метрики, проблемы, порты роутеров, службы | Zabbix API | `127.0.0.1:8080` |
| [`mikrotik-api/`](mikrotik-api/) — **MikroTik connection map** | Карта мира с соединениями роутера, входящие сканы, открытые порты, сессии на роутере | RouterOS API | `127.0.0.1:8787` |

Проекты не зависят друг от друга: у каждого свои настройки, `Dockerfile` и `docker-compose.yml`.

## Zabbix Galaxy

![Галактика хостов](zabbix/docs/screenshots/galaxy.jpg)

Каждый хост Zabbix — планета. Цвет и узор планеты зависят от ОС, пояс — от состояния хоста,
размер — от выбранной метрики. Роутеры (MikroTik, OpenWrt) нарисованы орбитальными станциями
с портами. Новые проблемы подсвечиваются на сцене и во всплывающих уведомлениях.

```bash
cd zabbix
cp .env_example .env && chmod 600 .env   # адрес Zabbix и API-токен
python3 server.py                        # или: docker compose up -d --build
```

Подробности: [zabbix/README.md](zabbix/README.md) (на английском).

## MikroTik connection map

![Карта соединений](mikrotik-api/docs/screenshot-map.jpg)

Живая карта соединений роутера MikroTik: трафик к городам и организациям, подключения к
роутеру и сканы из интернета, сервисы, доступные с WAN, активные сессии и состояние роутера.

```bash
cd mikrotik-api
cp .env.example .env                     # адрес роутера и пользователь API только для чтения
./start.sh                               # Windows: start.bat; или: docker compose up -d --build
```

При первом запуске скачиваются справочные базы геолокации и OUI (около 95 МБ).
Подробности: [mikrotik-api/README.md](mikrotik-api/README.md).

## Безопасность

- Файлы `.env` содержат учётные данные и не попадают в git. В репозитории лежат только примеры:
  `zabbix/.env_example` и `mikrotik-api/.env.example`.
- У дашбордов нет своей авторизации. По умолчанию они слушают только `127.0.0.1`.
  Адрес `0.0.0.0` используйте только в доверенной сети или за обратным прокси с авторизацией.
- Для обоих источников нужен пользователь только с правами на чтение. Как его создать, описано
  в README каждого проекта.

## Лицензия

Оба проекта распространяются по лицензии MIT, см. [LICENSE](LICENSE).

---

## English

Two standalone dashboards for home and small-office infrastructure. Both need Python 3.9+ and
nothing beyond the standard library. Each serves its web interface from the same process and runs
with or without Docker.

| Project | What it shows | Data source | Default address |
| --- | --- | --- | --- |
| [`zabbix/`](zabbix/) — **Zabbix Galaxy** | A 3D galaxy of hosts: planets, health, metrics, problems, router ports, services | Zabbix API | `127.0.0.1:8080` |
| [`mikrotik-api/`](mikrotik-api/) — **MikroTik connection map** | A world map of router connections, inbound scans, WAN-exposed ports, sessions on the router | RouterOS API | `127.0.0.1:8787` |

The projects do not depend on each other. Each one has its own settings, `Dockerfile` and
`docker-compose.yml`.

### Zabbix Galaxy

Every Zabbix host is a planet. Its colour and pattern show the OS, its belt shows the host health,
and its size follows a metric you choose. Routers (MikroTik, OpenWrt) are orbital stations with
their ports around them. New problems flare on the scene and appear as toasts.

```bash
cd zabbix
cp .env_example .env && chmod 600 .env   # Zabbix URL and API token
python3 server.py                        # or: docker compose up -d --build
```

Details: [zabbix/README.md](zabbix/README.md).

### MikroTik connection map

A live map of a MikroTik router's connections: traffic to cities and organisations, connections to
the router and scans from the internet, services reachable from the WAN, active sessions and router
health.

```bash
cd mikrotik-api
cp .env.example .env                     # router address and a read-only API user
./start.sh                               # Windows: start.bat; or: docker compose up -d --build
```

The first start downloads the geolocation and OUI databases (about 95 MB).
Details (in Russian): [mikrotik-api/README.md](mikrotik-api/README.md).

### Security

- `.env` files hold credentials and stay out of git. Only the examples are committed:
  `zabbix/.env_example` and `mikrotik-api/.env.example`.
- The dashboards have no login of their own and listen on `127.0.0.1` by default. Use `0.0.0.0`
  only in a trusted network or behind a reverse proxy with authentication.
- Both data sources need a read-only user. Each project's README shows how to create one.

### License

Both projects are released under the MIT License, see [LICENSE](LICENSE).
