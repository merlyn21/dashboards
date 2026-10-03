# Настройка роутера OpenWrt для карты соединений

Карта читает роутер по ubus поверх HTTP — тем же способом, каким с ним работает веб-интерфейс LuCI.
На роутере нужно сделать три вещи:

1. положить скрипт из двух строк, который отдаёт таблицу соединений;
2. описать права: что пользователю карты разрешено читать;
3. завести пользователя `map` с паролем.

Всё только на чтение: пользователь карты ничего не может изменить на роутере. Настройка занимает
несколько минут. Проверено на Xiaomi AX3000T с OpenWrt 23.05.5.

Команды выполняются на роутере от root: по SSH (`ssh root@192.168.1.1`) или в LuCI, если установлен
терминал. Блоки команд копируются целиком.

## Перед началом

Нужны пакеты `uhttpd-mod-ubus` и `rpcd-mod-file`. Они ставятся вместе с LuCI: если веб-интерфейс
роутера открывается в браузере, они уже есть. Проверить:

```
opkg list-installed | grep -E "uhttpd-mod-ubus|rpcd-mod-file"
```

Должны быть обе строки. Если какой-то нет:

```
opkg update
opkg install uhttpd-mod-ubus rpcd-mod-file
/etc/init.d/rpcd restart
/etc/init.d/uhttpd restart
```

## Шаг 1. Скрипт для таблицы соединений

`rpcd` читает файлы из `/proc` одним блоком и отдаёт только первые 4 КБ — около 18 соединений из сотен.
Скрипт выводит таблицу целиком:

```
cat > /usr/libexec/map-conntrack <<'EOF'
#!/bin/sh
exec cat /proc/net/nf_conntrack
EOF
chmod 755 /usr/libexec/map-conntrack
```

Проверка: `/usr/libexec/map-conntrack | wc -l` — число соединений, больше нуля.

Почему отдельный скрипт, а не право на `cat`: права `rpcd` даются на программу целиком, без учёта
аргументов. С правом на `cat` пользователь карты прочитал бы любой файл роутера, включая пароли.

## Шаг 2. Права пользователя карты

```
cat > /usr/share/rpcd/acl.d/map-readonly.json <<'EOF'
{
  "map-readonly": {
    "description": "Connection map: read-only",
    "read": {
      "ubus": {
        "system": ["board", "info"],
        "network.interface": ["dump"],
        "network.device": ["status"],
        "luci-rpc": ["getHostHints", "getDHCPLeases"],
        "iwinfo": ["devices", "assoclist"],
        "uci": ["get"],
        "file": ["read", "exec"]
      },
      "uci": ["firewall", "dhcp"],
      "file": {
        "/proc/net/nf_conntrack": ["read"],
        "/proc/net/arp": ["read"],
        "/proc/stat": ["read"],
        "/proc/meminfo": ["read"],
        "/proc/loadavg": ["read"],
        "/proc/sys/net/netfilter/nf_conntrack_count": ["read"],
        "/proc/sys/net/netfilter/nf_conntrack_max": ["read"],
        "/tmp/dhcp.leases": ["read"],
        "/etc/hosts": ["read"],
        "/bin/netstat": ["exec"],
        "/sbin/logread": ["exec"],
        "/sbin/ip": ["exec"],
        "/usr/libexec/map-conntrack": ["exec"]
      }
    }
  }
}
EOF
```

Что этим разрешено:

| Что | Зачем карте |
|---|---|
| `system board`, `system info` | модель, версия OpenWrt, время работы, память, диск |
| `network.interface dump`, `network.device status` | адреса WAN и LAN, шлюз, счётчики и скорость портов |
| `luci-rpc`, `/tmp/dhcp.leases`, `/etc/hosts` | имена устройств в сети |
| `iwinfo` | клиенты Wi-Fi |
| `uci get` для `firewall` | зона WAN, правила и пробросы — для плашки «Порты из интернета» |
| `/proc/stat`, `/proc/meminfo`, `/proc/loadavg` | загрузка процессора |
| `netstat` | какие порты слушают процессы роутера |
| `logread` | входы по SSH и в LuCI |
| `ip` | соседи IPv4 и IPv6: какие устройства сейчас в сети |
| `/usr/libexec/map-conntrack` | таблица соединений |

Чего намеренно нет:

- конфигураций `network` и `wireless`: в них пароль PPPoE, ключи Wi-Fi и VPN;
- раздела `write`: изменить что-либо на роутере этот пользователь не может;
- права запускать `cat`, `sh`, `uci` и другие программы.

## Шаг 3. Пользователь и пароль

Подставьте свой длинный пароль вместо `ПАРОЛЬ` (кавычки оставьте):

