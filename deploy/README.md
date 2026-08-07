# Deploying StockSync Analytics

A single Ubuntu host runs two things: **uvicorn** on `127.0.0.1:8000`, and **nginx**
on `:80`/`:443` serving the built SPA and proxying `/api` to uvicorn. The database
is SQLite on local disk.

The frontend always calls same-origin `/api` (`frontend/src/lib/api.ts`), so there
is no API URL to configure at build time — but it does mean **nginx must proxy
`/api`, or nothing works at all.**

---

## Diagnosing "The request failed."

That message is the frontend's last resort: the server answered with a non-2xx
whose body was not the app's `{error: {...}}` envelope. It is *not* a network
error — an unreachable server produces a different message. So something replied,
and it wasn't the API.

Start here. `/api/health` needs no authentication:

```bash
curl -i http://YOUR_HOST/api/health
```

First, find out which file is even serving the hostname — editing the wrong one
is the most common way to "fix" this and see no change:

```bash
sudo nginx -T | awk '/^# configuration file/{f=$4} /server_name|listen |proxy_pass|root /{print f"  "$0}'
```

| Response | Cause | Fix |
| --- | --- | --- |
| `200` + StockSync JSON | API is up and proxied | Problem is login-specific — check the browser Network tab for the `/api/auth/login` status |
| `200` + *different* JSON | The port belongs to another app | See "Wrong app on the port" below |
| `502` / `504` + HTML | uvicorn is down or unreachable | `journalctl -u stocksync-api -n 50` — usually a refused startup, see below |
| `404` / `405` + HTML | `/api` is not proxied; nginx is serving the SPA for it | The `location /api/` block is missing or sits *below* `try_files` |
| Connection refused | nothing is listening | `sudo systemctl status nginx` |

Test with the real hostname, not `localhost`. `localhost` matches no `server_name`
in this site and falls through to nginx's **default** server block, so a 404 from
`curl http://localhost/api/health` may be telling you nothing about your config:

```bash
curl -i -H 'Host: YOUR_HOST' http://localhost/api/health
```

### Wrong app on the port

A `200` is not enough — check the *shape*. StockSync's health endpoint returns:

```json
{"status":"ok","version":"…","environment":"production","database":{"status":"ok","latency_ms":1.2,"reason":null}}
```

There is no `ok` key and no `service` key anywhere in it. A response like
`{"ok":true,"service":"something-else"}` is a **different application** answering
on that port. Proxy `/api` to it and every StockSync route 404s, which the SPA
reports as the same generic "The request failed." — so this failure impersonates
the proxy failure above.

Find the right port:

```bash
for p in 8000 8001 8002 8080 9000; do
  printf '%s: ' "$p"; curl -s -m 2 "http://127.0.0.1:$p/api/health"; echo
done

# Decisive: StockSync answers 422 here (missing body). A 404 is the wrong app.
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/api/auth/login
```

Then set that port in the `upstream stocksync_api` block in
`deploy/nginx/stocksync.conf` — it is the only place the port appears.

If StockSync answers on **no** port, it was never started on this host: work
through "First deploy" below.

### Two apps on one host

Nginx picks a server block by `Host` header. Give each app its own `server_name`
and neither may be `default_server` — the default catches everything unmatched,
and load order decides the winner silently. Check what is currently claiming it:

```bash
sudo nginx -T | grep -n default_server
```

### The startup refusal

`app/config.py` raises rather than starting a misconfigured production server,
and `create_app()` runs at import — so the process exits and nginx returns 502.
Three settings are mandatory when `STOCKSYNC_ENV=production`:

| Setting | Why |
| --- | --- |
| `STOCKSYNC_JWT_SECRET` | ≥32 chars. Signs access tokens. |
| `STOCKSYNC_ENCRYPTION_KEY` | Encrypts the Shopify Admin API token at rest. |
| `STOCKSYNC_COOKIE_SECURE` | Must be `true` (needs HTTPS), or the insecure hop acknowledged explicitly. |

The third is the one a deployment gets wrong by omission — it defaults to
`false`, which is fine in development and refused in production.

**Set up TLS rather than reaching for `STOCKSYNC_ALLOW_INSECURE_COOKIES=true`.**
That flag exists for TLS terminating at a trusted upstream hop on a private
network. On a public host over plain HTTP it means the session cookie crosses
the internet in clear and anyone on the path can take the admin session. It is
also self-defeating: with `COOKIE_SECURE=true` and no HTTPS the browser silently
drops the cookie, so login appears to succeed and immediately bounces back.

---

## First deploy

```bash
sudo useradd --system --home /srv/stocksync --shell /usr/sbin/nologin stocksync
sudo mkdir -p /srv/stocksync && sudo chown stocksync:stocksync /srv/stocksync
sudo -u stocksync git clone <repo> /srv/stocksync
```

### 1. Backend

