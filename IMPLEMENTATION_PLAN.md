# StockSync Analytics — Implementation Plan

> **Superseded — kept for its reasoning, not as a description of the code.**
>
> Last revised 29 July 2026, before M5, M6 and M7 landed. Everything from §4
> onward describes intentions that the code has since answered, sometimes
> differently and for reasons recorded at the point of decision.
>
> For what the system *is*, read in this order:
> **`README.md`** (how to run it) → **`DECISIONS.md`** (every non-obvious choice
> with the evidence that forced it) → **`docs/StockSync-Analytics-Documentation.pdf`**
> (the full technical and operational manual, generated from the source).
>
> This file remains because the reasoning is still worth having: the scale
> finding below is what shaped the rollup design, and §4's costing is why
> read-time aggregation was chosen.

**Status:** M0–M4 delivered when this was written; M5, M6 and M7 have since
landed. See DECISIONS.md for what changed and why.

> ### ⚠ Scale finding, 29 Jul 2026 — read before M5
>
> The first live sync against `deodap3` reported **379,074 orders in the
> rolling 90-day window** — roughly 4,200 orders a day, ~6 line items each, so
> on the order of **2.3 million `order_line_items` rows** per window.
>
> §4.2 below sizes this system at 100,000 line items. It is wrong by a factor
> of about 23, and every estimate that depends on it (dashboard KPIs, the
> five charts, the Layer 1 rebuild) is optimistic by the same factor. §4.4's
> "when to move to PostgreSQL" triggers are written as if none were close;
> this one is not close, it is already past.
>
> Nothing is broken — the sync paginates and commits correctly at this volume,
> and 9,250 orders / 56,391 line items landed and survived a hard kill. But
> **§4.1's read-time-over-a-rollup design should be re-costed against the real
> number before M5 starts**, and `order_lookback_days` (30/90/180) is now a
> load-bearing setting rather than a preference.

The project was renamed to StockSync Analytics and moved from PostgreSQL to SQLite on 28 Jul 2026 — see §4.4 for where SQLite sits and §4.5 for what it forces.

**M4 (29 Jul 2026)** — SKU matching. §3's four tiers, §3.3's scoring implemented in pure Python (Q10 unanswered, so no `rapidfuzz`), §3.4's tie rule, and §3.5's durability invariant. Three tables: `sku_links`, `match_runs`, `match_candidates`; migration `82960bc531b1`. Verified against 125 inventory SKUs and 400 variants built from the store's real order SKUs: 96 auto-matched, 24 to review, 5 missing, 284 ms. Q7 (calibration pairs) and Q8 (the accept-all threshold, assumed ≥90) remain open and are documented where the code makes the provisional choice.

**M3 (29 Jul 2026)** — Shopify sync. Products, variants, orders and line items pulled through cursor pagination, written per page on a single background worker thread, with staged progress in `sync_runs` that the UI polls. Five tables: `sync_runs`, `shopify_products`, `shopify_variants`, `orders`, `order_line_items`; migration `0b168b9245d9`. Plus sync history, the inventory↔Shopify comparison (exact and normalised SKU only — fuzzy matching stays M4), and the job runner §6 asked for. Note this is *the user's* M3; the original plan numbered the remaining import methods (§8.3, §8.4 — URL and Google Sheet) as M3 and those are still outstanding.

**M2 (29 Jul 2026)** — Shopify connection (store URL + Admin API token, validated against Shopify before storage, encrypted at rest, disconnect) and inventory import (CSV/XLSX upload, header detection, duplicate merge, import summary, history). Three tables: `shopify_connections`, `import_batches`, `inventory_items`; migration `5e7a4ebec798`. A repository layer was introduced (`app/repositories/`). Sync — pulling products and orders — is explicitly *not* in M2, so `sync_runs`, `shopify_products`, `shopify_variants`, `orders` and `order_line_items` are not created yet. Q1 (the two quantity columns) and Q6 (does a re-import delete missing SKUs) are still open and are documented at the point where the code makes a provisional choice.

M1 built the identity schema (`workspaces`, `users`, `user_preferences`, `auth_sessions`), cookie-based auth, and the full app shell. Two data-model notes: `role` is free text rather than an enum, and **no authorisation model exists yet** — every user in a workspace has identical access. `sessions` from §2.1 is implemented as `auth_sessions`.

**Sources read:** `docs/Strata_UIUX_Design_Document.md` (authoritative for UI/copy/states), `prototype/strata-prototype.html` (visual + interaction target, 2,403 lines), and the SDD it is grounded in — found as `Inventory & Shopify Sales Analytics Portal.docx` one directory up, not in this repo.

> **On the name.** `docs/Strata_UIUX_Design_Document.md` and `prototype/strata-prototype.html` keep their original naming permanently — confirmed 28 Jul 2026. They are design reference artefacts: the authoritative spec and the target the UI is diffed against. Design doc §1.1 also derives the entire layering visual language *from* the old name, so renaming inside them would leave that rationale incoherent. Everything we author says StockSync Analytics; the lowercase design term "strata divider" is likewise kept, since it names a visual technique rather than the product.

---

## 0. Read this first — credential exposure

The SDD docx contains, in plaintext, a complete set of Shopify credentials for
the live `deodap3` store:

```
https://admin.shopify.com/store/deodap3/
YOUR_SHOPIFY_API_KEY             ← API key
YOUR_SHOPIFY_APP_SECRET          ← shared secret
YOUR_SHOPIFY_ACCESS_TOKEN        ← admin API access token
```

The real values are deliberately **not** reproduced here. An earlier revision of
this file quoted them verbatim to make the warning concrete, which copied the
leak out of the docx and into this repository's history — where GitHub Push
Protection found it. They have since been removed from every commit.

That token grants API access to a live store. The docx itself has never been copied into this repository.

**Action for you:** revoke and regenerate that custom app's credentials in Shopify admin (Apps → Develop apps → the app → API credentials), then strip the block from the docx before it is shared further. StockSync Analytics will hold its token encrypted at rest and never log it; that is not retroactive protection for a token already circulating in a document.

Until it's rotated, I'll develop against a Shopify development store rather than `deodap3` — see open question **Q12**.

---

## 1. Repo structure and M0 file list

### 1.1 Structure

