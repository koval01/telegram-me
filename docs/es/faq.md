# Preguntas frecuentes

**¿Necesita una cuenta de Telegram o un token de bot?**
No. Lee las mismas páginas web públicas que muestra un navegador en
`t.me/s/<channel>`.

**¿Qué canales funcionan?**
Los canales públicos con la vista previa web activada. Los canales privados,
los grupos, los usuarios y los bots se devuelven como `404`.

**¿Están disponibles todas las publicaciones?**
Todo lo que muestra la vista previa web. Telegram omite ahí parte del
contenido (por ejemplo, los vídeos muy grandes se marcan con
`available: false` y los comentarios no se incluyen).

**¿Qué tan recientes son los datos?**
Como mucho de hace 10 s con el valor por defecto `CACHE_MODE=normal` (120 s con
`aggressive`, en vivo con `off`). Las vistas previas se guardan en caché una
hora. Consulta
[Resumen de la API → Actualidad de los datos](api/index.md#freshness).

**¿Bloqueará Telegram mi servidor?**
Mucho tráfico sin caché puede hacer que t.me limite la frecuencia de
solicitudes de una IP (respuestas `503`). Mantén la caché activada, usa Redis
cuando ejecutes varios workers, mantén `TELEGRAM_MAX_CONCURRENCY` en un valor
moderado y coloca una CDN o una caché de proxy delante en los despliegues
públicos.

**¿Por qué está desactivado `/v1/feed`?**
Necesita una página de cada canal de la solicitud, así que es mucho más caro
que los demás endpoints. Actívalo con `ENABLE_FEED=true`, idealmente con Redis.

**¿Por qué los contadores son cadenas?**
Se devuelven exactamente con el formato de Telegram (`10.3M`), que puede
perder precisión; convertirlos sugeriría una precisión que no existe.

**¿Por qué `text.entities[].offset` a veces sorprende?**
Los desplazamientos indexan la cadena simple `string` tal como Python cuenta
los caracteres (puntos de código), no en unidades de código UTF-16 como hace la
Bot API de Telegram.

**Recibo `502`.**
Probablemente Telegram cambió su marcado. Por favor,
[abre un issue](https://github.com/koval01/telegram-me/issues) con la URL.

**Recibo `422` para un canal válido.**
Los nombres de usuario deben tener entre 5 y 32 caracteres, empezar por una
letra y contener solo letras, dígitos y `_`. Pásalos sin `@`.

**¿Puedo ejecutarlo sin Docker?**
Sí: `uv run telegram-me serve` (o `pip install .` y `telegram-me serve`).
