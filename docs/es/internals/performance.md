# Rendimiento

Todas las cifras de abajo se midieron en la misma máquina (un portátil Apple
Silicon, Python 3.14) durante la preparación de 2.0, con t.me sustituido por
páginas grabadas para medir solo el propio servicio. Tómalas como relativas, no
como absolutas.

## 1.x → 2.0, dentro del proceso { #1x-20-in-process }

Latencia mediana de una solicitud, con el origen servido desde fixtures:

| Solicitud | 1.x | 2.0 | |
|---|---|---|---|
| Analizar cinco páginas de canal (suma) | 33.7 ms | 25.0 ms | −26 % |
| `/v1/body/durov`, sin caché | 14.5 ms | 7.1 ms | 2× más rápido |
| `/v1/body/tginfo`, sin caché | 24.5 ms | 15.5 ms | 1.6× más rápido |
| `/v1/body/durov`, en caché | — (sin caché de páginas) | 0.9 ms | 16× más rápido |
| `/v1/body/tginfo`, en caché | — | 1.1 ms | 22× más rápido |
| `/v1/feed`, 5 canales, páginas en caché | 81.9 ms | 5.4 ms | 15× más rápido |

## Caché de dos niveles { #two-level-cache }

Una solicitud repetida costaba un viaje de ida y vuelta a Redis cuando
`REDIS_URL` estaba definida: el Upstash de producción está a ~90 ms por TLS,
así que casi todo el tiempo de una respuesta en caché era el `GET`. Ahora cada
worker responde primero desde su caché dentro del proceso (Rust, moka) y solo
va a Redis en un fallo (consulta [Caché](caching.md#levels)).

Mediana / p95 de 200 solicitudes repetidas para `@durov` (un worker, ASGI
llamado directamente, origen servido desde fixtures, Apple serie M,
Python 3.13). El Redis a 90 ms es un Valkey 9.1 local detrás de un proxy que
retrasa cada dirección 45 ms; "caché" es el tiempo dedicado a `cache.get` por
solicitud:

| Solicitud | Redis 90 ms: 2.0.0 | Redis 90 ms: ahora | Redis local: 2.0.0 | Redis local: ahora | Sin Redis: 2.0.0 | Sin Redis: ahora |
|---|---|---|---|---|---|---|
| `/v1/body/durov` | 95.0 / 97.4 ms (caché 94.3) | 0.10 / 0.12 ms (caché 0.04) | 0.35 / 0.69 ms | 0.10 / 0.13 ms | 0.15 / 0.16 ms | 0.10 / 0.11 ms |
| `/v1/post/durov/<id>` | 96.7 / 98.5 ms (caché 94.7) | 0.25 / 0.28 ms (caché 0.04) | 0.88 / 1.08 ms | 0.25 / 0.36 ms | 0.29 / 0.33 ms | 0.25 / 0.26 ms |
| `/v1/more/durov/before/<id>` | 95.0 / 96.1 ms (caché 94.4) | 0.09 / 0.10 ms (caché 0.03) | 0.34 / 0.54 ms | 0.09 / 0.12 ms | 0.11 / 0.12 ms | 0.09 / 0.10 ms |
| `/v1/preview/durov` | 94.4 / 96.0 ms (caché 93.9) | 0.08 / 0.08 ms (caché 0.003) | 0.30 / 0.53 ms | 0.08 / 0.11 ms | 0.08 / 0.09 ms | 0.08 / 0.08 ms |
| Carga mixta, 3 000 solicitudes, 8 clientes | 51.7 s | 33.3 s | 2.31 s | 2.33 s | 2.27 s | 2.04 s |
| RSS máximo de esa ejecución | 101 MiB | 120 MiB | 135 MiB | 166 MiB | 153 MiB | 152 MiB |

- La primera lectura de una clave en un worker sigue costando un viaje de ida
  y vuelta a Redis (`GET` y `PTTL` en un solo pipeline), como antes; también
  una escritura. La carga mixta es sobre todo de primeras lecturas (cada
  solicitud nombra un alias de canal nuevo), por eso gana menos.
- Sin Redis, la caché nativa sustituye al LRU de Python: un acierto ya no se
  descomprime en Python (`/v1/body` 0.15 → 0.10 ms) y una página de 95 KB se
  guarda en 83 µs en lugar de 461 µs (zlib en 3.13; el zstd de Python 3.14 se
  acerca más).
- Con Redis, cada worker ahora también guarda lo que leyó o escribió en los
  últimos `MEMORY_CACHE_MAX_TTL` (10) segundos, limitado por
  `MEMORY_CACHE_MAX_MB`: +19–31 MiB de RSS máximo en esta ejecución, en la que
  todo se leyó en menos de 10 s.
- Se midió y descartó liberar el GIL en lecturas y escrituras: junto a dos
  hilos de Python que consumen CPU (pydantic en los hilos de análisis), una
  lectura de 95 KB tardó 6.3 ms (p50) con el GIL liberado y 38 µs con él
  retenido.

## Montaje nativo del feed { #native-feed-assembly }

Un feed decodificaba cada página en caché en objetos de Python, construía una
entrada del clasificador por publicación y serializaba el resultado con
pydantic, en el event loop y con el GIL retenido: ~41 ms de un feed de 100
canales. Ahora es una sola llamada a `telegram_me_native.build_feed` con el
GIL liberado (entran los bytes de las páginas en caché, salen los bytes de la
respuesta). `scripts/audit.py`, dentro del proceso, en la misma máquina, feeds
de 100 canales con 2 000 publicaciones:

| | Antes | Después |
|---|---|---|
| `POST /v1/feed`, 100 canales | 83.7 ms | 42.7 ms |
| CPU de ese feed en un núcleo (`RAYON_NUM_THREADS=1`) | 131 ms | 106 ms |
| Asignaciones de Python de ese feed | 20.8 MiB | 9.8 MiB |
| `/v1/body` en caché junto a un bucle de feeds, p50 / p99 | 15.2 / 61.6 ms | 2.3 / 10.5 ms |
| RSS máximo de un worker que sirve 200 feeds así (Linux) | 220 MiB | 218 MiB (ruido) |

Detalles, costes por endpoint y lo que se mueve a continuación:
[Hoja de ruta hacia Rust](rust-roadmap.md).

## Agrupación de solicitudes { #request-coalescing }

`scripts/coalescing_load.py` arranca 6 procesos worker (piensa en dos
servidores de tres workers de granian) sobre un mismo Valkey, cada uno con 32
clientes que llaman directamente a su app. Cada cliente elige al azar entre
las mismas 37 solicitudes: cada canal grabado a través de `/v1/body` (la más
reciente y alrededor de una publicación), `/v1/post`, `/v1/more` en ambos
sentidos, tres vistas previas, un feed de 6 canales y una consulta GraphQL con
un body y el feed. t.me se sustituye por páginas grabadas que se responden tras
150 ms; `CACHE_MODE=normal` (10 s), así que las 27 solicitudes distintas al
origen caducan dos veces en una ejecución de 25 s. Dos ejecuciones de cada
caso, Apple serie M con 8 núcleos:

| | main (con el feed nativo) | este cambio, `SHARED_COALESCING=false` | este cambio |
|---|---|---|---|
| Solicitudes al origen, Valkey local | 417 / 412 | 421 / 428 | **75 / 75** |
| Solicitudes al origen, Valkey a 90 ms | 434 / 422 | 414 / 413 | **75 / 75** |
| Por solicitud distinta al origen | 15.3–16.1 | 15.3–15.9 | **2.8** (una vez por periodo de TTL) |
| Solicitudes servidas en 25 s, local | 117 k / 117 k | 530 k / 537 k | 548 k / 533 k |
| CPU por solicitud, local | 1.41 / 1.43 ms | 0.30 / 0.29 ms | 0.29 / 0.29 ms |
| CPU por solicitud, 90 ms | 1.43 / 1.41 ms | 0.27 / 0.27 ms | 0.27 / 0.27 ms |
| Latencia p99, local | 917 / 902 ms | 206 / 203 ms | 199 / 206 ms |
| Mayor bloqueo del event loop de un worker, local | 845 / 905 ms | 246 / 200 ms | 224 / 228 ms |

- La concesión es lo que elimina los duplicados entre procesos: 75 solicitudes
  al origen es el mínimo para esta ejecución (27 distintas, renovadas como
  mucho dos veces), y se mantiene con Redis a 90 ms. Sin ella, cada proceso
  descarga cada página por su cuenta, como antes.
- Compartir el renderizado es lo que recorta la CPU: los feeds idénticos se
  montan una vez (y, para GraphQL, se decodifican una vez) en lugar de una vez
  por solicitud, `/v1/post` se guarda en caché ya renderizado y los fallos
  concurrentes de `/v1/body` se renderizan una vez. La CPU liberada se dedicó a
  servir 4–5 veces más solicitudes en el mismo tiempo, con bloqueos del event
  loop más cortos.
- Un comando de Redis que agota su tiempo de espera abre el circuit breaker
  del worker; ese worker descarga entonces por su cuenta (una descarga por
  clave, como antes de la concesión) hasta que se cierra. Se vio en desarrollo
  cuando el event loop de un worker se bloqueó más de 2 s (antes de que los
  feeds de GraphQL se decodificaran una sola vez), no en las ejecuciones de
  arriba.

La ruta crítica en caché no cambia (un worker, mediana de 2 500 aciertos, ASGI
llamado directamente):

| Acierto | main | este cambio |
|---|---|---|
| `/v1/body/durov` | 0.101 ms | 0.101 ms |
| `/v1/more/durov/before/<id>` | 0.091 ms | 0.091 ms |
| `/v1/post/durov/<id>` | 0.242 ms | **0.067 ms** (en caché ya renderizado) |

La admisión son unas pocas llamadas nativas solo en un fallo de caché; un
acierto añade una llamada nativa que compara la antigüedad de la entrada con su
punto de renovación.

## Análisis de páginas: Python + lexbor → Rust { #page-parsing-python-lexbor-rust }

Tiempo mediano para analizar una página de canal grabada
(`scripts/benchmark.py`):

| Página | Python + selectolax/lexbor | Rust (html5ever) |
|---|---|---|
| @durov (20 publicaciones, muchos emoji personalizados) | 4.9 ms | 3.6 ms |
| @tginfo (20 publicaciones largas, muchos enlaces) | 12.4 ms | 6.3 ms |

La diferencia mayor se nota bajo carga: el parser de Rust libera el GIL, así
que las solicitudes concurrentes analizan páginas en paralelo en lugar de
hacer cola en un solo núcleo (24 páginas en 24 hilos: 111 ms → 67 ms en 8
núcleos, antes de las optimizaciones de arriba).

## El event loop durante un ciclo del actualizador { #event-loop-under-a-refresher-tick }

Retardo del event loop de un worker con Redis (Valkey) y el actualizador
integrado, con una carga dentro del proceso de 8 clientes × 3 000 solicitudes
dos veces y, entre ambas, un ciclo del actualizador que refresca los ~200
canales que la primera pasada hizo populares:

| | Retardo máximo | Retardo mediano | Duración | Aperturas del breaker |
|---|---|---|---|---|
| Análisis en el event loop, ciclo sin límite | 0.87–1.31 s | 9 ms | 32–36 s | 1 por ejecución |
| 2 hilos de análisis, ≤ 16 actualizaciones a la vez | 25–122 ms | 1.1 ms | 13 s | ninguna |

Cada descarga terminada analizaba su página (~3 ms en nativo) y la validaba y
renderizaba (~0.5–2 ms en pydantic) directamente en el event loop. Cuando las
descargas de un ciclo terminaban juntas, se ejecutaban una tras otra durante
alrededor de un segundo, más que `REDIS_TIMEOUT`, así que un comando de Redis
en curso agotaba su tiempo de espera y abría el circuit breaker durante 30 s.
Ahora las páginas se analizan en dos hilos dedicados: el executor por defecto
(hasta `cpu + 4` hilos) no era más rápido y, al competir con el event loop por
el GIL, triplicaba su retardo p99 (~21 ms en lugar de ~7 ms). El actualizador
refresca como mucho 16 canales a la vez, lo que además deja huecos hacia el
origen para las solicitudes de los usuarios.

## Cliente HTTP saliente { #outgoing-http-client }

2 000 solicitudes HTTPS de una página de 148 KB, 32 concurrentes, contra un
servidor TLS local:

| Cliente | Solicitudes/s | CPU por solicitud | Memoria máxima |
|---|---|---|---|
| httpx, HTTP/2 (1.x) | 584 | 1.31 ms | 219 MB |
| httpx, HTTP/1.1 | 378 | 1.85 ms | 151 MB |
| aiohttp | 5 842 | 0.17 ms | 71 MB |
| curl_cffi | 4 444 | 0.23 ms | 58 MB |
| httpr (Rust) | 9 308 | 0.23 ms | 53 MB |
| **pyreqwest (Rust)**: 2.0 | **10 563** | **0.15 ms** | **51–64 MB** |

Además, 1.x creaba un cliente nuevo (y un nuevo handshake TLS) para cada
solicitud a t.me.

## Servidor HTTP { #http-server }

Un worker, 64 conexiones, 10 s (`wrk`), la misma aplicación:

| Endpoint | uvicorn + httptools + uvloop | granian: 2.0 |
|---|---|---|
| `/healthz` | 15 774 sol./s, p99 7.97 ms | **21 120 sol./s, p99 3.23 ms** |
| `/v1/body/durov` (en caché, 91 KB) | 4 088 sol./s, p99 23.9 ms | **4 365 sol./s**, p99 31.4 ms |

Prerrenderizar las respuestas en caché elevó el rendimiento de `/v1/body` en
caché de ~850 a ~4 350 solicitudes por segundo en un worker.

## Apagado y memoria { #shutdown-and-memory }

| | 1.x | 2.0 |
|---|---|---|
| `docker stop` | 10 s y después `SIGKILL` (uvicorn no era el PID 1) | 0.3 s, código de salida 0 |
| Redis inaccesible, `/v1/preview` | 10 s por solicitud | un tiempo de espera de 0.5 s y después sin caché |
| Memoria del contenedor, 1 worker, en reposo | — | ~110 MB (consulta [Memoria](#memory)) |

## Memoria { #memory }

Medido en arm64 con la imagen de Docker (`docker stats`, un worker salvo que se
indique otra cosa), antes y después del trabajo sobre la memoria que se hizo
para 2.0. «Ligero» son unos cientos de solicitudes reales sobre seis canales;
«intenso» son 2 × 20 000 solicitudes desde 8 clientes concurrentes sobre 1 200
canales distintos, con t.me sustituido por las páginas grabadas.

| Escenario | Antes | Después |
|---|---|---|
| En reposo | 146–153 MiB | 110 MiB |
| Tráfico ligero | 185 MiB | 143 MiB |
| `WEB_CONCURRENCY=2`, en reposo / tráfico ligero | 217 / 278 MiB | 180 / 224 MiB |
| Tráfico intenso, caché en memoria (límite de 64 MB) | 458 MiB | 229–253 MiB |
| Tráfico intenso, Redis, durante / 15 s después | 296–302 / 299 MiB | 190–263 / 155 MiB |
| Proceso principal de granian (memoria privada) | 65 MiB | 29 MiB |

Qué cambió y por qué:

- **La caché en memoria contaba caracteres, no bytes.** Los valores eran `str`
  de Python; un solo emoji hace que CPython guarde toda la cadena con cuatro
  bytes por carácter, así que una página renderizada de 89 KiB en UTF-8
  ocupaba 353 KiB, y el límite de 64 MB permitía a un worker retener ~260 MiB
  de caché. Ahora los valores son bytes UTF-8, comprimidos con zstd nivel 1
  (zlib en Python 3.13), y el límite cuenta el valor comprimido, la clave y
  los datos de control (~230 bytes por entrada, medido):

    | Página | Como `str` | UTF-8 | En la caché ahora |
    |---|---|---|---|
    | @durov, renderizada | 353 KiB | 89 KiB | 21 KiB |
    | @tginfo, renderizada | 700 KiB | 233 KiB | 43 KiB |
    | @stranaua, renderizada | 93 KiB | 59 KiB | 13 KiB |

    Descomprimir una página (25–120 µs) es más rápido de lo que era codificar
    la misma `str` a UTF-8 en cada acierto (27–306 µs), y las respuestas de
    Redis también se leen como bytes.

- **glibc retenía la memoria liberada.** Las respuestas se construyen a partir
  de objetos grandes de vida corta y, tras una ráfaga, el heap de glibc se
  quedaba en su máximo: con Redis, un worker retenía 97 MiB de heap, de los
  que solo 15 MiB estaban en uso. Ahora los workers llaman a `malloc_trim(0)`
  cada [`MEMORY_TRIM_INTERVAL`](../configuration.md#memory_trim_interval)
  segundos (alrededor de 1 ms por llamada), lo que en ese caso devolvió
  ~80 MiB.
- **El proceso principal de granian importaba la aplicación** (FastAPI,
  GraphQL, Redis, el cliente HTTP, la extensión nativa) solo para supervisar a
  los workers. Ahora la importa en la fábrica de workers.
- **FastAPI ejecutaba las dependencias en un pool de hilos** (`def` simples),
  lo que mantenía un hilo adicional, con su propia arena de malloc, en cada
  worker. Ahora son `async def`.

Evaluado y no adoptado (tráfico intenso, medido cuando el parser todavía era
Python; compara las filas entre sí):

| Opción | Tráfico intenso, caché en memoria | Veredicto |
|---|---|---|
| glibc (por defecto) | 205 MiB | se mantiene |
| `MALLOC_ARENA_MAX=2` | 202 MiB | dentro del ruido |
| glibc con `MALLOC_MMAP_THRESHOLD_` / `MALLOC_TRIM_THRESHOLD_` fijos | 211 MiB | dentro del ruido; con Redis recuperaba solo la mitad de lo que recupera `malloc_trim` |
| jemalloc (`LD_PRELOAD`), por defecto / decaimiento de 1 s / 2 arenas | 212 / 206 / 200 MiB, +5–17 MiB en reposo | sin mejora |
| `PYTHONMALLOC=malloc` + jemalloc | 200–227 MiB, +15 MiB en reposo | sin mejora |
| mimalloc | 256 MiB | peor |
| tcmalloc | 252 MiB | peor |
| Workers creados por fork desde un proceso principal precargado + `gc.freeze()` (2 workers) | 273 en lugar de 333 MiB | no adoptado: granian hace fork desde un supervisor multihilo («use of fork() may lead to deadlocks»), así que los workers se siguen creando con spawn |

El análisis no retiene memoria: las páginas se analizan de forma nativa y se
devuelven como una única cadena JSON, y las pruebas de regresión de memoria no
muestran crecimiento tras cientos de análisis sin caché. `WEB_CONCURRENCY`,
`MEMORY_CACHE_MAX_MB` y `WORKERS_MAX_RSS_MB` conservan sus valores por
defecto: con una contabilidad real, 64 MB de caché alojan ahora entre 4 y 16
veces más páginas que antes dentro de un presupuesto fijo.

### Protección frente a regresiones { #guarding-against-regressions }

`tests/test_memory.py` ejecuta miles de solicitudes a través de la app y falla
cuando la memoria sigue creciendo tras un calentamiento o la caché supera su
límite; detecta tanto una página analizada que se pierde en cada solicitud
como memoria nativa que se pierde.
`rust/telegram-me-native/tests/cache_memory.rs` cuenta cada asignación de la
caché dentro del proceso y falla cuando su contabilidad ya no las cubre. Para
ver las cifras:

```bash
uv run python scripts/memory_profile.py --requests 20000 --channels 200
```

## Reproducir { #reproducing }

```bash
uv run python scripts/benchmark.py   # in-process, recorded pages, no network
uv run python scripts/audit.py --stages   # per endpoint (wall, CPU, allocations) and per stage
docker run -d --name tme-valkey -p 6390:6379 valkey/valkey:8
uv run python scripts/coalescing_load.py --redis redis://127.0.0.1:6390/0
```

Para el rendimiento del servidor, arranca una instancia y apunta un generador
de carga hacia ella:

```bash
make up                              # API + Valkey on :8000
wrk -t2 -c64 -d10s --latency http://127.0.0.1:8000/healthz
```