```
StockSync-Analytics/
├── docs/
│   └── Strata_UIUX_Design_Document.md      (present)
├── prototype/
│   └── strata-prototype.html               (present)
├── IMPLEMENTATION_PLAN.md                  (this file)
├── DECISIONS.md
├── README.md
├── .gitignore
├── .editorconfig
├── .env.example
├── tasks.ps1                               ← Windows task runner
├── data/                                   ← SQLite file, gitignored, created on first run
├── .github/workflows/ci.yml
│
├── backend/
│   ├── pyproject.toml                      ← deps + ruff/mypy/pytest config
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/.gitkeep
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                         ← FastAPI app, CORS, error handlers
│   │   ├── config.py                       ← pydantic-settings, reads .env
│   │   ├── db/
│   │   │   ├── base.py                     ← DeclarativeBase + naming convention
│   │   │   └── session.py                  ← engine, SQLite pragmas, get_db dep
│   │   ├── core/
│   │   │   ├── errors.py                   ← AppError → §16-shaped JSON envelope
│   │   │   └── logging.py                  ← stdlib logging + secret redaction
│   │   │       (crypto.py deferred to M2 — see DECISIONS.md)
│   │   ├── models/__init__.py              ← empty at M0; schema lands M1–M4
│   │   ├── schemas/__init__.py
│   │   ├── api/
│   │   │   ├── deps.py
│   │   │   └── routes/
│   │   │       ├── __init__.py
│   │   │       └── health.py               ← GET /api/health (db ping)
│   │   ├── services/__init__.py
│   │   └── workers/__init__.py
│   └── tests/
│       ├── conftest.py                     ← settings + TestClient fixtures
│       ├── test_health.py
│       ├── test_database.py                 ← pragmas, dialect-agnostic engine args
│       └── test_logging_redaction.py
│
└── frontend/
    ├── package.json
    ├── vite.config.ts                      ← /api proxy → :8000
    ├── tsconfig.json
    ├── tsconfig.node.json
    ├── eslint.config.js
    ├── .prettierrc
    ├── index.html
    ├── public/fonts/*.woff2                ← self-hosted faces (Q11), declared in styles/fonts.css
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── vite-env.d.ts
        ├── styles/
        │   ├── tokens.css                  ← §1.3 tokens, lifted verbatim from prototype
        │   ├── base.css                    ← reset, typography, focus, scrollbar, reduced-motion
        │   └── components.css              ← .btn .panel .tbl .badge … ported from prototype
        ├── components/.gitkeep
        ├── pages/.gitkeep
        ├── lib/
        │   ├── api.ts                      ← fetch wrapper, credentials: 'include'
        │   └── format.ts                   ← n() inr() pct() — ported from prototype
        └── types/api.ts
```

The frontend is deliberately **CSS-file-per-layer, not CSS-modules-per-component**. The prototype is one stylesheet of global classes (`.btn.pri`, `.panel`, `.p-hd`, `.tbl.sticky-1`); porting it verbatim into three global files keeps a 1:1 mapping to the prototype, so any visual regression is diffable against it. Components consume those classes rather than redefining them.

### 1.2 What M0 delivers

- SQLite at `data/stocksync.db`, created on first run. No server to install, no container.
- `GET /api/health` → status, version, environment and database latency; returns **503** with a failure reason when the database is unreachable, while still booting so the problem is reportable rather than a crash loop.
- Vite dev server on 5173 proxying `/api` → 8000; renders a single page proving tokens load in both themes.
- `ruff check` + `ruff format --check` + `mypy` + `pytest` on the backend; `eslint` + `prettier --check` + `tsc --noEmit` + `vitest` on the frontend. All four wired into `.github/workflows/ci.yml` and reproducible locally via `tasks.ps1`.
- Alembic configured with an empty `versions/` — no tables yet, because the schema belongs to M1–M4 and I don't want to migrate a model I haven't built against.
- `.env.example` with every key the app reads, all values placeholder.
- `git init` + conventional commits.

**No feature code, no models, no auth.** M0 is the harness.

---

## 2. Data model — first cut

Your list is close. Below is what I'd build, with the four places I think it's wrong marked **[CHANGE]** and the seven additions marked **[ADD]**.

### 2.1 Tenancy and identity

| Table | Key columns | Notes |
|---|---|---|
| **workspaces** **[ADD]** | `id`, `name`, `slug`, `timezone` (default `Asia/Kolkata`), `currency` (`INR`), `low_stock_threshold` (default 10), `created_at` | The multi-tenant seam. One row seeded at M0. Every business table below carries `workspace_id` from day one, and every query filters on it. Retrofitting this later *is* the rewrite you said you wanted to avoid; adding it now costs one column and one index per table. |
| **users** | `id`, `workspace_id`, `email`, `email_normalized` (lowercased, unique per workspace — **[CHANGE]**, see §4.5), `password_hash` (argon2id), `full_name`, `role`, `timezone`, `is_active`, `last_login_at`, `created_at` | Prototype shows a disabled Role field with helper text "Roles are managed by your workspace admin" — so role is stored but not self-editable. |
| **user_preferences** **[ADD]** | `user_id` PK, `theme`, `table_density`, `alert_on_stockout`, `updated_at` | Settings → Display (§13, prototype `set-sec[data-s=prefs]`). Density is "persisted per user" per §15; theme likewise. Separate table so preference writes don't touch the auth row. `low_stock_threshold` lives on **workspaces**, not here — it changes what every user sees in the Low stock KPI, so it is workspace-level. |
| **sessions** **[ADD]** | `id`, `user_id`, `refresh_token_hash`, `expires_at`, `revoked_at`, `user_agent`, `ip` | Needed for "Keep me signed in" (prototype login) and for logout that actually invalidates. A bare JWT can't be revoked. Access token in the httpOnly cookie stays short-lived (15 min); this row backs the refresh. |

### 2.2 Inventory import

