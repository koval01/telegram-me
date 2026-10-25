# Cliente HTTP

Cada página que sirve la API empieza como una solicitud a t.me. Además de las
cabeceras, un servidor ve dos huellas del cliente que la envía: el
`ClientHello` de TLS (JA3, JA4) y el prefacio de la conexión HTTP/2 (la huella
Akamai: SETTINGS, WINDOW_UPDATE, prioridad y orden de las pseudocabeceras).
Cuando nombran a un cliente distinto del que dice el `User-Agent`, la
solicitud es más fácil de señalar que una de un navegador real o de una
biblioteca honesta.

## Dos clientes { #two-clients }

[`TELEGRAM_CLIENT`](../configuration.md#telegram_client) elige el `Fetcher`:

| | `browser` (por defecto) | `reqwest` |
|---|---|---|
| Biblioteca | [wreq](https://crates.io/crates/wreq) 0.16 + [wreq-util](https://crates.io/crates/wreq-util) 0.2, en `rust/telegram-me-native` | [pyreqwest](https://pypi.org/project/pyreqwest/) |
| TLS | BoringSSL, como Chrome | rustls |
| HTTP/2 | SETTINGS, ventana y orden de cabeceras del perfil | los de hyper |
| Cabeceras | las del perfil | las del perfil |
| Conexiones | HTTP/2 en pool, gzip/br/zstd/deflate | lo mismo |

Los dos toman sus cabeceras de un mismo perfil,
[`TELEGRAM_BROWSER_PROFILE`](../configuration.md#telegram_browser_profile)
en [`TELEGRAM_BROWSER_PLATFORM`](../configuration.md#telegram_browser_platform)
(`chrome_149` en `android` por defecto): el `User-Agent`, `sec-ch-ua*`,
`accept`, los metadatos de fetch y su orden. Ninguna cabecera se escribe a
mano, así que no pueden contradecirse. Solo `browser` tiene además el
handshake de ese navegador.

El cliente envía lo que envía un navegador en la página del canal:

- una **navegación** (`GET /s/<channel>`, `GET /<channel>`): las cabeceras del
  perfil con `accept-language: ru`;
- el **XHR de "cargar más"** (`POST /s/<channel>?before=…`): la llamada jQuery
  de la página, con `x-requested-with`, `accept: application/json, …`,
  `sec-fetch-mode: cors`, `sec-fetch-site: same-origin`, `origin`, `referer`,
  `priority: u=1, i` y el orden de cabeceras que usa Chromium para ella,
  capturado de un navegador Chromium 152 (Electron).

## Huellas { #fingerprints }

Medidas el 2026-10-24 contra `https://tls.peet.ws/api/all` con
`scripts/fingerprint.py` (tres conexiones por fila). La fila de referencia es
un navegador Chromium 152 (Electron, con BoringSSL como Chrome) medido el
mismo día con el mismo servicio. No se midió una compilación de Google Chrome.

| Cliente | JA4 | Huella Akamai de HTTP/2 | Hash Akamai |
|---|---|---|---|
| `reqwest` antes de este cambio (cabeceras de Chrome 141 escritas a mano) | `t13d1011h2_61a7ad8aa9b6_0d308c48d2a3` | `2:0;4:2097152;5:16384;6:16384\|5177345\|0\|m,s,a,p` | `9b5dcd077a77c5e324b91d8cc306fd5a` |
| `reqwest` (cabeceras del perfil) | `t13d1011h2_61a7ad8aa9b6_0d308c48d2a3` | `2:0;4:2097152;5:16384;6:16384\|5177345\|0\|m,s,a,p` | `9b5dcd077a77c5e324b91d8cc306fd5a` |
| `browser` (`chrome_149`, `android`) | `t13d1516h2_8daaf6152771_d8a2da3f94cd` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `52d84b11737d980aef856699f885ca86` |
| Chromium 152 (referencia) | `t13d1516h2_8daaf6152771_806a8c22fdea` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `52d84b11737d980aef856699f885ca86` |

- **`reqwest`** ofrece 10 suites de cifrado y 11 extensiones, sin GREASE, sin
  ALPS ni compresión de certificados: un handshake de rustls bajo un
  `User-Agent` de Chrome. Su prefacio HTTP/2 es el de hyper (ventana de
  2 MiB, pseudocabeceras `m,s,a,p`), que Chrome nunca envía.
- **`browser`** tiene las 15 suites y 16 extensiones de Chrome (GREASE,
  `X25519MLKEM768`, ALPS, compresión brotli de certificados, GREASE de ECH) y
  el prefacio HTTP/2 de Chrome. Los hashes JA3 cambian en cada conexión, como
  los de Chrome (permuta sus extensiones), así que no se comparan; JA4 las
  ordena.
- **La referencia** coincide con `browser` en la parte de cifrados de JA4 y en
  toda la huella HTTP/2. La última parte de JA4 difiere: Chromium 152 ofrece
  además tres algoritmos de firma ML-DSA (`0x0904`–`0x0906`) que el perfil
  `chrome_149`, tres versiones más antiguo, no ofrece. Es el perfil más
  reciente que trae wreq-util hoy.

Orden de las cabeceras de una navegación, tras las pseudocabeceras:

- `reqwest` antes: `accept, accept-language, cache-control, dnt, priority,
  sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform, sec-fetch-dest,
  sec-fetch-mode, sec-fetch-site, sec-fetch-user, upgrade-insecure-requests,
  user-agent, accept-encoding` — alfabético, con `sec-ch-ua-platform:
  Android` sin comillas;
- `browser` y `reqwest` ahora: `sec-ch-ua, sec-ch-ua-mobile,
  sec-ch-ua-platform, upgrade-insecure-requests, user-agent, accept,
  sec-fetch-site, sec-fetch-mode, sec-fetch-user, sec-fetch-dest,
  accept-encoding, accept-language, priority` — el orden de Chrome; la
  referencia envía el mismo orden (Electron omite `sec-ch-ua*` en las
  navegaciones).

El XHR de "cargar más" de `browser` y de la referencia:
`content-length, sec-ch-ua-platform, x-requested-with, user-agent, accept,
sec-ch-ua, sec-ch-ua-mobile, origin, sec-fetch-site, sec-fetch-mode,
sec-fetch-dest, referer, accept-encoding, accept-language, priority`.
`reqwest` envía las cabeceras de navegación con `x-requested-with` y
`content-length` añadidas al final.

## Rendimiento y memoria { #performance }

Contra t.me real desde una máquina (12 cargas de página seguidas y luego 16 a
la vez):

| | Primera solicitud | p50 en caliente | 16 en paralelo | RSS máximo del proceso |
|---|---|---|---|---|
| `reqwest` | 246–296 ms | 98–121 ms | 172 ms\* | 58 MiB |
| `browser` | 180–281 ms | 93–105 ms | 160–178 ms | 60–61 MiB |

\* en una de dos ejecuciones el lote paralelo tardó 2 s; el lento fue t.me, no
el cliente.

En la imagen de Docker con `ENABLE_FEED=true`, tras la matriz de humo, los
workers tenían 105–108 MiB de RSS con `browser` y 117–128 MiB con `reqwest`.
Las solicitudes de `browser` se ejecutan en un runtime de tokio compartido de
dos hilos.

## Puente asíncrono { #async-bridge }

`BrowserClient.fetch` devuelve un future de asyncio creado por
[pyo3-async-runtimes](https://crates.io/crates/pyo3-async-runtimes): la
solicitud se ejecuta en el runtime de tokio sin el GIL; al terminar, un hilo
bloqueante toma el GIL una vez y fija el resultado mediante
`call_soon_threadsafe` del bucle de eventos. Cancelar la tarea de asyncio
descarta el future de Rust y, con él, la solicitud. Es el puente mantenido de
PyO3, así que el módulo no mantiene su propia integración con el bucle.

## Compilación { #build }

BoringSSL es C y C++ compilado con cmake (`btls-sys`); bindgen necesita
libclang, y el crate aplica sus parches con git. La etapa de compilación de
Docker instala `g++ make cmake clang libclang-dev git`; la imagen de ejecución
no recibe ninguno.

| | Compilación en frío, arm64 (nativa) | Compilación en frío, amd64 (emulada en arm64) | Imagen |
|---|---|---|---|
| antes | 121 s | 172 s | 317,1 MB (arm64), 69,7 MB (amd64) |
| con wreq | 196 s | 386 s | 324,4 MB (arm64), 72,2 MB (amd64) |

En CI, arm64 se compila bajo QEMU, como lo hace `release.yml`. Una compilación
en frío de esa imagen en `ubuntu-latest` tardó 17 min 26 s en main y
28 min 12 s con wreq (+10 min 46 s). El job de publicación compila las dos
plataformas en una ejecución, así que una publicación que recompila el crate
tarda aproximadamente eso más. La caché de capas evita ese coste en las
publicaciones que no tocan `rust/`.

## Mantener el perfil al día { #updates }

Chrome publica una versión cada cuatro semanas; un perfil envejece con él.
Dependabot actualiza `wreq` y `wreq-util` con los demás crates de cargo.
Cuando aparezca un perfil de Chrome más reciente, actualiza el valor por
defecto de `TELEGRAM_BROWSER_PROFILE` (settings, `DEFAULT_BROWSER_PROFILE` en
`telegram/client.py`, los tres `configuration.md`, `.env.example`) y ejecuta
`scripts/fingerprint.py` para comparar:

```bash
uv run python scripts/fingerprint.py --runs 3
```
