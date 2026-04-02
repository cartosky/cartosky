# Nginx routing (CartoSky V4 runtime)

## Target domains

- `cartosky.com`: primary frontend
- `www.cartosky.com`: redirect or alias to primary frontend
- `api.cartosky.com`: API edge, OAuth callback, boundary tiles
- `api.theweathermodels.com`: optional legacy redirect or temporary compatibility edge

## Route split

### cartosky.com

- `/` → static frontend build output
  - filesystem: `/opt/cartosky/frontend/dist/`
  - SPA fallback must cover frontend routes, for example:
  - `/viewer`
  - `/models`
  - `/variables`
  - `/status`
  - `/admin/performance`
  - `/admin/usage`

### api.cartosky.com

- `/auth/*` → API upstream `http://127.0.0.1:8200`
- `/twf/*` → API upstream `http://127.0.0.1:8200`
- `/api/v4/*` → API upstream `http://127.0.0.1:8200`
- `/api/regions` → API upstream `http://127.0.0.1:8200`
- `/loop/*` → static loop cache alias `/opt/cartosky/data/loop_cache/`
- `/tiles/v3/*` → API upstream `http://127.0.0.1:8200`

## Recommended CartoSky layout

Use these runtime paths for CartoSky:

- `/opt/cartosky/`
- `/etc/cartosky/api.env`
- `csky-api.service`

### Recommended filesystem layout

```bash
sudo cp /etc/nginx/sites-available/api.theweathermodels.com /etc/nginx/sites-available/api.cartosky.com
```

Then edit `/etc/nginx/sites-available/api.cartosky.com` to use the new hostnames and TLS paths.

This keeps the existing nginx layout intact and only adds the new host alongside the old one.

Activation flow:

```bash
sudo ln -s /etc/nginx/sites-available/api.cartosky.com /etc/nginx/sites-enabled/api.cartosky.com
```

Then disable the old site when you are ready to cut traffic over:

```bash
sudo rm -f /etc/nginx/sites-enabled/api.theweathermodels.com
```

## Recommended api.cartosky.com config

Path: `/etc/nginx/sites-available/api.cartosky.com`

```nginx
server {
  server_name api.cartosky.com;

  # TWF OAuth + posting endpoints (must support cookies)
  location ^~ /auth/ {
    proxy_pass http://127.0.0.1:8200;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  location ^~ /twf/ {
    proxy_pass http://127.0.0.1:8200;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  location = /api/v4 {
    proxy_pass http://127.0.0.1:8200;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    add_header Cross-Origin-Resource-Policy "cross-origin" always;
  }

  location = /api/v4/share/media {
    client_max_body_size 12m;

    proxy_pass http://127.0.0.1:8200;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    add_header Cross-Origin-Resource-Policy "cross-origin" always;
  }

  location /api/v4/ {
    proxy_pass http://127.0.0.1:8200;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    add_header Cross-Origin-Resource-Policy "cross-origin" always;
  }

  location = /api/v4/sample {
    limit_req zone=sample_api burst=20 nodelay;
    limit_req_status 429;

    proxy_pass http://127.0.0.1:8200;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_buffering off;
    add_header Cache-Control "no-store" always;
    add_header Cross-Origin-Resource-Policy "cross-origin" always;
  }

  location = /api/regions {
    proxy_pass http://127.0.0.1:8200;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_hide_header Access-Control-Allow-Origin;
    proxy_hide_header Access-Control-Allow-Credentials;
    proxy_hide_header Access-Control-Allow-Methods;
    proxy_hide_header Access-Control-Allow-Headers;

    add_header Access-Control-Allow-Origin "*" always;
    add_header Cross-Origin-Resource-Policy "cross-origin" always;
  }

  location ^~ /loop/ {
    alias /opt/cartosky/data/loop_cache/;
    try_files $uri =404;

    add_header Access-Control-Allow-Origin "*" always;
    add_header Cross-Origin-Resource-Policy "cross-origin" always;

    add_header Cache-Control "no-store" always;
    if ($status = 200) { add_header Cache-Control "public, max-age=31536000, immutable" always; }
    if ($status = 206) { add_header Cache-Control "public, max-age=31536000, immutable" always; }
  }

  # Dynamic manifests: MUST NOT be immutable cached
  location ~* ^/api/v4/.+/frames$ {
    proxy_pass http://127.0.0.1:8200;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_hide_header Cache-Control;
    proxy_hide_header Expires;
    proxy_hide_header Pragma;
    proxy_hide_header ETag;
    add_header Cache-Control "no-store" always;

    proxy_hide_header Access-Control-Allow-Origin;
    proxy_hide_header Access-Control-Allow-Credentials;
    proxy_hide_header Access-Control-Allow-Methods;
    proxy_hide_header Access-Control-Allow-Headers;
    add_header Access-Control-Allow-Origin "*" always;
    add_header Cross-Origin-Resource-Policy "cross-origin" always;
  }

  location ^~ /tiles/v3/ {
    proxy_pass http://127.0.0.1:8200;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_hide_header Access-Control-Allow-Origin;
    proxy_hide_header Access-Control-Allow-Credentials;
    proxy_hide_header Access-Control-Allow-Methods;
    proxy_hide_header Access-Control-Allow-Headers;

    add_header Access-Control-Allow-Origin "*" always;
    add_header Vary "Origin" always;
    add_header Cross-Origin-Resource-Policy "cross-origin" always;
  }

  listen 443 ssl;
  ssl_certificate /etc/letsencrypt/live/api.cartosky.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/api.cartosky.com/privkey.pem;
  include /etc/letsencrypt/options-ssl-nginx.conf;
  ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}

server {
  listen 80;
  server_name api.cartosky.com;
  return 301 https://$host$request_uri;
}
```

