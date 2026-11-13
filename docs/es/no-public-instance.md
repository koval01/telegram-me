# Sin instancia pública

TelegramMe no tiene ninguna instancia pública, y no la tendrá. Ejecútalo en tu
propio servidor o en tu propia máquina: es un solo contenedor.

La demo que funcionaba en `tme.koval-dev.org` se apagó definitivamente en
noviembre de 2026, junto con sus servidores y su pipeline de despliegue. Esa
dirección ya no sirve la API, y nada la sustituye.

## Por qué { #why }

La demo estaba para mostrar lo que hace el proyecto, no para servir las
aplicaciones de otros. Se usó justo para eso:

- **Se construyeron aplicaciones sobre ella.** En lugar de ejecutar su propia
  instancia o integrar TelegramMe en su producto, hubo quien apuntó sus
  aplicaciones a la demo y la trató como una API gratuita. El proyecto es de
  código abierto para que cualquiera pueda ejecutarlo, no para que una persona
  lo ejecute para todos.
- **Tráfico ajeno, responsabilidad del mantenedor.** Cada solicitud llegaba a
  t.me desde los servidores de la demo. Sin forma de saber quién la envía ni
  para qué, la demo podía acabar, tarde o temprano, sirviendo a abusos o
  delitos. El mantenedor no quiere cargar con eso.
- **La protección existía solo por el abuso.** Los límites de tasa, la
  protección contra bots y las reglas en el edge mantenían viva la demo, y aun
  así no evitaron que se usara como backend.

## Qué hacer en su lugar { #what-to-do-instead }

**Ejecuta tu propia instancia.** La imagen es pública y no necesita cuenta,
clave ni token:

```bash
docker run -d --name telegram-me -p 8000:3000 --restart unless-stopped \
  ghcr.io/koval01/telegram-me:latest
curl http://localhost:8000/v1/body/durov
```

Sirve la misma API que servía la demo, con la página de inicio, Swagger UI en
`/api` y esta documentación en `/docs/`. El
[Inicio rápido](getting-started/quickstart.md) cubre esto y Docker Compose con
Valkey.

**Intégralo en tu aplicación.** Ejecuta TelegramMe junto a tu aplicación, en el
mismo proyecto de compose o pod, y deja que solo tu aplicación llegue a él: sin
puerto publicado ni dirección pública. Tu aplicación lo llama por la red
privada como a cualquier otro servicio interno. Consulta
[Docker Compose](deployment/compose.md).

**Escala en tus propios servidores.** Varios servidores comparten un Redis;
consulta [Docker Compose](deployment/compose.md),
[Capa de pares](deployment/peers.md) y [Proxy inverso](deployment/reverse-proxy.md).

**Exponlo a internet solo a propósito.** Si tu instancia es pública, activa el
[límite de tasa y la prueba de trabajo](api/protection.md) y mantén la caché
activada. Quien ejecuta una instancia es responsable de su tráfico y de
cumplir los términos de Telegram y la ley (consulta `NOTICE`).

**Dejar la demo.** Cambia la URL base por la de tu instancia. Las rutas, los
parámetros y las respuestas son los mismos, porque es la misma imagen.
