# keboola.ex-lightspeed-x-series — Design Spec

> Type: extractor
> Component ID: keboola.ex-lightspeed-x-series
> Status: draft
> Date: 2026-07-15

Research basis: `docs/superpowers/2026-07-15-ex-lightspeed-x-series-research.md` (all API facts verified live on 2026-07-15). This spec settles the design decisions and the Keboola mapping; it writes no component code.

---

## 1. Overview & source system

- **What it does:** extracts retail POS data (sales, products, inventory, customers, and supporting reference objects) from a Lightspeed Retail **X-Series** store (formerly Vend) into Keboola Storage, with native incremental loading via the X-Series `version` cursor.
- **Source system:** Lightspeed Retail X-Series REST API. Docs: https://x-series-api.lightspeedhq.com/docs/introduction. Live API is a **per-store subdomain**: `https://{domain_prefix}.retail.lightspeed.app/api/{version}/…`.
- **Primary use case:** land a retailer's transactional and catalog data in a warehouse for BI/analytics — sales analysis, inventory reporting, customer segmentation — refreshed incrementally.

---

## 2. Keboola mapping

This is the section that makes the spec Keboola-shaped rather than a generic API-client plan.

### 2.1 Objects → output tables

One **config row per source object (stream)**. Most streams write a single parent table; `sales` and `products` write a parent plus child tables (nested arrays flattened out and keyed on the parent id). Table names are stable (derived from the stream name), never generated at runtime.

| Stream (row) | Parent table | Child tables (nested arrays flattened) | Parent PK |
|---|---|---|---|
| `sales` | `sales` | `sale_line_items`, `sale_payments`, `sale_taxes`, `sale_adjustments` | `id` |
| `products` | `products` | `product_images` | `id` |
| `inventory` | `inventory` | — | `id` (product_id + outlet_id composite fallback if `id` absent) |
| `customers` | `customers` | — | `id` |
| `outlets` | `outlets` | — | `id` |
| `registers` | `registers` | — | `id` |
| `payment_types` | `payment_types` | — | `id` |

Child-table PKs are composite/synthetic: `sale_line_items` → (`sale_id`, `id`); `sale_payments` → (`sale_id`, `id`); `sale_taxes` → (`sale_id`, `id`) (synthesise a row index if the tax line has no id); `sale_adjustments` → (`sale_id`, `id`/index); `product_images` → (`product_id`, `id`). Every child row carries the parent id as a foreign-key column injected during flattening.

### 2.2 Single config vs config rows

**Config rows, one row per object** — per Keboola convention for an extractor over several independent objects. Rationale: (a) each object has its **own independent `version` cursor**, and per-row `state.json` gives us one cursor per object for free; (b) rows can be enabled/scheduled/retried independently; (c) a customer who only wants sales+products doesn't pay for the rest. Row-level parallelism is **left off by default** (the OAuth token-rotation hazard in §3.4 makes parallel rows unsafe under OAuth; under personal-token it is safe but unnecessary for v1). Override note stated: if a user wants a single all-objects config we would collapse to a multipick, but that sacrifices per-object cursors — not recommended.

### 2.3 Incremental strategy → output mapping + state

- The source exposes a **monotonic `version` cursor** (an int64 bumped on every change to a record), returned per-page as `version.{min,max}` and consumable via the `after` query param. This is the vendor-blessed incremental signal, so we use it **instead of** the generic `last_run` timestamp watermark.
- **State shape (per row):** `{"last_version": <int>}`. First run (no state) → start from `after=0` (full history). Incremental run → `after = state.last_version`.
- **Advance rule:** page until `data` is empty, tracking the highest `version.max` seen; persist it to state **only after the output tables are written successfully**. A failed run keeps the old cursor and is safely retried. Never compute `max+1` — pass the returned `max` verbatim (the cursor is not guaranteed to increment by 1).
- **`load_type`** is a row-level dropdown (`full_load` | `incremental_load`), default `incremental_load`. `full_load` ignores stored state, restarts from `after=0`, and sets output mapping `incremental=false` (table overwritten). `incremental_load` sets `incremental=true`.
- **Output mapping:** `incremental=<load_type flag>` **with the primary key set** → upsert on PK (no unbounded growth). Child tables inherit the parent's incremental flag and use their composite PK.
- **Deletes:** the `version` cursor is a change feed, not a delete feed. Deleted/voided records surface as updated rows carrying a status/`deleted_at` flag (varies per object); we pass those through as normal upserts. True delete propagation (webhooks) is out of scope for a batch extractor — documented as a known limitation.

