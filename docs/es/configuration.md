# Configuración

Todo se configura mediante variables de entorno. También se leen de `.env` y
`.env.local` en el directorio de trabajo (los archivos posteriores tienen
prioridad, y las variables de entorno reales tienen prioridad sobre ambos). Los
nombres no distinguen mayúsculas de minúsculas; los booleanos aceptan
`true/false`, `1/0`, `yes/no`, `on/off`.

La fuente de verdad es
[`settings.py`](https://github.com/koval01/telegram-me/blob/main/src/telegram_me/settings.py).

!!! tip "Presets"
    `compose.yaml` carga uno de los
    [`deploy/presets/*.env`](https://github.com/koval01/telegram-me/tree/main/deploy/presets)
    (`standard`, `full`, `minimal`) antes de tu `.env`. Consulta
    [Inicio rápido](getting-started/quickstart.md#2-docker-compose-with-a-shared-cache-recommended).

## Resumen { #overview }

| Variable | Por defecto | Grupo |
|---|---|---|
| [`HOST`](#host) | `0.0.0.0` | Servidor |
| [`PORT`](#port) | `3000` | Servidor |
| [`WEB_CONCURRENCY`](#web_concurrency) | número de CPU, máx. 4 | Servidor |
| [`FORWARDED_ALLOW_IPS`](#forwarded_allow_ips) | `127.0.0.1` | Servidor |
| [`ACCESS_LOG`](#access_log) | `false` | Servidor |
| [`GRACEFUL_TIMEOUT`](#graceful_timeout) | `10` | Servidor |
| [`WORKERS_MAX_RSS_MB`](#workers_max_rss_mb) | — | Servidor |
| [`METRICS_PORT`](#metrics_port) | — | Servidor |
| [`DISABLE_DOCS`](#disable_docs) | `false` | API |
| [`CORS_ALLOW_ORIGINS`](#cors_allow_origins) | `["*"]` | API |
| [`PUBLIC_URL`](#public_url) | — | API |
| [`DOCS_SITE_DIR`](#docs_site_dir) | — (imagen: `/app/docs`) | API |
| [`ENABLE_PREVIEWS`](#enable_previews) | `true` | Funciones |
| [`ENABLE_FEED`](#enable_feed) | `false` | Funciones |
| [`ENABLE_RATE_LIMIT`](#enable_rate_limit) | `false` | Protección |
| [`RATE_LIMIT_RPM`](#rate_limit_rpm) | `120` | Protección |
| [`RATE_LIMIT_BURST`](#rate_limit_burst) | = `RATE_LIMIT_RPM` | Protección |
| [`RATE_LIMIT_SHARED`](#rate_limit_shared) | `true` | Protección |
| [`ENABLE_POW`](#enable_pow) | `false` | Protección |
| [`POW_SECRET`](#pow_secret) | — | Protección |
| [`POW_DIFFICULTY`](#pow_difficulty) | `18` | Protección |
| [`POW_CHALLENGE_TTL`](#pow_challenge_ttl) | `300` | Protección |
| [`POW_BIND_IP`](#pow_bind_ip) | `true` | Protección |
| [`ENABLE_GRAPHQL`](#enable_graphql) | `true` | GraphQL |
| [`GRAPHQL_MAX_DEPTH`](#graphql_max_depth) | `10` | GraphQL |
| [`GRAPHQL_MAX_COMPLEXITY`](#graphql_max_complexity) | `1500` | GraphQL |
| [`GRAPHQL_MAX_ALIASES`](#graphql_max_aliases) | `15` | GraphQL |
| [`GRAPHQL_MAX_TOKENS`](#graphql_max_tokens) | `2000` | GraphQL |
| [`GRAPHQL_MAX_BATCH_SIZE`](#graphql_max_batch_size) | `10` | GraphQL |
| [`CACHE_MODE`](#cache_mode) | `normal` | Rendimiento |
| [`PARSER_MODE`](#parser_mode) | `full` | Rendimiento |
| [`MEMORY_TRIM_INTERVAL`](#memory_trim_interval) | `10` | Rendimiento |
| [`TELEGRAM_BASE_URL`](#telegram_base_url) | `https://t.me` | Origen |
| [`TELEGRAM_TIMEOUT`](#telegram_timeout) | `10` | Origen |
| [`TELEGRAM_MAX_CONCURRENCY`](#telegram_max_concurrency) | `32` | Origen |
| [`TELEGRAM_CLIENT`](#telegram_client) | `browser` | Origen |
| [`TELEGRAM_BROWSER_PROFILE`](#telegram_browser_profile) | `chrome_156` | Origen |
| [`TELEGRAM_BROWSER_PLATFORM`](#telegram_browser_platform) | `android` | Origen |
| [`TELEGRAM_SESSION_LIFETIME`](#telegram_session_lifetime) | `14400` | Origen |
| [`UPSTREAM_QUEUE_LIMIT`](#upstream_queue_limit) | 16 × `TELEGRAM_MAX_CONCURRENCY` | Origen |
| [`MAX_WAITING_REQUESTS`](#max_waiting_requests) | `4096` | Origen |
| [`SHARED_COALESCING`](#shared_coalescing) | `true` | Origen |
| [`EARLY_REFRESH_WINDOW`](#early_refresh_window) | `0.1` | Origen |
| [`REDIS_URL`](#redis_url) | — | Caché |
| [`REDIS_TIMEOUT`](#redis_timeout) | `0.5` | Caché |
| [`REDIS_CONNECT_TIMEOUT`](#redis_connect_timeout) | `2` | Caché |
| [`REDIS_HEARTBEAT_INTERVAL`](#redis_heartbeat_interval) | `5` | Caché |
| [`PREVIEW_CACHE_TTL`](#preview_cache_ttl) | `3600` | Caché |
| [`MEMORY_CACHE_MAX_MB`](#memory_cache_max_mb) | `64` | Caché |
| [`MEMORY_CACHE_MAX_TTL`](#memory_cache_max_ttl) | `10` | Caché |
| [`ENABLE_PEERS`](#enable_peers) | `false` | Pares |
| [`PEERS`](#peers) | — | Pares |
| [`PEER_PORT`](#peer_port) | `7946` | Pares |
| [`PEER_ADVERTISE_ADDR`](#peer_advertise_addr) | dirección de la ruta por defecto | Pares |
| [`PEER_ALLOW_CIDR`](#peer_allow_cidr) | redes privadas | Pares |
| [`PEER_SECRET`](#peer_secret) | — | Pares |
| [`PEER_ENCRYPTION`](#peer_encryption) | `false` | Pares |
| [`PEER_TIMEOUT`](#peer_timeout) | `0.05` | Pares |
| [`FEED_FRESH_TTL`](#feed_fresh_ttl) | `60` | Feed |
| [`FEED_STALE_TTL`](#feed_stale_ttl) | `900` | Feed |
| [`FEED_REFRESH_MODE`](#feed_refresh_mode) | `embedded` | Feed |
| [`FEED_REFRESH_MIN_INTERVAL`](#feed_refresh_min_interval) | `30` | Feed |
| [`FEED_REFRESH_MAX_INTERVAL`](#feed_refresh_max_interval) | `600` | Feed |
| [`FEED_REFRESH_TOP`](#feed_refresh_top) | `200` | Feed |
| [`FEED_POPULARITY_HALF_LIFE`](#feed_popularity_half_life) | `3600` | Feed |
| [`FEED_HALF_LIFE_HOURS`](#feed_half_life_hours) | `24` | Feed |
| [`FEED_DIVERSITY`](#feed_diversity) | `0.5` | Feed |
| [`FEED_COLLAPSE_DUPLICATES`](#feed_collapse_duplicates) | `true` | Feed |
| [`LOG_LEVEL`](#log_level) | `INFO` | Logs |
| [`LOG_FORMAT`](#log_format) | `text` | Logs |

## Servidor { #server }

### `HOST`

Interfaz a la que se vincula el servidor HTTP (y el listener de métricas).
Mantén `0.0.0.0` dentro de los contenedores; usa `127.0.0.1` para una
instancia sin contenedor detrás de un proxy inverso local.

### `PORT`

Puerto del servidor HTTP. La imagen de Docker escucha en `3000`; publícalo con
`-p 8000:3000`.

### `WEB_CONCURRENCY`

Número de procesos worker. Por defecto, el número de CPU disponibles para el
proceso, con un máximo de 4. Cada worker tiene su propio bucle de eventos y,
sin Redis, su propia caché en memoria, así que usa Redis cuando ejecutes más
de uno.

### `FORWARDED_ALLOW_IPS`

Direcciones de proxy separadas por comas cuyas cabeceras `X-Forwarded-For` /
`X-Forwarded-Proto` son de confianza, o `*` para confiar en cualquiera (solo
cuando el puerto no es accesible directamente). Esto determina la IP del
cliente que usa el limitador de solicitudes. Ejemplo para nginx en otro host:
`FORWARDED_ALLOW_IPS=10.0.0.5`.

### `ACCESS_LOG`

Registra cada solicitud (`true`); desactivado por defecto para mantener los
logs tranquilos con tasas de solicitudes altas.

### `GRACEFUL_TIMEOUT`

Segundos que un worker puede tardar en terminar las solicitudes en curso tras
`SIGTERM` antes de que se le mate.

### `WORKERS_MAX_RSS_MB`

Recicla un worker cuya memoria residente supere esta cantidad de MiB. Una red
de seguridad para hosts con poca memoria; sin definir por defecto. Un worker
usa ~60 MiB en reposo y ~100 MiB bajo carga, más hasta `MEMORY_CACHE_MAX_MB`
para su caché dentro del proceso, así que un valor de alrededor de
`MEMORY_CACHE_MAX_MB + 150` solo salta ante problemas reales.

### `METRICS_PORT`

Expone las métricas de Prometheus del servidor en este puerto en `/metrics`.
Consulta [Endpoints de servicio](api/service.md#metrics).

## API { #api }

### `DISABLE_DOCS`

Oculta la página de inicio (`/`), la referencia de la API (`/api`),
`/openapi.json`, la documentación (`/docs/`) y `/sitemap.xml` y, en
`/graphql`, el IDE GraphiQL, las consultas `GET` y la introspección del
esquema. `/robots.txt` pasa entonces a prohibirlo todo. Consulta
[Endpoints de servicio](api/service.md).

### `CORS_ALLOW_ORIGINS`

Lista JSON de orígenes de navegador permitidos, p. ej.
`CORS_ALLOW_ORIGINS='["https://example.com"]'`. El valor por defecto `["*"]`
permite cualquier origen.

### `PUBLIC_URL`

Dirección pública de la instancia, esquema y host sin ruta ni barra final,
p. ej. `https://tme.example.com`. Las URL absolutas de `/robots.txt` y
`/sitemap.xml` empiezan por ella. Si no se define, usan la dirección por la que
llegó cada solicitud, que detrás de un proxy solo es correcta si este reenvía
el `Host` original. El [workflow de despliegue](deployment/production.md) la
fija a partir de `PUBLIC_URL` o `PUBLIC_HOST`.

### `DOCS_SITE_DIR`

Directorio de la documentación compilada para `/docs/`
([`scripts/build_docs.py`](https://github.com/koval01/telegram-me/blob/main/scripts/build_docs.py)),
que la aplicación sirve ahí. La imagen de Docker incluye la compilación y fija
`/app/docs`. Sin definir (una instalación desde el código fuente), `/docs/…`
redirige a la misma página de la documentación publicada. Consulta
[Endpoints de servicio](api/service.md#get-docs).

## Funciones { #features }

### `ENABLE_PREVIEWS`

Activa `/v1/preview/{channel}` y `/v1/previews`. Cuando están desactivados
responden `503`.

### `ENABLE_FEED`

Activa `POST /v1/feed` (y su actualizador en segundo plano). **Desactivado por
defecto** porque es, con diferencia, el endpoint más pesado. Cuando está
desactivado responde `503`.

## Protección { #protection }

Ambas están desactivadas por defecto; consulta [Límite de peticiones y prueba de trabajo](api/protection.md) para ver cómo funcionan y qué envían los clientes.

### `ENABLE_RATE_LIMIT`

Límite de peticiones por IP de cliente en todos los endpoints (GCRA): una
ráfaga de [`RATE_LIMIT_BURST`](#rate_limit_burst) peticiones y después
[`RATE_LIMIT_RPM`](#rate_limit_rpm) por minuto. Por encima del límite la API
responde `429` con `Retry-After`. Detrás de un proxy, configura
[`FORWARDED_ALLOW_IPS`](#forwarded_allow_ips) para usar la IP real del cliente.
Las peticiones del propio servidor (por loopback y sin reenviar), como los
health checks y el smoke test del despliegue, no se limitan.

### `RATE_LIMIT_RPM`

Peticiones sostenidas por minuto por IP de cliente.

### `RATE_LIMIT_BURST`

Peticiones que un cliente puede hacer de golpe antes de que se aplique el
ritmo por minuto. Por defecto, `RATE_LIMIT_RPM`.

### `RATE_LIMIT_SHARED`

Con [`REDIS_URL`](#redis_url), cuenta cada cliente en todos los workers y
servidores: cada proceso suma sus contadores en Redis una vez por segundo y
bloquea a los clientes que superan el límite allí. Las peticiones nunca esperan
a Redis. Con `false`, o sin Redis, cada proceso limita por su cuenta.

### `ENABLE_POW`

Exige una [prueba de trabajo](api/protection.md#proof-of-work) en cada petición
(salvo la documentación, `/healthz` y el endpoint de desafíos). Requiere
[`POW_SECRET`](#pow_secret); `GET /v1/pow/challenge` entrega los desafíos.

### `POW_SECRET`

Firma los desafíos (HMAC-SHA256). Al menos 32 caracteres, **el mismo en todos
los servidores** y secreto: con él cualquiera puede falsificar desafíos.
Genera uno con `openssl rand -hex 32`. Cambiarlo invalida los desafíos emitidos.

### `POW_DIFFICULTY`

Bits a cero iniciales que debe tener el SHA-256 de cada petición, de 8 a 32.
Cada bit duplica el trabajo del cliente: 18 son unos 0,2 s en Python y 17 ms en
código nativo por petición. Consulta [Elegir la dificultad](api/protection.md#choosing-the-difficulty).

### `POW_CHALLENGE_TTL`

Segundos que un desafío sigue siendo válido, de 30 a 3600. Un cliente reutiliza
un mismo desafío para todas sus peticiones durante ese tiempo.

### `POW_BIND_IP`

Acepta un desafío solo desde la IP a la que se emitió, para que los desafíos
resueltos no se puedan repartir. Desactívalo para clientes cuya dirección
cambia a menudo.

## GraphQL { #graphql }

Consulta [GraphQL](api/graphql.md#limits) para ver cómo se aplican los límites.

### `ENABLE_GRAPHQL`

Sirve la [API GraphQL](api/graphql.md) en `/graphql`. Cuando está desactivada,
la ruta responde `404`. GraphiQL, las consultas `GET` y la introspección
dependen además de [`DISABLE_DOCS`](#disable_docs).

### `GRAPHQL_MAX_DEPTH`

Anidamiento máximo permitido de las selecciones por debajo de un campo raíz (el
campo raíz tiene profundidad 0; los campos de introspección no cuentan).

### `GRAPHQL_MAX_COMPLEXITY`

Coste máximo permitido de una operación: 1 por campo seleccionado más 10 por
cada canal que un campo raíz puede descargar de t.me.

### `GRAPHQL_MAX_ALIASES`

Número máximo de alias permitidos en un documento.

### `GRAPHQL_MAX_TOKENS`

Longitud máxima aceptada de un documento, en tokens del analizador léxico de
GraphQL; acota el trabajo de análisis.

### `GRAPHQL_MAX_BATCH_SIZE`

Número máximo de operaciones aceptadas en una solicitud por lotes (un array
JSON). Los lotes más grandes se rechazan con `400`.

## Rendimiento { #performance }

### `CACHE_MODE`

| Valor | Caché de páginas | `Cache-Control` para Cloudflare |
|---|---|---|
| `off` | ninguna: cada solicitud llega a t.me | ninguno |
| `normal` | 10 s | `public, max-age=10, stale-while-revalidate=10` |
| `aggressive` | 120 s | `public, max-age=120, stale-while-revalidate=240` |

Se aplica a `/v1/body`, `/v1/post` y `/v1/more`. Las vistas previas y el feed
tienen sus propios ajustes, más abajo.

### `PARSER_MODE`

| Valor | Se extrae |
|---|---|
| `full` | todo: texto con entidades, multimedia, encuestas, botones, respuestas, vistas previas de enlaces, reacciones |
| `simplified` | texto (sin entidades), multimedia y botones: notablemente más barato en CPU |

### `MEMORY_TRIM_INTERVAL`

Segundos entre cada devolución al sistema operativo de la memoria del heap
liberada (`malloc_trim`, solo glibc, es decir, la imagen de Docker). Tras una
ráfaga de tráfico, glibc conserva de lo contrario la memoria de los objetos
grandes de vida corta para reutilizarla: un worker bajo carga retenía ~80 MiB
de ese heap libre. Un recorte tarda alrededor de 1 ms. `0` lo desactiva.

## Origen { #upstream }

### `TELEGRAM_BASE_URL`

Origen del que se descargan las páginas de t.me, `esquema://host[:puerto]` sin
ruta. Sirve para apuntar a un espejo o a un t.me falso: así ejecutan la imagen
las pruebas de integración contra `tests/integration/fake_telegram.py`.

### `TELEGRAM_TIMEOUT`

Tiempo de espera total en segundos de una solicitud a t.me (la fase de
conexión está limitada a 5 s). Una solicitud fallida se reintenta una vez en
una conexión nueva cuando el error es de red.

### `TELEGRAM_MAX_CONCURRENCY`

Máximo de solicitudes simultáneas a t.me por proceso worker. Evita que
Telegram limite la frecuencia de solicitudes del servidor durante solicitudes
grandes de feed o de vistas previas. Los huecos se reparten de forma justa: por
turnos entre canales y solicitudes de feed o de vistas previas, y las
solicitudes de los usuarios antes que las actualizaciones en segundo plano
(consulta [Caché](internals/caching.md#upstream-protection)).

### `TELEGRAM_CLIENT`

Cliente HTTP de las solicitudes a t.me:

- `browser` (por defecto): nativo (wreq sobre BoringSSL). El handshake TLS, la
  configuración de HTTP/2 y las cabeceras salen todos de
  [`TELEGRAM_BROWSER_PROFILE`](#telegram_browser_profile), así que describen
  el mismo navegador.
- `reqwest`: pyreqwest (rustls y hyper). Envía las cabeceras del mismo perfil,
  pero sus huellas TLS y HTTP/2 son las de rustls y hyper, que ningún
  navegador tiene, y no guarda cookies. No se usa en producción.

Las mediciones están en [Cliente HTTP](internals/http-client.md).

### `TELEGRAM_BROWSER_PROFILE`

El navegador que dicen ser las solicitudes. El valor por defecto,
`chrome_156`, es el perfil propio del proyecto del Chrome estable actual, en
`rust/telegram-me-native/src/http/chrome.rs`. También se pueden elegir los
perfiles Chromium de [wreq-util](https://crates.io/crates/wreq-util), como
`chrome_149`, `edge_148` u `opera_131`. Un nombre desconocido detiene el
servidor al arrancar, con el perfil de Chrome más reciente en el mensaje.

### `TELEGRAM_BROWSER_PLATFORM`

La plataforma de ese navegador: `android` (por defecto), `ios`, `windows`,
`macos` o `linux`. Define el `User-Agent`, `sec-ch-ua-platform` y
`sec-ch-ua-mobile`; el handshake es el mismo en todas las plataformas.
`chrome_156` no tiene variante `ios`, porque Chrome en iOS usa la pila TLS de
Apple, no BoringSSL; el servidor no arranca con esa combinación.

### `TELEGRAM_SESSION_LIFETIME`

Segundos que vive una identidad de navegador del cliente `browser`, ±25 % al
azar. Una identidad es el tarro de cookies (t.me establece `stel_ssid`), las
conexiones y sus tickets de sesión TLS. Cuando termina, el cliente empieza con
el tarro vacío y conexiones nuevas, como un perfil de navegador nuevo. El
valor por defecto es `14400` (4 horas, es decir, de 3 a 5); `0` mantiene una
identidad hasta que caducan sus cookies (las de t.me duran 24 horas) o t.me
responde con un 4xx distinto de 404. Ambas cosas también terminan una
identidad antes de tiempo. Cada proceso worker tiene su propia identidad
(véase [Cliente HTTP](internals/http-client.md#cookies)).

### `UPSTREAM_QUEUE_LIMIT`

Cuántas solicitudes deja un worker esperando a que quede libre un hueco hacia
el origen. Las siguientes solicitudes que necesitan t.me se responden con `503`
y una cabecera `Retry-After` en lugar de ponerse en cola. Por defecto, 16 por
cada hueco de `TELEGRAM_MAX_CONCURRENCY` (512).

### `MAX_WAITING_REQUESTS`

Cuántas solicitudes deja un worker esperando a la vez un trabajo hacia el
origen (una descarga o un renderizado que empezó otra solicitud); por encima,
`503` con `Retry-After`. Acota la memoria que puede retener una avalancha de
solicitudes.

### `SHARED_COALESCING`

Con `REDIS_URL`, descarga cada página una sola vez entre todos los workers y
servidores: el proceso que descarga toma una concesión (lease) breve en Redis
y los demás esperan su resultado en lugar de pedirlo a t.me por su cuenta.
Desactívalo para agrupar solo dentro de cada worker.

### `EARLY_REFRESH_WINDOW`

Renueva en segundo plano una página en caché que se sigue leyendo dentro de
esta última fracción de su actualidad (0.1: el último segundo de los 10 s de
`CACHE_MODE=normal`), para que las páginas populares nunca caduquen bajo
carga. `0` lo desactiva.

## Caché { #cache }

### `REDIS_URL`

URL de Redis o Valkey, p. ej. `redis://valkey:6379/0` o
`rediss://user:pass@host:6380/0`. Si se define, la caché de páginas, la caché
de vistas previas, la popularidad del feed y el bloqueo de líder del
actualizador se comparten entre todos los workers y servidores. Aun así, cada
worker responde primero a las solicitudes repetidas desde su caché dentro del
proceso ([`MEMORY_CACHE_MAX_MB`](#memory_cache_max_mb),
[`MEMORY_CACHE_MAX_TTL`](#memory_cache_max_ttl)), de modo que solo sus fallos
de caché esperan un viaje de ida y vuelta a Redis. Si no se define, la caché
dentro del proceso es la única.

Una caída de Redis no ralentiza las solicitudes durante mucho tiempo: los
comandos agotan su tiempo de espera tras [`REDIS_TIMEOUT`](#redis_timeout) y,
tras tres fallos seguidos en 10 segundos, Redis se omite durante 30 segundos
(circuit breaker), mientras la API funciona con su caché dentro del proceso.
Un solo comando fallido no lo activa.

Cada worker mantiene hasta `2 × TELEGRAM_MAX_CONCURRENCY` (al menos 32)
conexiones a Redis. Una ráfaga de comandos que supere esa cifra, como varias
solicitudes de feed de 100 canales a la vez, espera hasta `REDIS_TIMEOUT` a
que quede libre una conexión; un comando que aun así no consigue ninguna se
trata como un fallo de caché sin abrir el circuit breaker.

### `REDIS_TIMEOUT`

Tiempo de espera de un comando en segundos, y lo máximo que un comando espera a
que quede libre una conexión del pool. Un comando más lento abre el circuit
breaker.

### `REDIS_CONNECT_TIMEOUT`

Segundos para abrir una conexión nueva con Redis, incluido el handshake TLS. Es
mayor que `REDIS_TIMEOUT` porque conectar con un endpoint TLS lejano requiere
varios viajes de ida y vuelta: unos 0,3–0,7 s a 90 ms. Con el tiempo de espera
de los comandos, cada reconexión tras un error agotaría su tiempo y volvería a
abrir el circuit breaker, y Redis seguiría desconectándose. Además, cada worker
abre su primera conexión al arrancar, para que ninguna petición la pague.

### `REDIS_HEARTBEAT_INTERVAL`

Cada worker escribe su propia marca en Redis cada tantos segundos
(`tme:v2:node:<host>:<pid>`, con un TTL de tres intervalos); `0` lo desactiva.

- Mantiene una conexión abierta, para que las peticiones no paguen la
  reconexión a un endpoint TLS lejano.
- Detecta una caída en un intervalo como mucho.
- Se envía incluso con el circuit breaker abierto: en cuanto Redis vuelve a
  responder, el breaker se cierra sin esperar al resto de los 30 s.
- Las marcas vivas muestran qué procesos de qué servidores están en marcha:
  `redis-cli --scan --pattern 'tme:v2:node:*'`.

### `PREVIEW_CACHE_TTL`

Segundos que una vista previa de canal permanece en caché. Los canales
desconocidos se guardan en caché 60 segundos en cualquier caso.

### `MEMORY_CACHE_MAX_MB`

Límite de memoria de la caché dentro del proceso, por worker, con Redis o sin
él: las entradas se guardan comprimidas y el límite cuenta lo que ocupan
realmente (valor, clave y datos de control). Las entradas usadas menos
recientemente se expulsan primero. Con el valor por defecto de 64, un worker
con tráfico intenso se mantiene en torno a 170 MiB (todo el contenedor, en
torno a 230 MiB). Con Redis, un worker guarda solo lo que se leyó o escribió en
los últimos [`MEMORY_CACHE_MAX_TTL`](#memory_cache_max_ttl) segundos, por lo
general mucho menos que el límite.

### `MEMORY_CACHE_MAX_TTL`

Con Redis: el tiempo máximo, en segundos, que un worker conserva su propia
copia de una entrada de caché. Un worker ve una entrada que otro servidor (o el
worker del feed) escribió en Redis como mucho este tiempo después de que se
escribiera; el coste es una lectura de Redis por clave y worker con esta
frecuencia, para las claves que se están leyendo. Las copias nunca sobreviven a
la entrada de Redis. Sin Redis, y mientras Redis no está disponible, las
entradas conservan su TTL completo. Consulta
[Funcionamiento interno → Caché](internals/caching.md#how-the-levels-work-together).

## Pares { #peers }

Los nodos de una misma red privada pueden compartir su trabajo directamente:
cada clave la descarga de t.me un solo nodo, su propietario, y los demás se la
piden. Funciona sin Redis. Consulta
[Despliegue → Capa de pares](deployment/peers.md).

### `ENABLE_PEERS`

Activa la capa de pares. Cada proceso worker pasa a ser un nodo. Requiere
[`PEER_SECRET`](#peer_secret).

### `PEERS`

Dónde encontrar otros nodos: entradas `host` o `host:port` separadas por
comas, como `PEERS=10.0.0.108,10.0.0.165`. Un nombre DNS puede resolverse a
varias direcciones, y los nombres se resuelven de nuevo cada 30 segundos. Una
entrada sin puerto se prueba en todos los puertos del rango de
[`PEER_PORT`](#peer_port). Basta con un nodo alcanzable: los demás se conocen a
través de él. Con Redis, los nodos también se encuentran mediante sus
marcadores de latido, así que `PEERS` puede quedar vacío.

### `PEER_PORT`

Primer puerto de la capa de pares, tanto para UDP (pertenencia) como para TCP
(solicitudes). Cada worker toma el primer puerto libre entre `PEER_PORT` y
`PEER_PORT + WEB_CONCURRENCY` (uno de reserva para un worker que se está
reemplazando). `0` deja que el sistema operativo elija un puerto; solo es útil
en pruebas.

### `PEER_ADVERTISE_ADDR`

La dirección IP con la que los demás nodos llegan a este. Por defecto, la
dirección de origen de la ruta por defecto. Dentro de un contenedor esa es la
dirección del propio contenedor, así que ahí configúrala con la dirección
privada del host (el despliegue de producción lo hace).

### `PEER_ALLOW_CIDR`

Redes, separadas por comas, desde las que otros nodos pueden conectarse. Las
conexiones y datagramas de cualquier otro lugar se descartan antes de leer
nada. Por defecto, todos los rangos privados y de loopback: `10.0.0.0/8`,
`172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `fc00::/7` y `::1/128`.

### `PEER_SECRET`

Secreto compartido por los nodos, de al menos 32 caracteres, por ejemplo de
`openssl rand -hex 32`. Cada mensaje lleva una etiqueta HMAC-SHA256 hecha con
él, una marca de tiempo y un nonce; los mensajes falsificados, repetidos o con
más de 30 s de antigüedad se rechazan.

### `PEER_ENCRYPTION`

Cifra además cada mensaje (XChaCha20-Poly1305 con una clave derivada de
`PEER_SECRET`), para redes en las que no confías. Todos los nodos deben
coincidir.

### `PEER_TIMEOUT`

Segundos que tiene un par para conectarse y responder a una lectura de caché,
o para aceptar una carga, antes de que este nodo haga el trabajo por su
cuenta. Un par que no lo cumple se evita durante 3 segundos.

## Feed { #feed }

Todo esto solo importa con [`ENABLE_FEED=true`](#enable_feed). Consulta
[Funcionamiento interno → Feed](internals/feed.md).

### `FEED_FRESH_TTL`

Una página de canal en caché más reciente que esto (en segundos) se usa tal
cual.

### `FEED_STALE_TTL`

Una página en caché más antigua que `FEED_FRESH_TTL` pero más reciente que esto
se sirve de inmediato mientras una actualización en segundo plano la sustituye.
Las páginas más antiguas se descargan antes de responder.

### `FEED_REFRESH_MODE`

| Valor | Comportamiento |
|---|---|
| `embedded` | cada proceso de la API ejecuta el actualizador; con Redis, solo uno de ellos (el líder) hace el trabajo |
| `worker` | la API no lo ejecuta; arranca `telegram-me worker` por separado (requiere Redis) |
| `off` | sin actualización en segundo plano; las páginas solo se actualizan con las solicitudes del feed |

### `FEED_REFRESH_MIN_INTERVAL`

Intervalo de actualización más corto (en segundos), usado para los canales más
populares.

### `FEED_REFRESH_MAX_INTERVAL`

Intervalo de actualización más largo, usado para los canales casi sin demanda.
Un canal con puntuación de popularidad *s* se actualiza cada
`clamp(MAX / (1 + s), MIN, MAX)` segundos.

### `FEED_REFRESH_TOP`

De cuántos de los canales más populares se encarga el actualizador.

### `FEED_POPULARITY_HALF_LIFE`

Vida media en segundos de la popularidad de un canal: una solicitud del feed
cuenta la mitad pasado este tiempo. Los canales cuya puntuación cae por debajo
de 0.05 se olvidan.

### `FEED_HALF_LIFE_HOURS`

Horas tras las cuales la actualidad de una publicación se reduce a la mitad en
la clasificación. Los valores más bajos favorecen con más fuerza a las
publicaciones más recientes.

### `FEED_DIVERSITY`

De `0` (orden puro por puntuación) a `1` (máxima dispersión de canales y
temas). Consulta
[Funcionamiento interno → Feed](internals/feed.md#diversity).

### `FEED_COLLAPSE_DUPLICATES`

Fusiona la misma noticia publicada por varios canales en su mejor publicación,
enumerando las demás en `_duplicates`.

## Logs { #logging }

### `LOG_LEVEL`

`DEBUG`, `INFO`, `WARNING` o `ERROR`.

### `LOG_FORMAT`

`text` para personas, `json` para recolectores de logs (un objeto JSON por
línea con `time`, `level`, `logger`, `message` y cualquier dato estructurado
adicional).
