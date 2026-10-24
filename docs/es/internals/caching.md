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
(`"<unix time>\n<payload>"` en Redis), lo que permite a quien lo lee aplicar
sus propias reglas de actualidad además de la caducidad del backend: el
stale-while-revalidate del feed hace exactamente eso.

## Niveles { #levels }

Cada worker tiene una **caché dentro del proceso**. Con `REDIS_URL` definida es
el primer nivel (L1), delante de **Redis / Valkey** (L2), que comparten todos
los workers y servidores; sin Redis es el único nivel. Los servicios ven una
sola `Cache` en cualquier caso (`TwoLevelCache` o `MemoryCache` en
`cache.py`).

| Solicitud repetida en el mismo worker | 2.0.0 | Ahora (acierto en L1) |
|---|---|---|
| `/v1/body`, Redis a 90 ms (Upstash) | 95 ms | 0.10 ms |
| `/v1/body`, Redis en el mismo host | 0.35 ms | 0.10 ms |
| `/v1/body`, sin Redis | 0.15 ms | 0.10 ms |

Consulta [Rendimiento](performance.md#two-level-cache) para cada endpoint.

### Nivel dentro del proceso { #in-process-level }

`telegram_me_native.LocalCache`: Rust, sobre la caché concurrente de `moka`.

- **LRU, limitada por memoria real.** Una entrada pesa su clave, su valor
  almacenado y 512 bytes de datos de control (la entrada de moka y sus nodos de
  tabla hash, LRU y temporizador: ~390 bytes solicitados, medidos con un
  asignador que cuenta, más el redondeo de malloc). El total se mantiene dentro
  de `MEMORY_CACHE_MAX_MB` justo después de cada escritura;
  `rust/telegram-me-native/tests/cache_memory.rs` comprueba que lo contabilizado
  cubre lo que realmente se asigna. Toda entrada nueva se admite (sin filtro de
  admisión TinyLFU): una página recién descargada está a punto de volver a
  leerse.
- **Valores comprimidos.** zstd nivel 1 (libzstd, C): una página renderizada
  ocupa 12–43 KiB en lugar de 44–233 KiB de UTF-8, y volver a leerla
  (descomprimiéndola directamente en un nuevo `bytes`) lleva ~40 µs para una
  página de 95 KB. Los valores que no se reducen se guardan tal cual.
- **Caducidad por entrada** mediante el `Expiry` de moka: cada entrada vive su
  propio TTL. Las entradas caducadas nunca se devuelven y la rueda de
  temporizadores de moka las elimina en aproximadamente un segundo, se lean o
  no.
- **El GIL se mantiene.** Las lecturas y escrituras tardan decenas de
  microsegundos y se ejecutan en el event loop. Si se libera, el GIL pasa a un
  hilo de análisis ocupado en pydantic y el event loop espera a recuperarlo:
  con dos hilos de Python que consumen CPU en marcha, una lectura tardó 6.3 ms
  (p50) en lugar de 38 µs.

### Nivel Redis { #redis-level }

Los comandos agotan su tiempo de espera tras `REDIS_TIMEOUT` (0.5 s). El primer
fallo abre un circuit breaker: Redis se omite durante 30 segundos, así que una
caída de Redis cuesta como mucho una solicitud lenta por worker, no una por
solicitud. Las respuestas las analiza hiredis (C).

Una solicitud de feed o de vistas previas ejecuta hasta 100 consultas a la
caché a la vez, así que el pool de conexiones
(`2 × TELEGRAM_MAX_CONCURRENCY` por worker, al menos 32) hace que una ráfaga
espere hasta `REDIS_TIMEOUT` a que quede libre una conexión. Un comando que no
consigue ninguna se trata como un fallo de caché; un pool agotado es un límite
local, así que no abre el circuit breaker.

### Cómo trabajan juntos los niveles { #how-the-levels-work-together }

- **Lectura:** primero L1. En un fallo, un viaje de ida y vuelta a Redis (un
  pipeline de `GET` y `PTTL`) devuelve la entrada y su TTL restante; L1 guarda
  una copia durante ese TTL restante, como mucho `MEMORY_CACHE_MAX_TTL`
  (10 s). Los fallos concurrentes de una misma clave comparten una sola lectura
  de Redis. Una copia nunca sustituye a una entrada más reciente que se haya
  escrito en L1 mientras la lectura estaba en curso.
- **Escritura:** en ambos niveles, con el mismo momento de guardado. L1 guarda
  la entrada durante `min(TTL, MEMORY_CACHE_MAX_TTL)`.
- **Momento de guardado:** una copia conserva el momento en que la entrada se
  guardó por primera vez, así que una entrada tiene la misma antigüedad en
  todos los servidores y el stale-while-revalidate del feed funciona igual que
  antes.
- **Redis caído o circuit breaker abierto:** L1 funciona sola. Las lecturas que
  fallan en L1 son fallos de caché; las escrituras van solo a L1, con su TTL
  completo, ya que no hay una copia compartida que recoger en su lugar.

**Por qué `MEMORY_CACHE_MAX_TTL`.** Dos servidores (y el worker del feed)
comparten Redis, pero no sus L1, y nada avisa a un servidor de que otro ha
escrito una entrada más reciente. Para la mayoría de las claves, el desfase
queda acotado por su TTL de todos modos, lo cual es aceptable: una página
renderizada vive el TTL de página en todos los niveles. Sin embargo, la página
más reciente de un canal se guarda durante la ventana stale del feed
(`FEED_STALE_TTL`, 15 min), mientras que solo cuenta como fresca durante el TTL
de página (10 s) o `FEED_FRESH_TTL` (60 s). Sin un tope, un servidor
conservaría su copia antigua hasta 15 minutos y, cuando envejeciera, volvería a
descargar la página de t.me en lugar de leer la copia que el actualizador (o el
otro servidor) acababa de poner en Redis; con el worker del feed, los
servidores de la API ignorarían su trabajo. Con el tope, un servidor ve una
entrada más reciente en Redis como mucho 10 segundos después de que se
escribiera. El coste es, como mucho, una lectura de Redis por clave y worker
cada 10 segundos, para las claves que se están leyendo.

En ambos niveles los valores son bytes UTF-8. Mantener el JSON como `str`
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