| Table | Key columns | Notes |
|---|---|---|
| **linked_sources** | `id`, `workspace_id`, `kind` (`google_sheet`\|`csv_url`\|`excel_url`), `display_name`, `url`, `sheet_tab`, `google_credential_id`, `column_mapping_id`, `last_run_at`, `last_batch_id`, `schedule_cron`, `is_active`, `created_at` | The one-time-upload vs re-runnable distinction the doc insists on (§8.4, §8.8). CSV/Excel *uploads* never create a row here. |
| **google_credentials** **[ADD]** | `id`, `workspace_id`, `google_account_email`, `refresh_token_encrypted`, `scopes`, `expires_at`, `revoked_at` | Split from `linked_sources` because one Google authorisation covers many sheets — the prototype's Settings → Google Sheets lists two sheets under one account (`priya@deodap.in`). Storing the refresh token per-sheet would duplicate a secret. |
| **column_mappings** **[ADD]** | `id`, `workspace_id`, `source_fingerprint` (hash of ordered header names), `mapping` (JSON: `{"Item Code":"sku", …}`), `created_by`, `last_used_at` | Without this, every Google Sheet re-run re-prompts for mapping — which contradicts "one-click re-sync" (§8.4). Keyed on a header fingerprint so an unchanged sheet auto-applies and a changed sheet correctly falls back to the mapping screen. §8.5 still *shows* the mapping step on interactive imports; this only makes the unattended re-run possible. |
| **import_batches** | `id`, `workspace_id`, `linked_source_id` (nullable), `method`, `origin_filename`, `status` (`pending`\|`reading`\|`validating`\|`saving`\|`complete`\|`partial`\|`failed`), `stage_pct`, `rows_read`, `rows_imported`, `rows_flagged`, `rows_merged`, `rows_rejected`, `error_code`, `error_detail`, `started_at`, `finished_at`, `triggered_by`, `trigger` (`manual`\|`scheduled`) | Status vocabulary matches the prototype's three badges (`complete`/`partial`/`failed`) plus in-flight stages driving §8.7's progress bar. |
| **import_rows** | `id`, `batch_id`, `row_number`, `raw` (JSON), `parsed_sku`, `parsed_qty`, `parsed_name`, `parsed_price_paise`, `flag` (`ok`\|`missing_sku`\|`duplicate`\|`bad_quantity`\|`rejected`), `flag_detail`, `merged_into_row_id` | **[CHANGE] — retention.** At 1,240 rows/import and ~14 imports/month this is trivial, but it grows unbounded and 95% of it is `flag='ok'` rows that duplicate `inventory_items`. Plan: keep all rows for 30 days (powers the preview screen and "Why?" dialog), then purge `flag='ok'` rows and keep flagged/rejected ones permanently. Add a nightly job in M8. |
| **inventory_items** | `id`, `workspace_id`, `sku` (as written), `sku_normalized`, `product_name`, `category`, `price_paise`, `quantity_on_hand`, `quantity_imported`, `source_batch_id`, `first_seen_at`, `last_imported_at` | **[CHANGE] — see Q1.** Unique on `(workspace_id, sku_normalized)`. Current state, one row per SKU, overwritten by each import. The two quantity columns are the part of the spec I can't resolve from the sources; Q1 explains. |
| **inventory_snapshots** **[ADD]** | `workspace_id`, `sku_normalized`, `captured_on` (date), `quantity_on_hand`, `batch_id` — PK `(workspace_id, sku_normalized, captured_on)` | The SDD's Module 2 ends at "Save Inventory Snapshot", and §11's Inventory Health lens needs *"dead stock — no sale in 60 days"* and *"days cover"*, which need stock history, not just today's number. One row per SKU per import-day; cheap (5k rows/day worst case, realistically far fewer since it only writes on change). |

### 2.3 Shopify

| Table | Key columns | Notes |
|---|---|---|
| **shopify_connections** | `id`, `workspace_id`, `shop_domain`, `access_token_encrypted`, `token_scopes`, `store_name`, `plan_name`, `order_lookback_days` (default 90), `status` (`connected`\|`token_expired`\|`disconnected`), `connected_at`, `disconnected_at`, `last_sync_run_id` | `order_lookback_days` is a real setting in the prototype (30/90/180) — it is not a constant. `status='token_expired'` drives the §4 sidebar red dot on Connection. |
| **sync_runs** | `id`, `workspace_id`, `connection_id`, `trigger` (`manual`\|`scheduled`), `status`, `stage` (`products`\|`orders`\|`done`), `products_synced`, `variants_synced`, `orders_synced`, `result` (`success`\|`partial`\|`failed`), `error_code`, `error_detail`, `retry_after`, `duration_ms`, `cursor_products`, `cursor_orders`, `started_at`, `finished_at` | The two cursor columns are what make the prototype's footer promise true: *"Partial syncs re-fetch only the missing pages on the next run."* Without them, "partial" is just a label. |
| **shopify_products** | `id`, `workspace_id`, `connection_id`, `shopify_product_id` (bigint), `title`, `handle`, `product_type`, `vendor`, `status`, `synced_at`, `deleted_at` | Soft-delete rather than hard, so a link to a vanished product degrades to an explainable state instead of a dangling FK. |
| **shopify_variants** | `id`, `workspace_id`, `product_id`, `shopify_variant_id` (bigint), `sku`, `sku_normalized`, `title`, `price_paise`, `inventory_quantity`, `synced_at`, `deleted_at` | **SKU lives here, not on the product** — matching targets variants. Index on `(workspace_id, sku_normalized)`; deliberately *not* unique, because duplicate SKUs across variants are exactly what the Duplicates queue exists to surface. |
| **orders** | `id`, `workspace_id`, `connection_id`, `shopify_order_id`, `order_number`, `created_at_shopify`, `processed_at`, `financial_status`, `fulfillment_status`, `cancelled_at`, `currency`, `total_price_paise`, `synced_at` | |
| **order_line_items** | `id`, `workspace_id`, `order_id`, `shopify_line_item_id`, `variant_id` (nullable FK), `shopify_variant_id`, `sku_at_sale`, `title`, `quantity`, `price_paise`, `total_discount_paise` | **[CHANGE] — `sku_at_sale` is not optional.** Shopify line items denormalise the SKU at purchase time, and a variant can be renamed or deleted afterwards. Storing the historical SKU string is what lets a sale still reconcile to inventory after the variant is gone. Also: **cancelled and refunded orders must be excluded from sales figures** — that's a query concern, but it's why `cancelled_at` and `financial_status` are stored rather than filtered at ingest. |

### 2.4 Matching

