# Docker-образ

## Реестры и теги { #registries-and-tags }

Образ собирается для `linux/amd64` и `linux/arm64` и публикуется в:

- `ghcr.io/koval01/telegram-me`
- `docker.io/koval01/telegram-me`

| Тег | Указывает на |
|---|---|
| `latest` | последнюю сборку `main` |
| `sha-<12 hex>` | конкретный коммит — используйте для фиксации версии и для отката |
| `2.0.0`, `2.0`, `2` | релизы |

Каждый образ содержит SBOM и аттестацию происхождения сборки, а также OCI-метки
с версией и коммитом.

## Запуск { #run }

```bash
docker run -d --name telegram-me \
  -p 8000:3000 \
  --restart unless-stopped \
  --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  -e REDIS_URL=redis://cache.internal:6379/0 \
  ghcr.io/koval01/telegram-me:2.0.0
```

Флаги в середине — необязательное усиление защиты; образ рассчитан на работу
со всеми ними.

## Что внутри { #what-is-inside }

- Python 3.14 на Debian (slim), зафиксированные зависимости в `/app/.venv` и
  больше ничего — ни компиляторов, ни curl.
- Работает от непривилегированного пользователя `app` (uid/gid `10001`).
- `ENTRYPOINT ["telegram-me"]`, `CMD ["serve"]`: сервер — это PID 1 и
  получает `SIGTERM` напрямую, поэтому `docker stop` завершается гораздо
  быстрее секунды после того, как закончатся текущие запросы.
- `HEALTHCHECK` вызывает `/healthz` каждые 30 с встроенным Python.
- Порт `3000`.

## Команды { #commands }

```bash
docker run --rm ghcr.io/koval01/telegram-me version          # print the version
docker run -d ... ghcr.io/koval01/telegram-me worker         # feed refresher only
```

## Ресурсы { #resources }

Измерено на 2.0.0 (arm64), см.
[Производительность](../internals/performance.md#memory):

| `WEB_CONCURRENCY` | В простое | После лёгкого трафика |
|---|---|---|
| 1 | ~110 МБ (процесс сервера + один воркер) | ~145 МБ |
| 2 | ~180 МБ | ~225 МБ |

Закладывайте примерно 40 МБ на процесс сервера и 70–110 МБ на воркер, плюс до
`MEMORY_CACHE_MAX_MB` на воркер для кэша внутри процесса (воркер под высокой
нагрузкой без Redis держится около 170 МБ при значении по умолчанию 64; с
Redis он держит только записи за последние `MEMORY_CACHE_MAX_TTL` секунд).
Освобождённая
память возвращается системе каждые
[`MEMORY_TRIM_INTERVAL`](../configuration.md#memory_trim_interval) секунд.
[`WORKERS_MAX_RSS_MB`](../configuration.md#workers_max_rss_mb) добавляет
страховку. `WEB_CONCURRENCY` по умолчанию равен числу CPU, доступных
контейнеру (не больше 4); задайте его явно, если ограничиваете CPU.

## Сборка { #building }

```bash
docker build -t telegram-me:dev .
docker build --build-arg PYTHON_VERSION=3.13 -t telegram-me:py313 .
```

Сборка использует кэш-монтирования BuildKit для uv; зависимости вынесены в
отдельный слой, поэтому изменения только в коде пересобираются за секунды.
