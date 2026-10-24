# Pruebas

```bash
make test        # ~160 tests, about 2 seconds, no network
```

La batería de pruebas nunca se comunica con t.me. Dos tipos de pruebas
mantienen honesto al servicio.

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
a la respuesta REST correspondiente.

### Cambiar la salida a propósito { #changing-the-output-on-purpose }

```bash
make snapshots                 # UPDATE_SNAPSHOTS=1 pytest tests/test_snapshots.py tests/test_graphql_contract.py
git diff --stat tests/snapshots
git diff tests/snapshots        # review every change
```

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

Las pruebas admiten código asíncrono (`pytest-asyncio` en modo auto) y las
advertencias se tratan como errores.

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

## Prueba de humo en vivo { #live-smoke-test }

`scripts/smoke_matrix.py` ejercita una instancia en marcha contra t.me real:

```bash
make up && make smoke
uv run --no-project python scripts/smoke_matrix.py --base-url https://api.example.com --preset auto
```

La CI la ejecuta contra la imagen recién construida en cada pull request.