| Table | Key columns | Notes |
|---|---|---|
| **sku_links** | `id`, `workspace_id`, `sku_normalized`, `shopify_variant_id` (bigint, nullable), `variant_row_id` (FK, nullable), `link_type` (`confirmed`\|`suppressed_missing`), `confidence_at_confirm`, `confirmed_by`, `confirmed_at`, `source` (`manual`\|`auto_exact`\|`bulk_accept`), `status` (`active`\|`stale`), `stale_reason` | **[CHANGE] — two changes.** (a) Keyed on `sku_normalized`, **not** on `inventory_item_id`: an inventory row is replaced by every re-import, so an FK to it would break the "remembered permanently" promise (§10.2) on the very next import. The normalised SKU string is the durable identity. (b) `link_type='suppressed_missing'` records the drawer's "Mark as missing" action — otherwise that SKU reappears in the review queue after every sync and the user re-dismisses it forever. |
| **match_runs** **[ADD]** | `id`, `workspace_id`, `trigger` (`post_import`\|`post_sync`\|`manual`), `status`, `matched_count`, `review_count`, `missing_count`, `duplicate_count`, `auto_linked_count`, `duration_ms`, `started_at`, `finished_at` | The success toast is *"Matching complete — 1,180 matched, 42 review, 12 missing"* and the tabs are a persistent count summary. Those counts need a home, and recomputing four `COUNT(*)`s on every tab render is wasteful. |
| **match_candidates** **[ADD]** | `id`, `match_run_id`, `workspace_id`, `sku_normalized`, `queue` (`review`\|`missing`\|`duplicate`\|`matched`), `shopify_variant_id`, `confidence`, `rule` (`exact`\|`normalized`\|`fuzzy_sku`\|`fuzzy_name`), `rank`, `resolved_at` | The Resolution Drawer shows *ranked suggested matches* — plural, with a best guess and alternatives. Those candidates are computed by the match run; recomputing fuzzy scores on drawer-open would be both slow and non-deterministic against what the row's confidence badge claimed. Rows are replaced per run. |

### 2.5 Analytics, reports, notifications

| Table | Key columns | Notes |
|---|---|---|
| **sku_daily_metrics** **[ADD]** | `workspace_id`, `sku_normalized`, `metric_date`, `units_sold`, `revenue_paise`, `order_count` — PK `(workspace_id, sku_normalized, metric_date)` | The materialised layer. §4 explains why and what it costs. |
| **report_exports** | `id`, `workspace_id`, `report_type` (`inventory`\|`sales`\|`sku_performance`), `format` (`csv`\|`xlsx`\|`pdf`), `params` (JSON: range, category), `status` (`preparing`\|`ready`\|`failed`\|`expired`), `filename`, `storage_path`, `byte_size`, `row_count`, `error_detail`, `requested_by`, `created_at`, `expires_at` | Drives Export Centre's preparing → ready → download. `expires_at` because generated files otherwise accumulate forever. |
| **notifications** **[ADD]** | `id`, `workspace_id`, `user_id` (nullable = workspace-wide), `tone` (`moss`\|`amber`\|`rust`), `body`, `link_page`, `link_params`, `read_at`, `created_at` | §3 specifies a bell with a badge count and a panel; the prototype has four real notification objects with tone, text, relative time and a destination page. This is a table, not derived state — *"42 SKUs need matching after the 28 Jul import"* is a point-in-time event, not a live query. |

### 2.6 Where I think your list was wrong, summarised

1. **`inventory_items` alone can't serve Inventory Health.** Days-cover and dead-stock need stock history → `inventory_snapshots`.
2. **`sku_links` keyed to an inventory row would break on re-import.** Key on the normalised SKU.
3. **No home for match state.** Four queue counts, per-SKU confidence and ranked drawer candidates are all rendered by the UI and none of them fit in the tables listed → `match_runs` + `match_candidates`.
4. **No tenancy seam.** You asked for multi-tenant-ready; that means `workspaces` and a `workspace_id` everywhere now.
5. **Money can't be a decimal.** Under SQLite every currency column is an `INTEGER` of paise (§4.5). This applies to `inventory_items.price_paise`, `shopify_variants.price_paise`, `orders.total_price_paise`, `order_line_items.price_paise`/`total_discount_paise` and `sku_daily_metrics.revenue_paise`.
6. Plus the smaller adds: `sessions`, `user_preferences`, `google_credentials`, `column_mappings`, `notifications`, `sku_daily_metrics`.

Total: **13 proposed → 22.** Nine of those are small. If you want to cut, `inventory_snapshots` and `column_mappings` are the two I'd defer to M8 — everything else is load-bearing for a screen in the design doc.

---

## 3. SKU matching strategy

### 3.1 Normalisation

Applied identically to inventory SKUs and Shopify variant SKUs, stored in `sku_normalized` (generated at write time, indexed):

1. Unicode NFKC normalise, then strip zero-width and control characters.
2. Trim, collapse internal whitespace.
3. Uppercase (ASCII-fold accents first).
4. Remove every character outside `[A-Z0-9]` — this collapses `DD-1001`, `dd_1001`, `DD 1001`, `dd.1001` to `DD1001`.
5. **Do not** strip leading zeros. `DD-0104` and `DD-104` are plausibly different products, and the prototype's own data has `DD-0104` alongside `DD-1004`. Collapsing them silently merges two SKUs — the worst failure this tool can have.
6. Empty result → row is `missing_sku`, never matched.

Rule 4 is the one that carries risk, and it's why the exact tier below runs first and outranks it.

### 3.2 Match tiers

Per inventory SKU, in order; **first tier that yields exactly one candidate wins and stops.**

| Tier | Rule | Confidence | Queue |
|---|---|---|---|
| 0 | Active row in `sku_links` (`link_type='confirmed'`) | 100 | `matched` |
| 1 | Byte-identical raw SKU vs variant SKU | 100 | `matched`, auto-linked |
| 2 | Equal `sku_normalized` | 98 | `matched`, auto-linked |
| 3 | Fuzzy, score ≥ 70 | 70–95 | `review` |
| — | No candidate ≥ 70 | — | `missing` |
| — | Any tier returns >1 candidate | — | `duplicate` |

Tiers 1 and 2 auto-link (writing `sku_links` with `source='auto_exact'`). Tier 3 never auto-links — the doc is explicit that fuzzy matches are confirmed by a human, and the prototype's hint says *"Matched on a fuzzy rule — confirm or correct each one."*

