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
| HTTP/2 | SETTINGS, ventana, prioridad y orden de cabeceras del perfil | los de hyper |
| Cabeceras | las del perfil | las del perfil |
| Cookies | guardadas por worker y devueltas ([Cookies](#cookies)) | ninguna |
| Conexiones | HTTP/2 en pool, gzip/br/zstd/deflate | lo mismo |

Producción usa `browser`; `reqwest` no se usa allí.

Los dos toman sus cabeceras de un mismo perfil,
[`TELEGRAM_BROWSER_PROFILE`](../configuration.md#telegram_browser_profile)
en [`TELEGRAM_BROWSER_PLATFORM`](../configuration.md#telegram_browser_platform)
(`chrome_156` en `android` por defecto): el `User-Agent`, `sec-ch-ua*`,
`accept`, los metadatos de fetch y su orden. Ninguna cabecera se escribe por
solicitud, así que no pueden contradecirse. Solo `browser` tiene además el
handshake de ese navegador.

El cliente envía lo que envía un navegador en la página del canal:

- una **navegación** (`GET /s/<channel>`, `GET /<channel>`): las cabeceras del
  perfil con `accept-language: ru`;
- el **XHR de "cargar más"** (`POST /s/<channel>?before=…`): la llamada jQuery
  de la página, con `x-requested-with`, `accept: application/json, …`,
  `sec-fetch-mode: cors`, `sec-fetch-site: same-origin`, `origin`, `referer`,
  `priority: u=1, i` y el orden de cabeceras que usa Chromium para ella,
  capturado de un navegador Chromium 152 (Electron).

## Nuestro perfil de Chrome { #profile }

Chrome publica ahora una versión cada dos semanas; el perfil más reciente de
wreq-util, `chrome_149`, va siete versiones por detrás del canal estable. El
2026-10-25 el canal estable era Chrome 156 (despliegue completo el
2026-10-20; 157 llegaba a un pequeño porcentaje, completo el 2026-11-03).
`chrome_156` es nuestro propio perfil de esa versión, construido sobre la API
de emulación de wreq en `rust/telegram-me-native/src/http/chrome.rs`:

- **TLS**: las 15 suites de cifrado de Chrome; grupos `X25519MLKEM768`,
  `X25519`, P-256, P-384 con claves compartidas para los dos primeros;
  GREASE; permutación de extensiones; ALPS para `h2` con el código nuevo
  (17613); compresión brotli de certificados; GREASE de ECH; solicitudes de
  OCSP y SCT; reanudación con PSK.
- **HTTP/2**: `SETTINGS` 1:65536, 2:0, 4:6291456, 6:262144, un
  `WINDOW_UPDATE` de 15663105, pseudocabeceras `m,a,s,p` y la prioridad de
  `HEADERS` que envía Chrome: exclusiva sobre el flujo 0, **peso 256**. Los
  perfiles de Chrome de wreq-util envían peso 220, que Chrome no envía.
- **Cabeceras**: el orden de Chrome; `User-Agent` `Chrome/156.0.0.0` (la
  forma reducida: `Linux; Android 10; K` en Android); `sec-ch-ua` con la
  marca GREASE y el orden de marcas que Chromium deriva de la versión mayor
  (para 156: `"Not:A-Brand";v="8", "Chromium";v="156", "Google Chrome";v="156"`);
  `image/jxl` en `accept`, que Chrome envía desde que decodifica JPEG XL (155).
- `ios` se rechaza: Chrome en iOS es WebKit sobre la pila TLS de Apple, así
  que un handshake de BoringSSL contradiría su `User-Agent`.

### Lo que BoringSSL no puede enviar { #boringssl-limits }

Chrome 155 envía además tres cosas que wreq no puede, por el BoringSSL que
compila (`btls-sys` 0.5.6, versión de API de BoringSSL 37; wreq 0.16.1 y
btls 0.5.6 son las versiones más recientes):

- **Algoritmos de firma ML-DSA** `0x0904`, `0x0905`, `0x0906`. Sus nombres
  faltan en la tabla de algoritmos de firma de BoringSSL, y
  `set_sigalg_prefs` rechaza cualquier código que no esté en ella: un cliente
  construido con `mldsa65` en su lista falla con
  `INVALID_SIGNATURE_ALGORITHM`. wreq solo acepta la lista como nombres, así
  que los códigos tampoco pueden pasarse en crudo. Eso resuelve también la
  cuestión de seguridad: un servidor nunca puede elegir un algoritmo ML-DSA
  que no podríamos verificar, porque no podemos ofrecerlo. De todos modos,
  t.me sirve certificados clásicos (ECDSA).
- **Un valor GREASE en `signature_algorithms`** (Chrome envía uno al
  principio; este BoringSSL solo aplica GREASE a cifrados, grupos, versiones
  y extensiones).
- **La extensión `trust_anchors`** (`0xca34`, TLS Trust Anchor IDs), con los
  ID del Chrome Root Store. La biblioteca de C la tiene
  (`SSL_CTX_set1_requested_trust_anchors`), pero ni btls ni wreq la exponen,
  y el crate prohíbe el código `unsafe`. Electron (Chromium 152) tampoco la
  envía; no se midió si Chrome en Android la envía.

Por ellas la última parte de JA4 difiere de la de Chrome. Cerrar la
diferencia requiere una versión de btls con un BoringSSL más reciente y una
API para los trust anchors.

## Huellas { #fingerprints }

Medidas el 2026-10-25 contra `https://tls.peet.ws/api/all` con
`scripts/fingerprint.py` (tres conexiones por fila, cada una con handshake
completo). Las referencias se midieron el mismo día con el mismo servicio:
Google Chrome 155.0.8059.85 en macOS (headless, perfil nuevo) y Chromium 152
en Electron (el panel de navegador de la aplicación de escritorio). No hubo
un Chrome 156 para Android que capturar.

| Cliente | JA4 | Huella Akamai de HTTP/2 | Hash Akamai | Peso de `HEADERS` |
|---|---|---|---|---|
| `reqwest` | `t13d1011h2_61a7ad8aa9b6_0d308c48d2a3` | `2:0;4:2097152;5:16384;6:16384\|5177345\|0\|m,s,a,p` | `9b5dcd077a77c5e324b91d8cc306fd5a` | — |
| `browser`, `chrome_149` | `t13d1516h2_8daaf6152771_d8a2da3f94cd` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `52d84b11737d980aef856699f885ca86` | 220 |
| `browser`, `chrome_156` | `t13d1516h2_8daaf6152771_d8a2da3f94cd` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `52d84b11737d980aef856699f885ca86` | 256 |
| Chrome 155 (referencia) | `t13d1517h2_8daaf6152771_cb7bf5808d99` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `52d84b11737d980aef856699f885ca86` | 256 |
| Chromium 152, Electron (referencia) | `t13d1516h2_8daaf6152771_806a8c22fdea` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `52d84b11737d980aef856699f885ca86` | 256 |

- **`reqwest`** ofrece 10 suites de cifrado y 11 extensiones, sin GREASE, sin
  ALPS ni compresión de certificados: un handshake de rustls bajo un
  `User-Agent` de Chrome. Su prefacio HTTP/2 es el de hyper (ventana de
  2 MiB, pseudocabeceras `m,s,a,p`), que Chrome nunca envía.
- **`browser`** tiene las 15 suites, las 16 extensiones y el prefacio HTTP/2
  de Chrome; la parte de cifrados de JA4 (`8daaf6152771`) y el hash Akamai
  coinciden con las dos referencias. Los hashes JA3 cambian en cada conexión,
  como los de Chrome (permuta sus extensiones), así que no se comparan; JA4
  las ordena.
- **La última parte de JA4** es un hash de las extensiones y los algoritmos
  de firma. Chrome 155 tiene `0xca34` entre sus 17 extensiones y empieza sus
  algoritmos de firma con `0x0904,0x0905,0x0906`; Chromium 152 tiene las
  mismas 16 extensiones que `browser` más los algoritmos ML-DSA. Ambas cosas
  son los [límites de BoringSSL](#boringssl-limits) de arriba, así que
  `chrome_156` mantiene el handshake de `chrome_149`.
- **Grupos y claves compartidas** son los de Chrome: GREASE,
  `X25519MLKEM768`, `X25519`, P-256, P-384, con claves para GREASE,
  `X25519MLKEM768` y `X25519`.

Orden de las cabeceras de una navegación, tras las pseudocabeceras, de
`browser` y de Chrome 155: `sec-ch-ua, sec-ch-ua-mobile, sec-ch-ua-platform,
upgrade-insecure-requests, user-agent, accept, sec-fetch-site,
sec-fetch-mode, sec-fetch-user, sec-fetch-dest, accept-encoding,
accept-language, priority`. `reqwest` envía el mismo orden (pseudocabeceras
`m,s,a,p`).

El XHR de "cargar más" de `browser` y de Chromium 152:
`content-length, sec-ch-ua-platform, x-requested-with, user-agent, accept,
sec-ch-ua, sec-ch-ua-mobile, origin, sec-fetch-site, sec-fetch-mode,
sec-fetch-dest, referer, accept-encoding, accept-language, priority`.
`reqwest` envía las cabeceras de navegación con `x-requested-with` y
`content-length` añadidas al final.

Con una cookie, Chromium envía `cookie` entre `accept-language` y
`priority`, tanto en navegaciones como en XHR; `browser` hace lo mismo.

## Cookies { #cookies }

Lo que t.me establece, comprobado el 2026-10-25:

- **Una cookie, `stel_ssid`**, en cada tipo de solicitud que enviamos: la
  página del canal `/s/<channel>`, el `POST` de "cargar más" y la vista
  previa `/<channel>`. Sus atributos son
  `path=/; samesite=None; secure; HttpOnly` y caduca 24 horas después de
  emitirse.
- Si se devuelve, **no se vuelve a establecer**, así que su caducidad no se
  desplaza. Un valor desconocido se sustituye por uno nuevo, así que t.me lo
  comprueba.
- Sin cookies cada solicitud parecía un visitante nuevo: t.me emitía un
  `stel_ssid` nuevo para cada una.
- t.me además **escribe el valor en sus páginas**, en los enlaces de
  "Descargar Telegram" (`telegram.org/dl?tme=<value>`). El parser no conserva
  esos enlaces; `tests/unit/test_session_privacy.py` comprueba que ningún
  token grabado llega a las páginas analizadas (lo que guarda la caché) ni a
  ninguna respuesta.

El cliente `browser` mantiene un tarro de cookies (el tarro RFC 6265 de
wreq, que ordena las cookies como Chromium) y devuelve las cookies. Una
**sesión** es una identidad de navegador: el tarro, las conexiones del pool y
sus tickets de sesión TLS. El cliente la sustituye por una nueva, con el
tarro vacío y conexiones nuevas, cuando ocurre lo primero de esto:

- pasa [`TELEGRAM_SESSION_LIFETIME`](../configuration.md#telegram_session_lifetime),
  ±25 % al azar para que los workers de una flota no empiecen de nuevo a la
  vez (por defecto 4 horas, es decir, de 3 a 5);
- sus cookies han caducado, o t.me las borró;
- t.me responde con un 4xx distinto de 404 (un 403 o 429 puede significar que
  la identidad fue señalada; 404 es un canal que no existe).

Las solicitudes en curso terminan en la sesión en la que empezaron; sus
conexiones se cierran cuando acaba la última. Las primeras solicitudes de una
sesión nueva, enviadas a la vez (una actualización del feed), salen sin
cookie y pueden recibir cada una un `stel_ssid`; el tarro guarda el último,
como haría un navegador que abre varias pestañas a la vez.

**Una identidad por proceso worker**, no una compartida a través de Redis o
de la capa de pares. t.me no asocia a la cookie nada que quisiéramos
compartir: las páginas son las mismas con ella o sin ella. Una sola cookie
compartida por todos los workers de una flota sería un "navegador" que habla
desde varias direcciones IP a la vez, con otras tantas sesiones TLS y muchas
veces el ritmo de solicitudes de un navegador, lo que se parece menos a un
navegador, no más; y añadiría un viaje de ida y vuelta a Redis a las
solicitudes para nada. Cada worker es un navegador, como lo sería en su
propia máquina.

Las cookies nunca salen del cliente nativo: `fetch` devuelve solo el estado y
el cuerpo, la salida `Debug` del cliente omite el tarro, quien llama no puede
poner una cabecera `cookie` y ninguna línea de log lleva una. Las pruebas de
Rust (`tests/http_client.rs`) cubren guardar y devolver cookies, su lugar en
el orden de cabeceras, cada forma en que termina una sesión y que las cookies
se quedan dentro.

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

Chrome publica una versión cada dos semanas, y entre versiones su handshake
rara vez cambia. Para seguir a un Chrome estable nuevo, sube `MAJOR` y
`NAME` en `rust/telegram-me-native/src/http/chrome.rs` (el `User-Agent` y
`sec-ch-ua` lo siguen), luego el valor por defecto de
`TELEGRAM_BROWSER_PROFILE` (settings, `DEFAULT_BROWSER_PROFILE` en
`telegram/client.py`, los tres `configuration.md`, `.env.example`) y las
pruebas que lo nombran. Compara con un Chrome real en el mismo servicio de eco
antes de cambiar nada más:

```bash
uv run python scripts/fingerprint.py --runs 3
```

Dependabot actualiza `wreq` y `wreq-util` con los demás crates de cargo.
Cuando una versión de btls pueda ofrecer ML-DSA o trust anchors, añádelos al
perfil y vuelve a medir.
