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

## Where everything lives

Every path below assumes this layout, which is what the systemd units and the
nginx site are written against:

| Path | What |
| --- | --- |
| `/home/ubuntu/StockSync-Analytics` | The checkout. `PROJECT` below. |
| `/home/ubuntu/StockSync-Analytics/backend/.venv` | Python environment |
| `/home/ubuntu/StockSync-Analytics/data` | SQLite database and WAL sidecars |
| `/home/ubuntu/StockSync-Analytics/storage` | Generated exports and snapshots |

If yours differs, change it in four places and nowhere else:
`deploy/systemd/stocksync-api.service`, `deploy/systemd/stocksync-backup.service`,
`deploy/nginx/stocksync.conf`, and this file.

## First deploy

The units run as `stocksync`, a service account with no login. Running the app
as `ubuntu` would mean anything that compromised it owned the account you SSH
in with.

```bash
sudo useradd --system --shell /usr/sbin/nologin stocksync

# The checkout is under /home/ubuntu, so `stocksync` needs to traverse it.
# Ubuntu creates home directories 0750, which stops that — and the failure is
# opaque: systemd reports 203/EXEC for a binary that is plainly there, and
# nginx answers 403 for a file it can see.
sudo chmod o+x /home/ubuntu

sudo git clone <repo> /home/ubuntu/StockSync-Analytics
sudo chown -R stocksync:stocksync /home/ubuntu/StockSync-Analytics

# ReadWritePaths= in both units requires these to exist, or the unit fails
# with a mount-namespace error rather than anything about the application.
sudo -u stocksync mkdir -p /home/ubuntu/StockSync-Analytics/{data,storage}
```

Check it worked before going further — this is the failure that wastes an hour:

```bash
sudo -u stocksync stat /home/ubuntu/StockSync-Analytics/backend >/dev/null && echo "stocksync can reach the project"
```

### 1. Backend

```bash
cd /home/ubuntu/StockSync-Analytics/backend
sudo -u stocksync python3 -m venv .venv
sudo -u stocksync .venv/bin/pip install -e .
```

### 2. Configuration

```bash
cd /home/ubuntu/StockSync-Analytics
sudo -u stocksync cp .env.example .env
sudo chmod 600 .env
sudo -u stocksync backend/.venv/bin/python -c \
  "import secrets; print(secrets.token_urlsafe(48))"
sudo -u stocksync backend/.venv/bin/python -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Edit `/home/ubuntu/StockSync-Analytics/.env`:

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
# data/ and storage/ were created in "First deploy" — both units list them in
# ReadWritePaths= and will not start without them.
cd /home/ubuntu/StockSync-Analytics/backend
sudo -u stocksync .venv/bin/alembic upgrade head
sudo -u stocksync ADMIN_PW='<a strong password>' \
  .venv/bin/python -m app.cli seed --password-env ADMIN_PW
```

`--password-env` keeps the password out of `argv`, where any user on the box
could read it from `ps`.

### 4. Frontend

```bash
cd /home/ubuntu/StockSync-Analytics/frontend
sudo -u stocksync npm ci
sudo -u stocksync npm run build     # -> frontend/dist
```

### 5. Services

```bash
sudo cp /home/ubuntu/StockSync-Analytics/deploy/systemd/stocksync-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stocksync-api
systemctl status stocksync-api          # confirm it is running before nginx

sudo cp /home/ubuntu/StockSync-Analytics/deploy/nginx/stocksync.conf /etc/nginx/sites-available/stocksync
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
cd /home/ubuntu/StockSync-Analytics && sudo -u stocksync git pull

# alembic.ini sets `script_location = alembic` and `prepend_sys_path = .`, both
# relative to the working directory — so this must run from backend/, not the
# repo root with -c. Same reason the app finds ../.env from there.
cd /home/ubuntu/StockSync-Analytics/backend
sudo -u stocksync .venv/bin/pip install -e .
sudo -u stocksync .venv/bin/alembic upgrade head

cd /home/ubuntu/StockSync-Analytics/frontend
sudo -u stocksync npm ci && sudo -u stocksync npm run build

sudo systemctl restart stocksync-api
```

