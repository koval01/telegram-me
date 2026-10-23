# Proxy inverso y balanceo de carga

La API habla HTTP plano; termina TLS delante de ella. Importan dos cosas:

1. **IP de los clientes.** Reenvía `X-Forwarded-For` y define
   [`FORWARDED_ALLOW_IPS`](../configuration.md#forwarded_allow_ips) en la API
   con la dirección del proxy; de lo contrario, todas las solicitudes parecerán
   venir del proxy (lo que rompe el limitador de solicitudes).
2. **Caché.** Las respuestas son baratas de guardar en caché durante unos
   segundos; una caché de proxy delante absorbe las ráfagas para los canales
   populares.

## nginx { #nginx }

```nginx title="/etc/nginx/sites-available/telegram-me"
proxy_cache_path /var/cache/nginx/telegram levels=1:2 keys_zone=telegram:32m
                 max_size=512m inactive=30m use_temp_path=off;

upstream telegram_me {
    least_conn;
    server 10.0.0.11:8000 max_fails=3 fail_timeout=10s;
    server 10.0.0.12:8000 max_fails=3 fail_timeout=10s;
    keepalive 32;
}

server {
    listen 443 ssl;
    http2 on;
    server_name api.example.com;
    # ssl_certificate / ssl_certificate_key managed by certbot

    location / {
        proxy_pass http://telegram_me;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_cache telegram;
        proxy_cache_methods GET HEAD;
        proxy_cache_valid 200 10s;
        proxy_cache_valid 404 60s;
        proxy_cache_lock on;
        proxy_cache_use_stale updating error timeout http_502 http_503;
        add_header X-Cache-Status $upstream_cache_status always;

        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
    }
}
```

Después, en cada servidor de la API: `FORWARDED_ALLOW_IPS=<nginx address>`.

`proxy_cache_use_stale ... http_503` sigue sirviendo la última respuesta
correcta mientras t.me no está disponible durante un momento.

Certificados TLS: `sudo certbot --nginx -d api.example.com` (la renovación se
instala automáticamente como un temporizador de systemd).

## Caddy { #caddy }

```caddy title="Caddyfile"
api.example.com {
    reverse_proxy 10.0.0.11:8000 10.0.0.12:8000 {
        lb_policy least_conn
        health_uri /healthz
        health_interval 10s
    }
}
```

Caddy obtiene los certificados automáticamente y define `X-Forwarded-For`.

## Cloudflare { #cloudflare }

Cuando una solicitud lleva la cabecera `CF-RAY` de Cloudflare, la API añade una
cabecera `Cache-Control` acorde con
[`CACHE_MODE`](../configuration.md#cache_mode), de modo que una regla de caché
de Cloudflare («Eligible for cache» en `/v1/*`) guarda las respuestas en el
edge durante 10 s (`normal`) o 120 s (`aggressive`). Cloudflare se conecta
desde sus propios rangos de IP: enuméralos en `FORWARDED_ALLOW_IPS` o configura
el módulo `real_ip` de nginx para restaurar `CF-Connecting-IP`.

Sin un balanceador de carga propio, el workflow de despliegue puede ejecutar
Caddy en cada servidor para que Cloudflare se conecte a él. Se encarga por ti
de TLS y de `CF-Connecting-IP`: consulta
[Cloudflare sin balanceador de carga](production.md#cloudflare-without-a-load-balancer).
