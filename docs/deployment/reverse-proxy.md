# Reverse proxy and load balancing

The API speaks plain HTTP; terminate TLS in front of it. Two things matter:

1. **Client IPs.** Forward `X-Forwarded-For` and set
   [`FORWARDED_ALLOW_IPS`](../configuration.md#forwarded_allow_ips) on the API
   to the proxy's address, otherwise every request appears to come from the
   proxy (which breaks the rate limiter).
2. **Caching.** Responses are cheap to cache for a few seconds; a proxy cache
   in front absorbs bursts for popular channels.

## nginx

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

Then on every API server: `FORWARDED_ALLOW_IPS=<nginx address>`.

`proxy_cache_use_stale ... http_503` keeps serving the last good answer while
t.me is briefly unavailable.

TLS certificates: `sudo certbot --nginx -d api.example.com` (renewal is
installed automatically as a systemd timer).

## Caddy

```caddy title="Caddyfile"
api.example.com {
    reverse_proxy 10.0.0.11:8000 10.0.0.12:8000 {
        lb_policy least_conn
        health_uri /healthz
        health_interval 10s
    }
}
```

Caddy obtains certificates automatically and sets `X-Forwarded-For`.

## Cloudflare

When a request carries Cloudflare's `CF-RAY` header the API adds a
`Cache-Control` header matching [`CACHE_MODE`](../configuration.md#cache_mode),
so a Cloudflare cache rule ("Eligible for cache" on `/v1/*`) caches responses
for 10 s (`normal`) or 120 s (`aggressive`) at the edge. Cloudflare connects
from its own IP ranges: either list them in `FORWARDED_ALLOW_IPS`, or set the
nginx `real_ip` module to restore `CF-Connecting-IP`.

Without a load balancer of your own, the deploy workflow can run Caddy on
every server for Cloudflare to connect to. It handles TLS and
`CF-Connecting-IP` for you: see
[Cloudflare without a load balancer](production.md#cloudflare-without-a-load-balancer).
