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

Los comandos agotan su tiempo de espera tras `REDIS_TIMEOUT` (0.5 s). Tres
fallos seguidos en 10 segundos abren un circuit breaker: a partir de ahí Redis
se omite durante 30 segundos, así que una caída cuesta unas pocas solicitudes
lentas por worker, no una por solicitud. Un fallo aislado, como una conexión
TLS lenta a un Redis lejano o un paquete perdido, no lo abre, y cualquier
comando que llega a Redis reinicia la cuenta. Las respuestas las analiza
hiredis (C).

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

Las solicitudes concurrentes idénticas hacen el trabajo una sola vez: una
solicitud a t.me y un renderizado, lleguen donde lleguen. Las piezas de abajo
se miden en [Rendimiento](performance.md#request-coalescing); el núcleo de la
planificación (tabla de vuelos, puerta hacia el origen, puntos de renovación)
está en Rust, en
[`sched.rs`](https://github.com/koval01/telegram-me/blob/main/rust/telegram-me-native/src/sched.rs),
y los servicios de Python solo lo conectan.

### En un worker { #in-one-worker }

`SingleFlight` indexa el trabajo en curso por clave de caché, por separado para
páginas analizadas, cuerpos renderizados, publicaciones renderizadas,
fragmentos de `/v1/more` y vistas previas. Mientras se pide una página a t.me,
cualquier otra solicitud de ella espera a esa tarea en lugar de enviar la suya:
una ráfaga de 1000 solicitudes para un canal sin caché produce una sola
solicitud al origen. Un cliente que se desconecta no cancela la tarea
compartida. Como REST y GraphQL se resuelven a través de los mismos servicios,
un `GET /v1/body/durov` y un `body(channel: "durov")` de GraphQL también
comparten la descarga y el renderizado; `/v1/post/durov/531` y
`/v1/body/durov?position=531` comparten la página alrededor de la publicación
531; un feed y una solicitud de un solo canal comparten la página más reciente
del canal; las solicitudes de feed idénticas se montan, clasifican y renderizan
una sola vez.

Las claves se normalizan antes de compararlas: los nombres de canal no
distinguen mayúsculas (`Durov` y `durov` son una sola clave, también en los
feeds) y una posición ausente es la posición 0. Las páginas alrededor de
posiciones *distintas* no se tratan como intercambiables aunque contengan la
misma publicación: cada carga de página trae sus propios tokens `view` por
publicación (y t.me renderiza los footers y los reenvíos de forma ligeramente
distinta alrededor del ancla), así que servir una en lugar de otra cambiaría
las respuestas.

### Entre workers y servidores { #across-workers-and-servers }

Cada worker de granian de cada servidor tiene sus propios vuelos únicos, así
que, por sí solos, una clave popular que caduca se descarga una vez por worker
y servidor: 6 veces con dos servidores de tres workers. Con Redis, el worker
que lidera localmente toma primero una concesión (lease),
`tme:v2:lease:<key>`, en un solo viaje de ida y vuelta que además lee el valor
en caché (un script Lua: `SET NX PX` más `GET`):

- el valor en caché ya está fresco (otro proceso acaba de escribirlo): se usa
  y se guarda una copia en la caché dentro del proceso, sin descarga;
- se obtiene la concesión: descarga, escribe la caché, libera la concesión;
- otro proceso tiene la concesión: sondea con espera progresiva con jitter (de
  25 ms creciendo hasta 250 ms), reintentando la concesión en cada ronda, hasta
  que el resultado del titular está en la caché. Un titular que falló con un
  error de dominio (no encontrado, análisis, origen) deja ese error en la
  concesión durante 2 s, de modo que quienes esperan (y las solicitudes que
  llegan mientras tanto) fallan igual en lugar de reintentar todos contra t.me.

La concesión vive 3 s y su titular la prolonga cada segundo mientras dura la
descarga, así que un titular que se cayó o perdió Redis se sustituye en 3 s,
tarde lo que tarde t.me para uno vivo. Nadie espera más de
`2 × TELEGRAM_TIMEOUT + 3` segundos en total, y nadie espera a Redis más de
`REDIS_TIMEOUT` por comando: cuando Redis va lento, está caído o su circuit
breaker está abierto, un worker simplemente descarga por su cuenta. El vuelo
único local sigue aplicándose entonces, así que una caída cuesta una descarga
por worker (el comportamiento anterior a la concesión), nunca una estampida.
`SHARED_COALESCING=false` desactiva la concesión. Sin Redis no hay nada a
través de lo que coordinarse, y cada worker agrupa por su cuenta.

El actualizador del feed pasa por el mismo camino: una actualización que ya
está ejecutando una solicitud de usuario en otro servidor se une a ella, y una
página que algún proceso descargó en la última media del intervalo de
actualización no se vuelve a descargar.

### Renovación anticipada { #early-renewal }

Cada entrada recibe un punto de renovación en el último
`EARLY_REFRESH_WINDOW` (10 %) de su actualidad, derivado del momento en que se
guardó, para que todos los lectores de todos los procesos coincidan en él y
las entradas guardadas a la vez (un ciclo del actualizador) se renueven de
forma escalonada. La primera solicitud que lee una página, un cuerpo
renderizado, una publicación renderizada o una entrada de `/v1/more` pasado
ese punto se sigue respondiendo desde la caché y lanza una actualización en
segundo plano; las lecturas posteriores de la misma entrada en ese proceso no
vuelven a pedirla, y la actualización se agrupa como cualquier descarga. Así,
una clave popular se descarga más o menos una vez por TTL y nunca caduca bajo
carga: sin avalancha de fallos de caché y sin que nadie espere a t.me. Se probó
y descartó un esquema probabilístico como XFetch: con cientos de lecturas por
segundo renueva unas `ln(reads per window)` ventanas antes de la caducidad,
duplicando el tráfico hacia el origen justo de las claves más populares. Los
TTL largos (vistas previas, la ventana stale del feed) también se acortan en
una parte aleatoria de la ventana, para que las entradas escritas a la vez no
caduquen a la vez.

## Protección del origen { #upstream-protection }

- **Límite de concurrencia justo.** Cada worker envía como mucho
  `TELEGRAM_MAX_CONCURRENCY` (32) solicitudes a t.me a la vez. Los huecos
  libres se reparten por turnos entre *flujos* (un canal, o una solicitud de
  feed o de vistas previas en su conjunto) en lugar de por orden de llegada,
  así que un feed de 100 canales no hace esperar detrás de él a una solicitud
  de un solo canal. Un flujo ocupa como mucho tres cuartas partes de los
  huecos. Las actualizaciones en segundo plano ocupan como mucho la mitad y
  solo obtienen un hueco mientras no espera ninguna solicitud de usuario; un
  usuario que se une a una actualización que aún espera hueco la eleva a
  prioridad de usuario.
- **Colas acotadas, descarte de carga.** Como mucho `UPSTREAM_QUEUE_LIMIT`
  solicitudes (por defecto, 16 por hueco) esperan un hueco, y como mucho
  `MAX_WAITING_REQUESTS` (4096) solicitudes esperan trabajo hacia el origen de
  cualquier tipo por worker. Por encima, una solicitud se responde con `503` y
  `Retry-After` (estimado a partir de la longitud de la cola y de cuánto se
  ocupa un hueco) en lugar de ponerse en cola sin límite; una solicitud de feed
  o de vistas previas omite los canales descartados. El trabajo en segundo
  plano que supera los límites se descarta en silencio.
- Los canales desconocidos se guardan en caché como `null` durante 60 s.
- La caché en el edge de Cloudflare se activa mediante `Cache-Control`
  (consulta [Proxy inverso](../deployment/reverse-proxy.md#cloudflare)).