nginx serves `dist/` from disk, so a frontend-only change needs no reload.

## Rotating a password

```bash
cd /home/ubuntu/StockSync-Analytics/backend
sudo -u stocksync NEW_PW='<new password>' \
  .venv/bin/python -m app.cli set-password --email admin@deodap.in --password-env NEW_PW
```

## Backups

Two directories hold state, and both sit at the repo root — not under
`backend/`, wherever you run commands from:

| Path | What | Replaceable? |
| --- | --- | --- |
| `data/stocksync.db` | Everything — accounts, inventory, orders, rollups | **No** |
| `storage/exports/` | Generated report files | Yes, by re-exporting |
| `storage/backups/` | Snapshots written below | — |

Only the database is irreplaceable. Exports are regenerable, and a report whose
file has gone is reported as "not ready" rather than as an error.

### Automatic snapshots

```bash
sudo cp /home/ubuntu/StockSync-Analytics/deploy/systemd/stocksync-backup.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stocksync-backup.timer
systemctl list-timers stocksync-backup.timer
```

Daily at 02:30, `Persistent=true` so a host that was off overnight snapshots at
its next opportunity. Retention keeps `STOCKSYNC_BACKUP_KEEP` snapshots
(default 14) and deletes the rest.

On demand — do this before any migration:

```bash
cd /home/ubuntu/StockSync-Analytics/backend
sudo -u stocksync .venv/bin/python -m app.cli backup
```

**Not `cp`.** The database runs in WAL mode, so committed data lives partly in
`stocksync.db-wal`; copying the main file alone yields a database that opens
cleanly and is missing recent writes. `app.cli backup` uses SQLite's online
backup API, which is consistent by construction and does not block writers.

### Checking it actually ran

A green timer over a backup that failed is the thing to avoid. The command
exits non-zero on failure, so systemd records it:

```bash
systemctl is-failed stocksync-backup      # alert on this
ls -lh /home/ubuntu/StockSync-Analytics/storage/backups/    # and on the newest file's age
```

### Offsite

Snapshots on the same disk as the database do not survive losing the disk:

```bash
# The destination is a path on the backup host, not on this server.
rsync -a /home/ubuntu/StockSync-Analytics/storage/backups/ backup-host:/var/backups/stocksync/
```

### Restoring

Snapshots are ordinary SQLite files. Restore with the service stopped so
nothing writes underneath it.

```bash
sudo systemctl stop stocksync-api

# Keep what you are replacing: a restore that turns out to be the wrong
# snapshot is recoverable; one that overwrote the only other copy is not.
sudo -u stocksync cp /home/ubuntu/StockSync-Analytics/data/stocksync.db \
                     /home/ubuntu/StockSync-Analytics/data/stocksync.db.before-restore

# The -wal and -shm sidecars belong to the file being replaced. Left in place
# they are applied on top of the restored database, silently reintroducing
# part of what you just rolled back.
sudo -u stocksync rm -f /home/ubuntu/StockSync-Analytics/data/stocksync.db-wal \
                        /home/ubuntu/StockSync-Analytics/data/stocksync.db-shm

sudo -u stocksync cp /home/ubuntu/StockSync-Analytics/storage/backups/stocksync-YYYYMMDD-HHMMSS.db \
                     /home/ubuntu/StockSync-Analytics/data/stocksync.db

sudo systemctl start stocksync-api
curl -s http://localhost:8000/api/health
```

A snapshot older than your last deploy predates its migrations, so check the
schema before serving traffic:

```bash
cd /home/ubuntu/StockSync-Analytics/backend
sudo -u stocksync .venv/bin/alembic current    # compare against `alembic heads`
sudo -u stocksync .venv/bin/alembic upgrade head
```

Exports referenced by restored rows may no longer be on disk; those show as
unavailable and can be regenerated. Restore `storage/exports/` alongside the
database if you need them intact.

**Rehearse this.** A restore procedure nobody has run is a hypothesis.