### 2.4 Secrets

`#`-prefixed keys so the platform encrypts them (`KBC::ProjectSecure`): `#personal_token` (personal-token mode) and `#client_secret` / `#refresh_token` (OAuth mode). At runtime the component receives decrypted plaintext.

### 2.5 Sync actions

- **`testConnection`** — validates credentials + `domain_prefix` by calling a cheap endpoint (`GET /api/{version}/outlets?page_size=1`, or `/retailer`) and returning success/failure to the UI instead of only failing at runtime.
- The stream/object list is a **fixed known enum**, so the row's `object` field is a static schema `enum` dropdown, not a sync-action dropdown (enumeration adds nothing over the static list).

### 2.6 Output bucket / table naming

Follow default-bucket behaviour — the component does not hard-code bucket names. If `default_bucket: true` is set in the Dev Portal, outputs route to `in.c-keboola.ex-lightspeed-x-series-{configId}` and any manifest destination is overridden silently. Table names (`sales`, `sale_line_items`, …) are set as manifest `destination`/table names and are stable across runs.

---

## 3. Authentication & connection

### 3.1 Connection method

REST/JSON over HTTPS. Base URL built at runtime from config: `https://{domain_prefix}.retail.lightspeed.app/api/{api_version}/{endpoint}`. `Content-Type`/`Accept` = `application/json`. `api_version` is date-based (`YYYY-MM`), pinned in config with default `2026-07`; pinning avoids the silent downgrade the API applies to unsupported versions.

### 3.2 Chosen auth (v1): Personal Token

- Header `Authorization: Bearer {#personal_token}`. Long-lived, no refresh, single header — **headless and VCR-friendly**, and exactly how Fivetran/Airbyte authenticate.
- This is the **v1 default and the test/CI path**.

### 3.3 OAuth2 authorization-code (supported in schema, deferred activation)

- Config schema supports an `auth_type` = `oauth` branch (`client_id`, `#client_secret`, `#refresh_token`, obtained via the Lightspeed developer dashboard + the `/connect` authorize flow). Token endpoint: `https://{domain_prefix}.retail.lightspeed.app/api/1.0/token` (note `1.0`, the versioning exception).
- Read-only scopes only (e.g. `sales:read products:read inventory:read customers:read outlets:read registers:read payment_types:read retailer:read`).

### 3.4 OAuth refresh-token rotation vs per-row state — a real design hazard

Every X-Series token response returns a **new refresh token, and using a refresh token revokes the previous access token**. That rotating secret must be persisted somewhere the *next* run reads. In a config-rows component, **state is per-row and rows can run in parallel** — so storing the rotating refresh token in row state would have each row rotate independently and revoke the others. Therefore:

- **v1 ships personal-token auth** (no rotation → no hazard). This is why row parallelism is left off and why personal token is the recommended mode.
- If/when OAuth is activated, use **Keboola's OAuth broker** (which centralises token refresh outside per-row state) rather than hand-rolling refresh-token persistence in state. Hand-rolled OAuth would additionally require forcing the config to a single sequential row. This is captured in §9 risks.

### 3.5 Provisioning (the blocker to unblock before Phase 4/5 tests)

- Personal tokens are **Plus-plan only** and must be minted by an **admin** user in the X-Series web app (Setup → Personal Tokens). 
- **Blocker:** VCR recording needs a **Plus-plan trial/partner store with admin rights**. A default free trial may not be on the Plus tier. Obtaining this store is the single most important prerequisite for Phase 5; flag to the human now. Without it, no headless token → no cassette recording.
- OAuth needs a registered developer app (client_id/secret + redirect URI) — not required for v1.

---

## 4. Data model & endpoints

### 4.1 v1 stream set (in scope) and rationale

**Parents:** `sales`, `products`, `inventory`, `customers`, `outlets`, `registers`, `payment_types`.
**Child tables:** `sale_line_items`, `sale_payments`, `sale_taxes`, `sale_adjustments`, `product_images`.