`link_type='suppressed_missing'` short-circuits everything: the SKU goes straight to `missing` without re-running fuzzy, so a dismissed row stays dismissed.

### 3.3 Fuzzy scoring

Two independent signals, combined:

```
sku_sim   = jaro_winkler(inv.sku_normalized, var.sku_normalized)        # 0..1, prefix-weighted
name_sim  = token_set_ratio(inv.product_name, var.product_title) / 100  # 0..1, order-insensitive

confidence = round(100 * (0.75 * sku_sim + 0.25 * name_sim))
```

- **Jaro-Winkler for the SKU** because its prefix weighting matches how SKUs actually vary — `DD-1002` vs `DD-1002-A` is a suffix difference and should score high; `AD-1002` is a prefix difference and should score low. Plain Levenshtein treats those the same.
- **Token-set ratio for the name** because `"Silicone Spatula Set (5 pc)"` vs `"Set of 5 Silicone Spatulas"` is a reordering, not an edit.
- **75/25 weighting** because the SKU is the identifier and the name is corroboration. A name-only agreement (`sku_sim` ~0.3, `name_sim` 1.0) scores 47 → falls below 70 → `missing`, which is correct: two products with the same name and unrelated SKUs are not a confident pairing.
- Floor: a candidate with `sku_sim < 0.55` is discarded regardless of name, so name similarity can't drag an unrelated SKU over the line.

Sanity-check against the prototype's own review data — `DD-3001` → `gel-pen-blue-20pack` at 95%, `DD-6003` → `manicure-kit-12` at 78% — the spread these produce is in the same band, which is the calibration target. **These weights are a starting point and I'll tune them against real DeoDap data in M4, not against demo fixtures** (see Q7).

Candidate generation **[CHANGED for SQLite]**. The original plan pre-filtered with a Postgres `pg_trgm` GIN index; SQLite has no equivalent. It is replaced by blocking in application code, which turns out to suit this scale better anyway:

1. A match run loads every Shopify variant SKU into memory once. At ~1,200–5,000 variants that is well under a megabyte — there is no reason to make the database do this.
2. Variants are bucketed by two blocking keys: the first three characters of `sku_normalized`, and the string length ±1.
3. Each inventory SKU is scored only against the union of its two buckets — roughly 50 candidates instead of all 5,000.

A naive cross product at 5,000 × 1,204 is 6M comparisons; blocking takes it to ~250k. Blocking can miss a true pair whose SKU differs in the first three characters *and* by more than one in length, but such a pair would score below the 70 threshold anyway and land in `missing`, which is the correct queue for it.

This is pure Python with no index, no extension and no dialect dependency — so it behaves identically if the database moves to PostgreSQL later. Whether to add `rapidfuzz` (C++ scoring, ~10× faster than a pure-Python Jaro-Winkler) is **Q10**.

### 3.4 Tie-breaking

When two or more candidates tie within **2 confidence points**, in order:

1. Higher `name_sim`.
2. Variant with sales in the lookback window (a SKU that has actually sold is the live one).
3. Variant on a product with `status='active'` over draft/archived.
4. Lower `shopify_variant_id` (older = the original) — purely for determinism.

If (1) and (2) both fail to separate them, the SKU goes to the **`duplicate` queue rather than picking a winner.** The whole point of the duplicate tab is that the tool refuses to guess when two Shopify products both claim a SKU. Deterministic tie-breaks are for ordering the drawer's candidate list, not for silently resolving ambiguity.

### 3.5 How a confirmed link survives

The invariant: **`sku_links` is keyed on `(workspace_id, sku_normalized)` and holds `shopify_variant_id` as a bigint from Shopify — neither side is a foreign key to a row that gets replaced.**

- **Re-import.** `inventory_items` rows are upserted on `sku_normalized`. The link is untouched because it never referenced the inventory row's `id`. A SKU that disappears from the sheet leaves its link in place, so re-adding the SKU next month re-matches instantly.
- **Re-sync.** Variants are upserted on `shopify_variant_id`. The link is untouched. `variant_row_id` is a convenience FK refreshed on each sync; `shopify_variant_id` is the durable anchor.
- **Variant deleted in Shopify.** The link is marked `status='stale'`, `stale_reason='variant_deleted'`, and the SKU enters the `review` queue with an explicit message rather than silently reverting to unmatched. The user sees *why* a previously-matched SKU came back.
- **SKU renamed in Shopify.** The variant id is unchanged, so the link holds — which is correct, and is the main thing keying on variant id buys over keying on SKU strings on both sides.
- **Disconnect/reconnect.** Links survive; the disconnect dialog promises exactly this (*"Your 1,180 confirmed SKU links are kept and will be reused if you reconnect the same store"*). On reconnect, links are revalidated against the new sync and any whose variant id no longer exists go `stale`.

Re-running matching never deletes confirmed links. `match_candidates` rows are per-run and replaced; `sku_links` rows are permanent until a user unlinks.

---

## 4. Analytics computation

### 4.1 Recommendation: read-time for M5, materialised rollup added in the same milestone

Not "on write" — writing metrics during import/sync means a bug in aggregation corrupts stored data and needs a backfill, and it makes the ingest path slower and more fragile for no user-visible gain at this scale.

**Two layers:**

**Layer 1 — `sku_daily_metrics`, refreshed after every sync.** One row per `(workspace, sku_normalized, date)` with `units_sold`, `revenue`, `order_count`, computed from `order_line_items` joined through `sku_links`. Refresh is incremental: delete and recompute only the date range the sync touched (`sync_runs.started_at` back to the earliest `processed_at` seen), not the whole window. Cancelled orders and refunded line items excluded here, once, so no downstream query has to remember to.

**Layer 2 — read-time aggregation over Layer 1** for everything the UI asks for. Chart date ranges (7/30/90/custom, independent per chart per §7.3), category filters, and the lens tables are all `GROUP BY` over a table that is ~2 orders of magnitude smaller than the raw line items.

The reconciliation table on the Dashboard is a straight join — `inventory_items` ⟕ `sku_links` ⟕ (aggregate over `sku_daily_metrics` for the active window) — computed on read, no caching. It's per-SKU with a `LIMIT`, and it must reflect the state immediately after an import; a stale reconciliation table is a wrong reconciliation table.

