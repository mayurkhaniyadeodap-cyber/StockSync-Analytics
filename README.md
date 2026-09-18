# StockSync Analytics

Internal portal for DeoDap that reconciles an imported inventory sheet against Shopify sales, by SKU. It shows where the two layers agree and where they don't: what's selling with no stock behind it, what's sitting still, and what never matched at all.

**Status: simplified to inventory analytics with Shopify sales enrichment.** The uploaded sheet is the source of truth; Shopify supplies units sold and nothing else. The product catalogue, SKU matching and every catalogue-derived analytic were removed — see "What Shopify is for" below.

## Stack

| | |
|---|---|
| Frontend | React 19 · TypeScript · Vite · plain CSS with design tokens as custom properties |
| Backend | Python 3.12 · FastAPI · SQLAlchemy 2.x · Alembic |
| Database | SQLite — the supported default for the MVP and single-server deployments |
| Charts | Hand-rolled SVG, no charting library — each has a wide and a compact drawing, see [Layout](#layout) |

The database layer is dialect-agnostic: SQLite specifics are confined to [backend/app/db/session.py](backend/app/db/session.py) (engine construction, connection pragmas) and [backend/alembic/env.py](backend/alembic/env.py) (batch migrations). Moving to PostgreSQL means installing a driver and changing one environment variable — see [Moving to PostgreSQL later](#moving-to-postgresql-later).

## Source of truth for the UI

- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — every environment variable, what it defaults to, and which ones production requires.
- [frontend/src/styles/tokens.css](frontend/src/styles/tokens.css) — **authoritative** for colour, radius, shadow and spacing. Nothing else may hold a hex value.
- [prototype/strata-prototype.html](prototype/strata-prototype.html) — where the component *structure* came from, and still a fair guide to class names and the four states every screen has.

**The prototype is no longer the visual target.** The palette moved to a blue
primary on a light blue-grey page, radii went from 6px to 10px, cards gained a
hairline shadow, and the navigation rail became dark navy. Those values live in
`tokens.css` and every one of the twelve pages inherits them, which is why the
redesign touched almost no page code — the class names are still the
prototype's. Read the prototype for *what a panel is made of*; read the tokens
for what it looks like.

Prefer extending an existing component over inventing one: the four states every
screen needs (loading, empty, error, populated) are already solved in the ones
that exist, and `PanelHead`, `KpiCard` and `Pager` exist so a panel header, a
figure and a pager are the same object everywhere.

## Getting started

Requires Python 3.12+ and Node 20+. **No database server to install** — SQLite is a file, and Python ships the driver.

```powershell
./tasks.ps1 setup          # venv + npm install + seed .env
./tasks.ps1 migrate        # create the database schema
./tasks.ps1 seed           # create the default administrator
./tasks.ps1 check          # lint, format, typecheck, test — both sides
./tasks.ps1 dev            # API on :8000, web on :5173
```

Then open <http://localhost:5173>. The API docs are at <http://127.0.0.1:8000/api/docs>.

### The first account

There is no self-registration — accounts are issued. `./tasks.ps1 seed` creates
one administrator:

| | |
|---|---|
| Email | `admin@deodap.in` |
| Name | Administrator |
| Role | Admin |

**You choose the password.** There is no built-in one and no default: `seed`
reads `STOCKSYNC_ADMIN_PASSWORD`, or the variable named by `--password-env`, or
prompts for it twice with no echo. If it can find none of those it fails rather
than inventing one. Minimum 12 characters.

> An earlier version shipped a fixed password here and in
> [backend/app/cli.py](backend/app/cli.py). It was meant as a local convenience
> and reached production, which made the admin credential of a live service a
> constant in a public repository. **If you deployed before this change, rotate
> that account now:**
>
> ```powershell
> cd backend
> .\.venv\Scripts\python.exe -m app.cli set-password --email admin@deodap.in
> ```

For a scripted or unattended setup, put the value in the environment rather
than on the command line, where it would be visible in `ps` and shell history:

```powershell
$env:STOCKSYNC_ADMIN_PASSWORD = Read-Host -AsSecureString | ConvertFrom-SecureString -AsPlainText
.\tasks.ps1 reset-db
```

```bash
read -rs STOCKSYNC_ADMIN_PASSWORD && export STOCKSYNC_ADMIN_PASSWORD
python -m app.cli seed
```

To issue a different account, pass an address — `--name` is required for any
address other than the administrator's.

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.cli seed --email you@deodap.in --name "Your Name"
```

### Database

`data/stocksync.db` is created automatically on first run, along with its directory. Nothing to provision, and `.env` needs no database entry unless you want a different path.

The connection is opened with four pragmas, all set in [session.py](backend/app/db/session.py) and none of them optional:

| Pragma | Why |
|---|---|
| `foreign_keys=ON` | SQLite ignores foreign keys by default. Without this every FK in the schema would be a comment rather than a constraint. |
| `journal_mode=WAL` | Readers proceed during a write, so a Shopify sync doesn't block the dashboard for its duration. |
| `busy_timeout` | A competing writer waits instead of failing instantly with "database is locked". 30s, chosen to exceed the longest write the application makes — swapping in a rebuilt rollup. |
| `synchronous=NORMAL` | The documented safe pairing with WAL — crash-durable, and much faster on bulk inserts. |

To start over:

```powershell
./tasks.ps1 reset-db       # delete the file, rebuild from migrations, re-seed the admin
```

The database file, its `-wal`/`-shm` sidecars and the whole `data/` directory are gitignored.

### Files on disk

Two directories hold state, both at the repo root and both gitignored. Paths are
resolved against the repo root, never the working directory — which is what
stops `alembic` run from one folder and the server started from another using
two different database files.

| Path | What | Setting |
|---|---|---|
| `data/` | The database and its WAL sidecars | `STOCKSYNC_DATABASE_URL` |
| `storage/exports/` | Generated report files | `STOCKSYNC_EXPORT_DIR` |
| `storage/backups/` | Database snapshots | `STOCKSYNC_BACKUP_DIR` |

Only `data/` is irreplaceable. Exports are regenerable, and a report whose file
has gone is reported as unavailable rather than as an error.

### Backups

`cp` is not a backup here. In WAL mode committed data lives partly in
`stocksync.db-wal`, so copying the main file yields a database that opens
cleanly and is missing recent writes. `app.cli backup` uses SQLite's online
backup API instead — consistent by construction, and it does not block writers.

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.cli backup
```

It keeps the newest `STOCKSYNC_BACKUP_KEEP` snapshots (14) and deletes the rest,
by count rather than by age — an age rule would delete a quiet workspace's only
snapshots precisely when nothing is being written to replace them. In production
a systemd timer runs it nightly; see [deploy/README.md](deploy/README.md) for
that and for the restore procedure.

### Moving to PostgreSQL later

Nothing in the application layer is SQLite-specific. To switch:

1. `pip install "psycopg[binary]"` (add it to `backend/pyproject.toml`).
2. Set `STOCKSYNC_DATABASE_URL=postgresql+psycopg://user:password@host:5432/stocksync`.
3. `./tasks.ps1 migrate`.

`STOCKSYNC_DB_POOL_SIZE` and `STOCKSYNC_DB_MAX_OVERFLOW` already exist and start applying automatically; SQLite ignores them. Alembic drops out of batch mode on its own.

What does *not* move automatically is the data, and two schema choices exist only because SQLite has one writer: the background worker is a single thread, and the login and rate-limit counters are in process memory. Neither has to stay that way once a server dialect is behind the URL — see [core/throttle.py](backend/app/core/throttle.py) for the seam.

## Commands

Every routine command goes through `tasks.ps1` so local and CI agree. The
frontend entries are thin wrappers around the npm scripts, so `npm run check`
by hand does exactly the same thing.

| | |
|---|---|
| `./tasks.ps1 check` | Lint, format-check, typecheck, test — both sides. **Run before every commit.** |
| `./tasks.ps1 dev` | API and web together |
| `./tasks.ps1 api` / `web` | One side only |
| `./tasks.ps1 migrate` | `alembic upgrade head` |
| `./tasks.ps1 revision "msg"` | Autogenerate a migration |
| `./tasks.ps1 build` | Production frontend build |
| `./tasks.ps1 seed` | Create the workspace and the default administrator |
| `./tasks.ps1 reset-db` | Delete the SQLite file, rebuild it from migrations, re-seed |
| `python -m app.cli seed` | As above, or `--email`/`--name` for another account (run from `backend/`) |
| `python -m app.cli set-password` | Change a password and sign that user out everywhere |
| `python -m app.cli backup` | Write a consistent database snapshot and prune old ones |
| `python -m app.cli check-inventory` | Report SKUs whose two quantity fields disagree (`--repair` to fix) |

Run the `app.cli` commands **from `backend/`**. They read `../.env` relative to
the working directory, so from anywhere else they pick up different settings.

## Layout

```
backend/app/
  main.py          FastAPI app factory, CORS, error handlers
  config.py        settings from STOCKSYNC_* env vars
  core/errors.py   the error envelope — every failure says what to do next
  core/logging.py  logging with credential redaction on every record
  core/security.py argon2id hashing, JWT signing, refresh tokens
  core/crypto.py   Fernet encryption for third-party credentials at rest
  core/throttle.py failed-login counter and per-user limits on slow work
  db/              engine, session, SQLite pragmas, health probe, base
  api/routes/      endpoints
  models/          workspace, user, preferences, auth sessions, import
                   batches, inventory items, linked sheets, shopify
                   connection, sync runs, orders, order line items, sku daily
                   metrics, activity events, reports
  repositories/    every query lives here; services stay readable as rules
  workers/runner.py    one background thread; SQLite has one writer
  services/auth.py     authenticate, issue/rotate/revoke sessions
  services/import_files.py  parse CSV/XLSX — encoding, delimiter, headers
  services/import_url.py    fetch a URL safely: address guard, size cap
  services/google_sheets.py  a sheet link → its CSV export URL, and back
  services/sheets.py        remembering a sheet's address so it runs again
  services/imports.py       run an import and reconcile it into stock
  services/shopify.py       validate, store and revoke a store credential
  services/shopify_client.py  the Admin API: pagination, rate limits, errors
  services/sync.py          pull orders, committed per page and resumable
  services/activity.py      the account of how a run reached its result
  services/metrics.py       builds the daily rollup — the only writer of it
  services/analytics.py     reads it: the six cards, the trend, the SKU table
  services/insights.py      pure derivations over the facts — no query, no clock
  services/report_data.py   what goes in each report — one builder per type
  services/report_files.py  renders a report as CSV, XLSX or PDF
  services/reports.py       queue, generate on the worker, download, delete
  services/report_store.py  where an export lives on disk, and its cleanup
  services/backup.py        consistent SQLite snapshots, with retention
  cli.py           issue accounts, reset passwords, take a backup

frontend/src/
  styles/tokens.css      the palette, radii and shadows — the one place
  styles/base.css        reset, tabular numerals, reduced-motion
  styles/components.css  .btn .panel .badge … every page renders through these
  styles/fonts.css       self-hosted Figtree + JetBrains Mono @font-face
  lib/format.ts          n() inr() pct() freshness() — lakh/crore grouping
  lib/api.ts             fetch wrapper; auth cookie, error envelope, timeouts
  contexts/              auth, theme, toasts, shared Shopify status, range
  hooks/useSync.ts       polls sync state — fast during a run, slowly between
  hooks/useSharedRange.ts  the window every page reads over, or a local one
  hooks/useChartTooltip.ts  the one tooltip every chart on a page shares
  components/shell/      header, sidebar, page template, panel header
  components/Logo.tsx    the product mark — rail, sign-in, boot, favicon
  components/KpiCard.tsx a figure with a glyph, and a trend *or* a note
  components/Pager.tsx   numbered paging for the list pages
  components/ErrorBoundary.tsx   stops a render error becoming a blank page
  components/SyncStateNotice.tsx "sync running" / "figures behind", one copy
  components/charts/     line, horizontal bar, donut and stacked bar, drawn
                         as plain SVG. Each has a wide drawing and a compact
                         one: the SVG scales to its container and the type is
                         in viewBox units, so one geometry cannot serve both a
                         full-width panel and a third of a row
  components/charts/geometry.ts  those drawings, and the axis-gutter sizing
  pages/                 login, settings, import, import history, Shopify,
                         sync history, dashboard, reports
  pages/dashboard/       the panels, their reads, and their caveats
  pages/analytics/       overview, sales, inventory, performance, complaints

deploy/
  README.md              first deploy, updating, backups, restore, diagnosis
  nginx/stocksync.conf   serves the SPA, proxies /api to uvicorn
  systemd/               the API unit, and the nightly backup timer
```

## Inventory import

**A sheet needs a `SKU` column. That is the whole requirement.** Everything else
is optional and defaults to zero — a sheet carrying fewer columns is still worth
importing, and refusing real data over a column the user does not have helps
nobody. The SKU is the exception because it is the join key: there is nothing to
infer it from.

Column names are matched for you. Case, spaces, dots, hyphens and underscores
fold away before comparison, so `Total Qty.`, `TOTAL-QTY` and `total_qty` are one
name.

| Field | Also accepted as |
|---|---|
| SKU | `sku code`, `product sku`, `item sku`, `product code`, `item code`, `article code`, `style code`, `code` |
| Total Count | `count` |
| Total Orders | `orders`, `order count` |
| Total Qty | `qty`, `quantity`, `units`, `stock`, `on hand`, `available`, `inventory` … |
| Reason | `complaint`, `issue`, `issue type`, `complaint type` |
| Order No | `order number`, `order id`, `invoice no`, `awb` |

`Qty` and `Quantity` are deliberately **not** listed as Total Qty aliases in the
matcher: each field claims the first column matching any of its aliases
independently, so listing them there would make `Total Qty.` point at a
`Quantity` column sitting to its left. A sheet whose only stock column is called
`Quantity` still fills Total Qty — the resolved quantity is mirrored into it when
the sheet has no column of its own.

### Two shapes, detected automatically

**Format 1 — already aggregated.** One row per SKU, counts already totalled:

```
SKU | Total Count | Total Orders | Total Qty | Item Defect Partial | … | Missing
```

Taken as given.

**Format 2 — raw complaint rows.** One row per complaint, as a complaints system
exports it:

```
Date | Order No | SKU Code | Reason | Employee
```

Grouped by SKU here, with the three counts *derived*:

| | |
|---|---|
| Total Count | rows for this SKU — one complaint, one row |
| Total Orders | **distinct** order numbers, so two faulty items in one parcel count once. No order column at all means one order per row; a blank order number counts as its own, because nothing distinguishes blanks and folding them together would under-count |
| Total Qty | the quantity column if the export has one, otherwise one per row |

The presence of a `Reason` column is what tells the two apart — it is the only
reliable signal, since the raw export shares `SKU` with the aggregated one and
carries none of its count columns.

### Reason → complaint column

`Damage` → Item Damage Complete · `Non Working` → Electronics Item Nonworking
Complete · `Missing` → Missing · `Missing Part` → Missing Part · `Wrong Item` →
Item Mismatch Wrong Item Delivered — and the other five columns likewise.

The table is **ordered most specific first and matched in that order**, because
the general terms are substrings of the specific ones: `missing part` contains
`missing`, `damage partial` contains `damage`. Each reason is tried as an exact
fold across the whole table first, then as a substring — so `Damage` wins
outright, and `damaged in transit — partial` still reaches Item Damage Partial.

**An unrecognised reason is not an error.** The row still counts towards Total
Count, Total Orders and Total Qty; only the complaint breakdown misses it, and
the import result names every reason it could not place with a row count, so the
mapping can be extended rather than the data quietly under-reporting.

### Duplicate SKUs

The same SKU appearing more than once **merges, summing every count column** —
quantity, the three counts and all ten complaint categories. Matching is on the
normalised SKU, so `DD-1`, `dd 1` and `DD_1` are one SKU; the sheet's own first
spelling is kept, because that is what the user recognises.

Three things the parser handles because real exports do them:

| | |
|---|---|
| A title row above the headers | The header row is found, not assumed to be row 1 |
| Windows-1252 encoding | Tried after UTF-8, so `Café` survives an ERP export |
| Semicolon delimiters | Sniffed, not assumed — Excel writes them under a European locale |

Rows with no SKU are rejected individually and reported by row number — the rest
of the file still imports, and the batch is marked `partial`.

An import **replaces the whole dataset**: a SKU missing from the latest sheet
is gone, not kept. The workspace holds what the newest successful
import says and nothing else, so a 309-row export leaves 309 SKUs however many
were there before, and re-importing the same file twice does not double
anything.

That is a reversal. The previous rule — write the rows you were given, leave
every other SKU alone — is defensible per file and wrong in aggregate: a
1,372-row spreadsheet followed by a 309-row export left **1,641 SKUs on screen,
1,332 of them stale**, carrying complaint totals from a file nobody had looked
at in weeks into every figure the Dashboard showed. Nothing on screen
distinguished the two, which is what made it a data problem rather than a
preference.

Three things bound it:

- **Only a successful import replaces anything.** A file that will not parse, or
  one whose every row is rejected, raises before the delete and leaves the
  previous dataset exactly as it was. The route owns the transaction and does
  not commit until every replacement row is written.
- **Shopify is untouched.** Orders, line items and the rollup are built from the
  store, not the sheet. Re-importing without a SKU hides its row; the sale is
  still there when the SKU comes back.
- **Import History keeps every batch.** The data is replaced; the audit trail is
  not. The response also reports `items_removed`, and the import screen shows
  it, because a SKU count dropping from 1,641 to 309 should never be a surprise.

One consequence worth expecting: a workspace can no longer hold both a dated and
an undated *file*. The mixed state now only arises inside a single complaint
export where some rows carry a readable date and some do not.

Measured on a 4,000-row complaint export with 300 SKUs: grouped into 300 rows,
3,233 complaints classified, 767 rows carrying two unrecognised reasons reported
back by name. The same data as Excel under entirely different column headings
produced identical totals.

### Importing from a Google Sheet

A sheet can be imported by link instead of by file, and the link can be kept so
it can be run again. **A Google Sheet import *is* a CSV import that arrived by a
different door** — same fetch, same parser, same reconciliation. The only thing
Import History records differently is the method (`google_sheet` rather than
`csv_upload` or `excel_upload`).

The link is the one the browser bar shows. Three shapes are understood: a normal
sharing or editing link (`/d/<id>/edit#gid=123`), a published-to-web link
(`/d/e/<key>/pubhtml`, a different namespace exporting from `/pub` rather than
`/export`), and an export URL the user already built, which passes through
untouched.

| | |
|---|---|
| Which tab | `gid`, read from the fragment as well as the query — a browser puts it in `#gid=123`, and a fragment is never sent to a server, so it has to be moved into the query to survive. **An absent gid is left out entirely rather than defaulted to 0**: deleting a workbook's first tab leaves the next one with whatever id it was created with, and Google serves the first visible sheet when asked for none — which is the one the user is looking at |
| Access | The sheet is public or the import fails. Nothing here holds a credential, and StockSync has no OAuth token — which is also why a linked sheet is named by the user rather than by its real title, since that needs the Sheets API |
| A sign-in wall | Reported as *not publicly accessible*, with the Share → Anyone with the link → Viewer path to fix it, rather than as "that link returned a web page" |

**Linking one records the address, nothing else.** Re-syncing runs the same
fetch and the same importer against the same URL, so a link grants no access it
did not already have. One row per *tab*, not per document — two tabs of one
workbook are two sheets to import, and folding them together would make linking
the second silently rewrite the first. Settings shows the link as pasted, plus
when it was last run and how it went.

Fetching a user-supplied URL server-side is the part that needs care: the server
can reach the loopback interface, the private network it sits on, and a cloud
metadata endpoint the browser cannot. Every address is therefore checked before
a connection is opened — **on the original URL and on each redirect hop**, since
a public host can redirect to `127.0.0.1` — with at most three hops, the upload
size cap applied to the declared length *and* to the bytes as they stream, and
an HTML body rejected rather than parsed as a one-column sheet.

## Shopify connection

Store URL and Admin API access token, validated against Shopify **before**
anything is stored — a token that cannot authenticate never reaches the
database. The token needs **`read_orders`** — and only that — and is held as
Fernet ciphertext under `STOCKSYNC_ENCRYPTION_KEY`; it is never returned by the
API, in any form. Disconnecting overwrites the stored ciphertext and keeps the
record of what was connected.

### Connecting a development store from `.env`

Setting both of these connects a store without using the form, so a development
store survives `reset-db`:

```ini
SHOPIFY_STORE_URL=mystore.myshopify.com
SHOPIFY_ADMIN_API_TOKEN=<the Admin API access token from your custom app>
```

The store URL also accepts the full `https://admin.shopify.com/store/mystore`
address you get from the browser bar, or the bare store name.

These four are the only settings without the `STOCKSYNC_` prefix — they use
Shopify's own variable names, so a value can be pasted straight from where it
was generated. There is exactly one spelling for each; `STOCKSYNC_SHOPIFY_*` is
not read.

**A store connected through the app always wins over these**, and they are
**ignored entirely when `STOCKSYNC_ENV=production`** — a token in a file is
plaintext on disk, which is worse than the encrypted storage the Connect form
uses. The page shows a `From .env` badge and explains that Disconnect can't act
on it, since there's no row to remove. The token is never displayed and never
returned by the API.

Full reference: [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Shopify sync

**A sync starts after every successful import.** An import restates which SKUs
matter, and their sales are only as current as the last pull — so leaving that
to a button meant a freshly imported workspace showing stale figures, or none,
until someone remembered to press it. The import screen follows the run and says
what happened; nothing else has to be clicked.

The sync **is not scoped to the imported SKUs, and cannot be**: Shopify serves
orders by date, not by SKU. It does not need to be. Analytics left-joins from
`inventory_items`, so only the latest import's SKUs are ever displayed, while
`shopify_sales_all` keeps counting the whole store because that is the
denominator Shopify Sales % divides by.

Two refusals are not import failures — the response says `started: false` with a
reason and the rows stand: **not connected** (no store to read from) and
**already running** (a run in flight already covers these SKUs). Both fall back
to the rollup rebuild a sync would otherwise have done at its own end. A sync
that starts and then fails leaves the import in place and offers **Retry**,
because re-uploading the file would fix nothing.

**Sync now** survives on the Shopify page alone — the way back after a failure,
and the way to refresh between imports. It was removed from the Dashboard
header, the Shopify widget and Sync History, where it only asked the user to
remember something the import now does.

Either way the sync queues a background pull of orders and their line items —
one stage, because there is no catalogue. It returns immediately with a run row;
the page polls that row, so progress survives a reload and a restart leaves a
visible record rather than a job that quietly never existed.

| | |
|---|---|
| Pagination | Cursor-based (`page_info`), 250 per page, following Shopify's `Link` header |
| Rate limits | A 429 is retried with `Retry-After` rather than failing the sync |
| Writes | Committed **per page**, so SQLite's write lock is held for milliseconds and an interrupted sync keeps what already landed |
| Concurrency | One sync at a time, on one worker thread — SQLite serialises writes, so a second would not go faster |
| Time budget | A run stops after `STOCKSYNC_SYNC_MAX_SECONDS` and queues a continuation from its cursor, so a long first sync arrives in chunks that each finish — see [below](#a-long-sync-finishes-in-chunks) |
| Partial | If the stage stops part-way the pages that landed are kept, the run is `partial`, and the cursor is stored so the next run **resumes** instead of restarting |
| Stale cursors | A resume cursor **older than the lookback window is discarded**. `page_info` encodes the `created_at_min` that produced it, so following an old one walks the result set as it was when the cursor was issued — orders created since were never in that sequence. On the production store that showed as syncs completing successfully while the newest 30 hours stayed unfetched, indefinitely |
| Interrupted | A run that has gone **quiet for five minutes** is closed out, keeping its cursor. The window matters: reclaim used to close *every* running row on the assumption that this process is the only one, which is never true under `uvicorn --reload`, so a live sync in another process was stamped `sync_interrupted` while it carried on working |
| Recovery | Reclaim runs at start-up, when a sync is started, **and on every poll of `GET /shopify/sync`**. Start-up alone was not enough: a run killed less than five minutes before the process returned was too fresh to reclaim then, and nothing looked again — so it stayed `running`, blocked every future sync, and only a second restart cleared it |
| Duplicates | Orders and line items are deduplicated **within a page**, not only against the database. Cursor pagination is not a snapshot — an order updated mid-walk shifts position and can be returned twice — and inserting the second sighting killed the page flush on the unique constraint |
| Termination | The walk stops if a cursor **repeats**. A `rel="next"` pointing somewhere already visited looped forever, re-fetching and re-committing pages while the run's counters climbed — indistinguishable from progress, with `finished_at` never set |

Money arrives from Shopify as a decimal string and is stored as integer paise.
`sku_at_sale` records the SKU as it was when the sale happened, so a sale still
reconciles after the variant is renamed or deleted.

### A long sync finishes in chunks

A first sync covers `order_lookback_days` — 90 by default — at 250 orders a page
against Shopify's roughly two requests a second. That is minutes to tens of
minutes on a real store, with no natural stopping point. Unbounded, any
interruption inside that window lost the run's *status* (never its data, which
commits per page) and the next attempt started the same long walk. In
development that meant every file save, because the worker is a thread inside a
server started with `--reload` — so the run never converged.

A run therefore has a budget: `STOCKSYNC_SYNC_MAX_SECONDS`, 120 by default. The
check happens **after a page commits**, so a run only ever stops on a boundary
it has already made durable, and it measures monotonic time, because a clock
adjustment mid-sync must not stretch or truncate the budget.

Running out of time is not a failure — nothing went wrong and there is nothing
for the user to fix. The run is recorded as `partial` with code `sync_paused`,
which is what the amber badge and the resume cursor already mean, and it queues
its own successor with the trigger `continuation`. Sync History labels that
**Continued**, so a chain reads as one long sync in pieces rather than as the
application syncing over and over for no reason.

Four independent reasons the chain terminates, because an automatic chain that
does not stop is worse than a slow one:

| | |
|---|---|
| The window is finite | `order_lookback_days` of orders, and no more |
| Progress is required | A chunk continues only if the cursor **moved**. A chain that stops making progress stops entirely |
| The ordinary exit | When Shopify offers no next page the stage is not paused at all — the run succeeds and the chain ends |
| A cycle on Shopify's side | The walk refuses a cursor it has already followed |

**Failures never chain.** A revoked scope or an expired token would otherwise
retry itself in a loop against a credential nobody has fixed yet. Nor does a
continuation displace a person: if someone starts their own sync in the
meantime, or the store is disconnected, the continuation simply does not start —
the cursor is committed, so the next sync resumes from it either way.

### How current the orders are

`GET /api/shopify/freshness` asks Shopify for its newest order, compares it with
the newest order synced, and records the answer on the connection.

This exists because the previous staleness signal could not detect the problem it
was there for. It compared `sku_daily_metrics.computed_at` against
`orders.synced_at` — whether the *derived* layer had caught up with the rows we
hold, which says nothing about whether those rows have caught up with Shopify. On
the real store it reported "current" while the database was 34 hours and 10,731
orders behind.

| | |
|---|---|
| Cost | One Shopify request. Called from the Shopify page and at the end of every sync — **never** on a dashboard read, which would put the rate limit in the path of every page load |
| Tolerance | 15 minutes. A sync takes minutes and orders arrive continuously, so a small gap is the steady state, not a fault |
| Unreachable | `behind: null`, and the UI says **Unknown** — "we do not know" is a different answer from "we are current", and reporting the second when the first is true is exactly how a stale figure gets presented as a fresh one |
| Stored | `store_latest_order_at` and `freshness_checked_at` on the connection, so other screens can report the gap without paying for the call again |

A failed check leaves the last good value alone rather than erasing it.

## What Shopify is for

**One thing: how many units a SKU sold.** Nothing else.

Order line items carry `sku_at_sale`, so a sheet SKU matches a sale without the
product catalogue ever being fetched. That is why the catalogue is gone — it was
load-bearing for nothing, and it made `read_products` a hard requirement for a
figure that never depended on it. The integration runs on **`read_orders`
alone**.

| | Source |
|---|---|
| SKU, Quantity, Total Count, Total Orders, Total Qty, all ten complaint columns | the uploaded sheet |
| Shopify Sales, Shopify Sales % | Shopify, joined on the normalised SKU |

Matching is a single left join on `sku_normalized`. There is no fuzzy scoring, no
review queue, no variant, no vendor, no product status and no inventory feed. A
sheet SKU with no Shopify sales shows zero rather than disappearing — the sheet
is the source of truth, so its rows exist whether or not the store sold any.

## The upload format

Fixed, and the importer knows it exactly:

```
SKU · Quantity · Total Count · Total Orders · Total Qty
Item Defect Partial · Item Defect Complete
Item Damage Partial · Item Damage Complete
Order Wrong Parcel
Electronics Item Nonworking Partial · Electronics Item Nonworking Complete
Missing · Missing Part
Item Mismatch Wrong Item Delivered
```

Every one is an explicit typed column on `inventory_items`, not a JSON blob:
"Total complaints" is then one SQL expression rather than ten JSON extracts.
The list lives in **one** place — `COMPLAINT_COLUMNS` in
`backend/app/models/inventory.py` — from which the importer derives its header
aliases, the analytics derives its sum, and the API derives the column set it
sends the table. Adding a category is one edit there.

**`Quantity` and `Total Qty` are different columns.** An earlier build treated
"Total Qty." as a spelling of Quantity; once the format was pinned down they
turned out to be different measures, so that alias was removed. A test pins it.

Headers match after case, spaces, dots, hyphens and underscores are stripped, so
`TOTAL-QTY`, `total qty.` and `Total_Qty` all land on the same column. A blank or
unreadable count cell is zero, not a rejected row: a missing complaint means no
complaints, and discarding the SKU's stock over it would be worse.

## The shell

A dark navy rail on the left, a white bar across the top, and one scrolling
region between them. The rail collapses to 72px from its own footer and becomes
an overlay drawer below 1024px.

**The rail lists all twelve destinations outright.** Five operational ones
first, unlabelled — they are the daily path through the product and need no
heading to explain them — then `Analytics`, `Reports` and `Settings`, each
heading introducing a set rather than a pair. The Analytics pages used to be
children that appeared only while the section was open, which hid four of the
twelve behind a click and made the rail a poor answer to "what is in this
product". The active row is a filled blue pill: on a rail this dark a 12%-alpha
tint is barely a shade, and *which page am I on* is the one question the rail
must answer from across a room.

**The header carries three things: the brand, the sync pill, and the user
menu.** What it does *not* carry is worth writing down, because each was added
in good faith and each turned out to belong somewhere else:

| Tried | Why it went |
|---|---|
| A global search box | It searched SKUs alone, which is what SKU Performance's own filter already does — beside the status, complaint and quantity filters that make a SKU search useful. Before that it was a placeholder wired to nothing |
| A date-range control | The range belongs on the pages that draw the figures, where the control sits beside the chart it changes rather than a screen away from it |
| A notifications bell | It listed what the pages already say out loud: the sync pill turns amber for "not connected" and red for a failure, the Shopify cards say so directly, and `SyncStateNotice` banners stale figures. A second, quieter copy was a place for those to be missed |

`Header.test.tsx` asserts all three absences, and says why each one went. Every
one of them looked reasonable on the day it was added, which is exactly how a
header quietly accumulates controls.

**The range is still shared**, through `RangeContext` — the Dashboard and the
four Analytics pages read one value, so moving between them keeps the window you
chose. The provider is optional and `useSharedRange` falls back to local state,
so a page rendered on its own still has a working control rather than a dead
one.

## The dashboard

Six cards. Five from the sheet, one from Shopify:

| Card | Meaning |
|---|---|
| Total SKUs | rows in the sheet |
| Total Quantity | `SUM(Quantity)` |
| Shopify Sales | units sold, of SKUs that are in the sheet — with its share of **every** unit the store sold on the line beneath it |
| Total Orders | `SUM(Total Orders)` — from the sheet, not from Shopify |
| Total Complaints | all ten categories summed |
| Complaint Rate | complaints ÷ orders, both the sheet's own figures. No orders is no rate — shown as `—`, which is not a rate of zero |

**Only Shopify Sales carries a trend arrow.** `trend.previous` is the one prior
period the API reports, computed server-side as the same window shifted back.
The other five are sheet totals from the newest import, and an import replaces
the whole dataset — there is no previous value to compare them against, so those
cards carry a note saying what they count instead. `KpiCard` takes a `trend` or
a `note` and will not dress one as the other.

Then, in order down the page: the **Shopify Connection** and **Sync Status**
cards; a row of three charts — the sales trend, an inventory-health donut and a
complaint breakdown; and two tables — **Products Requiring Attention** (the
SKUs the server marked Critical or Needs attention, worst first) and **Product
Performance** (the shared `SkuTable`, with the same filters and the same export
SKU Performance offers).

The inventory donut's four buckets are a partition, not four interesting facts:
no stock, low stock, stocked but never sold, and stocked and selling. They are
counted over disjoint ranges of the same column so the shares sum to 100%, and a
count that could not be read is left out rather than folded into its neighbour —
which would report unknown SKUs as healthy ones.

**Shopify Sales % is a share, not a ratio over stock** — but the card and the
column are shares of *different wholes*, deliberately, because they answer
different questions:

| Where | Denominator | What it tells you |
|---|---|---|
| The card | every unit the store sold | match coverage — how much of the store's sales the sheet accounts for |
| The table column | what the imported SKUs sold | this SKU's place among the SKUs you carry |

So the column is a composition: no row can exceed 100% and the column sums to
100%, while the card sits below 100% for as long as the store sells anything the
sheet does not carry. The card states its own denominator on screen, because a
share whose denominator is invisible is a number nobody can check.

Both come through `sales_pct` in `backend/app/core/calc.py`; two denominators,
one piece of arithmetic, rounded to two decimals in one place.

That definition is deliberate. Sales-over-stock read **8,512%** on this store:
30 days of sales against a current-stock snapshot is not a ratio of anything.
And dividing the imported SKUs' sales by the *store's* total on every row made
the column an unreadable run of hundredths — 1,641 rows sharing 41.5% between
them — which is what moved the column onto the sheet's own total.

One rounding note: the column sums to exactly 100% before display and reads
99.55% once every row is rounded to two decimals, because 1,641 roundings of up
to ±0.005% each accumulate. That is display precision, not a different formula.

The SKU table carries SKU, Complaints, Shopify Sales, Shopify Sales %, Total
Quantity, Total Orders and all ten complaint columns. The complaint headers
arrive from the server with each page, so the table's headers and its cells
cannot disagree about the set.

## Loading, and what happens when a read fails

A dashboard load makes nine requests. They are independent on purpose: each
panel holds its own data, its own error and its own skeleton, so one slow or
failed endpoint costs its own panel and nothing else.

Three things were making that untrue, and all three are fixed:

| | |
|---|---|
| **No request ever timed out** | `fetch` has none of its own: a request that never answers never rejects, so whatever was waiting on it waited for ever. That is exactly how a dashboard ends up stuck on skeletons with no error to show. Reads now abort at 20 seconds and uploads at 120, and a timeout says *took too long* rather than *could not reach* — two different problems with two different things to do about them |
| **Refreshing cleared the screen first** | Every finished sync and every range change nulled the panels' state, so figures that were already correct vanished back to skeletons for the length of the request. Panels now hold the previous answer until a new one arrives; the first load still shows skeletons, because the state starts null |
| **A panel could spin for ever on a failed read** | Inventory Health takes its totals from the KPI payload and rendered a skeleton when that read failed — so a failed request looked identical to a slow one. It reports the failure and offers Retry |

### The two indexes

`/analytics/overview` took **964 ms** on the production store and
`/analytics/insights` **966 ms** — and roughly 700 ms of each went on staleness
metadata rather than on any figure a user reads. Both routes take two `max()`
values over large tables, and neither column was indexed:

* `max(sku_daily_metrics.computed_at)` — 721,906 rows, 214 ms, run **twice** per
  request: once for `last_computed_at` on the payload and again inside the
  staleness check.
* `max(orders.synced_at)` — 624,636 rows, 265 ms.

SQLite answers `max()` on an indexed column by seeking the last key, so
`(workspace_id, computed_at)` and `(workspace_id, synced_at)` take both to 0 ms.
Overview fell to 305 ms, insights to 250 ms, and a full dashboard load from
**1,859 ms to 1,009 ms**. The migration is
[20260918_0940_staleness_lookup_indexes](backend/alembic/versions/20260918_0940_staleness_lookup_indexes.py);
it adds no column and changes no value, so nothing that reads or writes those
tables behaves differently.

`/shopify/sales/summary` is now the slowest single read at ~460 ms, and is
deliberately left alone: it is three counts over 3.5m line items, and an index
on `(workspace_id, sku_normalized)` measured *worse* — SQLite chose a poorer
plan — while taking 10.6 s to build. A count over that many rows costs what it
costs, and it only feeds the two Shopify cards.

## Analytics

The rail's **Analytics** group. The dashboard is the quick look; these five
answer the questions it raises.

| Page | Purpose |
|---|---|
| `/analytics` — *Overview* | executive summary — six KPIs, four findings, two small charts, **no tables** |
| `/analytics/sales` — *Sales Analytics* | everything Shopify contributes: trend, distribution, top and bottom sellers, ranking |
| `/analytics/complaints` — *Complaint Analytics* | the ten sheet categories: distribution, category bars, top SKUs, ranking by count |
| `/analytics/inventory` — *Inventory Analytics* | stock against demand, with a recommendation badge per finding |
| `/analytics/performance` — *Product Performance* | every SKU — six filters, nine sortable columns, CSV and Excel export |

The split is the point. One page carrying eight KPIs, five charts and seven
tables cannot be scanned; each of these five has one job, and the overview links
onward rather than showing the detail itself. The six cards it repeats from the
dashboard are context — they are what every other page is read against — while
the two derived averages live only on the pages that use them.

All five are listed in the rail at all times. They used to appear only while you
were inside `/analytics`, which meant four of the product's twelve pages were
invisible from anywhere else. `Overview` and its four siblings are peers in one
list now, so opening Sales Analytics does not also light up Overview — `end` on
the link is what stops `/analytics` matching `/analytics/sales`.

### Complaint dates: both upload formats are supported

**Whether complaints answer the date range depends on the file they came from,
and the importer works that out for itself.** No setting, no prompt, and no
requirement that two uploads have the same columns.

| The file | What happens |
|---|---|
| Has a **Complaint Date** column (the raw export, one row per complaint) | Each complaint is stored on its day in `sku_daily_complaints`. The Complaints column and the ten category columns are summed over the selected range, exactly as Shopify Sales is. |
| Has **no** date column (the aggregated sheet, one row per SKU) | The totals it stated are reported unchanged in every range. Only Shopify Sales moves. The page shows: *"Complaint totals are based on imported data and are not filtered by date because no Complaint Date column was provided."* |

The decision is made **per SKU, not per workspace**, because each import replaces
only the SKUs it names — so a workspace that has taken one file of each kind
holds both, and each SKU is treated the way its own file allows. A mixed
workspace shows the note with the counts it applies to, and the rest of the
tally follows the range.

`repositories/complaints.py` is the only place that decides this. Both read
paths go through it — the Dashboard's own table via `services.analytics`, and
Analytics via `repositories.analytics` → `services.insights` — because these are
the same two modules that drifted over Shopify Sales %, and a figure computed
twice is one that eventually disagrees with itself. `scope_from` counts the note
for both from one implementation.

One consequence worth expecting: a dated SKU with no complaints in the selected
window reports **0**, which is a real answer and not missing data. Complaint rows
whose own date cell was blank cannot be placed in any window; they still reach
the totals, and the note's count says how many there are so a filtered view never
looks complete when it is not.

### Complaint trend: why there still isn't one

A dated export could support one. The aggregated sheet cannot, and a chart that
appeared or vanished depending on which file was last uploaded would be worse
than one that is consistently absent — so the slot still says so, and the
overview shows a complaint *mix* where a trend would be. Spreading undated
totals across a window to fill the gap would be inventing data.

### Status

Computed per SKU, first match winning:

| Status | Rule |
|---|---|
| Critical | complaint rate ≥ 10% |
| Needs attention | complaint rate ≥ 3%, **or** holding stock with zero sales |
| Excellent | sells at or above the median, complaint rate < 1% |
| Good | everything else |

Complaints dominate deliberately: a defective SKU that sells well is a problem
that sells well. Status is computed against the whole workspace, never the
filtered subset — otherwise the goalposts move with every keystroke.

### Thresholds are relative

"High stock", "low sales" and the rest cut at the **median of this workspace's own
SKUs**, not at a constant — a hard-coded "sales < 10 is low" is wrong for every
store but the one it was written for. The medians come back with the findings, so
each panel states the cut it made.

### One read, so the figures agree

`GET /api/analytics/insights` serves four of the five pages in one call, and both
it and `GET /api/analytics/performance` derive from a single read of the per-SKU
facts (`repositories/analytics.py` → `services/insights.py`). The point is not the
round trip saved but that **the cards and the table fold the same list**: "Total
complaints" on a card is the same addition as the table's column, not a second
aggregate that drifts.

`GET /api/analytics/performance/export?format=csv|xlsx` takes the *same* query
string as the table, so a download is always the rows on screen — the whole
filtered set up to 20,000, never one page. It renders through the Reports module's
own writers, which matters beyond duplication: they neutralise cells a spreadsheet
would execute as a formula, and a SKU is user-supplied text.

Measured on 1,200 SKUs against 12,010 order line items: insights 86 ms,
performance 67 ms, CSV export 77 ms (49 KB, 1,200 rows), XLSX 446 ms. The facts
are held in memory to fold — tens of microseconds at this size. A sheet two orders
of magnitude larger would need revisiting, and the boundary is one repository so
that change stays local.

Nothing on any page is fabricated. A finding with no data behind it is omitted
rather than rendered empty, and every list says what would have to be true for it
to fill.

## The rollup

Shopify sales come from `sku_daily_metrics`: one row per workspace, SKU and day,
rebuilt from `order_line_items` rather than incremented during ingest, so a bug
in the arithmetic is fixed by re-running instead of by a backfill. Cancelled and
refunded orders are excluded there, once, so no downstream query has to remember
to.

Two things earn its speed and are easy to lose:

- **A covering index** on `(workspace_id, metric_date, sku_normalized,
  units_sold, revenue_paise, order_count)`.
- **`ANALYZE`, run at the end of every rebuild.** Without planner statistics that
  index actively misleads SQLite. The dashboard measured **15.3 s** without it
  and **0.38 s** with it. Load-bearing, not housekeeping.

**The refresh is a stage of the sync, not something that happens afterwards.**
Any sync that brought in orders recomputes them — bounded to the last 120 days —
*before* the run is recorded as successful. Orders in the database that the
rollup has not seen are orders no figure on any screen reflects, so a run that
fetched them and stopped there has not done its job. If the recompute fails the
run is `partial` with `rollup_failed`: the orders landed and nothing needs
re-fetching, so the next sync simply tries again.

That is what makes `stale` mean one thing. It compares the newest `synced_at`
against the newest `computed_at`, and because a successful sync always leaves the
second ahead of the first, **the "Sales figures are behind the last sync" banner
now appears only when the automatic recomputation actually failed.**

There is no Recompute button anywhere in the UI. It asked the user to perform a
repair the system already makes, and its presence on the staleness banner implied
staleness was routine. `POST /api/analytics/rebuild` is unchanged and still
serves `metrics.refresh`; nothing in the interface points at it.


## Authentication

Two credentials, both in httpOnly cookies that JavaScript cannot read:

- a **15-minute access JWT** that authorises requests, and
- a **long-lived refresh token**, stored only as a SHA-256 digest, that mints new
  access tokens and rotates on every use.

The access token's backing session row is re-checked on every request, so logging
out revokes access immediately instead of leaving a valid credential live until
it expires. Login answers identically — and takes the same time — for an unknown
email and a wrong password, so it cannot be used to discover who has an account.

### Two throttles, counting different things

argon2id costs ~95 ms per verification, which is often mistaken for a
brute-force control. It is not — it is a price per attempt on one connection,
and an attacker opening fifty connections pays it fifty times in parallel.
Measured here before the counter existed: twelve wrong passwords in 1.14
seconds, nothing refused.

| | Counts | Keyed on | Cleared by |
|---|---|---|---|
| **Login throttle** | *Failed* sign-ins | Account **and** client address | A correct password |
| **Rate limiter** | *Every* import, sync, recompute and report | User, workspace and operation | The window draining |

The second exists because sign-in is not the only expensive request. An import
parses a spreadsheet, a sync walks Shopify for minutes, a recompute takes
SQLite's write lock, a report renders up to 50,000 rows — and all four queue on
the one worker thread SQLite's single-writer limit already forces. None of it
looks like an attack, so the failure counter never saw it; one user, or one
browser tab retrying in a loop, could make the application unresponsive.

Refusals carry `Retry-After`, so a client is never left guessing and looping.

**Both counters live in this process's memory.** The alternative is a table, and
a write per refused attempt is a write an unauthenticated caller can force. The
cost is a real deployment constraint: counters reset on restart, and they are
per process — run uvicorn with `--workers 2` and every limit silently doubles.
The job runner and SQLite's single writer assume one process too. The store
behind both counters is a four-method protocol so a shared implementation can
replace it without touching either counter.

### Keeping a session alive

There is no Authorization header and no token in `localStorage`, so there is
nothing for the page to "load first" — the browser either sends the cookie or it
does not. What the client does have to do is **spend the refresh token**, and for
a long time it did not: the access cookie died after fifteen minutes and nothing
renewed it, so a tab left open past the quarter hour answered *"You're not signed
in."* to whatever the user did next while a thirty-day refresh cookie sat unused.
It looked intermittent because it depended only on how long the tab had been
sitting there.

Three things keep it alive now, in `lib/api.ts` and `contexts/AuthContext.tsx`:

| | |
|---|---|
| **Reactive** | A 401 on a protected path renews the session and replays the request once. The user sees nothing. |
| **Proactive** | `/auth/login`, `/auth/refresh` and `/auth/me` return `access_expires_at`, and the provider renews a minute before it — so the schedule follows `access_token_minutes` rather than a hard-coded fifteen. It also re-checks the clock on `visibilitychange`, because a suspended laptop's timers do not fire. |
| **Terminal** | If the renewal itself is refused, the session really is over: `onSessionExpired` drops the provider to `anonymous` and the router redirects to `/login`, remembering where the user was. |

Two details that are easy to get wrong and are pinned by tests:

- **Renewal is single-flight.** Refresh tokens rotate, so six parallel renewals
  would mean one success and five rejections — and the five would revoke the
  session they had just created. Everything that needs one while another is in
  flight awaits the same promise.
- **`/auth/login` is never retried.** A 401 there is the answer to a wrong
  password, not a stale session, and retrying it would swallow the message the
  form has to show.

`access_expires_at` is a timestamp, not a credential — the tokens stay in the
httpOnly cookies and are never serialised.

**There is exactly one `fetch` in the frontend**, inside `lib/api.ts`, and
`credentials: 'include'` is set *after* the caller's `init` is spread so no call
site can turn it off. A bare `fetch` elsewhere would fail twice over — no session
cookie, and no renew-and-replay — so `no-restricted-globals` in
`eslint.config.js` rejects `fetch`, `window.fetch` and `XMLHttpRequest`
everywhere in `src/` except that file, and `no-restricted-imports` rejects
`axios`. Tests are exempt; they stub the global to script responses.

The two report downloads are the deliberate exception: they navigate the browser
to a protected URL rather than fetching it, because the browser should handle the
`Content-Disposition` filename and a 50 MB export should never exist in a JS
string. They are same-origin, so the cookie rides along, and both call
`ensureSession()` first because a navigation that 401s gets no second chance.

## Reports

An export is queued, built on the worker thread, and downloaded from the Export
Centre. The row exists the moment it is asked for, so *Preparing → Ready* is a
real status rather than one the UI invented. A report is a **snapshot**: once
ready its bytes never change, which is what makes "the export I sent you last
Tuesday" a meaningful sentence. Re-running one makes a second row.

**The file is on disk; the row holds the metadata.** It used to be a `BLOB` on
the row, which removed a class of bugs — no orphans, no directory to keep
writable, deleting was a `DELETE` — and was right while exports were small. At
the 50,000-row cap with 50 retained per workspace it stopped being right: those
bytes sat in the same SQLite file that answers every analytics query, and
downloading one read it whole into the worker's memory before a byte reached the
socket. Downloads now stream with `FileResponse`.

What moving to disk costs, and how each cost is paid:

| Risk | Answer |
|---|---|
| Orphaned files | Deleting a row deletes its file **first**; a file already gone is not an error. A start-up sweep clears the residue |
| A half-written export | Written to `.part` and renamed — the rename is atomic, so a reader never sees a truncated file |
| A `ready` row whose file has gone | Reported as *not ready*, not as a 500. From the user's side that is exactly what happened |
| Path traversal | Structurally impossible: the stored key is `{workspace_id}/{report_id}.{fmt}` — two integers and a value from a fixed enum. The generated filename is used only in `Content-Disposition` |

## Deployment

One Ubuntu host: uvicorn on `127.0.0.1:8000`, nginx serving the built SPA and
proxying `/api` to it. The frontend always calls same-origin `/api`, so there is
no API URL to configure at build time — but **nginx must proxy `/api`, or
nothing works at all**.

Everything is in [deploy/](deploy/) — the nginx site, the systemd units, and
[deploy/README.md](deploy/README.md) for first deploy, updating, backups,
restore, and diagnosing a deployment that will not serve.

Three things a production server refuses to start without, all checked at
import so a missing one is a hard exit rather than a degraded server:
`STOCKSYNC_JWT_SECRET`, `STOCKSYNC_ENCRYPTION_KEY`, and `STOCKSYNC_COOKIE_SECURE`
(which needs HTTPS). The third is the one a deployment gets wrong by omission.

## Conventions

- **Numbers.** Every figure renders in the tabular mono face and is right-aligned in tables. Use `.num` (or `td.n`). This is a reconciliation tool — digits must line up in columns.
- **A figure with no prior period does not get a trend arrow.** `KpiCard` takes a `trend` or a `note`, never a fabricated delta. The sheet is a snapshot; most of its totals have nothing to be compared against.
- **Colour.** Never hard-code a hex value. Use the tokens in `styles/tokens.css`. The token *names* are the prototype's and have not changed — `--slate` is still the primary action, `--clay` still the single committing CTA, status still Moss / Amber / Rust — but the values are a blue-primary SaaS palette. That is what let the redesign reach all twelve pages without touching page code, and it is why renaming a token is a much larger change than revaluing one.
- **Cards carry a hairline shadow**, not elevation. `--shadow-card` is one pixel; `--shadow` is heavier and for overlays only.
- **Charts pick a drawing, they do not measure.** Each chart takes a `compact` flag rather than reading its container: the SVG scales to its container and its font sizes are in viewBox units, so the geometry decides legibility, and there are only two useful answers. Sizing the axis gutter from the labels themselves is not optional — a store selling tens of thousands a day gets six-character labels, and a fixed gutter paints the first one off the edge.
- **Copy is never invented.** Labels, empty states, errors and button verbs come from an existing screen. If a string is missing, ask.
- **Errors say what happened and what to do next.** Both travel from the server in the error envelope so screens never invent a recovery instruction.
- **Secrets.** Never commit them, never log tokens. `.env.example` only. Logging redacts credential-shaped strings at the handler, so a careless log line can't leak a token.
- **Database portability.** Keep dialect-specific code inside `db/session.py` and `alembic/env.py`. No raw SQL that only SQLite understands, and no PostgreSQL-only types in models.

## Known issues

### VS Code interpreter

`.vscode/settings.json` already points the interpreter at `backend/.venv/Scripts/python.exe`. If imports still show as unresolved, reload the window (Ctrl+Shift+P → *Developer: Reload Window*).