Rationale — this is the minimal set that makes the extractor immediately useful for the primary BI use case:
- `sales` (+ its children) is the transactional core — revenue, line-item, tender and tax analysis. It is also the most nested object and thus the flattening design driver.
- `products` (+ `product_images`) and `inventory` give catalog + stock-on-hand, needed to contextualise sales.
- `customers` enables segmentation/CLV.
- `outlets`, `registers`, `payment_types` are small, slowly-changing **reference/dimension** tables that sales rows join to (outlet_id, register_id, payment_type_id) — cheap to pull and required to make sales analysable.

**Explicitly deferred** (documented, not built in v1): consignments/stock transfers, suppliers, product_categories/brands/tags/attributes, price_books/promotions, quotes, gift_cards, serial_numbers, fulfillments, store_credits, users, audit, custom_fields, webhooks. These are additive later rows — the architecture (one row per stream + a stream-definition registry) makes adding them low-cost.

### 4.2 Pagination & how the component respects it

- Version-cursor paging: request page → read `version.max` → next request `?after={max}` → repeat until `data` is empty. Explicit stopping condition = empty `data` array (never an off-by-one on the cursor).
- `page_size` is a row-level int with a safe default of **200** (endpoint-specific caps are not globally documented; 200 matches community/Airbyte practice). Configurable so it can be lowered if an endpoint rejects it.

### 4.3 Rate limits & backoff

- Budget = `300 × registers + 50` per 5-minute rolling window (≈350/5min single register). Every response carries `X-RateLimit-Limit` / `X-RateLimit-Remaining`.
- On **429**, the `Retry-After` header is an **RFC1123 HTTP-date (absolute timestamp), not seconds**. The client parses it as a date and sleeps until that instant (with a small clock-skew guard and a max-sleep cap), then resumes from the last cursor. The client also proactively watches `X-RateLimit-Remaining` and paces near exhaustion. Backoff logic lives in the client, is bounded, and logs a clear message when it waits.

### 4.4 Response shape / flattening

Responses are `{"data": [...], "version": {"min":…, "max":…}}` with deeply nested records. A **stream-definition registry** describes, per stream: endpoint path, parent PK, and the list of nested array fields to extract into child tables (with the child PK and the parent-id FK column name). The flattener strips the response envelope and per-record `_metadata`/`version` internal keys from output columns (the cursor is persisted to state, not written as data), emits scalar columns for the parent, and one child table per registered nested array. Variable-shape nested objects that are not modelled as child tables are kept as a single JSON string column rather than exploded.

---

## 5. Configuration & schema

The actual `configSchema.json` / `configRowSchema.json` JSON is built by **`component-build-ui`** (Phase 6). This section specifies the fields.

### 5.1 Root config (`configSchema.json`) — connection & global

| Field | Type | Req | Default | Notes |
|---|---|---|---|---|
| `domain_prefix` | string | yes | — | store subdomain, e.g. `example` |
| `api_version` | string | yes | `2026-07` | date-based `YYYY-MM` |
| `auth_type` | enum `personal_token`\|`oauth` | yes | `personal_token` | drives conditional fields (`options.dependencies`) |
| `#personal_token` | string (secret) | conditional | — | shown when `auth_type=personal_token` |
| `client_id` | string | conditional | — | OAuth only |
| `#client_secret` | string (secret) | conditional | — | OAuth only |
| `#refresh_token` | string (secret) | conditional | — | OAuth only |

Sync action: `testConnection`.

### 5.2 Row config (`configRowSchema.json`) — per object

| Field | Type | Req | Default | Notes |
|---|---|---|---|---|
| `object` | enum (the v1 stream names) | yes | — | static dropdown; picks which stream this row extracts |
| `load_type` | enum `full_load`\|`incremental_load` | yes | `incremental_load` | maps to `incremental` output flag |
| `page_size` | integer | no | `200` | pagination page size |

### 5.3 Pydantic config model (Phase 4, `configuration.py`)

