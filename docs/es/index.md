---
title: TelegramMe
description: API REST para canales públicos de Telegram, basada en las vistas previas web de t.me.
---

# TelegramMe

TelegramMe convierte la vista previa web pública de cualquier canal de Telegram
(`https://t.me/s/<channel>`) en una API JSON limpia y tipada: perfiles de
canales, publicaciones con entidades de texto, multimedia, encuestas,
reacciones, respuestas, reenvíos y botones inline, paginación por cursor,
vistas previas de canales y un feed de varios canales ordenado por relevancia.

No hace falta una cuenta de Telegram, un token de bot ni una sesión MTProto:
solo se puede acceder a los canales públicos, exactamente como los ve un
navegador.

<div class="grid cards" markdown>

-   :lucide-rocket: **En marcha en un minuto**

    ---

    Un `docker run` o `docker compose up` y la API queda en el puerto 8000.

    [:octicons-arrow-right-24: Inicio rápido](getting-started/quickstart.md)

-   :lucide-book-open: **Referencia de la API**

    ---

    Cada endpoint, parámetro, campo de respuesta y error, con ejemplos reales.

    [:octicons-arrow-right-24: Referencia de la API](api/index.md)

-   :lucide-server: **Despliegue en producción**

    ---

    Contenedores reforzados, despliegues en varios servidores con reversión
    automática, proxy inverso y configuración de caché.

    [:octicons-arrow-right-24: Despliegue](deployment/production.md)

-   :lucide-cpu: **Cómo funciona**

    ---

    Capas, caché, agrupación de solicitudes, el actualizador del feed y los
    componentes nativos (Rust / C) que hacen el trabajo pesado.

    [:octicons-arrow-right-24: Funcionamiento interno](internals/architecture.md)

</div>

## Pruébalo { #try-it }

Hay una demo pública en **<https://tme.koval-dev.org>**: prueba un canal en
su página de inicio, explora la API en
[Swagger UI](https://tme.koval-dev.org/api) o lee esta documentación en
[/docs](https://tme.koval-dev.org/docs/es/).

```bash
curl https://tme.koval-dev.org/v1/body/durov
```

```json
{
  "channel": {
    "username": "durov",
    "title": {"string": "Pavel Durov", "html": "Pavel Durov"},
    "description": {"string": "Founder of Telegram.", "html": "Founder of Telegram."},
    "avatar": "https://cdn4.telesco.pe/file/…",
    "counters": {"subscribers": "10.3M", "photos": "102", "videos": "47", "files": null, "links": "200"},
    "labels": ["verified"]
  },
  "content": {"posts": [ … 20 posts … ]},
  "meta": {"offset": {"before": 531, "after": null}}
}
```

## Endpoints de un vistazo { #endpoints-at-a-glance }

| Método | Ruta | Qué devuelve |
|---|---|---|
| `GET` | [`/v1/body/{channel}`](api/channel-page.md) | Perfil del canal, últimas publicaciones (o las publicaciones alrededor de `?position=`), cursores de paginación |
| `GET` | [`/v1/more/{channel}/{direction}/{position}`](api/more.md) | Publicaciones más antiguas (`before`) o más recientes (`after`) que una publicación dada |
| `GET` | [`/v1/post/{channel}/{id}`](api/post.md) | Una publicación |
| `GET` | [`/v1/preview/{channel}`](api/previews.md#get-v1previewchannel) | Resumen breve del canal desde `t.me/<channel>` |
| `POST` | [`/v1/previews`](api/previews.md#post-v1previews) | Hasta 100 vistas previas a la vez |
| `POST` | [`/v1/feed`](api/feed.md) | Publicaciones de hasta 100 canales, ordenadas por interacción y actualidad (desactivado por defecto) |
| `GET` | [`/healthz`](api/service.md) | Sonda de disponibilidad (liveness) |

## Qué hay dentro { #what-is-inside }

- **FastAPI + Pydantic v2** para la capa HTTP y modelos de respuesta estrictos.
- **wreq** (Rust: tokio, BoringSSL) para todo el tráfico hacia t.me: un único
  cliente HTTP/2 con pool de conexiones fuera del GIL, cuyos TLS, HTTP/2 y
  cabeceras coinciden con un navegador
  ([Cliente HTTP](internals/http-client.md)); pyreqwest se mantiene, pero no
  se usa en producción.
- **telegram-me-native** (Rust, de este proyecto) para el análisis de HTML
  (html5ever) y la clasificación del feed.
- **granian** (Rust) como servidor HTTP, con **uvloop** (C) como bucle de
  eventos.
- Una caché dentro del proceso en Rust (**moka**, zstd) por worker, delante de
  **Redis / Valkey** (opcional, con **hiredis**, C), compartido por todos los
  workers y servidores.

Consulta [Rendimiento](internals/performance.md) para ver cifras medidas.

## Licencia y datos { #license-and-data }

telegram-me se distribuye bajo la licencia
[Apache-2.0](https://github.com/koval01/telegram-me/blob/main/LICENSE). Es un
proyecto independiente, sin relación con Telegram. Lee páginas públicas de
t.me: las publicaciones, los archivos multimedia y los datos de los canales
que devuelve pertenecen a sus autores y no están cubiertos por la licencia. Si
ejecutas una instancia, eres responsable de cumplir los términos de servicio de
Telegram y las leyes que te sean aplicables, incluida la normativa de
protección de datos.