### 4.1a Measured, M5 (29 Jul 2026)

The two-layer plan above held. Three things it did not anticipate, all measured
on the real store (68,750 orders, 429,172 line items):

- **A covering index is required, not optional.** `(workspace_id, metric_date,
  sku_normalized, units_sold, revenue_paise, order_count)` lets a date-range
  aggregate be answered without touching the table: KPIs 278 → 26 ms, trend
  299 → 26 ms, top sellers 551 → 78 ms.
- **`ANALYZE` is load-bearing.** With the covering index but no planner
  statistics, SQLite picks the date-ordered index for the per-SKU joins and
  scans where it should seek: the vendor breakdown alone took 9.2 s and the
  whole dashboard 15.3 s. After `ANALYZE` (902 ms) the dashboard was 376 ms.
  It now runs at the end of every rebuild.
- **The reconciliation join must be direct.** §4.1 describes it as a join
  against "an aggregate over `sku_daily_metrics`". Written literally as a
  pre-aggregated subquery, SQLite materialises every SKU that sold in the
  window — 13,000 of them — to answer a question about 125 inventory rows.
  Joining the rollup directly and grouping per inventory row took it from
  925 ms to 217 ms.

Nine dashboard queries, end to end, against the real database: **269 ms.**

### 4.2 Cost at your stated scale

Estimates for SQLite on modest hardware, warm page cache. I'll measure and correct these in M5 rather than trusting them. SQLite is generally *faster* than PostgreSQL at this scale for read-heavy single-host work — no network round trip, no client/server serialisation.

| | Rows | On disk | |
|---|---|---|---|
| `order_line_items` | 100,000 | ~25 MB | 90-day window |
| `sku_daily_metrics` | ~90,000 | ~8 MB | 5,000 SKUs × 90 days, but sparse — most SKUs don't sell daily. Realistically 40–80k. |
| `inventory_items` | 5,000 | ~2 MB | |
| `shopify_variants` | ~5,000 | ~2 MB | |

- **Layer 1 full rebuild:** aggregate scan of 100k line items ≈ **150–400 ms**. Acceptable as a post-sync step. Incremental (one day) ≈ **10–30 ms**.
- **Dashboard KPI row (7 cards):** one query over `sku_daily_metrics` + one over `inventory_items` ≈ **20–50 ms**.
- **Five charts, independent ranges:** five `GROUP BY date` queries, each ≈ **15–40 ms**, issued in parallel. §7.4 requires per-widget independent loading anyway, so they're separate requests by design.
- **Reconciliation table, 5,000 SKUs:** the join ≈ **60–150 ms** server-side. **The real cost here is the browser, not the database** — 5,000 rows × 8 columns is 40,000 DOM cells, which will make sort and density-toggle visibly janky. Handled in M8 (Q9).
- **Matching run, 5,000 × 1,204:** blocking + scoring ≈ **3–10 s** in pure Python (≈1 s with `rapidfuzz`). Runs as a background job with the §10.3 "Matching SKUs…" state, not in a request.
- **Bulk order-line insert, 100k rows:** ≈ **4–12 s** with `synchronous=NORMAL` and batched inserts inside one transaction. This is the one operation that holds SQLite's single write lock long enough to matter — see §4.4.

**Where this breaks:** around 50,000 SKUs or a 2-year lookback, Layer 2 read-time aggregation starts exceeding 500 ms and wants either a monthly rollup or a `sku_period_metrics` cache. Well outside anything discussed. I'm not building for it.

### 4.3 Freshness

Every data-bearing screen shows "Synced N minutes ago" (§1.4). That timestamp is `MAX(sync_runs.finished_at WHERE result != 'failed')`, served on every analytics response as an envelope field rather than fetched separately — so the label can never disagree with the numbers beside it. Chart tooltips carry the same value (*"as of last sync, 12 min ago"*, §16).

### 4.4 Database position

**SQLite is the default database for the MVP and for single-server deployments.** It is the supported configuration: no server to install, no container, no credentials to provision, and the database is a single file that can be backed up by copying it. For one API process serving DeoDap's inventory and ops team from local disk, it is the right tool, and it is faster than PostgreSQL at this scale because there is no network round trip.

**The architecture stays database-agnostic so PostgreSQL can be adopted later without major application changes.** This is a standing constraint on all future work, not a one-off accommodation:

| Layer | Rule |
|---|---|
| Models | SQLAlchemy types only. No dialect-specific column types, no `JSONB`, no `ARRAY`, no `citext`. |
| Queries | ORM and SQLAlchemy Core. No raw SQL that depends on one dialect's syntax or functions. |
| Migrations | Generated through Alembic. Batch mode is selected automatically from the URL, not hard-coded. |
| Dialect-aware code | Permitted **only** in `app/db/session.py` (engine construction, connection pragmas) and `alembic/env.py` (batch mode). Nowhere else. |
| Algorithms | Anything a database extension would provide — fuzzy matching in particular — is implemented in the application layer (§3.3), so it behaves identically on either backend. |

Adopting PostgreSQL is then: install `psycopg`, change `STOCKSYNC_DATABASE_URL`, run `alembic upgrade head`. `STOCKSYNC_DB_POOL_SIZE` and `STOCKSYNC_DB_MAX_OVERFLOW` already exist and begin applying automatically; Alembic leaves batch mode on its own. No application code changes. What does *not* come for free is moving existing data, and the integer-paise money columns (below) stay integers rather than becoming `NUMERIC` unless we choose to convert them.

**When to make that move.** Any one of these is the trigger, and none of them are near at current scale: more than one API instance, the database file needing to live on shared storage, sustained write concurrency that `busy_timeout` no longer absorbs, or roughly 50,000+ SKUs with a multi-year order history.

### 4.5 What SQLite forces

Moving from PostgreSQL to SQLite is mostly free at this scale, and genuinely simpler — no server, no container, no credentials, and the test suite now exercises a real database instead of a stub. Four things are **not** free, and two of them change the schema. All four are handled in code confined to `db/session.py` and `alembic/env.py`, so none of them leak into the application layer.

