# Hoja de ruta hacia Rust

Dónde sigue gastando tiempo un worker en Python, por qué camino pasa a Rust lo
que queda de la ruta crítica y en qué orden. La regla que se sigue: el trabajo
pesado se ejecuta de forma nativa; el código nativo que escribimos nosotros es
Rust (los componentes ya hechos pueden ser C, C++ o Rust); Python es el
pegamento: reglas de negocio, cableado, orquestación y scripts, nada en la
ruta crítica.

Dos cosas no cambian por el camino: las respuestas REST `/v1` y el esquema de
GraphQL (ambos fijados por `tests/snapshots/`, REST byte a byte), y el
despliegue (Docker, compose, `deploy/remote-deploy.sh`, máquinas virtuales de
1 GB).

## Auditoría { #audit }

Medido con [`scripts/audit.py`](https://github.com/koval01/telegram-me/blob/main/scripts/audit.py)
en Apple Silicon (8 núcleos, Python 3.13), dentro del proceso, con t.me
sustituido por las páginas grabadas y la caché en proceso (el L1 de la
[caché de dos niveles](caching.md)) como caché; la memoria, en Linux
(arm64, Python 3.14). La referencia es `main` después de la caché de dos
niveles (PR #201). «Asignado» es el pico de asignaciones de Python mientras se
atiende una petición (tracemalloc). Los valores absolutos deben leerse como
relativos.

```bash
uv run python scripts/audit.py --stages        # por endpoint y por etapa
RAYON_NUM_THREADS=1 uv run python scripts/audit.py  # coste de CPU en un núcleo
uv run python scripts/audit.py --memory 200    # RSS de un bucle de feeds (Linux)
```

### Coste por petición { #cost-per-request }

| Petición | Tiempo | CPU (todos los hilos) | Asignado | Respuesta |
|---|---|---|---|---|
| `GET /healthz` | 0.03 ms | 0.03 ms | 15 KiB | 15 B |
| `GET /v1/body/durov`, en caché | 0.11 ms | 0.11 ms | 105 KiB | 91 KB |
| `GET /v1/body/tginfo`, en caché | 0.17 ms | 0.17 ms | 248 KiB | 238 KB |
| `GET /v1/post/durov/531`, en caché | 0.33 ms | 0.33 ms | 278 KiB | 3 KB |
| `GET /v1/preview/durov`, en caché | 0.07 ms | 0.07 ms | 18 KiB | 0.5 KB |
| `POST /v1/previews`, 100 canales, en caché | 1.14 ms | 1.10 ms | 327 KiB | 65 KB |
| `POST /graphql`, un `body`, en caché | 0.95 ms | 0.95 ms | 122 KiB | 175 B |
| `POST /v1/feed`, 6 canales, en caché | 8.97 ms | 15.8 ms | 2.0 MiB | 0.6 MB |
| `POST /v1/feed`, 30 canales, en caché | 27.0 ms | 54.0 ms | 6.2 MiB | 0.9 MB |
| `POST /v1/feed`, 100 canales, en caché | 83.7 ms | 170 ms | 20.8 MiB | 1.7 MB |
| `GET /v1/body/durov`, sin caché | 4.90 ms | 4.86 ms | 742 KiB | 91 KB |
| `GET /v1/body/tginfo`, sin caché | 9.40 ms | 9.35 ms | 1.5 MiB | 238 KB |

Los feeds de 100 canales usan alias de los seis canales grabados, cada uno con
su propio nombre de usuario: 2 000 publicaciones con muchas noticias
repetidas, un caso pesado para la búsqueda de duplicados.

**Canal de una página en frío**, por página de canal grabada (ms, un hilo):

| Página | análisis (Rust) | `from_json` | validar | dump | encode | zstd | `decode` |
|---|---|---|---|---|---|---|---|
| @durov | 3.55 | 0.13 | 0.30 | 0.28 | 0.10 | 0.42 | 0.13 |
| @telegram | 2.84 | 0.09 | 0.24 | 0.22 | 0.07 | 0.27 | 0.09 |
| @tginfo | 5.97 | 1.00 | 0.66 | 0.56 | 0.21 | 1.14 | 1.00 |
| @nebudetgg | 2.06 | 0.16 | 0.21 | 0.18 | 0.06 | 0.23 | 0.16 |

El análisis libera el GIL; todo lo que viene después (`from_json` a objetos
de Python, validación y serialización con pydantic, la codificación para la
caché) lo retiene: 0.5–2.6 ms por página. Un fallo de `/v1/body` además valida
y renderiza la página una segunda vez en el bucle de eventos
(`body_json` → `body()`), aunque el hilo de análisis ya la había renderizado.

**Construcción del feed, 100 páginas en caché, 2 000 publicaciones** (código
2.0, ms):

| Etapa | Dónde | Tiempo |
|---|---|---|
| decodificar cada página a objetos de Python | bucle de eventos, GIL retenido | 33 |
| construir un `Candidate` por publicación | bucle de eventos, GIL retenido | 5.5 |
| clasificar | hilo de trabajo, Rust, GIL liberado | 35 (8 núcleos) / 82 (1 núcleo) |
| construir las entradas, validar, serializar | bucle de eventos, GIL retenido | 2.3 |

**Bloqueo del bucle de eventos.** `/v1/body/durov` en caché desde dos
clientes a 50 peticiones/s cada uno, con la latencia contada desde el momento
en que tocaba enviar cada petición, junto a un cliente que construye feeds de
100 canales en bucle:

| | p50 | p99 | Feeds/s |
|---|---|---|---|
| 8 núcleos | 15.2 ms | 61.6 ms | 11.2 |
| 1 núcleo (`RAYON_NUM_THREADS=1`) | 2.2 ms | 61.0 ms | 7.4 |

(Por sí sola, esa petición tarda 0.11 ms.)

### Por componente { #by-component }

| Componente | Trabajo de Python por petición | Limitado por | Notas |
|---|---|---|---|
| granian + enrutado de FastAPI, validación, 3 middlewares | ~30 µs dentro del proceso; ~50 µs por petición con un socket real (21 000 peticiones/s por worker en `/healthz`) | CPU (Python) | el coste mínimo de cualquier endpoint |
| Construcción de la respuesta | `/v1/body`, `/v1/more` en caché: ninguno (bytes de la caché); `/v1/post`: decodificar la página, validar una publicación, serializar (0.25 ms); `/v1/preview(s)`: decodificar, validar, serializar (~10 µs por canal) | CPU (Python, pydantic-core) | |
| Canal de la página en un fallo | 0.5–2.6 ms por página tras el análisis nativo, más el segundo renderizado de `/v1/body` | CPU (Python, pydantic-core) | se ejecuta en 2 hilos de análisis, pero retiene el GIL |
| Caché | L1: nativa (moka + zstd), decenas de µs con el GIL retenido; L2: el cliente asyncio de redis-py (parser hiredis) por comando más el viaje de ida y vuelta | L1 CPU, L2 E/S | la caché de dos niveles (PR #201) |
| Single-flight (`coalesce.py`) | una tarea, un shield y una entrada de diccionario por llamada: ~40 µs, sobre todo una vuelta extra del bucle de eventos | CPU (Python) | la agrupación de peticiones entre workers se hace aparte |
| Límite de peticiones, prueba de trabajo (`protection.py`) | se comprueban en Rust (`ratelimit.rs`, `pow.rs`) en menos de un microsegundo, dentro de un único middleware ASGI puro; la ventana deslizante en Python a la que sustituyó costaba +4 µs por petición | CPU (Rust) | desactivados por defecto; PR #202 |
| Construcción del feed | ver arriba: 41 ms de Python con el GIL retenido por cada 100 canales | CPU (Python) | **etapa 1** |
| Popularidad, bloqueo de líder | un pipeline de Redis por feed (`ZINCRBY` × canales), un `EVAL` por ciclo | E/S | redis-py construye el pipeline en Python |
| Bucle de actualización | solo planificación; las páginas pasan por el canal de la página | E/S + canal de la página | ≤ 16 actualizaciones a la vez |
| Cliente HTTP (`client.py`) | un semáforo y un await; `/v1/more` además hace `json.loads` de la respuesta (~0.2 ms) | E/S (Rust, pyreqwest) | |
| GraphQL (strawberry) | ~0.85 ms por consulta además de los servicios | CPU (Python) | análisis y validación de la consulta, un resolver por campo |
| Modelo de workers | un proceso por worker, cada uno con su intérprete, su caché L1 y su pool HTTP | memoria | ver abajo |

### Memoria por worker { #memory-per-worker }

Linux, arm64, Python 3.14 (`scripts/memory_profile.py`, `scripts/audit.py --memory`):

| | RSS | Anónima |
|---|---|---|
| Solo el intérprete | 9.0 MiB | 3.4 MiB |
| Proceso principal de granian (supervisor) | 43.7 MiB | 26.2 MiB |
| Worker con la aplicación construida, en reposo | 85.3 MiB | 63.2 MiB |
| Worker tras 20 000 peticiones mixtas sobre 1 200 canales (41 MiB contabilizados en la caché en proceso) | 187–212 MiB, pico 220 MiB | |
| Worker que ha servido 200 feeds de 100 canales | pico 220 MiB | |

Los objetos vivos de Python se mantienen en ~13 MiB durante toda la carga
mixta (tracemalloc); el resto es el intérprete con sus importaciones, la
caché en proceso, los búferes nativos y lo que malloc conserva entre ráfagas.

La base de un worker es el intérprete y la aplicación importada (FastAPI,
pydantic, strawberry, redis-py, la extensión nativa), más hasta
`MEMORY_CACHE_MAX_MB` de L1. `WEB_CONCURRENCY` workers duplican todo eso, L1
incluida.

### Qué muestra { #what-it-shows }

- Casi todo el trabajo limitado por CPU fuera del analizador y del
  clasificador estaba en la **construcción del feed**: 41 ms de Python por
  feed de 100 canales con el GIL retenido, ~20 MiB de objetos de Python de
  vida corta y un bloqueo de todas las demás peticiones del worker durante
  ese tiempo. Es la partida individual más grande.
- La siguiente es el **canal de la página después del analizador**
  (0.5–2.6 ms de pydantic con el GIL retenido por página, dos veces en un
  fallo de `/v1/body`). Importa para los ciclos de actualización y el tráfico
  en frío, no para los aciertos de caché.
- **Los aciertos de caché ya son baratos** (0.07–0.33 ms). Lo que queda ahí es
  el coste mínimo por petición del puente ASGI de granian y de FastAPI, y
  pydantic para `/v1/post` y las vistas previas.
- **GraphQL** cuesta ~0.85 ms de Python por consulta, sean cuales sean los
  datos.
- Las partes limitadas por E/S (cliente HTTP, Redis) ya son nativas o C; lo
  que Python añade ahí es coste por comando, no límite de rendimiento.
- El **clasificador** (Rust) es ahora la mayor partida de CPU de un feed:
  ~80 ms por 2 000 publicaciones en un núcleo. Optimizarlo no depende de dónde
  se ejecute el pegamento.

## Opciones { #options }

### (a) Mantener el envoltorio granian/FastAPI y llevar canales completos a código nativo { #a-keep-the-granianfastapi-shell-move-whole-pipelines-native }

El trabajo de cada endpoint se convierte en una sola llamada nativa:
descargar, analizar, validar y renderizar bytes con el GIL liberado; caché,
single-flight y límite de peticiones nativos. Python conserva el enrutado, la
configuración, los flags de funcionalidades, la correspondencia de errores,
las reglas de frescura del feed y la planificación de las actualizaciones.

- **A favor:** es incremental, cada paso se publica por separado y se
  comprueba con los snapshots byte a byte; OpenAPI (desde los modelos de
  pydantic), GraphQL y la CLI se quedan como están; las reglas de negocio se
  quedan en Python; Docker y los scripts de despliegue no cambian. Es el
  camino que ya siguieron el analizador, el clasificador y la caché L1.
- **En contra:** el coste mínimo por petición se mantiene (el puente ASGI de
  granian, FastAPI, ~50 µs por petición, ~20 000 peticiones/s por worker), y
  también un intérprete completo por worker. GraphQL sigue en Python.

### (b) Un servidor HTTP en Rust (axum/hyper sobre tokio) que sirve `/v1` { #b-a-rust-http-server-axumhyper-on-tokio-serving-v1 }

El servidor es Rust; Python va embebido (PyO3) o solo se llama para las
reglas de negocio.

- **A favor:** el coste mínimo más bajo (decenas de µs y varias veces más
  rendimiento por núcleo), un único proceso multihilo en lugar de un proceso
  por worker, y por tanto una caché L1 y un pool HTTP por servidor en lugar de
  uno por worker: el mayor ahorro de memoria.
- **En contra:** un cambio de golpe del servidor, el enrutador, los
  middlewares, la correspondencia de errores y la generación de OpenAPI; un
  intérprete embebido recupera el GIL para cada regla que ejecuta, así que las
  reglas tienen que pasar a ser datos o Rust para notar la ganancia; GraphQL
  necesitaría un port a Rust (async-graphql) con el mismo esquema y los mismos
  errores, o seguir como un segundo servicio en Python. Las reglas dejan de
  ser «fáciles de cambiar en Python».

### (c) Un servicio central en Rust y un plano de control ligero en Python { #c-a-rust-core-service-and-a-thin-python-control-plane }

Un binario de Rust sirve `/v1` (y más adelante GraphQL) y ejecuta la
actualización del feed; Python se queda con la configuración, la CLI, las
fixtures y los scripts, y quizá con GraphQL como un proceso aparte que llama
al núcleo.

- **A favor:** las mismas ganancias en ejecución que (b) sin embeber; una
  frontera limpia.
- **En contra:** dos procesos por máquina virtual que desplegar, configurar y
  vigilar; el plano de control necesita una interfaz (archivo de
  configuración, API de administración); el mismo cambio de servidor de golpe
  que en (b), y todo lo que hoy es pegamento en Python (flags, reglas de
  frescura) tiene que expresarse de nuevo en Rust o en configuración.

### Recomendación: (a), por etapas { #recommendation-a-staged }

La opción (a) obtiene las ganancias de CPU y de GIL donde están medidas
(construcción del feed, canal de la página, resolución de GraphQL) sin un
cambio arriesgado. Cada etapa lleva un canal completo a `telegram-me-native`,
se publica por separado, se verifica con los snapshots byte a byte y tiene su
propia ganancia medida. Cuando los canales sean nativos, el proceso ya será un
núcleo en Rust detrás de un envoltorio en Python: lo que queda en Python es el
coste mínimo de ~50 µs de ASGI/FastAPI y un intérprete por worker. Si merece
la pena sustituirlo por un frontal en axum (avanzando hacia (c)) se decide
entonces, con cifras (etapa 6), no ahora.

## Etapas { #stages }

Cada etapa: snapshots (`make snapshots`, hashes byte a byte) sin cambios, sus
propias cifras de antes y después, su propio PR.

1. **Construcción nativa del feed** — *hecho en esta versión.* `FeedService`
   entrega los bytes de las páginas en caché a `telegram_me_native.build_feed`
   y recibe los bytes de la respuesta (una llamada, GIL liberado): lectura de
   las páginas, señales de clasificación, clasificación, renderizado. Python
   conserva las reglas de stale-while-revalidate y la popularidad. Ver los
   [resultados](#stage-1-results).
2. **Canal nativo de la página.** Analizar, validar y renderizar en una sola
   llamada: `parse_channel_page` devuelve directamente la codificación para la
   caché y los bytes renderizados de `/v1/body` (y de `/v1/more`), con las
   comprobaciones de esquema que hoy hace pydantic (normalización de URL
   —pydantic-core usa el mismo crate `url`—, valores literal, valores por
   defecto, validadores de campos, descarte de publicaciones que no validan).
   Los modelos de pydantic siguen siendo el esquema documentado (OpenAPI,
   tipos de GraphQL) y pasan a ser el oráculo de una prueba diferencial sobre
   las fixtures y publicaciones generadas. Elimina 0.5–2.6 ms de Python con el
   GIL retenido por página y el segundo renderizado en un fallo de
   `/v1/body`; los ciclos de actualización dejan de necesitar el GIL.
3. **Lecturas nativas de la caché.** `/v1/post`, `/v1/preview` y
   `/v1/previews` se renderizan de forma nativa a partir de los bytes en caché
   (extraer una publicación, renderizar vistas previas), de modo que ningún
   acierto de caché construye objetos de Python. Se apoya en el renderizador
   de la etapa 2.
4. **Nivel de Redis y popularidad nativos.** Un cliente de Redis en Rust
   (redis-rs o fred, sobre el runtime de tokio) detrás del protocolo `Cache`
   existente y de la caché de dos niveles, más la popularidad y el bloqueo de
   líder; un solo viaje de ida y vuelta por feed para todas sus páginas
   (`MGET`). Coordinado con el trabajo de agrupación de peticiones entre
   workers, que también vive en Redis.
5. **Resolución de GraphQL.** Los resolvers leen bytes renderizados de forma
   nativa y las selecciones más pesadas (`body.posts`) se proyectan de forma
   nativa; strawberry conserva el esquema, así que el snapshot del SDL no
   cambia. Elimina la mayor parte de los ~0.85 ms por consulta.
6. **El envoltorio, decidido con cifras.** Con 1–5 hechas, medir cuánto
   cuestan el puente ASGI, FastAPI y los middlewares y cuánta RAM cuesta un
   intérprete por worker. Si merece la pena, servir `/v1` desde un frontal en
   axum en la misma imagen (Python se queda con GraphQL, la documentación y la
   CLI) o ejecutar un solo worker con hilos nativos en lugar de varios
   procesos. El límite de peticiones y la prueba de trabajo ya son nativos
   (`ratelimit.rs`, `pow.rs`, PR #202).

No previsto: el algoritmo del clasificador. Su velocidad es un trabajo aparte:
con ~80 ms por 2 000 publicaciones en un núcleo es ahora la mayor partida de
CPU de un feed.

## Resultados de la etapa 1 { #stage-1-results }

Misma máquina, mismo script, `main` (con la caché de dos niveles) frente a
esta versión. Las respuestas son idénticas byte a byte
(`tests/snapshots/raw_digests.json`).

| | Antes | Después | |
|---|---|---|---|
| `POST /v1/feed`, 6 canales, tiempo | 8.97 ms | 6.17 ms | −31 % |
| `POST /v1/feed`, 30 canales, tiempo | 27.0 ms | 14.7 ms | −45 % |
| `POST /v1/feed`, 100 canales, tiempo | 83.7 ms | 42.7 ms | −49 % |
| `POST /v1/feed`, 100 canales, CPU en un núcleo | 131 ms | 106 ms | −19 % |
| `POST /v1/feed`, 6 / 30 canales, CPU en un núcleo | 11.3 / 40.6 ms | 9.1 / 32.5 ms | −20 % |
| `POST /v1/feed`, 100 canales, asignado por Python | 20.8 MiB | 9.8 MiB | −53 % |
| Asignaciones de Python, feed de 6 canales | 2.0 MiB | 1.1 MiB | −44 % |
| `/v1/body` en caché junto a un bucle de feeds, p50 / p99 (8 núcleos) | 15.2 / 61.6 ms | 2.3 / 10.5 ms | 6.6× / 5.9× |
| Lo mismo, un núcleo, p99 | 61.0 ms | 4.7 ms | 13× |
| Feeds de 100 canales por segundo, un worker (8 núcleos / 1 núcleo) | 11.2 / 7.4 | 23.3 / 9.3 | 2.1× / 1.3× |

La memoria residente no cambia de forma medible: en Linux, un worker que
sirvió 200 feeds de 100 canales llegó a un pico de 220 MiB antes y 218 MiB
después, y la carga mixta de 20 000 peticiones a 220 y 223 MiB (la variación
entre ejecuciones es de unos ±10 MiB). Los objetos de Python que el feed ya no
construye eran de vida corta, y el RSS de un worker lo dominan el intérprete
con sus importaciones (85 MiB) y la caché en proceso. La ganancia de memoria
de esta hoja de ruta llega con la etapa 6 (menos intérpretes, una caché por
servidor), no con mover canales concretos.

Lo que queda del feed en Python es resolver las páginas (las consultas a la
caché, cuyas lecturas de L1 aún retienen el GIL unas decenas de µs cada una)
y actualizar la popularidad. El resto de la CPU es del clasificador.
