# Lightspeed Retail X-Series API — Research Summary (Phase 2)

Target: new Keboola extractor `ex-lightspeed-x-series` (ex-Vend POS).
All facts verified against live docs on 2026-07-15. URLs cited inline.

---

## 0. TL;DR / key corrections to the starting assumptions

- **Base host is NOT `x-series-api.lightspeedhq.com`.** That host is the *docs/OpenAPI* site. Live API calls go to a **per-store subdomain**: `https://{domain_prefix}.retail.lightspeed.app/api/{version}/...`
- **Versioning is now date-based (`YYYY-MM`), not `/api/2.0`.** `v0.9` and `v2.0` are deprecated. Current supported versions include `2026-07`, `2026-04`, `2026-01`. New quarterly release every 3 months; each supported min 12 months. The version sits **in the URL path** (`/api/2026-07/products`). (The OAuth token endpoint is the exception — it is still `/api/1.0/token`.)
- **Incremental is fully supported natively** via the monotonic `version` cursor + `after` param on collection endpoints — this is the crown jewel for incremental loads. Note: the **Airbyte** reference connector does NOT use it (full-sync only); **Fivetran** does sync incrementally. So we must implement the cursor ourselves, and can't lean on Airbyte's design here.

---

## 1. API style & versioning

- **Style:** REST, JSON over HTTPS, token-based auth. `Content-Type` and `Accept` = `application/json` (exceptions: webhooks + OAuth token = `application/x-www-form-urlencoded`, image upload = `multipart/form-data`).
  Source: https://x-series-api.lightspeedhq.com/docs/request_format
- **Base URL:** `https://{domain_prefix}.retail.lightspeed.app/api/{version}/[endpoint]`
  Example: `GET https://example.retail.lightspeed.app/api/2026-07/retailer`
  Source: https://x-series-api.lightspeedhq.com/docs/quick_start
- **`domain_prefix`** = the retailer's store subdomain (e.g. `example` in `example.retail.lightspeed.app`). It is a **required config field** the user supplies; not auto-discovered from a personal token. (OAuth flow can surface it, but for our extractor it's user-entered — matches how Airbyte asks for "subdomain" and Fivetran/Postman ask for `{{domain_prefix}}`.)
- **Versioning:** date-based `YYYY-MM`, specified **in the URL path**. Quarterly cadence, min 12-month support window. A request to an unsupported version is auto-served by the oldest still-supported version (silent downgrade — pin an explicit version to avoid surprise behavior). Beta of next release available early but may have breaking changes.
  Source: https://x-series-api.lightspeedhq.com/docs/versioning-strategy
- **OpenAPI spec:** published. Machine index at **`https://x-series-api.lightspeedhq.com/llms.txt`** (Markdown pages + OpenAPI endpoints). Per-endpoint reference pages are `https://x-series-api.lightspeedhq.com/reference/{operation}.md` (e.g. `listsales.md`, `listproducts.md`). Postman collection also exists: https://www.postman.com/lightspeedhq/x-series-api

**Design impact:** config needs `domain_prefix` + a pinned `api_version` (default a current date version like `2026-07`). Build the client base URL from these.

---

## 2. Authentication (both flows)

Source: https://x-series-api.lightspeedhq.com/docs/authorization , scopes: https://x-series-api.lightspeedhq.com/docs/scopes

### A. Personal Token (recommended for headless dev/testing + single-store)
- Header: `Authorization: Bearer {token_value}`
- Minted by an **admin user** in the X-Series web app (Setup → Personal Tokens / private apps).
- **Plan-gated: Plus plan only.** Using personal tokens limits the app to Plus-plan retailers.
- No refresh needed; long-lived until regenerated. This is what both Fivetran and Airbyte use.
- Support docs: https://x-series-support.lightspeedhq.com/hc/en-us/articles/25534105904027-Retail-POS-X-Series-Personal-Tokens

### B. OAuth2 Authorization Code grant (recommended for production public/multi-store apps)
- **Authorize URL:** `https://secure.retail.lightspeed.app/connect?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}&state={state}&scope={scope}`
  - `state` ≥ 8 chars (CSRF). `redirect_uri` must exactly match the one registered in the developer dashboard. `scope` = space-delimited list.