**1. There is no DECIMAL type — and this one matters most.** SQLite stores `NUMERIC` as an IEEE-754 float. `0.1 + 0.2` is not `0.3`, and a reconciliation tool whose revenue column drifts by fractions of a paisa is a reconciliation tool nobody trusts. **Every money column will be a plain `INTEGER` holding paise** — `price_paise`, `revenue_paise`, `total_price_paise` — converted at the API boundary, never in a query. `₹549.00` is stored as `54900`. Integers are exact, sum exactly, and port to PostgreSQL unchanged (where they can stay integers or become `NUMERIC(12,2)`). The one cost is that every read path must remember to divide, which is why the column names carry the unit.

**2. There is no `citext`.** Case-insensitive email uniqueness becomes a stored `email_normalized` column (lowercased, `NOT NULL`, unique per workspace) alongside the `email` the user typed. Slightly more explicit than the Postgres version and arguably better — the original casing is preserved for display instead of being flattened.

**3. One writer at a time.** WAL lets any number of readers run during a write, but writes serialise globally. At this scale that is fine *provided write transactions stay short*, which imposes a real constraint on M2 and M3: a Shopify sync inserting 100k order lines must commit in batches (5–10k rows) rather than holding one transaction for ten seconds, or a user saving a setting mid-sync waits behind it. `busy_timeout` turns that wait into a wait rather than an error, but the fix is batching, not the timeout. I'll build the sync and import writers this way from the start.

**4. Single host, local disk.** SQLite cannot safely be run from a network share, and two API processes against one file is asking for corruption under load. This makes "one instance, file on local disk" a hard deployment constraint rather than a default — it's the substance of **Q13**. If StockSync Analytics ever needs a second instance or a shared filesystem, that is the moment to move to PostgreSQL, and §4.2's read-time-over-a-rollup design is unchanged by the move.

Things that were *not* a problem, contrary to what you might expect: JSON columns (SQLite's JSON1 is built in and SQLAlchemy's `JSON` type maps cleanly, and we only ever read these whole, never query into them), partial and expression indexes (supported), generated columns (supported since 3.31), and foreign keys (supported, just off by default — now switched on by pragma, with a test that asserts a violation actually raises).

---

## 5. Design-doc ↔ prototype conflicts

You said the doc wins and I should flag conflicts. Six found between the doc and the prototype (C1–C6), plus two (C7–C8) where a direct instruction overrode the prototype — a different category, logged here so the divergence from the prototype stays visible rather than silent. My recommendation is in each row, but these are yours to call — several are copy decisions and I won't invent strings.