## Optional legacy API redirect

After the new API host is verified, you can repurpose the old hostname into a redirect:

```nginx
server {
  listen 80;
  listen 443 ssl;
  server_name api.theweathermodels.com;

  ssl_certificate /etc/letsencrypt/live/api.theweathermodels.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/api.theweathermodels.com/privkey.pem;
  include /etc/letsencrypt/options-ssl-nginx.conf;
  ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

  return 301 https://api.cartosky.com$request_uri;
}
```

If you still have active clients pinned to `api.theweathermodels.com`, keep proxying temporarily instead of redirecting.

## Frontend host config

Serve the frontend from `cartosky.com`, not from the API host.

Recommended behavior:

- `cartosky.com` serves `/opt/cartosky/frontend/dist`
- `www.cartosky.com` redirects to `https://cartosky.com$request_uri`
- all SPA routes fall back to `/index.html`

Example:

```nginx
server {
  server_name cartosky.com;
  root /opt/cartosky/frontend/dist;
  index index.html;

  location / {
    try_files $uri $uri/ /index.html;
  }

  listen 443 ssl;
  ssl_certificate /etc/letsencrypt/live/cartosky.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/cartosky.com/privkey.pem;
  include /etc/letsencrypt/options-ssl-nginx.conf;
  ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}

server {
  listen 80;
  server_name cartosky.com;
  return 301 https://$host$request_uri;
}

server {
  listen 80;
  listen 443 ssl;
  server_name www.cartosky.com;

  ssl_certificate /etc/letsencrypt/live/cartosky.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/cartosky.com/privkey.pem;
  include /etc/letsencrypt/options-ssl-nginx.conf;
  ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

  return 301 https://cartosky.com$request_uri;
}
```

## Required non-nginx changes

These must also be updated or the new hostnames will not work correctly.

- `/etc/cartosky/api.env`
  - `TWF_REDIRECT_URI=https://api.cartosky.com/auth/twf/callback`
  - `FRONTEND_RETURN=https://cartosky.com`
  - `CORS_ORIGINS=https://cartosky.com,https://www.cartosky.com`
  - `R2_PUBLIC_BASE=https://cdn.cartosky.com`
  - `CARTOSKY_BOUNDARIES_MBTILES=/opt/cartosky/data/boundaries/v1/cartosky_boundaries.mbtiles`
  - `CARTOSKY_TILES_PUBLIC_BASE_URL=https://api.cartosky.com`

### Scheduler env files

Your scheduler env files remain in:

- `/etc/cartosky/scheduler.env`
- `/etc/cartosky/scheduler-gfs.env`
- `/etc/cartosky/scheduler-nam.env`
- `/etc/cartosky/scheduler-nbm.env`

For this domain/branding cutover, they usually do not need changes.

Leave them alone unless you are intentionally changing:

- loop cache paths
- data root paths
- worker counts
- model build settings

After this boundary migration cutover, the standalone `csky-tile-server.service` is no longer needed. Once nginx is pointing `/tiles/v3/*` at port `8200` and boundary TileJSON is verified, disable the old service:

```bash
sudo systemctl disable --now csky-tile-server
```

## Deploy commands

After editing the vhost files:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

If nginx complains about missing certificates, issue them first with Certbot or install the Cloudflare origin cert before reloading.

## Certbot caveat

Certbot only rewrites the nginx site it decides is currently serving the requested hostname.

If `certbot --nginx -d api.cartosky.com` updates `/etc/nginx/sites-enabled/default` instead of your dedicated CartoSky site, that usually means one of these is true:

- `/etc/nginx/sites-available/api.cartosky.com` was not enabled in `sites-enabled`
- the active CartoSky vhost still had the wrong `server_name`
- the copied file still had the old HTTP redirect block for `api.theweathermodels.com`
- nginx had another catch-all or default site that Certbot matched first

Before rerunning Certbot, verify these conditions:

```bash
sudo ls -l /etc/nginx/sites-enabled
sudo grep -R "server_name .*api.cartosky.com\|server_name .*api.theweathermodels.com" /etc/nginx/sites-available /etc/nginx/sites-enabled
```

The dedicated CartoSky file should own both of these blocks:

- `server_name api.cartosky.com;` on port 443
- `server_name api.cartosky.com;` on port 80

It should also reference the CartoSky certificate paths once the certificate exists:

```nginx
ssl_certificate /etc/letsencrypt/live/api.cartosky.com/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/api.cartosky.com/privkey.pem;
```

If Certbot already inserted CartoSky TLS directives into `/etc/nginx/sites-enabled/default`, move ownership back to the dedicated CartoSky vhost and remove the CartoSky-specific server block from `default`.

## Verification

- `https://api.cartosky.com/api/v4/health`
- `https://api.cartosky.com/tiles/v3/boundaries/v1/tilejson.json`
- TWF login round-trip through `https://api.cartosky.com/auth/twf/callback`
- frontend requests from `https://cartosky.com`
- share uploads returning `https://cdn.cartosky.com/...`

## Notes

- `nslookup` returning Cloudflare IPs is normal when proxying is enabled.
- systemd units must still use absolute venv paths.
- Weather overlays are now grid-only; active runtime traffic should use `/api/v4/{model}/{run}/{var}/grid-manifest` plus `/api/v4/grid/...` frame files.
- Previous-version runtime endpoints should remain retired.
