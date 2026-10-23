# Feed

```http
POST /v1/feed
```

Combina las últimas publicaciones de hasta 100 canales en una sola lista,
ordenada por una puntuación que combina interacción, riqueza del contenido y
actualidad.

!!! warning "Desactivado por defecto"
    El feed es el endpoint más pesado: necesita una página de cada canal
    solicitado. Responde `503` hasta que se activa con
    [`ENABLE_FEED=true`](../configuration.md#enable_feed). Ejecútalo con Redis
    (`REDIS_URL`) cuando tengas más de un worker o servidor, para que todos
    compartan la caché de páginas y la clasificación de popularidad.

## Solicitud { #request }

```json
{"channels": ["durov", "telegram", "stranaua"]}
```

Entre 1 y 100 nombres de usuario válidos. Los duplicados se ignoran. Una
entrada no válida se responde con `422` incluso mientras el feed está
desactivado.

## Respuesta { #response }

```json
{
  "result": [
    {
      "id": 552,
      "content": { … },
      "footer": { … },
      "view": "…",
      "channel": {
        "username": "durov",
        "title": {"string": "Pavel Durov", "html": "Pavel Durov"},
        "description": { … },
        "avatar": "https://…",
        "counters": {"subscribers": "10.3M", "photos": "102", "videos": "47", "links": "200"},
        "labels": ["verified"]
      },
      "_score": 0.1688
    }
  ]
}
```

Cada elemento es una publicación en su **forma analizada sin procesar** (los
campos que Telegram no proporcionó se omiten en lugar de valer `null`), más:

- `channel`: el perfil del canal al que pertenece la publicación;
- `_score`: la puntuación de clasificación en `[0, 10]`;
- `_duplicates` *(opcional)*: la misma noticia publicada por otros canales,
  agrupada en esta publicación: `[{"channel": "…", "id": 123}, …]`;
- `_topic` *(opcional)*: el tema que esta publicación comparte con otras del
  feed: `{"id": 3, "keywords": ["starlink", "ограничения", …]}`.

El orden es el orden del feed. Es en su mayor parte, pero no estrictamente,
por `_score`: el clasificador intercala canales y temas (consulta
«Clasificación» más abajo). Los canales que no se pueden cargar (desconocidos,
errores de t.me) se omiten.

## Actualidad de los datos { #freshness }

El feed usa una caché stale-while-revalidate por página de canal:

| Antigüedad de la página en caché | Qué ocurre |
|---|---|
| < [`FEED_FRESH_TTL`](../configuration.md#feed_fresh_ttl) (60 s) | se usa tal cual |
| < [`FEED_STALE_TTL`](../configuration.md#feed_stale_ttl) (15 min) | se usa de inmediato; una actualización en segundo plano la sustituye |
| más antigua / ausente | se descarga en ese momento: todos los canales que faltan en paralelo |

Un **actualizador** en segundo plano mantiene al día los canales que la gente
realmente pide: cuanto más a menudo aparece un canal en las solicitudes del
feed, más a menudo se actualiza (cada 30 s los más populares, hasta cada
10 min). Por eso la mayoría de las solicitudes del feed nunca esperan a t.me.
Detalles: [Funcionamiento interno → Feed](../internals/feed.md).

## Clasificación { #ranking }

La clasificación se ejecuta de forma nativa en Rust y combina:

- **interacción relativa al canal**: las visualizaciones, ajustadas por la
  antigüedad de la publicación y comparadas con las demás publicaciones del
  canal, para que las publicaciones destacadas de canales pequeños compitan
  con los canales grandes;
- **reacciones**, **riqueza del contenido** y **tamaño de la audiencia**;
- **actualidad** con una vida media de
  [`FEED_HALF_LIFE_HOURS`](../configuration.md#feed_half_life_hours) (24 h);
- **agrupación de duplicados** entre canales;
- **diversidad**: menos rachas de un mismo canal o de un mismo tema
  ([`FEED_DIVERSITY`](../configuration.md#feed_diversity));
- **temas** inferidos de los textos.

El algoritmo completo está en
[Funcionamiento interno → Feed](../internals/feed.md#ranking). Con
`PARSER_MODE=simplified` las reacciones no se analizan y no cuentan.

!!! note "Cambió en 2.1"
    La clasificación pasó a Rust y cambió: los valores de `_score` y el orden
    difieren de 2.0, y se añadieron `_duplicates` / `_topic`.