```
uci add rpcd login
uci set rpcd.@login[-1].username='map'
uci set rpcd.@login[-1].password="$(uhttpd -m 'ПАРОЛЬ')"
uci add_list rpcd.@login[-1].read='map-readonly'
uci commit rpcd
/etc/init.d/rpcd restart
```

В `/etc/config/rpcd` попадает хеш пароля, не сам пароль. Системного пользователя с таким именем не
появляется: войти по SSH под `map` нельзя.

Сменить пароль позже — найти номер секции и задать новый хеш:

```
uci show rpcd | grep username
uci set rpcd.@login[1].password="$(uhttpd -m 'НОВЫЙ-ПАРОЛЬ')"
uci commit rpcd
/etc/init.d/rpcd restart
```

`@login[1]` — секция с `username='map'` из вывода первой команды (счёт с нуля).

## Шаг 4. Сохранить настройку при обновлении прошивки

`/etc/config/rpcd` переживает `sysupgrade` сам. Скрипт и файл прав лежат вне `/etc` и при обновлении
прошивки пропадут. Чтобы они сохранялись:

```
cat >> /etc/sysupgrade.conf <<'EOF'
/usr/libexec/map-conntrack
/usr/share/rpcd/acl.d/map-readonly.json
EOF
```

## Проверка

На машине, где будет работать карта, заполните `.env` (адрес роутера, `username=map`, пароль, прокси,
если нужен) и запустите проверку из каталога проекта:

```
python3 check.py          # Linux, macOS
py -3 check.py            # Windows
```

Она входит на роутер и делает каждый вызов, который нужен карте. Все строки должны начинаться с `[ok]`:

```
[ok] вход выполнен
[ok] модель и версия: Xiaomi Mi Router AX3000T, OpenWrt 23.05.5
[ok] таблица соединений: 395 записей, 88 КБ (в ядре сейчас 405)
[ok] firewall: зона wan, вход REJECT, правил для WAN 6, пробросов 0
[ok] доступ к конфигурации network: закрыт, как и должно быть
```

## Если что-то не работает

| Что видно | Причина и что делать |
|---|---|
| `login failed for map` | Неверный пароль, или секция `login` не создалась. Проверьте `uci show rpcd`: должна быть секция с `username='map'` и `read='map-readonly'`. После правки — `/etc/init.d/rpcd restart`. |
| `HTTP 5xx from the proxy` | Прокси не видит роутер по этому адресу. Через прокси роутер обычно доступен только по адресу LAN (например `192.168.1.1`), не по адресу WAN: firewall не пускает к веб-интерфейсу снаружи. |
| `the answer is not JSON` | По адресу `router_url` отвечает не ubus: нет пакета `uhttpd-mod-ubus` или указан не тот адрес. |
| `[!!] таблица соединений: … permission denied` | Нет скрипта (шаг 1), или его нет в файле прав (последняя строка списка `file` в шаге 2). |
| В журнале сервиса `only the first 4 KB of the table are read` | То же: скрипт не установлен или не разрешён. Карта работает, но видит около 18 соединений. |
| Другие строки `[!!] … permission denied` | Файл прав неполный или с ошибкой в JSON (лишняя или пропущенная запятая). Перезапишите его блоком из шага 2 и выполните `/etc/init.d/rpcd restart`. |
| `нет интерфейса wan` | WAN называется иначе (например `wwan`, `pppoe-wan`). Впишите в `.env` имя из списка, который печатает проверка: `wan_interface=…`. |
| Проверка проходит, но имена устройств — адреса | У устройства нет имени в DHCP (или адрес статический). Задайте имя в `names.conf` по MAC или IP. |

Права перечитываются только при перезапуске `rpcd`. После любой правки файла прав или `/etc/config/rpcd`
выполните `/etc/init.d/rpcd restart`.

## Безопасность

- Пароль пользователя `map` идёт по сети открытым текстом, если в `router_url` указан `http://`.
  В домашней сети и внутри VPN это приемлемо. Если на роутере включён HTTPS (`luci-ssl`), укажите
  `https://…`: канал будет зашифрован; сертификат роутера при этом не проверяется.
- С паролем `map` можно читать таблицу соединений, имена устройств, правила firewall и журнал роутера.
  Изменить настройки, прочитать пароли Wi-Fi или PPPoE — нельзя.
- Не открывайте веб-интерфейс роутера в интернет ради карты: карта должна ходить к роутеру из его
  локальной сети или через VPN.

## Отозвать доступ

```
uci show rpcd | grep username          # найти номер секции с username='map'
uci delete rpcd.@login[1]              # подставьте её номер
uci commit rpcd
rm /usr/share/rpcd/acl.d/map-readonly.json /usr/libexec/map-conntrack
/etc/init.d/rpcd restart
```

И уберите две строки из `/etc/sysupgrade.conf`, если добавляли их на шаге 4.