```bash
cd /srv/stocksync/backend
sudo -u stocksync python3 -m venv .venv
sudo -u stocksync .venv/bin/pip install -e .
```

### 2. Configuration

```bash
cd /srv/stocksync
sudo -u stocksync cp .env.example .env
sudo chmod 600 .env
sudo -u stocksync backend/.venv/bin/python -c \
  "import secrets; print(secrets.token_urlsafe(48))"
sudo -u stocksync backend/.venv/bin/python -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Edit `/srv/stocksync/.env`:

```ini
STOCKSYNC_ENV=production
STOCKSYNC_DEBUG=false
STOCKSYNC_LOG_FORMAT=json
STOCKSYNC_JWT_SECRET=<the token_urlsafe value>
STOCKSYNC_ENCRYPTION_KEY=<the Fernet key>
STOCKSYNC_COOKIE_SECURE=true
STOCKSYNC_CORS_ORIGINS=https://YOUR_HOST
STOCKSYNC_DATABASE_URL=sqlite+pysqlite:///./data/stocksync.db
```

`CORS_ORIGINS` is belt-and-braces here — the SPA is same-origin, so CORS is
never consulted for it. It matters only if something else ever calls the API
from a browser.

Leave `SHOPIFY_STORE_URL` / `SHOPIFY_ADMIN_API_TOKEN` empty. Production ignores
them by design (a token in a file is plaintext on disk); connect the store
through the UI, which stores it encrypted.

### 3. Database

```bash
cd /srv/stocksync/backend
sudo -u stocksync mkdir -p /srv/stocksync/data /srv/stocksync/storage
sudo -u stocksync .venv/bin/alembic upgrade head
sudo -u stocksync ADMIN_PW='<a strong password>' \
  .venv/bin/python -m app.cli seed --password-env ADMIN_PW
```

`--password-env` keeps the password out of `argv`, where any user on the box
could read it from `ps`.

### 4. Frontend

```bash
cd /srv/stocksync/frontend
sudo -u stocksync npm ci
sudo -u stocksync npm run build     # -> frontend/dist
```

### 5. Services

```bash
sudo cp /srv/stocksync/deploy/systemd/stocksync-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stocksync-api
systemctl status stocksync-api          # confirm it is running before nginx

sudo cp /srv/stocksync/deploy/nginx/stocksync.conf /etc/nginx/sites-available/stocksync
sudo ln -sf /etc/nginx/sites-available/stocksync /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Set `server_name`, `root`, and the `upstream` port to match your host first.

**Do not blindly `rm /etc/nginx/sites-enabled/default`** if anything else is
served from this box — that is another app's site on many installs. Check what
it contains and what claims `default_server` before removing anything.

Applying it safely, with a way back:

```bash
# 1. Snapshot the working config
sudo tar czf /root/nginx-$(date +%F-%H%M).tar.gz /etc/nginx

# 2. Parse-check BEFORE applying. Reload refuses a bad config, so a failed
#    test costs nothing and the running server keeps serving.
sudo nginx -t

# 3. reload, not restart: reload keeps existing connections alive and rolls
#    back to the running config if the new one fails to load.
sudo systemctl reload nginx

# 4. Verify with the real Host header
curl -i -H 'Host: YOUR_HOST' http://localhost/api/health
```

Rollback:

```bash
sudo rm /etc/nginx/sites-enabled/stocksync
sudo nginx -t && sudo systemctl reload nginx
```

### 6. TLS

`nip.io` hostnames resolve publicly, so certbot can issue for them:

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d YOUR_HOST
```

Certbot rewrites the site to listen on 443 and redirect 80. Only after this does
`STOCKSYNC_COOKIE_SECURE=true` work — set it, then `sudo systemctl restart
stocksync-api`.

---

## Updating

```bash
cd /srv/stocksync && sudo -u stocksync git pull

# alembic.ini sets `script_location = alembic` and `prepend_sys_path = .`, both
# relative to the working directory — so this must run from backend/, not the
# repo root with -c. Same reason the app finds ../.env from there.
cd /srv/stocksync/backend
sudo -u stocksync .venv/bin/pip install -e .
sudo -u stocksync .venv/bin/alembic upgrade head

cd /srv/stocksync/frontend
sudo -u stocksync npm ci && sudo -u stocksync npm run build

sudo systemctl restart stocksync-api
```

nginx serves `dist/` from disk, so a frontend-only change needs no reload.

## Rotating a password

```bash
cd /srv/stocksync/backend
sudo -u stocksync NEW_PW='<new password>' \
  .venv/bin/python -m app.cli set-password --email admin@deodap.in --password-env NEW_PW
```

## Backups

Everything that matters is `data/stocksync.db`. SQLite is in WAL mode, so copying
the file while the app runs can capture a torn state — use SQLite's own backup:

```bash
sudo -u stocksync sqlite3 /srv/stocksync/data/stocksync.db \
  ".backup '/srv/stocksync/backups/stocksync-$(date +%F).db'"
```
