# Обратный прокси и балансировка нагрузки

API работает по обычному HTTP; TLS завершайте перед ним. Важны две вещи:

1. **IP клиентов.** Передавайте `X-Forwarded-For` и задайте в API
   [`FORWARDED_ALLOW_IPS`](../configuration.md#forwarded_allow_ips) равным
   адресу прокси, иначе все запросы будут выглядеть пришедшими от прокси (что
   ломает ограничитель частоты запросов).
2. **Кэширование.** Ответы дёшево кэшировать на несколько секунд; кэш прокси
   перед API сглаживает всплески запросов к популярным каналам.

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

Затем на каждом сервере API: `FORWARDED_ALLOW_IPS=<nginx address>`.

`proxy_cache_use_stale ... http_503` продолжает отдавать последний удачный
ответ, пока t.me ненадолго недоступен.

TLS-сертификаты: `sudo certbot --nginx -d api.example.com` (продление
устанавливается автоматически как таймер systemd).

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

Caddy получает сертификаты автоматически и выставляет `X-Forwarded-For`.

## Cloudflare { #cloudflare }

Когда запрос содержит заголовок Cloudflare `CF-RAY`, API добавляет заголовок
`Cache-Control` в соответствии с
[`CACHE_MODE`](../configuration.md#cache_mode), так что правило кэширования
Cloudflare («Eligible for cache» на `/v1/*`) кэширует ответы на edge на 10 с
(`normal`) или 120 с (`aggressive`). Cloudflare подключается со своих
диапазонов IP: либо перечислите их в `FORWARDED_ALLOW_IPS`, либо настройте
модуль nginx `real_ip`, чтобы восстанавливать `CF-Connecting-IP`.

Без собственного балансировщика workflow развёртывания может запускать Caddy
на каждом сервере, чтобы Cloudflare подключался к нему. Он берёт на себя TLS и
`CF-Connecting-IP`: см.
[Cloudflare без балансировщика](production.md#cloudflare-without-a-load-balancer).
