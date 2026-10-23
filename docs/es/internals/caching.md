# Caché

## Qué se guarda en caché { #what-is-cached }

| Clave (prefijo `tme:v2:`) | Valor | TTL |
|---|---|---|
| `json:body:page:{mode}:{channel}:{position}` | Respuesta `/v1/body` renderizada | TTL de página (`CACHE_MODE`: 0 / 10 / 120 s) |
| `page:{mode}:{channel}:{position}` | Página analizada (dict sin procesar como JSON) | TTL de página; para la página más reciente con el feed activado: `max(page TTL, FEED_STALE_TTL)` |
| `more:{mode}:{channel}:{direction}:{position}` | Respuesta `/v1/more` renderizada | TTL de página |
| `preview:{channel}` | Vista previa analizada, o `null` para canales desconocidos | `PREVIEW_CACHE_TTL` (1 h), 60 s para los desconocidos |
| `feed:popularity` | Conjunto ordenado canal → puntuación | — (lo hace decaer el actualizador) |
| `feed:refresher-leader` | Token del actualizador líder | 30 s, renovado cada 5 s |

`{mode}` es el modo del parser, así que las instancias `full` y `simplified`
pueden compartir un Redis sin mezclar resultados. Los nombres de canal se
pasan a minúsculas.

Cada valor se guarda junto con el momento en que se escribió
(`"<unix time>\n<payload>"`), lo que permite a quien lo lee aplicar sus propias
reglas de actualidad además de la caducidad del backend: el
stale-while-revalidate del feed hace exactamente eso.

## Backends { #backends }

**Redis / Valkey** (con `REDIS_URL` definida): compartido por todos los workers
y servidores. Los comandos agotan su tiempo de espera tras `REDIS_TIMEOUT`
(0.5 s). El primer fallo abre un circuit breaker: Redis se omite durante 30
segundos y las solicitudes se sirven sin caché, así que una caída de Redis
cuesta como mucho una solicitud lenta por worker, no una por solicitud. Las
respuestas las analiza hiredis (C).

Una solicitud de feed o de vistas previas ejecuta hasta 100 consultas a la
caché a la vez, así que el pool de conexiones
(`2 × TELEGRAM_MAX_CONCURRENCY` por worker, al menos 32) hace que una ráfaga
espere hasta `REDIS_TIMEOUT` a que quede libre una conexión. Un comando que no
consigue ninguna se trata como un fallo de caché; un pool agotado es un límite
local, así que no abre el circuit breaker.

**Memoria** (por defecto): un diccionario LRU por worker con caducidad por
entrada, limitado por `MEMORY_CACHE_MAX_MB` de memoria real: los valores se
guardan comprimidos (zstd nivel 1; zlib en Python 3.13) y el límite cuenta el
valor comprimido, la clave y los datos de control de cada entrada. Una página
renderizada ocupa ahí 14–53 KiB en lugar de 93–700 KiB como `str` de Python
(consulta [Rendimiento](performance.md#memory)).

En ambos backends los valores son bytes UTF-8. Mantener el JSON como `str`
costaba hasta cuatro bytes por carácter (un solo emoji ensancha toda la
cadena) y una codificación en cada acierto de caché.

## Respuestas prerrenderizadas { #pre-rendered-responses }

Para `/v1/body` y `/v1/more`, el valor en caché es el JSON final. Cuando se
carga una página, se analiza, se valida y se serializa una sola vez; cada
acierto de caché se sirve como bytes. Esto hace que los aciertos de caché sean
unas 5 veces más baratos que volver a validar la página analizada en cada
solicitud (consulta [Rendimiento](performance.md)).

## Agrupación de solicitudes { #request-coalescing }

`SingleFlight` indexa el trabajo en curso por clave de caché. Mientras se pide
una página a t.me, cualquier otra solicitud de la misma página espera a esa
tarea en lugar de enviar su propia solicitud: una ráfaga de 1000 solicitudes
para un canal sin caché produce una sola solicitud al origen. Un cliente que se
desconecta no cancela la tarea compartida.

## Protección del origen { #upstream-protection }

- Un semáforo por proceso limita las solicitudes simultáneas a t.me
  (`TELEGRAM_MAX_CONCURRENCY`, 32).
- Los canales desconocidos se guardan en caché como `null` durante 60 s.
- La caché en el edge de Cloudflare se activa mediante `Cache-Control`
  (consulta [Proxy inverso](../deployment/reverse-proxy.md#cloudflare)).
