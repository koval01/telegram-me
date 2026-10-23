# Errores

Todos los errores salvo `422` y `429` tienen el mismo cuerpo (la API GraphQL
informa de los errores de otra forma, consulta
[Errores de GraphQL](graphql.md#errors)):

```json
{"detail": "Requested Telegram resource was not found"}
```

## Referencia { #reference }

| Estado | `detail` | Causa |
|---|---|---|
| `404` | `Requested Telegram resource was not found` | El canal no existe, es privado o no es un canal |
| `404` | `Requested post was not found` | `/v1/post`: la publicación no está en su página |
| `422` | *lista, ver más abajo* | Falló la validación de la solicitud |
| `422` | `Invalid Telegram username format` / `Invalid post position` | Entrada rechazada por el cliente de Telegram (poco frecuente: la API valida antes) |
| `429` | `Rate limit exceeded` *(texto plano)* | Más de `RATE_LIMIT_RPM` solicitudes desde una misma IP en un minuto |
| `502` | `Failed to parse Telegram channel page` (y similares) | t.me respondió, pero no con el marcado esperado |
| `502` | `Telegram response did not match the expected schema` | No se pudieron validar los datos analizados |
| `503` | `Failed to fetch Telegram upstream resource` | Error de red o tiempo de espera agotado al comunicarse con t.me |
| `503` | `Telegram upstream returned status 429` (cualquiera ≥ 400) | t.me rechazó la solicitud |
| `503` | `Feature 'feed' is disabled by server configuration` | Función desactivada (`feed`, `previews`) |

## Errores de validación 422 { #422-validation-errors }

Los errores de validación usan el formato estándar de FastAPI:

```json
{
  "detail": [
    {
      "type": "string_pattern_mismatch",
      "loc": ["path", "channel"],
      "msg": "String should match pattern '^[a-zA-Z][a-zA-Z0-9_]{3,31}$'",
      "input": "bad-name",
      "ctx": {"pattern": "^[a-zA-Z][a-zA-Z0-9_]{3,31}$"}
    }
  ]
}
```

## Reintentos { #retrying }

- Un `503` debido a problemas de t.me suele ser transitorio: reintenta con
  espera exponencial.
- Un `502` suele significar que Telegram cambió su marcado; reintentar no
  servirá. Por favor,
  [abre un issue](https://github.com/koval01/telegram-me/issues) con el canal
  y la URL.
- `429`: espera los segundos indicados en `Retry-After`.

!!! note "Cambió en 2.0"
    Las respuestas que no se podían validar contra el esquema eran `400` en
    1.x; ahora son `502`. Una sola publicación inesperada ya no hace fallar una
    página: se omite y se registra en el log.