- One `Configuration` model over the **merged** config (root ∪ row), all fields typed (no `Any`/raw `dict` for structured data), `model_config = ConfigDict(extra="ignore")`.
- Secrets via `Field(alias="#personal_token")` etc., alias matching the schema property exactly.
- Enums: `AuthType(StrEnum)`, `ObjectName(StrEnum)`, `LoadType(StrEnum)`; `incremental` as a `@computed_field` from `load_type`.
- **No `debug` field** — the platform `debug` parameter is consumed by the component base (auto-switches the root logger to DEBUG); a model `debug` field would be redundant. (The scaffold's example `print_hello` / `debug` / uppercase-token validator all get removed.)
- Validators are tolerant of `None`/empty and raise `UserException` (not bare `ValueError`) on genuinely bad config. A cross-field validator enforces that the fields for the selected `auth_type` are present.

---

## 6. Code architecture

- **`src/client/lightspeed_client.py`** — `LightspeedXSeriesClient`, the only module that talks HTTP.
  - Built from `domain_prefix`, `api_version`, and auth material; composes the base URL and auth header.
  - `paginate(endpoint, after, page_size) -> Iterator[list[dict]]` — yields pages, follows the `version` cursor, stops on empty `data`, returns/yields the running `version.max`.
  - Rate-limit handling: reads `X-RateLimit-Remaining`; on 429 parses the RFC1123 `Retry-After` date and sleeps until it (bounded, logged).
  - OAuth token refresh (when `auth_type=oauth`) isolated here; personal-token path is a static header.
- **`src/configuration.py`** — the Pydantic model(s) from §5.3.
- **`src/streams.py`** (stream-definition registry) — declarative map: stream → {endpoint, parent PK, child-array specs (name, child PK, parent-FK column), schema/column list with native types}. Adding a deferred stream later = adding an entry here.
- **`src/component.py`** — `Component(ComponentBase)`:
  - Client and config built in `__init__` (not in `run()`); config validated early.
  - `run()` is a **thin orchestrator (<30 lines)**: load merged config → resolve the stream definition for `object` → read state cursor → fetch+flatten → write parent+child tables & manifests → advance state. All real work in well-named private methods (`_fetch_stream`, `_flatten`, `_write_tables`, `_advance_state`).
  - `execute_action()` dispatches `run` vs the `testConnection` sync action.
- **Native types:** emit the **authoritative `schema` manifest** (`data_type.base.type`) with explicit columns + PK per table. CSVs written headerless (matching `has_header:false` default) — or `has_header=True` if a header is written; the write path and manifest must agree. `dataTypeSupport=authoritative` is set on the Dev Portal app in Phase 6 so the platform honours the schema.
- **Error handling / exit codes:** user-fixable conditions (missing/invalid config, auth failure `401/403`, unknown `domain_prefix`/DNS failure, `404` on endpoint, bad `api_version`) → `UserException` → **exit 1** with an actionable message. Unexpected failures bubble up → **exit 2**. 429 is *handled* (backoff), not raised.
- **Scratch files:** none written to `data/out/tables/` beyond real outputs (everything there is uploaded); any temp work uses `/tmp`.
- **Dependencies:** `keboola.component` (base/interface), `pydantic` (config), `requests` (HTTP; `keboola.http-client` optional for retry helpers). No heavyweight SDK — the API is a thin REST surface.

---

## 7. Testing

- **Datadir tests** (`tests/functional/*`), each a merged single-object `config.json` (root ∪ row shape) with row-scoped `state.json`, backed by a VCR cassette:
  - `sales_first_run` — no state → `after=0`, produces `sales` + 4 child tables; assert expected CSVs and manifests (PKs, schema).
  - `sales_incremental` — seeded `state.json` `{"last_version": N}` → asserts `after=N` request and advanced `last_version` in output state.
  - one happy-path case per other stream (`products` incl. `product_images`, `inventory`, `customers`, `outlets`, `registers`, `payment_types`).
  - `auth_failure` — 401 cassette → expects **exit 1** + clear message.
  - `rate_limit_retry` — a 429 (with a near-future `Retry-After` date) then 200 → asserts the client waits and completes (use a tiny/mocked sleep so the test is fast).
  - `empty_first_page` — `data:[]` → zero rows, no crash, state unchanged/graceful.
- **VCR strategy:** record real interactions against the **Plus-plan admin trial store** (§3.5) using a personal token. Sanitizers **must** scrub: `Authorization` header, `#personal_token`, the `domain_prefix` in request URLs/host, and PII in customer/sale payloads. Cassettes replay offline in CI (no network, no creds).
- **Sync-action test:** `testConnection` returns success on a 200 cassette and a `UserException`/failure payload on a 401 cassette.
- **First-run / missing-state** and **state-shape backward-compat** (unknown/legacy keys tolerated) are covered by the two `sales_*` cases.
- **Seed payloads:** capture a sample `listsales` / `listproducts` response during provisioning to seed the first cassettes.
- **`KBC_STACKID` in datadir tests:** `keboola-component` ≥1.6.7 emits the **authoritative typed `schema` manifest locally only when `KBC_STACKID` is set** — otherwise it falls back to the legacy format, so local manifest assertions would test the wrong shape. The datadir test harness must set `KBC_STACKID` (and, if asserting authoritative output, `KBC_DATA_TYPE_SUPPORT=authoritative`) to simulate the KBC runtime, rather than deferring the schema-format check to the Phase 7 cf-dev run.

---

## 8. Deployment & validation (cf-dev)

- Build an `initial-implementation` image (CI on push) and note its tag/digest.
- Via **`kbagent`**: create an `keboola.ex-lightspeed-x-series` config in the **cf-dev** project with `runtime.tag` (a.k.a. image tag override) = the `initial-implementation` build, root config = trial-store `domain_prefix` + `api_version` + `#personal_token`, and rows for at least `sales` and `products`.
- Run a job; **success looks like:** job exit 0; `in.c-…` bucket contains `sales` + `sale_line_items`/`sale_payments`/`sale_taxes`/`sale_adjustments` and `products` + `product_images` with non-zero rows on the trial store; a second run advances `last_version` and upserts (no duplicate PKs / no unbounded growth).
- Confirm the resolved image tag matches the branch build. Verify the `schema`/native-types load path on this **real** run (local datadir tests can't catch header/`has_header` or type-load failures).

---

## 9. Open risks & blockers

1. **Plus-plan admin trial store (BLOCKER, owner: human/provisioning).** Personal token is Plus-plan + admin only; it gates VCR recording (Phase 5) and the cf-dev smoke run (Phase 7). Must be secured before Phase 4/5. Highest priority.
2. **OAuth refresh-token rotation vs per-row/parallel state (design, deferred).** Rotating refresh token can't live safely in per-row parallel state. v1 = personal token; OAuth activation should use Keboola's OAuth broker (or force single-row sequential). Do not enable row parallelism while OAuth is a live path.
3. **`page_size` caps unknown per endpoint (low).** Default 200, configurable; verify empirically during recording and lower if any endpoint rejects it.
4. **Per-object delete semantics vary (medium).** The version cursor is a change feed, not a delete feed; deletes surface as flagged updates or not at all. Incremental = append/upsert of changes; true delete capture (webhooks) is out of scope. Documented limitation.
5. **`has_header` / native-type load mismatch (medium).** Only observable on a real Storage import — must be verified on the Phase 7 cf-dev run, not just local diffs.
6. **Child-table PKs where the nested line has no id (low).** Synthesise a stable composite key (parent_id + array index) for tax/adjustment lines lacking their own id; confirm against real payloads during recording.

---

## Grounding reconciliation (keboola-context)

Filled from the mandatory fresh-subagent reconciliation pass (Phase 3 gate, fresh context, all 8 references read in full). Each behaviour-relevant reference → `correct` or `corrected:`.

- `architecture-conventions.md` → correct
- `config-rows.md` → correct (sequential-by-default rows, parallelism opt-in and deferred; per-row state, root state unused)
- `incremental-state.md` → correct (watermark persisted only after successful write; version cursor used instead of timestamp, justified)
- `output-mapping.md` → correct (incremental+PK upsert; no scratch files under `data/out/tables/`)
- `native-data-types.md` → correct (authoritative `schema` gated on Dev Portal `dataTypeSupport`; `has_header` agreement flagged as locally unverifiable)
- `encryption.md` → correct (`#`-prefix → `KBC::ProjectSecure`, plaintext at runtime)
- `exit-codes.md` → correct (`UserException`→exit 1, unexpected→exit 2)
- `default-bucket.md` → correct (no hard-coded bucket; default-bucket override understood)

**Additional gate finding folded in (not one of the 8, but a concrete gap):** datadir/VCR tests must set `KBC_STACKID` so `keboola-component` ≥1.6.7 emits the authoritative typed manifest locally — now specified in §7 ("`KBC_STACKID` in datadir tests").
