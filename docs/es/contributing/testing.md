# Pruebas

```bash
make test          # ~550 tests, about 30 seconds, no network
make rust          # Rust: fmt, clippy, tests (parsers on hostile HTML included)
make integration   # the built image as two servers with Valkey (Docker)
```

La batería por defecto nunca se comunica con t.me. Cada merge a `main` se
despliega en producción, así que estas pruebas y las
[comprobaciones de CI](#ci-jobs) son lo que separa un commit roto de los
usuarios.

## Snapshots del contrato { #contract-snapshots }

`tests/test_snapshots.py` llama a cada endpoint contra **páginas de t.me
grabadas** y compara la respuesta JSON completa con un archivo de
`tests/snapshots/`. Cualquier cambio en lo que devuelve la API (un campo
renombrado, un número distinto, una publicación que falta) hace fallar una
prueba y aparece como un diff legible. Los archivos JSON se comparan ya
analizados, así que también se fijan los bytes exactos de cada respuesta,
mediante su SHA-256 en `tests/snapshots/raw_digests.json`: tampoco pueden
cambiar el orden de las claves, el formato de los números ni el escapado de
las cadenas, y eso es lo que permite mover código de pydantic a Rust sin tocar
el contrato.

Las páginas grabadas están en `tests/fixtures/telegram/` (gzip), con un
`manifest.json` que describe a qué solicitud responde cada archivo. Un
`FixtureFetcher` sustituye la capa de red y hace fallar la prueba ante
cualquier solicitud que no se haya grabado. El tiempo se congela con
`time-machine`, porque la puntuación del feed depende del «ahora».

La cobertura incluye seis canales (`durov`, `telegram`, `stranaua`, `tginfo`,
`nebudetafk`, `nebudetgg`), los dos modos del parser, la paginación en ambas
direcciones, las vistas previas, un canal inexistente, el feed y publicaciones
individuales con un botón inline, una encuesta, un sticker, una respuesta, una
vista previa de enlace y un mensaje de voz. Un feed sintético, sembrado
directamente en la caché, añade duplicados agrupados y páginas que el parser
nunca produciría (sin nombre de usuario, sin información del canal,
publicaciones sin footer ni content).

`tests/test_graphql_contract.py` hace lo mismo para GraphQL: el esquema
completo (SDL) en `tests/snapshots/graphql/schema.graphql`, un snapshot por
consulta y comprobaciones de paridad de que cada resultado de GraphQL es igual
a la respuesta REST correspondiente. `tests/test_openapi_contract.py` fija el
contrato REST completo, el documento OpenAPI con todas las funciones
activadas, en `tests/snapshots/openapi.json`.

### Cambiar la salida a propósito { #changing-the-output-on-purpose }

```bash
make snapshots                 # UPDATE_SNAPSHOTS=1 pytest tests/test_snapshots.py tests/test_graphql_contract.py tests/test_openapi_contract.py
git diff --stat tests/snapshots
git diff tests/snapshots        # review every change
```

### Cambios incompatibles { #breaking-changes }

Un snapshot solo muestra que el contrato cambió. `scripts/api_contract.py`
decide si el cambio rompe a los clientes, comparando `openapi.json` y
`schema.graphql` con la base del pull request, que es lo que sirve
producción:

```bash
make contract                  # against origin/main
```

Es incompatible: eliminar una operación, un campo o un valor de enum, un campo
de respuesta que ahora puede faltar o tener otro tipo (`null` incluido), y
todo lo que rechace solicitudes aceptadas antes (un parámetro o campo
obligatorio nuevo, un tipo más estrecho, un límite más bajo). Las adiciones
son compatibles. El esquema GraphQL lo compara graphql-core
(`find_breaking_changes`). El workflow **Guards** hace fallar un pull request
con un cambio incompatible salvo que lleve la etiqueta `breaking-change`, que
significa una versión mayor (véase [Publicar versiones](releasing.md)).

### Grabar páginas nuevas { #recording-new-pages }

```bash
make fixtures                          # re-record everything (changes many snapshots)
make fixtures CHANNELS="nebudetgg"     # add or refresh specific channels
```

La grabación usa la pila de red de producción. Volver a grabar sustituye el
contenido (visualizaciones, publicaciones nuevas), así que después regenera y
revisa los snapshots. Para cubrir un tipo de publicación poco frecuente, añade
su ID a `EXTRA_POSTS` en `scripts/record_fixtures.py`.

## Pruebas unitarias { #unit-tests }

`tests/unit/` cubre las piezas por separado:

| Archivo | Cubre |
|---|---|
| `test_client.py` | Construcción de URL, correspondencia de códigos de estado, validación, el único reintento de red |
| `test_fetcher.py` | `ReqwestFetcher` contra un servidor HTTP local real: gzip, cabeceras, cuerpos POST vacíos |
| `test_cache.py` | Caché dentro del proceso (límite, compresión, caducidad), backend de Redis, circuit breaker, lecturas de dos niveles, rellenos, caídas |
| `test_memory_trim.py` | `malloc_trim` periódico (solo glibc), su desactivación |
| `test_coalesce.py` | Compartición single-flight, propagación de errores, seguridad ante cancelaciones |
| `test_popularity.py` | Decaimiento de la popularidad y elección de líder en Redis (fakeredis con Lua) |
| `test_services.py` | Cachés de páginas y de respuestas, vistas previas, feed stale-while-revalidate, planificación del actualizador |
| `test_parsing.py` | El parser nativo de páginas visto desde Python: entidades, multimedia, reenvíos, aislamiento de publicaciones defectuosas |
| `test_feed_ranking.py` | Señales del feed y el clasificador nativo: interacción relativa, actualidad, duplicados, temas, diversidad |
| `test_graphql.py` | Forma del esquema de GraphQL, resolvers, errores |
| `test_graphql_limits.py` | Límites de GraphQL de profundidad, coste, alias, tokens y lotes |
| `test_api.py` | Validación, feature flags, cabeceras, límite de solicitudes, CORS, documentación |
| `test_runtime.py` | Ciclo de vida del actualizador integrado, logs en JSON |
| `test_docs_config.py` | Las traducciones de la documentación: todas las páginas presentes, configuración sincronizada con el inglés |
| `test_api_contract.py` | Qué cambios del contrato `scripts/api_contract.py` considera incompatibles |
| `test_properties.py` | Pruebas basadas en propiedades, véase [más abajo](#property-and-fuzz-tests) |

Las pruebas admiten código asíncrono (`pytest-asyncio` en modo auto) y las
advertencias se tratan como errores.

## Pruebas de propiedades y fuzzing { #property-and-fuzz-tests }

Cada página de t.me es una entrada que el servicio no controla, y la extensión
nativa se compila con `panic = "abort"`: un pánico en un parser tumba todo el
proceso worker. La entrada generada busca lo que los casos escritos a mano no
cubren.

- `tests/unit/test_properties.py` (hypothesis): sea cual sea la página que
  devuelva t.me (páginas grabadas con partes borradas, repetidas, cortadas o
  insertadas), cada endpoint responde 200, 404 o 502, nunca 500; los
  envoltorios del parser solo fallan con `TelegramError`; el middleware de
  protección responde a cualquier cabecera `X-PoW`, ruta y método con una
  respuesta bien formada, acepta una prueba válida exactamente una vez y
  cuenta el límite de solicitudes con exactitud; el feed contiene cada
  publicación una sola vez, solo de los canales pedidos, en cualquier orden.
  Los ejemplos salen de una semilla fija; `HYPOTHESIS_PROFILE=nightly` busca
  más tiempo con semillas aleatorias.
- `rust/telegram-me-native/tests/hostile_html.rs` (`cargo test`): cada página
  grabada entera y con mutaciones de semilla fija, una sopa de marcado de
  t.me y valores absurdos, marcado anidado a gran profundidad en un hilo con
  1 MiB de pila, y tiempos de parseo que deben crecer de forma lineal (4× la
  entrada en menos de 9× el tiempo) con muchas publicaciones, entidades,
  etiquetas sin cerrar y patrones con look-ahead. `HOSTILE_CASES` y
  `HOSTILE_SEED` lo hacen correr más o de otra forma.
- `rust/telegram-me-native/fuzz/` (cargo-fuzz, Rust nightly): un fuzzer
  guiado por cobertura sobre todos los parsers de páginas, sembrado con las
  páginas grabadas:

    ```bash
    python3 scripts/fuzz_corpus.py rust/telegram-me-native/fuzz/corpus/pages
    cd rust/telegram-me-native/fuzz && cargo +nightly fuzz run -s none pages corpus/pages -- -max_total_time=60
    ```

## Memoria { #memory }

`tests/test_memory.py` (marcador `memory`) envía miles de solicitudes
directamente a la app ASGI sobre alias de los canales grabados
(`tests/workload.py`) y falla cuando las asignaciones de Python o, en Linux, la
memoria residente siguen creciendo tras un calentamiento, o cuando la caché
dentro del proceso supera su límite. Forma parte de la ejecución por defecto,
así que la CI la ejecuta; en local puedes omitirla con `-m "not memory"`. Las
entradas de la caché viven en el heap de Rust, que `tracemalloc` no ve: que el
límite cuenta lo que realmente ocupan lo comprueba
`rust/telegram-me-native/tests/cache_memory.rs` con un asignador que cuenta.

`scripts/memory_profile.py` muestra las cifras que hay detrás: el coste de
importación del proceso principal y de un worker, el tamaño de una página en
caché, y el RSS y las asignaciones de Python muestreados durante una carga de
trabajo larga:

```bash
uv run python scripts/memory_profile.py --requests 20000 --channels 50
```

El RSS se lee de `/proc`, así que ejecútalo en Linux para obtener cifras
significativas.

## Pruebas de integración { #integration-tests }

`tests/integration/` ejecuta la imagen construida tal como corre en
producción, algo que las pruebas anteriores solo imitan con fakeredis o
dentro de un proceso: `tests/integration/compose.yaml` arranca dos servidores
con dos workers de granian cada uno, que comparten un Valkey real, delante de
un t.me falso (`tests/integration/fake_telegram.py` sirve las páginas grabadas
y cuenta las solicitudes; los servidores llegan a él mediante
[`TELEGRAM_BASE_URL`](../configuration.md#telegram_base_url)).

| Prueba | Demuestra |
|---|---|
| `test_second_server_reads_the_first_servers_cache` | la caché de dos niveles: una página que obtiene un servidor la sirve el otro desde Redis |
| `test_concurrent_misses_on_both_servers_fetch_once` | el lease de descarga: 24 fallos simultáneos en 4 procesos, una sola solicitud a t.me |
| `test_shared_rate_limit_counts_a_client_on_every_server` | `RATE_LIMIT_SHARED`: un cliente queda limitado aunque ningún proceso vea por sí solo suficientes solicitudes |
| `test_proof_of_work_from_one_server_is_accepted_by_the_other` | un desafío de un servidor resuelve una solicitud al otro; repetida, falsificada |
| `test_rolling_upgrade.py` | la versión anterior (`TME_PREVIOUS_IMAGE`) y la nueva leen las entradas de caché de la otra |
| `test_valkey_outage.py` | Valkey se detiene: ambos servidores siguen respondiendo; vuelve: comparten de nuevo |

```bash
make docker
TME_TEST_IMAGE=telegram-me:dev TME_PREVIOUS_IMAGE=docker.io/koval01/telegram-me:latest make integration
```

Solo hablan HTTP y Docker, así que se ejecutan sin instalar el proyecto
(`uv run --only-group integration`) y se omiten sin `TME_TEST_IMAGE`.

## Scripts de despliegue { #deploy-scripts }

`tests/deploy/test_remote_deploy.py` ejecuta `deploy/remote-deploy.sh` en el
Docker local como en un servidor de producción (`deploy/compose.prod.yaml`,
con Valkey y el t.me falso accesibles a través del gateway de la pila): la
actualización desde la versión anterior, una versión que nunca llega a estar
sana, una versión sana que falla en cada solicitud de página (solo la prueba
de humo lo nota), la comprobación canary (`--verify`), `--rollback` y el
perfil `proxy` con Caddy. `tests/deploy/test_rollout.py` ejecuta
`deploy/rollout.sh`, la lógica de flota del workflow de despliegue, con `ssh`
y `scp` simulados: primero el canary, y vuelta atrás de cada servidor que
recibió una versión cuando falla uno posterior, el canary o la prueba de humo
pública. Necesita bash 4.4 o posterior y forma parte de `make test`.

```bash
TME_TEST_IMAGE=telegram-me:dev make deploy-test
```

## Umbrales de cobertura { #coverage-floors }

La CI falla cuando la cobertura de líneas baja de un umbral: `fail_under` en
`pyproject.toml` para Python, `[package.metadata.coverage] fail-under-lines`
en `rust/telegram-me-native/Cargo.toml` para Rust (`cargo llvm-cov`). El
workflow **Guards** hace fallar un pull request que baja un umbral
(`scripts/coverage_floor.py`): los umbrales solo suben, junto con las pruebas
que los justifican.

## Prueba de humo en vivo { #live-smoke-test }

`scripts/smoke_matrix.py` ejercita una instancia en marcha contra t.me real.
Además de los códigos de estado comprueba el contenido de las respuestas
principales (identidad del canal, publicaciones completas, paginación del lado
correcto, puntuaciones del feed): un cambio del marcado de t.me suele seguir
respondiendo 200, solo que con campos vacíos.

```bash
make up && make smoke
uv run --no-project python scripts/smoke_matrix.py --base-url https://api.example.com --preset auto
```

Solo usa la biblioteca estándar y solo páginas que también están grabadas,
así que también pasa contra el t.me falso. La CI la ejecuta contra la imagen
recién construida en cada pull request, cada despliegue la ejecuta en cada
servidor y a través de la URL pública (véase
[Producción](../deployment/production.md#how-a-deployment-works)), y el
workflow **Nightly** la ejecuta contra t.me real y contra producción.

## Trabajos de CI { #ci-jobs }

| Workflow, trabajo | Cuándo | Duración | Detecta |
|---|---|---|---|
| CI: Rust | cada push | ~3 min | fmt, clippy, pruebas de Rust (HTML hostil), umbral de cobertura de Rust |
| CI: Lint & types | cada push | ~3,5 min | ruff, mypy, shellcheck, actionlint |
| CI: Tests | cada push | ~2 min | la batería anterior en Python 3.13 y 3.14, umbral de cobertura de Python |
| CI: Docker image smoke test | tras los tres anteriores | ~2 min | la imagen arranca endurecida, matriz de humo contra t.me real, parada ordenada |
| CI: Integration | tras la imagen | ~2 min | [pruebas de integración](#integration-tests) |
| CI: Deploy scripts | tras la imagen | ~2 min | [pruebas de los scripts de despliegue](#deploy-scripts) |
| Guards | cada pull request, cambios de etiquetas | ~10 s | [cambios incompatibles de la API](#breaking-changes), umbrales de cobertura rebajados |
| Nightly | 03:23 UTC a diario | ~15 min | pruebas de humo contra t.me real y producción, 10 min de fuzzing, ejecuciones largas de propiedades; abre una issue con la etiqueta `nightly` |

Release (pushes a `main`) ejecuta CI y Guards antes de construir y desplegar
la imagen.
