# keboola.ex-lightspeed-x-series — Implementation Plan

> Executes the design spec `docs/superpowers/specs/2026-07-15-ex-lightspeed-x-series-design.md`.
> Run via `superpowers:subagent-driven-development`: one fresh subagent per task, review between tasks.
> Owner skills: implementation → `component-develop` (delegates schema/UI to `component-build-ui`); tests → `component-test` / `generate-vcr-tests`.
> All work on branch `initial-implementation`. Each task ends with `ruff check` clean and a commit.

## Prerequisite (blocks Phase 5 & 7, not Phase 4)
- [ ] **P0. Secure a Plus-plan admin X-Series trial/partner store** and mint a personal token. Capture one sample `listsales` and `listproducts` response to seed cassettes. Owner: human/provisioning. Phase 4 code can proceed against these fixtures before the store exists, but VCR recording (Phase 5) and cf-dev smoke (Phase 7) are blocked until it does.

## Phase 4 — Implementation (owner: `component-develop`)

- [ ] **T1. Remove scaffold example + define the config model.** Strip `print_hello`/`debug`/uppercase-token example from `src/component.py` and `src/configuration.py`. Write the Pydantic `Configuration` over the merged root∪row config per spec §5.3: `AuthType`/`ObjectName`/`LoadType` StrEnums, `domain_prefix`, `api_version` (default `2026-07`), auth fields with `#`-alias secrets, `page_size` (default 200), `load_type` (default `incremental_load`) with `incremental` computed_field, `extra="ignore"`, **no `debug` field**, cross-field validator asserting the selected `auth_type`'s fields exist, all raising `UserException`. Gate: `configuration`, `typing`.

- [ ] **T2. Stream-definition registry (`src/streams.py`).** Declarative map for the 7 v1 streams per spec §2.1/§4.4: endpoint path, parent PK, child-array specs (name, child PK, parent-FK column), and explicit column list with native `base.type` per table. Include the 5 child tables (`sale_line_items`, `sale_payments`, `sale_taxes`, `sale_adjustments`, `product_images`). Gate: `architecture`, `typing`.

- [ ] **T3. API client (`src/client/lightspeed_client.py`).** `LightspeedXSeriesClient`: base-URL composition from `domain_prefix`+`api_version`; personal-token Bearer header (OAuth refresh path stubbed/isolated but not activated in v1); `paginate(endpoint, after, page_size)` following the `version` cursor, stopping on empty `data`, exposing the running `version.max`; rate-limit handling reading `X-RateLimit-Remaining` and, on 429, parsing the **RFC1123 `Retry-After` HTTP-date** and sleeping until it (bounded, clock-skew guard, logged); auth/DNS/404/bad-version failures raised as `UserException`. Gate: `architecture`, `error-handling`.

- [ ] **T4. Flattener.** Given a stream definition + a list of raw records, strip the response envelope and per-record `_metadata`/`version` internal keys, emit scalar parent columns, and build one child-table row-set per registered nested array (injecting the parent-id FK; synthesising composite PKs for id-less lines). Non-modelled nested objects → single JSON string column. Gate: `output-state`, `architecture`.

- [ ] **T5. `run()` orchestrator + output writing (`src/component.py`).** Client+config built in `__init__`; `run()` <30 lines delegating to `_fetch_stream`/`_flatten`/`_write_tables`/`_advance_state`. Read per-row state `{"last_version": N}` (first run → `after=0`); for `full_load` ignore state and set `incremental=false`. Write parent+child tables with **authoritative `schema` manifests** (explicit columns, PK, native types), CSV/`has_header` agreement per spec §6. Persist highest `version.max` to state **only after successful write**. Wire `testConnection` sync action dispatch. Gate: `architecture`, `output-state`, `logging`.

- [ ] **T6. Config schema + UI (owner: `component-build-ui`).** `component_config/configSchema.json` (root: `domain_prefix`, `api_version`, `auth_type` with `options.dependencies` conditional secret fields) and `configRowSchema.json` (`object` enum, `load_type` dropdown, `page_size`). `testConnection` sync action declaration. Update `component_config/*_description.md`, `configuration_description.md`, `documentationUrl.md`. Schema-tester validation. Gate: `schema-ui`, `sync-actions`, `component-config`.

## Phase 5 — Tests (owner: `component-test` / `generate-vcr-tests`) — needs P0

- [ ] **T7. Datadir + VCR test suite.** Cases per spec §7: `sales_first_run`, `sales_incremental`, one happy path per other stream, `auth_failure`(→exit 1), `rate_limit_retry`(429→200, mocked sleep), `empty_first_page`. Merged single-object `config.json` fixtures, row-scoped `state.json`. **Set `KBC_STACKID`** (+ `KBC_DATA_TYPE_SUPPORT=authoritative` where asserting native types) in the harness so the authoritative manifest is emitted locally. `testConnection` sync-action tests. Gate: `testing`, `output-state`.

- [ ] **T8. Record + sanitize cassettes.** Record against the P0 store with the personal token; VCR sanitizers scrub `Authorization`, `#personal_token`, `domain_prefix` (URL/host), and customer/sale PII. Confirm cassettes replay offline (no network/creds). Full `pytest` green. Gate: `testing`, `credentials`.

## Phase 6 — Developer Portal value setup (owner: `component-dev-portal`, via `kbagent`)
- [ ] **T9.** After bootstrap release: push configSchema/row schema, sync actions, descriptions, and **set `dataTypeSupport=authoritative`** and confirm `default_bucket` behaviour. Verify with a fresh `kbagent dev-portal` GET.

## Phase 7 — cf-dev smoke test (owner: `component-test`) — needs P0
- [ ] **T10.** `kbagent`-create a cf-dev config (trial `domain_prefix`+`#personal_token`, rows for `sales`+`products`) with `runtime.tag` = the `initial-implementation` build. Run a job → exit 0, expected tables with non-zero rows; second run advances `last_version` and upserts (no dup PKs). Verify resolved image tag matches the branch build and the native-type/`has_header` load path succeeds on the real Storage import.

## Phase 8 — Final review
- [ ] **T11.** Open `initial-implementation → main` PR, request Copilot, run full `component-checklist-review`, converge `babysit-pr`. Hand clean PR to maintainer.
