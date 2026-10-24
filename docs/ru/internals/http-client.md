# HTTP-клиент

Каждая страница, которую отдаёт API, начинается с запроса к t.me. Кроме
заголовков, сервер видит два отпечатка клиента: TLS `ClientHello` (JA3, JA4)
и преамбулу соединения HTTP/2 (отпечаток Akamai: SETTINGS, WINDOW_UPDATE,
приоритет и порядок псевдозаголовков). Если они указывают не на того клиента,
которым представляется `User-Agent`, такой запрос заметить проще, чем запрос
настоящего браузера или честной библиотеки.

## Два клиента { #two-clients }

[`TELEGRAM_CLIENT`](../configuration.md#telegram_client) выбирает `Fetcher`:

| | `browser` (по умолчанию) | `reqwest` |
|---|---|---|
| Библиотека | [wreq](https://crates.io/crates/wreq) 0.16 + [wreq-util](https://crates.io/crates/wreq-util) 0.2, в `rust/telegram-me-native` | [pyreqwest](https://pypi.org/project/pyreqwest/) |
| TLS | BoringSSL, как в Chrome | rustls |
| HTTP/2 | SETTINGS, окно и порядок заголовков профиля | от hyper |
| Заголовки | из профиля | из профиля |
| Соединения | пул HTTP/2, gzip/br/zstd/deflate | то же |

Оба берут заголовки из одного профиля:
[`TELEGRAM_BROWSER_PROFILE`](../configuration.md#telegram_browser_profile)
на [`TELEGRAM_BROWSER_PLATFORM`](../configuration.md#telegram_browser_platform)
(по умолчанию `chrome_149` на `android`) — `User-Agent`, `sec-ch-ua*`,
`accept`, метаданные fetch и их порядок. Ни один заголовок не написан
вручную, поэтому они не могут противоречить друг другу. Только у `browser`
ещё и рукопожатие этого браузера.

Клиент отправляет то же, что браузер на странице канала:

- **навигацию** (`GET /s/<channel>`, `GET /<channel>`): заголовки профиля с
  `accept-language: ru`;
- **XHR «загрузить ещё»** (`POST /s/<channel>?before=…`): вызов jQuery со
  страницы, с `x-requested-with`, `accept: application/json, …`,
  `sec-fetch-mode: cors`, `sec-fetch-site: same-origin`, `origin`, `referer`,
  `priority: u=1, i` и порядком заголовков, который Chromium использует для
  него; порядок снят с браузера на Chromium 152 (Electron).

## Отпечатки { #fingerprints }

Измерено 2026-10-24 через `https://tls.peet.ws/api/all` скриптом
`scripts/fingerprint.py` (три соединения на строку). Эталонная строка —
браузер на Chromium 152 (Electron, BoringSSL как в Chrome), измеренный в тот
же день тем же сервисом. Сборку Google Chrome не измеряли.

| Клиент | JA4 | Отпечаток Akamai для HTTP/2 | Хеш Akamai |
|---|---|---|---|
| `reqwest` до изменения (заголовки Chrome 141, написанные вручную) | `t13d1011h2_61a7ad8aa9b6_0d308c48d2a3` | `2:0;4:2097152;5:16384;6:16384\|5177345\|0\|m,s,a,p` | `9b5dcd077a77c5e324b91d8cc306fd5a` |
| `reqwest` (заголовки профиля) | `t13d1011h2_61a7ad8aa9b6_0d308c48d2a3` | `2:0;4:2097152;5:16384;6:16384\|5177345\|0\|m,s,a,p` | `9b5dcd077a77c5e324b91d8cc306fd5a` |
| `browser` (`chrome_149`, `android`) | `t13d1516h2_8daaf6152771_d8a2da3f94cd` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `52d84b11737d980aef856699f885ca86` |
| Chromium 152 (эталон) | `t13d1516h2_8daaf6152771_806a8c22fdea` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `52d84b11737d980aef856699f885ca86` |

- **`reqwest`** предлагает 10 наборов шифров и 11 расширений, без GREASE, без
  ALPS и сжатия сертификатов: рукопожатие rustls под `User-Agent` Chrome. Его
  преамбула HTTP/2 — от hyper (окно 2 МиБ, псевдозаголовки `m,s,a,p`), такую
  Chrome никогда не отправляет.
- **`browser`** — 15 наборов шифров и 16 расширений Chrome (GREASE,
  `X25519MLKEM768`, ALPS, сжатие сертификатов brotli, GREASE для ECH) и
  преамбула HTTP/2 Chrome. Хеши JA3 меняются от соединения к соединению, как у
  Chrome (он переставляет расширения), поэтому их не сравниваем; JA4 их
  сортирует.
- **Эталон** совпадает с `browser` в части шифров JA4 и во всём отпечатке
  HTTP/2. Последняя часть JA4 отличается: Chromium 152 предлагает ещё три
  алгоритма подписи ML-DSA (`0x0904`–`0x0906`), которых нет в профиле
  `chrome_149`, на три версии старше. Это самый новый профиль, который сейчас
  есть в wreq-util.

Порядок заголовков навигации после псевдозаголовков:

- `reqwest` до изменения: `accept, accept-language, cache-control, dnt,
  priority, sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform, sec-fetch-dest,
  sec-fetch-mode, sec-fetch-site, sec-fetch-user, upgrade-insecure-requests,
  user-agent, accept-encoding` — по алфавиту, и `sec-ch-ua-platform: Android`
  без кавычек;
- `browser` и `reqwest` теперь: `sec-ch-ua, sec-ch-ua-mobile,
  sec-ch-ua-platform, upgrade-insecure-requests, user-agent, accept,
  sec-fetch-site, sec-fetch-mode, sec-fetch-user, sec-fetch-dest,
  accept-encoding, accept-language, priority` — порядок Chrome; эталон
  отправляет тот же порядок (Electron не шлёт `sec-ch-ua*` при навигации).

XHR «загрузить ещё» у `browser` и у эталона:
`content-length, sec-ch-ua-platform, x-requested-with, user-agent, accept,
sec-ch-ua, sec-ch-ua-mobile, origin, sec-fetch-site, sec-fetch-mode,
sec-fetch-dest, referer, accept-encoding, accept-language, priority`.
`reqwest` отправляет заголовки навигации, добавляя в конец
`x-requested-with` и `content-length`.

## Скорость и память { #performance }

Против живого t.me с одной машины (12 загрузок страниц подряд, затем 16
одновременно):

| | Первый запрос | p50 на прогретом пуле | 16 параллельно | Пиковый RSS процесса |
|---|---|---|---|---|
| `reqwest` | 246–296 мс | 98–121 мс | 172 мс\* | 58 МиБ |
| `browser` | 180–281 мс | 93–105 мс | 160–178 мс | 60–61 МиБ |

\* в одном из двух прогонов параллельная пачка заняла 2 с; медленным был t.me,
а не клиент.

В Docker-образе с `ENABLE_FEED=true` после smoke-матрицы воркеры занимали
105–108 МиБ RSS с `browser` и 117–128 МиБ с `reqwest`. Запросы `browser`
выполняются в общем рантайме tokio с двумя рабочими потоками.

## Асинхронный мост { #async-bridge }

`BrowserClient.fetch` возвращает asyncio-future, созданный
[pyo3-async-runtimes](https://crates.io/crates/pyo3-async-runtimes): запрос
выполняется в рантайме tokio без GIL; по завершении блокирующий поток один
раз берёт GIL и передаёт результат через `call_soon_threadsafe` цикла
событий. Отмена asyncio-задачи уничтожает Rust-future, а с ним и запрос. Это
поддерживаемый мост PyO3, поэтому модуль не держит собственную интеграцию с
циклом событий.

## Сборка { #build }

BoringSSL — это C и C++, собираемые cmake (`btls-sys`); bindgen нужен
libclang, а крейт накладывает свои патчи через git. Стадия сборки Docker
ставит `g++ make cmake clang libclang-dev git`; в рабочий образ ничего из
этого не попадает.

| | Холодная сборка, arm64 (нативно) | Холодная сборка, amd64 (эмуляция на arm64) | Образ |
|---|---|---|---|
| до | 121 с | 172 с | 317,1 МБ (arm64), 69,7 МБ (amd64) |
| с wreq | 196 с | 386 с | 324,4 МБ (arm64), 72,2 МБ (amd64) |

В CI arm64 собирается под QEMU, как в `release.yml`. Холодная сборка этого
образа на `ubuntu-latest` заняла 17 мин 26 с на main и 28 мин 12 с с wreq
(+10 мин 46 с). Задание релиза собирает обе платформы за один прогон, поэтому
релиз, который пересобирает крейт, станет примерно на столько же дольше. Кэш
слоёв избавляет от этого релизы, не затрагивающие `rust/`.

## Обновление профиля { #updates }

Chrome выходит раз в четыре недели, и профиль стареет вместе с ним.
Dependabot обновляет `wreq` и `wreq-util` вместе с остальными крейтами
cargo. Когда появится профиль более нового Chrome, обновите значение по
умолчанию `TELEGRAM_BROWSER_PROFILE` (settings, `DEFAULT_BROWSER_PROFILE` в
`telegram/client.py`, три файла `configuration.md`, `.env.example`) и
запустите `scripts/fingerprint.py` для сравнения:

```bash
uv run python scripts/fingerprint.py --runs 3
```
