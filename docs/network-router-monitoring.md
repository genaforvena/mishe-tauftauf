# Router traffic monitoring: proposal (not installed)

Status: proposal only. Nothing is installed or changed on the router. It waits for Rozalia's yes.
Owner of the proposal: the network mind. The operator sends it to Rozalia once, from 09:00 local
(06:00Z). This file is the text to send.

## Русский (для Розалии)

Сейчас окно сети читает роутер только на чтение: кто подключён к Wi‑Fi, сила сигнала, нагрузка,
счётчики ошибок на линии. Это не меняет ни одной настройки. Вопрос в том, стоит ли поставить
небольшую программу, которая будет считать трафик по устройствам (сколько каждое скачало и отдало
за день). Ответ «нет» тоже нормален: окно и так показывает многое.

**Что предлагается.** Одна из стандартных программ для учёта трафика: `nlbwmon` (учёт по клиентам)
или `vnstat` (учёт по интерфейсам). Обе известны, работают в фоне и пишут небольшой файл. Какую
именно — решим после проверки, что есть в репозитории пакетов для этой модели роутера (сейчас не
проверено).

**Что она добавит.** Суточный объём трафика по каждому устройству (только числа: байты, без того,
куда и что передавалось). Окно сможет сказать, например, «какое устройство тянет основной объём
ночью». Сейчас это не видно.

**Что она НЕ трогает.** Никаких изменений в настройках файрвола, DHCP (dnsmasq), сети (network) и
Wi‑Fi (wireless). Маршруты, VPN и Tailscale не меняются. Роутер не перезагружается.

**Место и память.** На роутере свободно около 117 МБ на /overlay и около 235 МБ оперативной памяти
(по данным последнего чтения). Пакет учёта обычно занимает единицы мегабайт и несколько мегабайт
памяти. Точные цифры будут проверены до установки, и если места меньше разумного порога, ставить
не будем.

**Как отключить и убрать одной командой.** (проверить на роутере перед установкой)
`/etc/init.d/nlbwmon stop && /etc/init.d/nlbwmon disable && opkg remove nlbwmon`
(для vnstat — аналогично, имя службы `vnstat`). Данные учёта лежат в своей папке; её тоже удаляется
той же командой, если Розалия попросит.

**Что это даст, чего нельзя увидеть только чтением.** Объём по устройствам за сутки, а не мгновенный
снимок. Сейчас окно показывает счётчики ошибок с момента загрузки роутера, но не скорость их роста.

## English (for the reviewer)

Today the window reads the router only: Wi‑Fi clients, signal, load, and WAN error counters. It
changes nothing. The question is whether to install a small accounting package that records traffic
per device per day.

- **Candidate:** `nlbwmon` (per-client accounting) or `vnstat` (per-interface). Package availability
  for this router model is not yet checked.
- **Adds:** daily byte totals per client, numbers only, no destinations or content.
- **Does not touch:** firewall, dnsmasq, network, wireless config; routing; VPN/sing-box; Tailscale;
  no reboot.
- **Resources (last read):** about 117 MB free on /overlay, about 235 MB RAM available. Both to be
  re-checked before install; a package larger than a set threshold is not installed.
- **Undo (to verify on the router before install):**
  `/etc/init.d/nlbwmon stop && /etc/init.d/nlbwmon disable && opkg remove nlbwmon`
  (vnstat: same pattern with its service name). Its data directory is removed with it on request.
- **Monitoring read-only cannot give:** per-device daily volume, and the trend of the WAN counters.

## Self-limit for any read (already in the window)

The network window's ROUTER LOAD line shows 1-minute load and free RAM for each router read. Reads
stay at one per 5 minutes. A read above load 1.0 or below 120 MB free is shown as over the
self-limit; the probe itself does not yet skip the read when over the limit (next step).

The window's HOST GUARD line says whether the host guard's trip marker
(`/var/lib/mishe-containment/tripped`) is present. A trip makes the router units skip, so the router
readings go STALE; the guard line names that cause. It is a read-only stat; an unreadable marker shows
UNKNOWN, never a clear.
