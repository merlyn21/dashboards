// Interface language (GALAXY_LANG in the settings file: ru | en).
// Russian is the source language of the code. For English, whole UI phrases are
// translated automatically as they appear in the DOM (text nodes and title /
// placeholder / aria-label attributes) by exact match against EN; strings that
// mix words with numbers are written with tr(ru, en) at their call sites.
// Exact matching keeps Zabbix data (host names, problem texts) untouched.

export let LANG = 'ru';

export function setLang(lang) {
  LANG = lang === 'en' ? 'en' : 'ru';
  document.documentElement.lang = LANG;
}

export const tr = (ru, en) => (LANG === 'en' ? en : ru);
export const t = (s) => (LANG === 'en' && Object.prototype.hasOwnProperty.call(EN, s) ? EN[s] : s);
export const locale = () => (LANG === 'en' ? 'en-GB' : 'ru');

const EN = {
  // --- left panel, legend, static page
  'Zabbix Galaxy': 'Zabbix Galaxy',
  'обновлено': 'updated',
  'Свернуть панель': 'Collapse panel',
  'Поиск: имя, IP, группа  ( / )': 'Search: name, IP, group  ( / )',
  'Раскладка': 'Layout',
  'По состоянию — проблемные ближе к ядру': 'By state — troubled hosts closer to the core',
  'Свободная галактика': 'Free galaxy',
  'Размер планеты': 'Planet size',
  'Метрики у планет': 'Planet metrics',
  'Карточка над планетой': 'Card above the planet',
  'Кольца вокруг планеты': 'Rings around the planet',
  'Карточка и кольца': 'Card and rings',
  'Не показывать': 'Hide',
  'Отображение': 'Display',
  'Спутники и Docker-луны': 'Satellites and Docker moons',
  'Потоки трафика к ядру': 'Traffic flows to the core',
  'Число пользователей под планетой': 'Logged-in users under the planet',
  'Оповещения о событиях': 'Event alerts',
  'Сводка галактики справа': 'Galaxy summary on the right',
  'Только проблемные': 'Only hosts with problems',
  'Автовращение': 'Auto-rotate',
  'Свечение': 'Glow',
  'Звёздная пыль': 'Star dust',
  'Подписи хостов': 'Host labels',
  'Подписи спутников': 'Satellite labels',
  'все': 'all',
  'при наведении': 'on hover',
  'у выбранного': 'selected host',
  'Обновлять каждые': 'Refresh every',
  '15 с': '15 s', '30 с': '30 s', '1 мин': '1 min', '5 мин': '5 min',
  'Группы': 'Groups',
  'Хосты': 'Hosts',
  'Легенда': 'Legend',
  'Поверхность планеты = операционная система': 'Planet surface = operating system',
  'Метрики: полоса и число = % загрузки, ≥80% жёлтым, ≥90% красным': 'Metrics: bar and number = % load, ≥80% yellow, ≥90% red',
  'Сияние вокруг планеты = худшая проблема': 'Glow around the planet = worst problem',
  'Потоки трафика:': 'Traffic flows:',
  'синие частицы летят к планете — входящий': 'blue particles fly to the planet — inbound',
  'оранжевые летят к ядру — исходящий': 'orange ones fly to the core — outbound',
  '. Толщина, число и скорость частиц растут с объёмом': '. Width, number and speed of particles grow with volume',
  '(логарифмическая шкала). Ядро — условный «внешний мир», а не реальный узел сети.': '(log scale). The core is a notional “outside world”, not a real network node.',
  'Оповещения: новая проблема (Average и ниже) или новый вход — планета вспыхивает цветом': 'Alerts: a new problem (Average and below) or a new login makes the planet flare in the',
  'события, свечение и сокращающееся кольцо гаснут за 3 минуты, плашка справа вверху держится столько же. High и Disaster — плашка висит,': 'event colour; the glow and the shrinking ring fade within 3 minutes, and so does the toast at the top right. High and Disaster — the toast stays',
  'пока проблема не решена, планета пульсирует красным.': 'until the problem is resolved, and the planet pulses red.',
  'Значок под планетой — сколько пользователей сейчас залогинено (Linux: system.users.num,': 'The badge under a planet is the number of logged-in users (Linux: system.users.num,',
  'Windows: RDP-сессии, если есть такая метрика).': 'Windows: RDP sessions, if such an item exists).',
  'Маленькие синие луны — Docker-контейнеры: размер = память, яркость = CPU, красная — остановлен': 'Small blue moons are Docker containers: size = memory, brightness = CPU, red = stopped',
  'Искусственные спутники — обнаруженные сервисы: 🐘 PostgreSQL (синий корпус), ▣ 1С (жёлтый),': 'Artificial satellites are detected services: 🐘 PostgreSQL (blue body), ▣ 1C (yellow),',
  '💽 SMART (серебристый), ⚙ службы (золотистый), ◎ Zabbix (красный). Огонёк: зелёный редко мигает — ОК,': '💽 SMART (silver), ⚙ services (gold), ◎ Zabbix (red). Beacon: slow green blink — OK,',
  'жёлтый или красный часто мигает — есть проблемы.': 'fast yellow or red blink — problems.',
  '◎ Вся галактика': '◎ Whole galaxy',
  'Вернуться к галактике (Esc или клик по пустому месту)': 'Back to the galaxy (Esc or click on empty space)',
  'Для работы нужен JavaScript и WebGL.': 'JavaScript and WebGL are required.',
  'Хосты Zabbix в виде планет: кольца показывают загрузку CPU, RAM и диска, спутники показывают Docker, PostgreSQL и другие сервисы.': 'Zabbix hosts as planets: rings show CPU, RAM and disk load, satellites show Docker, PostgreSQL and other services.',
  'Свернуть / развернуть сводку': 'Collapse / expand the summary',
  'Свернуть сводку': 'Collapse the summary',
  'Сводка галактики': 'Galaxy summary',
  'Данные получены': 'Data received',
  'нет связи': 'offline',
  // size options, legend rows
  'Заполнение диска': 'Disk usage', 'Загрузка CPU': 'CPU load', 'Загрузка RAM': 'RAM load', 'Объём RAM': 'RAM size',
  'Объём диска': 'Disk size', 'Число проблем': 'Problem count', 'Число контейнеров': 'Container count',
  'Сетевой трафик': 'Network traffic', 'Одинаковый': 'Equal',
  'Нет связи с агентом': 'Agent unreachable',
  'клик — подробнее': 'click for details',
  // --- states, severities, statuses
  'В сети': 'Online', 'Нет связи': 'Offline', 'Неизвестно': 'Unknown', 'Отключён': 'Disabled',
  'ОК': 'OK', 'Есть проблемы': 'Problems', 'Недоступен': 'Down', 'Нет данных': 'No data',
  'Не классиф.': 'Not classified', 'Информация': 'Information', 'Предупреждение': 'Warning',
  'Средняя': 'Average', 'Высокая': 'High', 'Катастрофа': 'Disaster',
  'Вход': 'In', 'Выход': 'Out', 'Диск': 'Disk', 'Обслуживание': 'Maintenance',
  // --- health belts
  'Критично': 'Critical', 'Критично — нет связи, High, Disaster': 'Critical — High, Disaster',
  'Warning и Info': 'Warning and Info', 'Норма': 'Healthy',
  'Нет связи — данных нет': 'Offline — no data',
  '↓ Вх': '↓ In', '↑ Исх': '↑ Out', 'логарифмическая шкала': 'log scale',
  'Ош.': 'Err.', 'ошибок в секунду': 'errors per second', 'Порты': 'Ports', 'Порт': 'Port', 'Скорость': 'Speed', 'Ошибки/с': 'Errors/s', 'Модель': 'Model',
  'Отклик': 'Response', 'Потери': 'Loss', 'ICMP ping': 'ICMP ping',
  '1ч': '1h', '6ч': '6h', '24ч': '24h', '7д': '7d', '▣ 1С': '▣ 1C',
  // --- detail panel
  'Загрузка…': 'Loading…', 'Закрыть (Esc)': 'Close (Esc)', 'Закрыть': 'Close',
  'История': 'History', 'Сеть': 'Network', 'Проблемы': 'Problems', 'Активных проблем нет': 'No active problems',
  'Система': 'System', 'Аптайм': 'Uptime', 'Процессы': 'Processes', 'Swap свободно': 'Swap free',
  'Пользователи': 'Users', 'RDP-сессии': 'RDP sessions', 'Метрик': 'Items', 'Агент': 'Agent', 'Hostname': 'Hostname',
  'Диски': 'Disks', 'Файловые системы': 'Filesystems', 'Сетевые интерфейсы': 'Network interfaces',
  'Ошибки интерфейса': 'Interface errors', 'Показать все': 'Show all', 'Свернуть': 'Collapse',
  'Свернуть / развернуть': 'Collapse / expand',
  'Загрузка устройства': 'Device utilisation', 'Чтение': 'Read', 'Запись': 'Write', 'Очередь': 'Queue',
  'Задержка чтения': 'Read latency', 'Задержка записи': 'Write latency', 'Операции, в секунду': 'Operations per second',
  'чтение': 'read', 'запись': 'write', 'виртуальный диск': 'virtual disk',
  'Темп.': 'Temp.', 'Наработка': 'Power-on', 'Самотест': 'Self-test', 'Износ': 'Wear',
  'Ошибки носителя': 'Media errors', 'Переназнач.': 'Reallocated', 'Неисправимые': 'Uncorrectable',
  'Включений': 'Power cycles', '✓ пройден': '✓ passed', '✗ ошибка': '✗ failed', 'SMART: нет свежих данных': 'SMART: no fresh data',
  'Контейнеры': 'Containers', 'Образы': 'Images', 'Версия': 'Version', 'Контейнер': 'Container',
  'Соединения': 'Connections', 'Активные': 'Active', 'База': 'Database', 'Размер': 'Size',
  'Все метрики PostgreSQL': 'All PostgreSQL metrics', 'Все метрики MySQL': 'All MySQL metrics',
  'Все метрики 1С по базам': 'All 1C metrics by infobase',
  'MySQL — главный сервис': 'MySQL — main service', 'Статус': 'Status', 'Подключено': 'Connected',
  'Выполняется': 'Running', 'Макс. подключений': 'Max connections', 'Запросов/с': 'Queries/s',
  'Медленных/с': 'Slow/s', 'Buffer pool': 'Buffer pool', 'работает': 'running', 'недоступен': 'down',
  'Сеансы': 'Sessions', 'Польз.': 'Users', 'CPU 5м': 'CPU 5m', 'есть ожидания блокировок': 'sessions waiting for locks',
  'Очередь >10м': 'Queue >10m', 'Хостов': 'Hosts', 'Службы': 'Services', 'нет свежих данных': 'no fresh data',
  'остановлена': 'stopped', 'Нет данных истории': 'No history', 'Load average (1 мин)': 'Load average (1 min)',
  'CPU, %': 'CPU, %', 'RAM, %': 'RAM, %', 'Диск, %': 'Disk, %', 'Ping': 'Ping', 'нет': 'no',
  // --- alerts
  'Оповещения — показать или скрыть список': 'Alerts — show or hide the list',
  'Скрыть плашку': 'Hide toast', 'Скрыть': 'Hide', '✓ подтверждена': '✓ acknowledged',
  // --- service windows (PostgreSQL / MySQL / 1C)
  'Ошибка загрузки:': 'Loading error:', 'Нет истории': 'No history', 'Динамика': 'Trends',
  'Доступность': 'Availability', 'Роль': 'Role', 'Cache hit': 'Cache hit', 'доля чтений из кеша': 'reads served from cache',
  'Коммиты': 'Commits', 'Deadlocks': 'Deadlocks', 'Idle in transaction': 'Idle in transaction',
  'соединения, держащие транзакцию': 'connections holding a transaction',
  'Возраст старейшей xid': 'Oldest xid age', 'wraparound при ~2 млрд': 'wraparound at ~2 billion',
  'Autovacuum': 'Autovacuum', 'активных воркеров': 'active workers', 'WAL': 'WAL', 'Архивация': 'Archiving',
  'Соединения по состояниям': 'Connections by state',
  'Соединения': 'Connections', 'Транзакции, в секунду': 'Transactions per second',
  'Коммиты по базам, в секунду': 'Commits per database, per second', 'Cache hit, %': 'Cache hit, %',
  'Чтение строк, в секунду': 'Row reads per second', 'Изменение строк, в секунду': 'Row changes per second',
  'Блоки, в секунду': 'Blocks per second', 'Буферы bgwriter, в секунду': 'Bgwriter buffers per second',
  'Temp-файлы, байт/с': 'Temp files, bytes/s', 'из кеша': 'from cache', 'с диска': 'from disk',
  'Базы данных': 'Databases', 'клик по строке — подробности': 'click a row for details',
  'Подключ.': 'Conn.', 'Commit/с': 'Commit/s', 'Rollback/с': 'Rollback/s', 'Строк/с': 'Rows/s',
  'Блокировки': 'Locks', 'Медл. запросы': 'Slow queries', 'Макс. запрос': 'Max query', 'Bloat табл.': 'Bloated tables',
  'Возраст xid': 'xid age', 'метрика не собирается': 'item is not collected', 'н/д': 'n/a',
  'Служебные процессы': 'Background processes', 'Checkpoint': 'Checkpoint', 'Bgwriter': 'Bgwriter',
  'По расписанию': 'Scheduled', 'По требованию': 'Requested', 'Время записи': 'Write time', 'Время sync': 'Sync time',
  'Буферов checkpoint': 'Checkpoint buffers', 'Выделено буферов': 'Buffers allocated', 'Записано bgwriter': 'Written by bgwriter',
  'Остановок (maxwritten)': 'Stops (maxwritten)', 'Репликация': 'Replication', 'Реплик': 'Replicas',
  'Отставание': 'Lag', 'Отставание, байт': 'Lag, bytes', 'WAL и архив': 'WAL and archive', 'Сегментов WAL': 'WAL segments',
  'Записано': 'Written', 'Получено': 'Received', 'Заархивировано': 'Archived', 'Ждут архивации': 'Waiting for archive',
  'Размер очереди': 'Queue size', 'Ошибок архивации': 'Archive errors', 'Ввод-вывод (сумма)': 'I/O (total)',
  'Время чтения блоков': 'Block read time', 'Время записи блоков': 'Block write time', 'Temp-файлов': 'Temp files',
  'Конфликтов': 'Conflicts', 'Ошибок checksum': 'Checksum errors', 'Бэкендов': 'Backends',
  'Строки, в секунду': 'Rows per second', 'Блоки и I/O': 'Blocks and I/O', 'из кеша/с': 'from cache/s',
  'с диска/с': 'from disk/s', 'время чтения': 'read time', 'время записи': 'write time', 'temp-файлов/с': 'temp files/s',
  'temp байт/с': 'temp bytes/s', 'Блокировки по типам': 'Locks by type', 'нет данных': 'no data', 'Запросы': 'Queries',
  'Транзакции': 'Transactions', 'Прочее': 'Other', 'конфликты/с': 'conflicts/s', 'ошибки checksum': 'checksum errors',
  'bloat таблиц': 'bloated tables', 'Метрики с ошибкой сбора': 'Items failing to collect',
  // MySQL window
  'Работает': 'Running', 'Запросов': 'Queries', 'Медленные': 'Slow', 'Buffer pool hit': 'Buffer pool hit',
  'чтения из памяти': 'reads from memory', 'заполнен': 'filled', 'Ожидания блокировок': 'Lock waits',
  'Tmp-таблицы на диске': 'Tmp tables on disk', 'Отказы подключений': 'Aborted connects', 'Трафик': 'Traffic',
  'потоков прямо сейчас': 'threads right now', 'Команды по типу, в секунду': 'Commands by type, per second',
  'Запросы, в секунду': 'Queries per second', 'Команды, в секунду': 'Commands per second', 'Потоки': 'Threads',
  'Buffer pool hit, %': 'Buffer pool hit, %', 'Трафик, байт/с': 'Traffic, bytes/s', 'Подключения, в секунду': 'Connections per second',
  'Медленные запросы, в секунду': 'Slow queries per second', 'Временные таблицы, в секунду': 'Temporary tables per second',
  'Ожидания блокировок строк (всего)': 'Row lock waits (total)', 'все': 'all', 'клиентские': 'client', 'подключено': 'connected',
  'выполняется': 'running', 'принято': 'received', 'отправлено': 'sent', 'новые': 'new', 'отказы': 'aborted',
  'обрывы клиентов': 'client aborts', 'в памяти': 'in memory', 'на диске': 'on disk', 'Доля': 'Share', 'Замер': 'Measured',
  'Подробности': 'Details', 'InnoDB': 'InnoDB', 'Страниц всего': 'Pages total', 'Страниц свободно': 'Pages free',
  'Логических чтений': 'Logical reads', 'Чтений с диска': 'Disk reads', 'Ожиданий свободной страницы': 'Free page waits',
  'Открытых файлов': 'Open files', 'Записано в redo log': 'Written to redo log', 'Размер redo log': 'Redo log size',
  'Потоки и таблицы': 'Threads and tables', 'В кеше потоков': 'Threads cached', 'Создано потоков': 'Threads created',
  'Открытых таблиц': 'Open tables', 'Определений таблиц': 'Table definitions', 'Tmp-файлов на диске': 'Tmp files on disk',
  'Binlog cache на диске': 'Binlog cache on disk', 'Подключения': 'Connections', 'Новых': 'New', 'Отказов': 'Aborted',
  'Обрывов клиентов': 'Client aborts',
  // 1C window
  '1С:Предприятие': '1C:Enterprise', 'Информационных баз': 'Infobases', 'Сеансов': 'Sessions', 'Пользователей': 'Users',
  'уникальных': 'unique', 'Фоновых заданий': 'Background jobs', 'Серверных вызовов': 'Server calls', 'за 5 мин': 'per 5 min',
  'за 5 мин, все базы': 'per 5 min, all infobases', 'Ждут блокировок': 'Waiting for locks', 'СУБД + управляемые': 'DBMS + managed',
  'Информационные базы': 'Infobases', 'Самый долгий вызов': 'Longest call', 'Сеансы по типу клиента': 'Sessions by client type',
  'Тонкий': 'Thin', 'Толстый': 'Thick', 'Web': 'Web', 'Конфигуратор': 'Designer', 'Фоновые': 'Background',
  'Спящие': 'Hibernated', 'Прочие': 'Other', 'Нагрузка за 5 мин': 'Load per 5 min', 'Вызовов': 'Calls',
  'Время вызовов': 'Call time', 'Время СУБД': 'DBMS time', 'Сервисы кластера': 'Cluster services', 'Память': 'Memory',
  'Память вызова, max': 'Call memory, max', 'Трафик за 5 мин': 'Traffic per 5 min', 'Клиент ↔ сервер': 'Client ↔ server',
  'Сервер ↔ СУБД': 'Server ↔ DBMS', 'Чтение с диска': 'Disk read', 'Запись на диск': 'Disk write',
  'Серверные вызовы за 5 мин': 'Server calls per 5 min', 'вызовов': 'calls', 'Время за 5 мин': 'Time per 5 min',
  'вызовы': 'calls', 'СУБД': 'DBMS', 'клиент': 'client', 'Ожидание блокировок': 'Lock waits', 'управляемые': 'managed',
  'CPU за 5 мин, %': 'CPU per 5 min, %', 'активные': 'active', 'пользователи': 'users', 'всего': 'total',
  'Метрики кластера 1С': '1C cluster items', '(шаблон уровня сервера)': '(server-level template)', 'Метрика': 'Item',
  'Значение': 'Value', 'Состояние': 'State', 'не поддерживается': 'not supported', 'Нет метрик 1С': 'No 1C items',
  'Скорее всего, на агенте Zabbix нет UserParameter-скриптов 1С из этого шаблона.': 'Most likely the Zabbix agent lacks the 1C UserParameter scripts of this template.',
  // --- galaxy summary
  '◢ Сводка галактики ◣': '◢ Galaxy summary ◣', 'на связи': 'online', 'включено': 'enabled', 'отключено': 'disabled',
  'Свежие': 'Recent', 'Самая старая:': 'Oldest:', 'Пояса состояния': 'Health belts', 'Самые нагруженные': 'Busiest hosts',
  'Трафик галактики': 'Galaxy traffic', 'Сервисы': 'Services', '👤 Залогинено на хостах': '👤 Logged in on hosts',
  'Пользователи веба': 'Web users', 'Мониторинг': 'Monitoring', 'С ошибками сбора': 'Failing to collect',
  'Очередь >10 мин': 'Queue >10 min', 'связь': 'online', 'трафик': 'traffic', 'нет': 'no',
  // --- units
  'Б': 'B', 'КБ': 'KB', 'МБ': 'MB', 'ГБ': 'GB', 'ТБ': 'TB', 'ПБ': 'PB',
  'бит/с': 'bit/s', 'Кбит/с': 'Kbit/s', 'Мбит/с': 'Mbit/s', 'Гбит/с': 'Gbit/s',
  // multi-line legend paragraphs (compared with collapsed whitespace)
  '. Толщина, число и скорость частиц растут с объёмом (логарифмическая шкала). Ядро — условный «внешний мир», а не реальный узел сети.':
    '. Width, number and speed of particles grow with volume (log scale). The core is a notional “outside world”, not a real network node.',
  'Оповещения: новая проблема (Average и ниже) или новый вход — планета вспыхивает цветом события, свечение и сокращающееся кольцо гаснут за 3 минуты, плашка справа вверху держится столько же. High и Disaster — плашка висит, пока проблема не решена, планета пульсирует красным.':
    'Alerts: a new problem (Average and below) or a new login makes the planet flare in the event colour; the glow and the shrinking ring fade within 3 minutes, and so does the toast at the top right. High and Disaster: the toast stays until the problem is resolved, and the planet pulses red.',
  'Значок под планетой — сколько пользователей сейчас залогинено (Linux: system.users.num, Windows: RDP-сессии, если есть такая метрика).':
    'The badge under a planet is the number of logged-in users (Linux: system.users.num, Windows: RDP sessions, if such an item exists).',
  'Искусственные спутники — обнаруженные сервисы: 🐬 MySQL (бирюзовый, главный сервис), 🐘 PostgreSQL (синий корпус), ▣ 1С (жёлтый), 💽 SMART (серебристый), ⚙ службы (золотистый), ◎ Zabbix (красный). Огонёк: зелёный редко мигает — ОК, жёлтый или красный часто мигает — есть проблемы.':
    'Artificial satellites are detected services: 🐬 MySQL (teal, main service), 🐘 PostgreSQL (blue body), ▣ 1C (yellow), 💽 SMART (silver), ⚙ services (gold), ◎ Zabbix (red). Beacon: slow green blink — OK, fast yellow or red blink — problems.',
  // --- demo alerts
  'Демо: высокая загрузка CPU (>90% в течение 5 мин)': 'Demo: high CPU load (>90% for 5 min)',
  'Демо: диск / заполнен на 98%': 'Demo: disk / is 98% full',
  // --- OS names from the backend
  'ОС неизвестна': 'Unknown OS', 'Raspberry Pi OS': 'Raspberry Pi OS',
};

