# Ограничение частоты и proof of work

Две необязательные защиты, которые владелец сервера включает по желанию:
**ограничение частоты запросов** для каждого клиента и **proof of work** (PoW,
доказательство работы). Обе выключены по умолчанию. Обе проверяются в нативном
коде до маршрутизации, поэтому отклонённый запрос не доходит ни до валидации,
ни до кэша, ни до t.me. Проверка занимает намного меньше микросекунды.

Сначала проверяется лимит, затем proof of work. Запросы, отклонённые из-за
отсутствующего или неверного доказательства, тоже засчитываются в лимит — так
ограничено, как быстро можно перебирать варианты.

## Ограничение частоты { #rate-limit }

Включается через [`ENABLE_RATE_LIMIT`](../configuration.md#enable_rate_limit).

- **Алгоритм.** Каждый IP клиента может сделать сразу
  [`RATE_LIMIT_BURST`](../configuration.md#rate_limit_burst) запросов (по
  умолчанию [`RATE_LIMIT_RPM`](../configuration.md#rate_limit_rpm)), а затем ещё
  по одному каждые `60 / RATE_LIMIT_RPM` секунд. Это GCRA (generic cell rate
  algorithm). Его состояние на клиента — одна отметка времени, поэтому память не
  растёт, как бы быстро клиент ни отправлял запросы.
- **По всем воркерам и серверам.** С Redis и
  [`RATE_LIMIT_SHARED`](../configuration.md#rate_limit_shared) (по умолчанию)
  каждый процесс раз в секунду добавляет принятые запросы в поминутный счётчик в
  Redis. Клиент, превысивший лимит там, блокируется везде до конца минуты.
  Запросы никогда не ждут Redis; цена — клиент может превысить общий лимит
  максимум на секунду трафика на каждый процесс. Без Redis каждый процесс
  ограничивает сам по себе.
- **Адрес клиента.** За обратным прокси укажите
  [`FORWARDED_ALLOW_IPS`](../configuration.md#forwarded_allow_ips), чтобы IP
  клиента брался из `X-Forwarded-For`, иначе все клиенты делят лимит прокси.

В каждом ответе есть заголовки:

| Заголовок | Значение |
|---|---|
| `X-RateLimit-Limit` | `RATE_LIMIT_RPM` |
| `X-RateLimit-Remaining` | Сколько запросов клиент ещё может сделать прямо сейчас |

При превышении лимита API отвечает `429`:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 12
X-RateLimit-Limit: 120
X-RateLimit-Remaining: 0
Content-Type: application/json

{"detail":"Rate limit exceeded"}
```

`Retry-After` — сколько секунд осталось до следующего разрешённого запроса.

## Proof of work { #proof-of-work }

Включается через [`ENABLE_POW`](../configuration.md#enable_pow) и
[`POW_SECRET`](../configuration.md#pow_secret) длиной не меньше 32 символов,
одинаковый на всех серверах. После этого каждый запрос должен нести хэш,
который стоил клиенту работы, а серверу проверить его — микросекунды.
Выкачивать API через чужой экземпляр становится дорого, а заваливать его
запросами — ещё дороже.

### Как клиент отвечает { #how-a-client-answers }

1. **Получить задачу.** Вызовите `GET /v1/pow/challenge` или возьмите задачу
   из любого отказа (см. ниже):

    ```json
    {
      "challenge": "1.1792860000.18.3q2-7w8_aBcDeFgHiJkL.Zm9vYmFyYmF6cXV4cXV1eA",
      "difficulty": 18,
      "expires": 1792860000,
      "algorithm": "sha256",
      "header": "X-PoW",
      "input": "{challenge}\\n{nonce}\\n{METHOD} {target}"
    }
    ```

2. **Решить её для запроса.** Найдите `nonce` (1–64 символа из
   `A–Z a–z 0–9 - _`), такой что

    ```text
    SHA-256(challenge + "\n" + nonce + "\n" + METHOD + " " + target)
    ```

    начинается с `difficulty` нулевых бит. `target` — путь и query запроса ровно
    в том виде, в котором они отправляются, например `/v1/body/durov?position=5`.
    В среднем на это уходит `2^difficulty` хэшей.

3. **Отправить** в заголовке `X-PoW` в виде `challenge:nonce`.

Используйте одну задачу для всех запросов, пока она не истечёт
([`POW_CHALLENGE_TTL`](../configuration.md#pow_challenge_ttl), по умолчанию 5
минут), с новым nonce для каждого запроса. Решение принимается один раз: оно
привязано к своему методу и target, а каждый процесс сервера помнит принятые
решения, пока их задача не истечёт.

=== "Python"

    ```python
    import hashlib
    import itertools

    import requests

    API = "https://api.example.com"


    def solve(challenge: str, difficulty: int, method: str, target: str) -> str:
        prefix = hashlib.sha256(f"{challenge}\n".encode())
        suffix = f"\n{method} {target}".encode()
        for n in itertools.count():
            nonce = format(n, "x")
            digest = prefix.copy()
            digest.update(nonce.encode() + suffix)
            value = int.from_bytes(digest.digest(), "big")
            if value >> (256 - difficulty) == 0:
                return nonce
        raise AssertionError


    pow_ = requests.get(f"{API}/v1/pow/challenge", timeout=10).json()
    target = "/v1/body/durov"
    nonce = solve(pow_["challenge"], pow_["difficulty"], "GET", target)
    response = requests.get(API + target, headers={"X-PoW": f"{pow_['challenge']}:{nonce}"}, timeout=30)
    ```

=== "JavaScript"

    ```javascript
    const API = "https://api.example.com";

    async function solve(challenge, difficulty, method, target) {
      const encoder = new TextEncoder();
      for (let n = 0; ; n++) {
        const nonce = n.toString(16);
        const data = encoder.encode(`${challenge}\n${nonce}\n${method} ${target}`);
        const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", data));
        let bits = 0;
        for (const byte of hash) {
          if (byte === 0) { bits += 8; continue; }
          bits += Math.clz32(byte) - 24;
          break;
        }
        if (bits >= difficulty) return nonce;
      }
    }

    const pow = await (await fetch(`${API}/v1/pow/challenge`)).json();
    const target = "/v1/body/durov";
    const nonce = await solve(pow.challenge, pow.difficulty, "GET", target);
    const response = await fetch(API + target, {
      headers: { "X-PoW": `${pow.challenge}:${nonce}` },
    });
    ```

    `crypto.subtle` асинхронный, поэтому каждый хэш стоит одного `await`. Для
    высокой сложности используйте синхронную реализацию SHA-256 в Web Worker.

### Отказы { #rejections }

Запрос без действительного доказательства получает JSON-ошибку со **свежей
задачей**, так что клиент может решить её и повторить запрос без лишнего
обращения к серверу:

```json
{
  "detail": "Proof of work required",
  "pow": {
    "code": "missing",
    "challenge": "1.1792860000.18.…",
    "difficulty": 18,
    "expires": 1792860000,
    "algorithm": "sha256",
    "header": "X-PoW",
    "input": "{challenge}\\n{nonce}\\n{METHOD} {target}"
  }
}
```

Та же задача приходит и в заголовках `X-PoW-Challenge`, `X-PoW-Difficulty` и
`X-PoW-Expires`. Эти заголовки, `Retry-After` и заголовки лимита доступны
браузерам через CORS.

| Статус | `code` | Значение |
|---|---|---|
| `428` | `missing` | Нет заголовка `X-PoW` |
| `428` | `expired` | Срок задачи истёк |
| `428` | `too_easy` | Задача требует меньше работы, чем сервер требует сейчас |
| `400` | `malformed` | Не `challenge:nonce` или поле вне допустимого диапазона |
| `403` | `forged` | Задачу выдал не этот сервер или она выдана другому IP |
| `403` | `insufficient_work` | В хэше не хватает нулевых бит — обычно метод или target отличаются от того, что хэшировалось |
| `403` | `replayed` | Это решение уже использовано |

### Без доказательства { #no-proof-needed }

Некоторые запросы обслуживаются без доказательства: документация (`/`,
`/openapi.json`, `/static/…`), `/healthz`, сам `/v1/pow/challenge`, страница
GraphiQL (`GET /graphql` без запроса) и CORS-preflight (`OPTIONS`). Запросам
GraphQL (`POST /graphql`) доказательство нужно, как и любым другим, с методом и
target `POST /graphql`.

### Безопасность { #security-notes }

- **Задачи не хранят состояния.** Задача имеет вид
  `1.<expires>.<difficulty>.<salt>.<tag>`, где `tag` — HMAC-SHA256 остальных
  полей (и IP клиента при [`POW_BIND_IP`](../configuration.md#pow_bind_ip)).
  Выдача задачи ничего не хранит, поэтому запросы задач не могут исчерпать
  сервер, а сам эндпоинт задач ограничивается по частоте, как и всё остальное.
- **Проверки идут от дешёвых к дорогим:** размер (заголовок не длиннее 256
  байт), синтаксис, срок, сложность, `tag` (сравнение за постоянное время), хэш
  и только потом кэш повторов. До кэша доходят лишь действительно верные
  доказательства.
- **Повышение сложности** действует сразу: более лёгкие задачи отклоняются с
  `too_easy`, а в ответе приходит новая.
- **Повторы.** Каждый процесс помнит принятые решения до истечения их задачи
  (до 500 000). Между процессами одно решение можно использовать по разу на
  процесс, но только для того же метода и target, ответ на которые обычно уже в
  кэше.
- **Привязка к IP** не даёт решать задачи централизованно и раздавать их
  ферме. Выключайте её, только если адреса клиентов меняются в пределах
  `POW_CHALLENGE_TTL` — например, в мобильных сетях или с приватными адресами
  IPv6.
- **Тело запроса не хэшируется.** `POST /v1/feed` и `POST /v1/previews`
  привязываются только к target; правило одноразовости всё равно делает
  каждый запрос стоимостью в одно решение.

### Выбор сложности { #choosing-the-difficulty }

Ожидаемая работа — `2^difficulty` хэшей на запрос, каждый лишний бит удваивает
её.

| `POW_DIFFICULTY` | Нативный код (≈ 15 млн хэшей/с на ядро) | Python `hashlib` (≈ 1,5 млн/с) |
|---|---|---|
| 16 | ~4 мс | ~45 мс |
| 18 (по умолчанию) | ~17 мс | ~0,2 с |
| 20 | ~70 мс | ~0,7 с |
| 22 | ~0,3 с | ~3 с |

SHA-256 дёшев на GPU: PoW поднимает цену злоупотреблений, но не останавливает
решительного атакующего с оборудованием. Используйте его вместе с ограничением
частоты.
