# Configuration

Every setting is read from the environment at start-up, once, into
[`backend/app/config.py`](../backend/app/config.py). Values come from real
environment variables first, then from `.env` at the repo root.
[`.env.example`](../.env.example) is the template; `.env` itself is gitignored
and must never be committed.

Names map to `STOCKSYNC_*` — with one documented exception, the four Shopify
settings, which use Shopify's own unprefixed names.

Changing any of these requires an API restart. Nothing is re-read at runtime.

---

## Application

| Variable | Default | Notes |
|---|---|---|
| `STOCKSYNC_ENV` | `development` | `development` \| `test` \| `production`. Several settings behave differently in production — see the table notes below. |
| `STOCKSYNC_DEBUG` | `false` | |
| `STOCKSYNC_API_PREFIX` | `/api` | |
| `STOCKSYNC_LOG_LEVEL` | `INFO` | |
| `STOCKSYNC_LOG_FORMAT` | `console` | `console` \| `json` |

## Database

| Variable | Default | Notes |
|---|---|---|
| `STOCKSYNC_DATABASE_URL` | `sqlite+pysqlite:///./data/stocksync.db` | A relative SQLite path is anchored to the repo root, not the working directory, so `alembic` run from `backend/` and a server started from the root use the same file. |
| `STOCKSYNC_DB_BUSY_TIMEOUT_SECONDS` | `10` | How long a write waits for another writer. SQLite allows one at a time. |
| `STOCKSYNC_DB_POOL_SIZE` | `5` | Ignored by SQLite; applies if the URL is pointed at a server dialect. |
| `STOCKSYNC_DB_MAX_OVERFLOW` | `10` | As above. |

A bare `postgresql://` URL is rejected on purpose — it resolves to a driver
that isn't installed, and the resulting `ImportError` points at the wrong
problem. Use `postgresql+psycopg://`.

## Web

| Variable | Default | Notes |
|---|---|---|
| `STOCKSYNC_CORS_ORIGINS` | `http://localhost:5173` | Comma-separated. Credentials are allowed, so this cannot be `*`. |

## Authentication

| Variable | Default | Notes |
|---|---|---|
| `STOCKSYNC_JWT_SECRET` | *(none)* | **Required in production** — at least 32 characters, or the app refuses to start. Development falls back to a random per-process value, so restarting signs everyone out. |
| `STOCKSYNC_ACCESS_TOKEN_MINUTES` | `15` | |
| `STOCKSYNC_REFRESH_TOKEN_DAYS` | `30` | With "Keep me signed in" checked. |
| `STOCKSYNC_REFRESH_TOKEN_SESSION_HOURS` | `12` | With it unchecked. |
| `STOCKSYNC_COOKIE_SECURE` | `false` | Set `true` in production. Requires HTTPS — with it on over plain HTTP, browsers refuse to send the auth cookies and nobody can sign in. |
| `STOCKSYNC_COOKIE_DOMAIN` | *(none)* | |

## Outbound email

Password reset and email verification are the only things that send mail. Both
work by mailing a link, so neither works at all until this section is filled in.

| Variable | Default | Notes |
|---|---|---|
| `STOCKSYNC_SMTP_HOST` | *(none)* | **This is the switch.** Unset means nothing is ever sent — see below. |
| `STOCKSYNC_SMTP_PORT` | `587` | 587 with STARTTLS is what nearly every relay wants. |
| `STOCKSYNC_SMTP_STARTTLS` | `true` | Leave on for port 587. |
| `STOCKSYNC_SMTP_USERNAME` | *(none)* | Omit for a relay on the same network that needs no credentials. |
| `STOCKSYNC_SMTP_PASSWORD` | *(none)* | For Gmail this is an **app password**, not the account password. |
| `STOCKSYNC_SMTP_FROM` | *(falls back to the username)* | The envelope sender. With neither set it is `no-reply@localhost`, which most relays reject. |
| `STOCKSYNC_SMTP_TIMEOUT_SECONDS` | `10` | |
| `STOCKSYNC_APP_BASE_URL` | `http://localhost:5173` | Where the links point — the **browser's** origin, not the API's. |
| `STOCKSYNC_PASSWORD_RESET_TTL_MINUTES` | `30` | 5–240. Applies to reset and verification links alike. |
| `STOCKSYNC_MAIL_OUTBOX_DIR` | `./storage/outbox` | Where undeliverable mail is written. |

### With no SMTP host, mail is not sent — it is spooled

This is deliberate, and it is the single most common reason a link "was sent"
and never arrived. With `STOCKSYNC_SMTP_HOST` unset, `mailer.send` writes the
whole message to `storage/outbox/` as a `.eml` file and returns `False`. The
start-up log says so:

```
WARNING  email: STOCKSYNC_SMTP_HOST is unset — password-reset and email-verification
         links will NOT be delivered; each message is written to …/storage/outbox instead
```

In development that directory *is* the inbox: open the newest file and click
the link. In production an unset host is a misconfiguration, and the warning at
start-up is the only notice you get — the endpoints cannot tell you, because
varying their response by whether mail was delivered is what leaks which
addresses have accounts.