- **Token URL:** `https://{domain_prefix}.retail.lightspeed.app/api/1.0/token` (note: `1.0`, per-store host)
- **Token response:** access token + `expires` (absolute unix ts) + `expires_in` (seconds) + a **refresh token** on every response.
- **Refresh behavior:** request a new access token when expired using the refresh token; **using a refresh token revokes the previous access token** (rotate/persist the newest one). Token endpoint is itself rate-limited (`X-RateLimit-*` headers).
- **Scopes:** fine-grained `resource:read` / `resource:write` (full list in scopes doc). For a read-only extractor we need the `:read` scopes only, e.g. `sales:read products:read inventory:read customers:read registers:read outlets:read suppliers:read taxes:read payment_types:read promotions:read consignments:read fulfillments:read gift_cards:read serial_numbers:read price_books:read` (+ `products:read:price_books`), plus `retailer:read`.

**Design impact:** Support **personal token first** (simple, headless, VCR-friendly). OAuth requires a registered dev app + redirect URI + refresh-token persistence in Keboola component state; heavier. Recommend shipping personal-token auth for v1 and adding OAuth (via Keboola's OAuth broker or manual refresh-token config) later.

---

## 3. Pagination

Source: https://x-series-api.lightspeedhq.com/docs/pagination + reference/listsales.md

- **Mechanism:** version-cursor. Every collection response has:
  - `data`: resources **sorted ascending by version**
  - `version`: object with `min` and `max` (lowest/highest version in this page)
  - per-record `_metadata.version` (monotonic int, bumped on every change to that resource)
- **`after` param** (int64): lower bound on version to include. Default `0` (omit on first page).
- **`before` param** (int64): documented on some endpoints (e.g. sales) as an upper bound; not the primary paging tool.
- **`page_size`:** max is endpoint-dependent; docs don't give a single number. Community/Airbyte practice caps around **200**. Treat as configurable with a safe default (e.g. 200) and don't assume it globally.
- **Paging loop:** request page → take `version.max` → next request `?after={max}` → repeat until `data` is empty.
- **`version` is NOT guaranteed to increment by 1** — never compute `max+1`; always pass the returned `max` verbatim as the next `after`.

---

## 4. Rate limits

Source: https://x-series-api.lightspeedhq.com/docs/rate_limiting

- **Formula confirmed:** `limit = 300 × (number of registers) + 50` per **5-minute rolling window**. Single-register store ≈ **350 req / 5 min**.
- **Headers on every response:** `X-RateLimit-Limit`, `X-RateLimit-Remaining`. (Token endpoint also returns `X-RateLimit-Reset`.)
- **On exceed:** HTTP **429** with body `{"error":"Too Many Requests","message":"Rate limiting enforced"}` and a **`Retry-After` header in RFC1123 HTTP-date format** (e.g. `Retry-After: Wed, 15 Jul 2020 15:04:05 GMT`) — i.e. an absolute timestamp, **not** seconds. Parse it as a date and sleep until then.
- **Recommended backoff:** queue/defer on first 429 until the window resets; don't sleep-per-request. For our extractor: proactively watch `X-RateLimit-Remaining`, and on 429 sleep until the `Retry-After` datetime (with clock-skew guard), then resume from the last cursor.

---

## 5. Incremental support (per object)

- **Native, uniform mechanism:** the `version` cursor on collection endpoints (§3). Store `version.max` as Keboola component **state**; next run pass it as `after` to pull only created/updated records. Works for the major streams: sales, products, customers, inventory, consignments, etc.
- This is a **change cursor, not a deleted-record feed** — deletes generally surface as records with a `deleted_at`/status flag (varies per object) rather than disappearing; webhooks are the path for real-time delete signals. For a batch extractor, incremental append + relying on the updated record carrying a deleted flag is the practical model.
- There are also `date_from`/`date_to` / `updated_at`-style filters on some endpoints, but the `version` cursor is the vendor-blessed, reliable incremental key and should be the primary strategy.
- **Reference-connector divergence (important):**
  - **Fivetran** — "incrementally syncs the following tables and their child tables to capture new records" (uses the cursor). Source: https://fivetran.com/docs/connectors/applications/lightspeed-retail-xseries
  - **Airbyte** — declares **Full Refresh only, no Incremental**, for all 24 streams (it uses CursorPagination purely for paging, not for state). Source: https://github.com/airbytehq/airbyte/blob/master/docs/integrations/sources/lightspeed-retail.md
  - Takeaway: we implement the `version`-cursor state ourselves (Fivetran-style); Airbyte is only useful as a stream inventory, not an incremental blueprint.

---

## 6. Data objects (extractable endpoints)

Endpoint reference index: https://x-series-api.lightspeedhq.com/llms.txt (per-endpoint `reference/*.md`).
Airbyte stream inventory (24 streams, all `id` PK, cursor pagination):
https://github.com/airbytehq/airbyte/blob/master/docs/integrations/sources/lightspeed-retail.md

**Core streams:** sales, payments (nested in sales + `payment_types`), registers, products, product_categories, brands, attributes, tags, price_books, promotions, inventory (records/levels/adjustments), consignments (+ consignment_products — stock orders/transfers/counts), customers, customer_groups, suppliers, outlets, taxes, users, services, quotes, gift_cards, serial_numbers, fulfillments, audit, store_credits, custom_fields, webhooks, retailer (single object).

**Response shape — nested, needs flattening.** The `sale` object (reference/listsales.md) is deeply nested; child arrays to flatten into separate tables:
- `line_items[]` (with nested `promotions[]`, `customizations[]`, `surcharges[]`)
- `payments[]` (with nested `external_applications[]`, `external_attributes[]`)
- `adjustments[]`, `taxes[]`, `ecom_custom_charges[]`, `external_applications[]`

Similarly **products** carry variants/composites and images; **inventory** is per-outlet; **consignments** have `consignment_products`. Plan a parent table + child tables keyed by parent id (Fivetran splits e.g. `sale_line_items`, and models child tables explicitly).

**Design impact:** use a JSON flattener (parent + child tables joined on parent id) rather than one wide table. Primary key = `id` for parents; child tables need composite/synthetic keys.

---

## 7. Feasibility & provisioning verdict

**Overall: FEASIBLE and well-suited to a Keboola extractor.** REST+JSON, clean version-cursor incremental, published OpenAPI.

- **Headless auth (VCR recording): VIABLE via personal token.** Single `Authorization: Bearer` header, no interactive redirect — ideal for VCR functional tests recorded against a real store. This is exactly how both Fivetran and Airbyte authenticate.
- **Sandbox / trial store:** Lightspeed offers **free trials** of X-Series. That gives a live store to record VCR cassettes against.
  - **BLOCKER to confirm:** personal tokens are **Plus-plan only**. A default free trial may not be on the Plus tier, and admin access is required to mint the token. **Provisioning need:** obtain a Plus-plan trial (or a partner/dev store on Plus) with admin rights so a personal token can be created. Without Plus + admin, no headless token → no VCR recording. **This is the single most important thing to unblock before Phase 4 tests.**
- **Self-serve dev-app registration (OAuth):** developer dashboard exists (register app, set redirect URI, get client_id/secret). Fine for production but not needed for v1 if we ship personal-token auth. OAuth also needs refresh-token rotation handling (previous token revoked on refresh) — extra state-management work.
- **Rate limits:** generous enough for batch extraction (~350/5min single register, scales with registers). Must implement `Retry-After` (HTTP-date) backoff — easy.
- **Data-model complexity:** the main real cost is flattening deeply-nested objects (sales, products) into parent+child tables. No blocker, just design effort.

### Open unknowns / to confirm in Phase 3–4
1. Exact **max `page_size`** per endpoint (docs vague; assume ~200, verify empirically).
2. Whether the trial/dev store we get is **Plus plan with admin** (gates personal token + VCR). — top priority.
3. Precise **delete semantics** per object (deleted flag vs disappearance) for incremental correctness.
4. Whether to use **Keboola's OAuth broker** for the OAuth flow or ship personal-token-only for v1 (recommend v1 = personal token).
5. Confirm the exact **child-table breakdown** and PKs by pulling each `reference/*.md` / the OpenAPI schema during spec.

---

## Source URLs
- Introduction: https://x-series-api.lightspeedhq.com/docs/introduction
- Authorization: https://x-series-api.lightspeedhq.com/docs/authorization
- Scopes: https://x-series-api.lightspeedhq.com/docs/scopes
- Rate limiting: https://x-series-api.lightspeedhq.com/docs/rate_limiting
- Pagination: https://x-series-api.lightspeedhq.com/docs/pagination
- Versioning strategy: https://x-series-api.lightspeedhq.com/docs/versioning-strategy
- Request format: https://x-series-api.lightspeedhq.com/docs/request_format
- Quick start (base URL/domain_prefix): https://x-series-api.lightspeedhq.com/docs/quick_start
- OpenAPI/llms index: https://x-series-api.lightspeedhq.com/llms.txt
- Sale object shape: https://x-series-api.lightspeedhq.com/reference/listsales
- Fivetran connector: https://fivetran.com/docs/connectors/applications/lightspeed-retail-xseries
- Airbyte connector (stream list): https://github.com/airbytehq/airbyte/blob/master/docs/integrations/sources/lightspeed-retail.md