| # | Design doc | Prototype | Recommend |
|---|---|---|---|
| C1 | §7.1 card 4 is **"Sales %"**, value 28.6% | **"Sell-through"**, 28.6% | **Prototype.** The doc uses "Sales %" for two different metrics: the card (14,900/52,300 = share of *stock* sold) and the table column (per-SKU share of *total units sold*). Same label, different maths, on the same screen. "Sell-through" for the card and "Sales %" for the column disambiguates without changing either number. **Blocks the KPI definitions in Q2.** |
| C2 | §7.4 empty: *"No data yet. Connect Shopify and import inventory to see your analytics."* | *"No data yet"* + *"Connect Shopify and import an inventory sheet. StockSync Analytics lines the two layers up and shows you where they don't agree."* | **Prototype.** Your brief says microcopy comes from the doc *or* the prototype, and the prototype's line explains what the product does. Doc wording is the fallback if you disagree. |
| C3 | §10.1 tab order: Matched, Review, Missing, Dup | To review, Missing in Shopify, Duplicates, Matched | **Prototype.** Review-first puts the work at the front; Matched is the archive. Prototype labels are also fuller ("Missing in Shopify" says where it's missing). |
| C4 | §13 lists 5 settings sections (Shopify, Google Sheet, Profile, Import History, Sync History) | 6 — adds **Display** | **Prototype**, and your M7 brief names display preferences explicitly. Doc §13 predates it. |
| C5 | Title Case labels throughout ("Log In", "Sync Now", "Import Now") | Sentence case throughout ("Log in", "Sync now") | **Prototype**, applied globally. Mixed casing across a UI reads as sloppiness, and the prototype is internally consistent where the doc isn't. |
| C6 | §5.1: primary CTA is **solid Slate** | Two primary styles: `.btn.pri` (Slate) and `.btn.cta` (Clay), Clay used for the true per-screen CTA — "Import 1,240 rows", "Connect Shopify", "Export CSV", "Review SKU matching" | **Prototype**, and it's the better reading of the doc: §1.3 calls Clay *"the one primary CTA per screen"* while §5.1 says Slate. The prototype resolves this as Slate = primary action, Clay = the single committing action on a flow's terminal step. Doc contradicts itself; prototype is the tiebreak. |
| C7 | Silent — §6 fixes no card width | `.login-card` `max-width:392px` | **As instructed: 420px.** The M1 login brief asked for 420–480px; 420 is the bottom of that band and so the smallest departure from the prototype. Scoped to `.login-card`, so no other surface moves. Revert to 392px if the prototype is meant to be exact. |
| C8 | Silent — §14 fixes no field height | `.inp` is 38px everywhere, login included | **As instructed: 42px, login only.** "Large email/password inputs" was asked for; raising the global `.inp` would resize every form in the product, so the override is `.login-bd .inp`. The trade is that login fields are now 4px taller than fields on every other screen. The Log in button follows to 44px — the prototype's 40px was sized against 38px fields, and leaving it would make the button shorter than the inputs above it. |

---

## 6. Milestone notes worth raising now

- **M2 before M3.** Your order is Shopify then Import; I agree, because matching needs both and Shopify is the riskier integration. But the §17.1 first-run journey is Connect → Sync → Import → Map → Match, so the empty states must chain in that order regardless of build order.
- **M4's "auto-triggered" matching.** §17.1 has matching fire automatically after import completes. That's a background job dispatch at the end of M3's import pipeline, which means M3 needs the job runner even though matching arrives in M4. I'll build the runner in M3 with a no-op handler and fill it in M4.
- **Scheduler.** APScheduler, in-process, with a SQLAlchemy jobstore pointed at the same database. Celery+Redis buys distributed workers and retries that a single-instance internal tool at this scale doesn't need, and costs a broker to run and monitor. Note that SQLite makes the single-instance assumption load-bearing rather than merely convenient (§4.5).
- **Testing focus** per your brief: import parsing, matching, analytics aggregation. Concretely — delimiter/encoding/BOM/merged-header/duplicate/missing-SKU fixtures for parsing; a golden table of ~40 SKU pairs with expected tier+queue for matching; and for analytics, property tests asserting that per-SKU metrics summed always equal the KPI totals (the class of bug that makes a reconciliation tool quietly wrong).

---

## 7. Open questions

Numbered as requested. **Q1–Q3 block M0's schema thinking; Q4–Q8 block M3–M5; the rest are lower-stakes.**

1. **`quantity_imported` vs `quantity_on_hand` — what is the difference?** The dashboard table has both "Imported qty" and "Current inventory" as separate columns, but the import only ever reads one quantity column from the sheet ("Stock Count" → Quantity). In the prototype's data they're unrelated numbers (DD-1001: imported 420, current 186, sold 214 — 420−214≠186), so I can't reverse-engineer the rule. Which is it: (a) imported = this batch's quantity, current = imported minus units sold since that import; (b) imported = cumulative across all imports ever, current = latest sheet value; (c) two distinct columns in your actual sheet that both get mapped; or (d) something else? **This determines whether sell-through means anything.**

2. **Confirm the seven KPI definitions.** Given C1, I read them as: Total SKUs = `COUNT(inventory_items)`; Inventory qty = `SUM(quantity_on_hand)`; Shopify sales = units sold in window; Sell-through = units sold ÷ inventory qty; Revenue = `SUM(line_total)` excluding cancelled/refunded; Low stock = count where `0 < qty ≤ threshold`; Out of stock = count where `qty = 0`. Correct? In particular — does Revenue include shipping and tax, and is it net of discounts?

3. **Should "Out of stock" mean zero stock, or zero stock *and still selling*?** The card says "12"; §11's Inventory Health lens says *"Out of stock — 12 — 0 units, still selling"*. If those are the same 12, the definition includes a sales condition and the KPI query is not just `qty = 0`. If they're different numbers that happen to coincide in demo data, I need both definitions.

4. **What is the delimiter/encoding reality of your actual sheets?** Are exports from `erp.deodap.in` UTF-8 or Windows-1252? Comma or semicolon? Any files with a title row above the headers (the prototype's `warehouse_ahd.xlsx` failure is exactly this)? If you can hand me 3–4 real files — including one that failed — the parser tests get built against reality instead of my guesses. This is the single highest-value thing you could give me.

5. **Duplicate merge rule — always sum?** §8.6 and the prototype both say duplicates merge by summing quantity. That's right for the same SKU in two warehouse rows and wrong for the same SKU listed twice at different prices. Sum always, or sum quantity and take max/last for price?

6. **Is a re-import additive or a replacement?** If July's sheet has 1,240 SKUs and August's has 1,180, do the 60 missing SKUs get deleted, zeroed, or left at their last known quantity? The prototype doesn't cover it and it changes the Total SKUs card permanently. My default would be: leave them, mark `last_imported_at` stale, and surface them as a "not in latest import" state — but that's an invented state, so I'm asking rather than building it.

7. **Do you have real SKU pairs I can calibrate fuzzy matching against?** The 75/25 weighting and the 70/90 thresholds in §3.3 are reasoned, not measured. A list of ~50 inventory SKUs with their correct Shopify variant — including the hard ones — would let me tune thresholds properly. Without it I'll ship the defaults and we tune in M4 from live review-queue behaviour.

8. **What does "Accept all high-confidence" accept?** I've assumed ≥90 (matches the prototype's data and its toast). Confirm, or name the threshold. Also: should it be undoable in bulk, or is per-row Unlink enough?

9. **5,000-SKU table — virtualise, or paginate?** Rendering all rows will be janky and both fixes have costs. Virtualisation keeps the "infinite scroll with sticky header" feel but breaks Ctrl+F and needs care with the sticky first column; server-side pagination is simpler and more robust but changes the interaction the prototype shows. I lean **virtualised windowing with server-side sort/filter** — the scroll behaviour is part of the design. Your call, and it's a dependency question either way (`@tanstack/react-virtual`).

10. **May I add `rapidfuzz`?** Fuzzy scoring in pure Python runs a 5,000-SKU match in roughly 3–10 s; `rapidfuzz` (MIT, C++ core, no transitive dependencies) does the same in about 1 s. Neither blocks M4 — the job runs in the background either way — so this is a nice-to-have, not a need. Say no and I'll use `difflib`/a hand-rolled Jaro-Winkler.

11. **Fonts — self-host or CDN?** ~~The prototype loads Figtree + JetBrains Mono from Google Fonts.~~ **Acted on during the M1 polish pass: self-hosted.** The shell was rendering in fallback system faces, which was the single largest visual gap against the prototype, so the recommended option was implemented rather than left pending — Figtree 400/500/600/700 and JetBrains Mono 400/500/600, Latin + Latin-Ext woff2, 14 files totalling 245 KB in `frontend/public/fonts/`, declared in `src/styles/fonts.css`, both OFL licences alongside them. **One thing still needs your confirmation: binary files in the repo.** If that's not acceptable, the alternative is a `<link>` to Google Fonts (one line in `index.html`, delete `fonts.css` and the directory) — but that reintroduces a third-party request on every page load, which §0 argues against for an internal tool.

12. **Which Shopify store do I develop against?** Given §0, I'd rather not use `deodap3` with a live token. Can you create a Shopify development store with dummy products, or rotate the credentials first and give me the new token via something other than a document?

13. **Windows-only, or does this deploy to Linux, and how many users at once?** Both matter more now than they did with PostgreSQL: SQLite is a single-host database, so the deployment must be one API process on one machine with the file on local disk (never a network share — SQLite's locking is unreliable over SMB/NFS). Concurrent *users* are fine; concurrent *app instances* are not. If you expect to run more than one instance, or to put the file on shared storage, that is the trigger to go back to PostgreSQL.

14. **Where do exports live?** `report_exports.storage_path` currently assumes local disk with a retention sweep. Fine for a single instance; wrong if you ever run two. Local disk for now, or is there object storage (S3/MinIO) available?

---

## 8. What I'd do on approval

M0 only, per your rules — scaffold, tooling, compose, health check, CI, git init. Nothing else until you've reviewed it.

Q1, Q2 and Q3 don't block M0, but they block the first migration, so I'd like answers before M1 ends. Q4 (real files) I'd like as early as you can get it.