The link is not in the log, only the path to the file. `RedactingFilter` scrubs
`token=…` from every log record, which is correct — a reset link is a
password-equivalent — and which is why logging the message never worked as a
delivery mechanism.

### Checking that it works

There is no endpoint that will tell you, by design. Use the CLI:

```powershell
cd backend
python -m app.cli send-test-email --to you@example.com
```

It prints the settings it resolved (never the password), sends one message, and
exits non-zero if nothing left the machine. Any SMTP error appears in full on
the log line above the failure.

### Gmail

Gmail refuses ordinary account passwords over SMTP. Turn on 2-Step Verification
for the account, create an app password at
<https://myaccount.google.com/apppasswords>, and use that:

```ini
STOCKSYNC_SMTP_HOST=smtp.gmail.com
STOCKSYNC_SMTP_PORT=587
STOCKSYNC_SMTP_STARTTLS=true
STOCKSYNC_SMTP_USERNAME=you@gmail.com
STOCKSYNC_SMTP_PASSWORD=<the 16-character app password>
```

Leave `STOCKSYNC_SMTP_FROM` unset unless the address is a verified alias on that
account — Gmail rewrites the sender to the authenticated user anyway, and a
mismatched one is a reason for the message to be filed as spam.

## Inventory import

| Variable | Default | Notes |
|---|---|---|
| `STOCKSYNC_MAX_UPLOAD_MB` | `25` | Design doc §8.2. The whole file is read into memory to parse, so this is also the per-request ceiling. |

## Shopify

| Variable | Default | Notes |
|---|---|---|
| `STOCKSYNC_ENCRYPTION_KEY` | *(none)* | Fernet key encrypting the Admin API token at rest. **Required in production.** Development falls back to a random per-process key, which means a stored token becomes unreadable after a restart and the store must be reconnected — the API says exactly that rather than returning a 500. |
| `SHOPIFY_STORE_URL` | *(none)* | Development-only credential — see below. |
| `SHOPIFY_ADMIN_API_TOKEN` | *(none)* | Development-only credential — see below. |
| `SHOPIFY_API_VERSION` | `2026-07` | Shopify versions its Admin API by date. Pinned so a promotion to a new stable version can't change behaviour underneath us. |
| `SHOPIFY_TIMEOUT_SECONDS` | `15` | Per Admin API request. |

Generate the encryption key with:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### The four Shopify settings use Shopify's own names

`SHOPIFY_STORE_URL`, `SHOPIFY_ADMIN_API_TOKEN`, `SHOPIFY_API_VERSION` and
`SHOPIFY_TIMEOUT_SECONDS` are unprefixed, because that is what Shopify's own
documentation calls them — a value can be pasted straight from the page that
generated it without being mentally renamed first.

**There is exactly one spelling for each.** `STOCKSYNC_SHOPIFY_STORE_URL` and
its three siblings are **not read**; setting one has no effect at all, which
for the credential pair means the store simply will not connect. If a store you
expected to be configured shows as not connected, check the variable name first.

Every other setting in this document keeps the `STOCKSYNC_` prefix, and new
ones should. The prefix exists to stop generic names like `ENV`, `DEBUG` or
`DATABASE_URL` colliding with other tooling in a shared environment; the
Shopify four are worth the exception because their names are already
vendor-specific and therefore already unambiguous.

### Credentials from `.env` — development only

Setting **both** `SHOPIFY_STORE_URL` and `SHOPIFY_ADMIN_API_TOKEN` connects a
store without going through the Connect form. This exists so a development
store survives `./tasks.ps1 reset-db` without being re-entered.

Three rules govern it:

**A stored connection always wins.** Connecting a store through the app takes
effect immediately and cannot be silently overridden by a stale value someone
left in a file months ago. Resolution order is database → `.env` → not
connected.

**It is ignored in production.** A token in `.env` is plaintext on disk, which
is strictly worse than the encrypted-at-rest storage the Connect flow uses.
With `STOCKSYNC_ENV=production` the pair is not used, and start-up logs a
warning naming it rather than failing silently.

**Only one is not enough.** A URL without a token cannot call anything, so an
incomplete pair counts as absent.

On the Shopify Connection page an `.env`-configured store shows its URL, a
`From .env` badge and a note explaining that Disconnect cannot act on it —
there is no database row to remove, so the way to change it is to edit `.env`
and restart. The Connect form stays available, because storing a credential
there is how you override the development value. **The token is never rendered
in the UI and never returned by the API**, whichever source it came from.

### Rotating a leaked token

If a token has been pasted into a document, a chat, or committed:

1. Revoke and regenerate it in Shopify admin → Settings → Apps and sales
   channels → your app → API credentials.
2. Replace any committed copies with an obvious fake. Editing the file is not
   enough on its own — the old value stays in git history, so treat the
   original as public from the moment it was committed.
3. Paste the new token into the app's Connect form rather than a file, so it
   is stored encrypted.