const ATTRS = ['title', 'placeholder', 'aria-label'];

function translateText(node) {
  const raw = node.nodeValue;
  if (!/[А-Яа-яЁё]/.test(raw)) return;
  const s = raw.trim();
  let en = EN[s];
  if (en === undefined) en = EN[s.replace(/\s+/g, ' ')]; // text wrapped over several source lines
  if (en === undefined) return;
  const lead = raw.match(/^\s*/)[0], trail = raw.match(/\s*$/)[0];
  node.nodeValue = lead + en + trail;
}

function translateEl(el) {
  for (const a of ATTRS) {
    const v = el.getAttribute?.(a);
    if (v && /[А-Яа-яЁё]/.test(v) && EN[v.trim()] !== undefined) el.setAttribute(a, EN[v.trim()]);
  }
}

export function translateTree(root) {
  if (LANG !== 'en' || !root) return;
  if (root.nodeType === Node.TEXT_NODE) { translateText(root); return; }
  if (root.nodeType !== Node.ELEMENT_NODE) return;
  translateEl(root);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (n.nodeType === Node.TEXT_NODE) translateText(n);
    else translateEl(n);
  }
}

// Translate the static page once, then everything the app renders later.
export function startAutoTranslate() {
  if (LANG !== 'en') return;
  document.title = 'Zabbix Galaxy';
  translateTree(document.head);
  translateTree(document.body);
  new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === 'childList') r.addedNodes.forEach(translateTree);
      else if (r.type === 'characterData') translateText(r.target);
      else if (r.type === 'attributes') translateEl(r.target);
    }
  }).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
}
