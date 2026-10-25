# Límite de peticiones y prueba de trabajo

Dos protecciones opcionales que el dueño del servidor activa si quiere: un
**límite de peticiones** por cliente y una **prueba de trabajo** (PoW, proof of
work). Ambas están desactivadas por defecto. Ambas se comprueban en código nativo
antes del enrutado, así que una petición rechazada nunca llega a la validación,
la caché ni t.me. Una comprobación tarda bastante menos de un microsegundo.

Primero se aplica el límite y después la prueba de trabajo. Las peticiones
rechazadas por una prueba ausente o incorrecta también cuentan para el límite,
lo que acota la velocidad a la que alguien puede probar.

## Límite de peticiones { #rate-limit }

Se activa con [`ENABLE_RATE_LIMIT`](../configuration.md#enable_rate_limit).

- **Algoritmo.** Cada IP de cliente puede hacer
  [`RATE_LIMIT_BURST`](../configuration.md#rate_limit_burst) peticiones de golpe
  (por defecto [`RATE_LIMIT_RPM`](../configuration.md#rate_limit_rpm)) y después
  una más cada `60 / RATE_LIMIT_RPM` segundos. Es GCRA (generic cell rate
  algorithm). Su único estado por cliente es una marca de tiempo, así que la
  memoria no crece por rápido que envíe un cliente.
- **Entre workers y servidores.** Con Redis y
  [`RATE_LIMIT_SHARED`](../configuration.md#rate_limit_shared) (por defecto),
  cada proceso suma una vez por segundo sus peticiones aceptadas a un contador
  por minuto en Redis. Un cliente que supera el límite allí queda bloqueado en
  todas partes hasta que termina el minuto. Las peticiones nunca esperan a
  Redis; a cambio, un cliente puede superar el límite compartido en hasta un
  segundo de tráfico por proceso. Sin Redis, cada proceso limita por su cuenta.
- **Dirección del cliente.** Detrás de un proxy inverso, configura
  [`FORWARDED_ALLOW_IPS`](../configuration.md#forwarded_allow_ips) para que la IP
  del cliente se tome de `X-Forwarded-For`; si no, todos los clientes comparten
  el límite del proxy.

Cada respuesta incluye:

| Cabecera | Significado |
|---|---|
| `X-RateLimit-Limit` | `RATE_LIMIT_RPM` |
| `X-RateLimit-Remaining` | Peticiones que el cliente aún puede hacer ahora mismo |

Por encima del límite la API responde `429`:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 12
X-RateLimit-Limit: 120
X-RateLimit-Remaining: 0
Content-Type: application/json

{"detail":"Rate limit exceeded"}
```

`Retry-After` es el número de segundos hasta que se permita la siguiente
petición.

## Prueba de trabajo { #proof-of-work }

Se activa con [`ENABLE_POW`](../configuration.md#enable_pow) y un
[`POW_SECRET`](../configuration.md#pow_secret) de al menos 32 caracteres, el
mismo en todos los servidores. A partir de ahí cada petición debe llevar un
hash que al cliente le costó trabajo encontrar y que el servidor comprueba en
microsegundos. Extraer datos de la API a través de la instancia de otro se
vuelve caro, e inundarla de peticiones, más caro todavía.

### Cómo responde un cliente { #how-a-client-answers }

1. **Obtener un desafío.** Llama a `GET /v1/pow/challenge` o toma uno de
   cualquier rechazo (ver más abajo):

    ```json
    {
      "challenge": "1.1792860000.18.3q2-7w8_aBcDeFgHiJkL.Zm9vYmFyYmF6cXV4cXV1eA",
      "difficulty": 18,
      "expires": 1792860000,
      "algorithm": "sha256",
      "header": "X-PoW",
      "input": "{challenge}\\n{nonce}\\n{METHOD} {target}"
    }
    ```

2. **Resolverlo para la petición.** Encuentra un `nonce` (1–64 caracteres de
   `A–Z a–z 0–9 - _`) tal que

    ```text
    SHA-256(challenge + "\n" + nonce + "\n" + METHOD + " " + target)
    ```

    empiece con `difficulty` bits a cero. `target` es la ruta y la query de la
    petición exactamente como se envían, por ejemplo `/v1/body/durov?position=5`.
    De media hacen falta `2^difficulty` hashes.

3. **Enviarlo** en la cabecera `X-PoW` como `challenge:nonce`.

Reutiliza el desafío para todas las peticiones hasta que caduque
([`POW_CHALLENGE_TTL`](../configuration.md#pow_challenge_ttl), 5 minutos por
defecto), con un nonce nuevo en cada petición. Una solución se acepta una sola
vez: está ligada a su método y su target, y cada proceso del servidor recuerda
las soluciones aceptadas hasta que caduca su desafío.

=== "Python"

    ```python
    import hashlib
    import itertools

    import requests

    API = "https://api.example.com"


    def solve(challenge: str, difficulty: int, method: str, target: str) -> str:
        prefix = hashlib.sha256(f"{challenge}\n".encode())
        suffix = f"\n{method} {target}".encode()
        for n in itertools.count():
            nonce = format(n, "x")
            digest = prefix.copy()
            digest.update(nonce.encode() + suffix)
            value = int.from_bytes(digest.digest(), "big")
            if value >> (256 - difficulty) == 0:
                return nonce
        raise AssertionError


    pow_ = requests.get(f"{API}/v1/pow/challenge", timeout=10).json()
    target = "/v1/body/durov"
    nonce = solve(pow_["challenge"], pow_["difficulty"], "GET", target)
    response = requests.get(API + target, headers={"X-PoW": f"{pow_['challenge']}:{nonce}"}, timeout=30)
    ```

=== "JavaScript"

    ```javascript
    const API = "https://api.example.com";

    async function solve(challenge, difficulty, method, target) {
      const encoder = new TextEncoder();
      for (let n = 0; ; n++) {
        const nonce = n.toString(16);
        const data = encoder.encode(`${challenge}\n${nonce}\n${method} ${target}`);
        const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", data));
        let bits = 0;
        for (const byte of hash) {
          if (byte === 0) { bits += 8; continue; }
          bits += Math.clz32(byte) - 24;
          break;
        }
        if (bits >= difficulty) return nonce;
      }
    }

    const pow = await (await fetch(`${API}/v1/pow/challenge`)).json();
    const target = "/v1/body/durov";
    const nonce = await solve(pow.challenge, pow.difficulty, "GET", target);
    const response = await fetch(API + target, {
      headers: { "X-PoW": `${pow.challenge}:${nonce}` },
    });
    ```

    `crypto.subtle` es asíncrono, así que cada hash cuesta un `await`. Para
    dificultades altas usa una implementación síncrona de SHA-256 en un Web Worker.

### Rechazos { #rejections }

Una petición sin una prueba válida recibe un error JSON con un **desafío
nuevo**, para que el cliente lo resuelva y reintente sin un viaje de ida y
vuelta extra:

```json
{
  "detail": "Proof of work required",
  "pow": {
    "code": "missing",
    "challenge": "1.1792860000.18.…",
    "difficulty": 18,
    "expires": 1792860000,
    "algorithm": "sha256",
    "header": "X-PoW",
    "input": "{challenge}\\n{nonce}\\n{METHOD} {target}"
  }
}
```

El mismo desafío llega también en las cabeceras `X-PoW-Challenge`,
`X-PoW-Difficulty` y `X-PoW-Expires`. Estas cabeceras, `Retry-After` y las del
límite de peticiones están expuestas a los navegadores mediante CORS.

| Estado | `code` | Significado |
|---|---|---|
| `428` | `missing` | Falta la cabecera `X-PoW` |
| `428` | `expired` | El desafío ha caducado |
| `428` | `too_easy` | El desafío pide menos trabajo del que el servidor exige ahora |
| `400` | `malformed` | No es `challenge:nonce`, o un campo está fuera de rango |
| `403` | `forged` | El desafío no lo emitió este servidor, o se emitió para otra IP |
| `403` | `insufficient_work` | Al hash le faltan bits a cero, normalmente porque el método o el target no son los que se usaron al calcularlo |
| `403` | `replayed` | Esta solución ya se usó |

### Sin prueba { #no-proof-needed }

Algunas peticiones se sirven sin prueba: las páginas que un navegador carga
antes de poder resolverla (la página de inicio `/`, `/api`, `/openapi.json`,
`/docs/…` y `/static/…`), `/robots.txt`, `/sitemap.xml`, `/healthz`, el
propio `/v1/pow/challenge`, la página de GraphiQL (`GET /graphql` sin
consulta) y los preflight de CORS (`OPTIONS`). El límite de tasa no tiene
excepciones: esas páginas cuentan como cualquier otra petición. Las consultas GraphQL (`POST /graphql`) necesitan prueba como
cualquier otra petición, con método y target `POST /graphql`.

### Notas de seguridad { #security-notes }

- **Los desafíos no guardan estado.** Un desafío tiene la forma
  `1.<expires>.<difficulty>.<salt>.<tag>`, donde `tag` es un HMAC-SHA256 de los
  demás campos (y de la IP del cliente con
  [`POW_BIND_IP`](../configuration.md#pow_bind_ip)). Emitirlo no ocupa
  almacenamiento, así que pedir desafíos no puede agotar el servidor, y el
  endpoint de desafíos tiene el mismo límite de peticiones que todo lo demás.
- **Las comprobaciones van de la más barata a la más cara:** tamaño (la cabecera
  admite como mucho 256 bytes), sintaxis, caducidad, dificultad, el `tag`
  (comparación en tiempo constante), el hash y, por último, la caché de
  repeticiones. Solo las pruebas realmente válidas llegan a la caché.
- **Subir la dificultad** tiene efecto inmediato: los desafíos más fáciles se
  rechazan con `too_easy` y la respuesta trae uno nuevo.
- **Repeticiones.** Cada proceso recuerda las soluciones aceptadas hasta que
  caduca su desafío (hasta 500 000). Entre procesos, una solución podría usarse
  una vez por proceso, pero solo para el mismo método y target, cuya respuesta
  normalmente ya está en caché.
- **La vinculación a la IP** impide resolver desafíos de forma centralizada y
  repartirlos a una granja. Desactívala solo si los clientes cambian de
  dirección dentro de `POW_CHALLENGE_TTL`, por ejemplo en redes móviles o con
  direcciones IPv6 de privacidad.
- **El cuerpo de la petición no se incluye en el hash.** `POST /v1/feed` y
  `POST /v1/previews` se vinculan solo a su target; la regla de un solo uso
  sigue haciendo que cada petición cueste una solución.

### Elegir la dificultad { #choosing-the-difficulty }

El trabajo esperado es de `2^difficulty` hashes por petición: cada bit de más lo
duplica.

| `POW_DIFFICULTY` | Código nativo (≈ 15 M hashes/s por núcleo) | Python `hashlib` (≈ 1,5 M/s) |
|---|---|---|
| 16 | ~4 ms | ~45 ms |
| 18 (por defecto) | ~17 ms | ~0,2 s |
| 20 | ~70 ms | ~0,7 s |
| 22 | ~0,3 s | ~3 s |

SHA-256 es barato en GPU: la prueba de trabajo encarece el abuso, pero no
detiene a un atacante decidido con hardware. Combínala con el límite de
peticiones.
