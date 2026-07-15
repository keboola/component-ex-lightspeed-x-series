# HANDOFF — keboola.ex-lightspeed-x-series

**Date:** 2026-07-15
**Repo:** `keboola/component-ex-lightspeed-x-series` (public)
**Branch:** `initial-implementation` (all work; `main` is bootstrap `0.0.1` only, protected)
**HEAD:** `32b7bd4`

This component was built via the Component Factory (8-phase). The build is **paused at Phase 5** pending a
credential decision AND a newly-surfaced scoping question (see below). This file is the committed handoff;
the full per-phase tracker lives locally at `docs/superpowers/keboola.ex-lightspeed-x-series-lifecycle.md`
(gitignored — not in this repo).

---

## 🚨 READ FIRST — THE OPEN DECISION THAT PAUSED THE BUILD

**WE BUILT X-SERIES. THE DRIVING CUSTOMER DOES NOT HAVE X-SERIES.**

- The customer's actual Lightspeed access is **R-SERIES (retail, legacy)** + **L-SERIES** and **K-SERIES**
  (both restaurant). They have **NO X-SERIES** and no Golf.
- X-Series was chosen as a **strategic/marketplace** connector (self-serve to develop, best-documented,
  go-forward retail POS, general market demand) — NOT because this customer runs it. The original brief
  said explicitly: build X-Series, do NOT build R-Series (legacy, leaky-bucket rate limit, uncertain
  future), and do not re-evaluate that choice.
- **CONSEQUENCE 1:** The customer's credentials **CANNOT** test/validate this component. X-Series and
  R-Series are entirely different APIs (different host, auth, data model). Their tokens will not
  authenticate against X-Series. "Validate against real customer data at the end" is impossible for
  X-Series with this customer.
- **CONSEQUENCE 2:** For this customer's *retail* data, the correct connector is **R-Series**, not
  X-Series. (Their restaurant data would be L-Series + K-Series — separate components, explicitly out of
  scope for now per the customer.)

### ⛔ DO NOT CONTINUE BUILDING UNTIL A HUMAN DECIDES ONE OF:

1. **CONTINUE X-SERIES ONLY** — as a strategic marketplace connector. Requires creating **our own**
   X-Series Plus-plan admin trial store to record cassettes + smoke-test. This customer is NOT the
   validator. Their R/L/K needs are out of scope for this repo.
2. **PIVOT TO R-SERIES** — build the retail connector the customer actually uses. Their existing creds
   would unblock testing immediately. This CONTRADICTS the original brief and needs explicit human
   sign-off. Much of this repo's scaffolding is reusable (see "What transfers").
3. **X-SERIES NOW + R-SERIES NEXT** — finish X-Series (still needs our own trial store), then a separate
   R-Series build for the customer.
4. **RETHINK SCOPE** — reassess the engagement before more building.

---

## ✅ WHAT IS DONE (Phases 1–4 complete + gated; Phase 5 scaffolded)

| Phase | State | Notes |
|---|---|---|
| 1 Scaffold + bootstrap | ✅ | Repo public under `keboola`; `0.0.1` built+deployed, visible in Dev Portal; CI creds set; no secrets committed. Independently gated. |
| 2 Research | ✅ | `docs/superpowers/2026-07-15-ex-lightspeed-x-series-research.md`. Gated. |
| 3 Spec + plan | ✅ | `docs/superpowers/specs/2026-07-15-ex-lightspeed-x-series-design.md` + `plans/…`. User-approved. Gated. |
| 4 Implementation | ✅ | API client, Pydantic config, version-cursor incremental, RFC1123 rate-limit backoff, thin `run()`. Independently gated ✅; 2 minor findings fixed (`202d066`). |
| 5 VCR tests | ⏳ SCAFFOLDED, PARKED | 63 unit/logic tests green (also green in `docker --target test`); datadir + VCR harness + sanitizers + `secrets.json` template ready. **Cassettes NOT recorded — needs a live token.** |
| 6 Dev Portal values | ⬜ | configSchema/configRowSchema, sync actions, portal props. Needs NO token — could proceed independently. |
| 7 cf-dev smoke test | ⬜ | Needs a working token + a recorded/real store. |
| 8 Final review + PR | ⬜ | Open `initial-implementation → main` PR, full audit, babysit-pr loop. |

Commits on `initial-implementation`: `a1e440a` (core impl) → `ba0432e` (import fix: `from __future__
import annotations`) → `202d066` (harden client: Timeout handling + bounded 429) → `32b7bd4` (Phase 5
tests + VCR scaffolding).

---

## 🔑 WHAT IS NEEDED TO CONTINUE (if staying on X-Series)

**A Plus-plan, admin X-Series trial store** (only that tier can mint personal tokens). The customer's creds
do NOT work here (they're R/L/K). Then populate `secrets.json` (repo root, gitignored) — 3 fields:

```json
{
  "parameters": {
    "domain_prefix": "<store-subdomain, e.g. mystore from mystore.retail.lightspeed.app>",
    "api_version": "2026-07",
    "#personal_token": "<personal token>"
  }
}
```

Then: record cassettes (start with a cheap stream like `outlets`/`registers`, plus an auth-failure case
and the `sales` first-run → incremental chain), sanitize, run full `pytest` green, run the cassette
validation gate, finish Phases 5 → 6 → 7 → 8.

> NOTE the trial store has been HARD to create — X-Series marketing is demo/quote-oriented and the
> self-serve trial can be region-gated. Account creation + email confirmation must be done by a human.

---

## ♻️ WHAT TRANSFERS IF WE PIVOT TO R-SERIES

Reusable with little/no change: Keboola plumbing, Pydantic `Configuration` pattern (`src/configuration.py`),
stream-definition registry pattern (`src/streams.py`), flattener (`src/flattener.py`), thin `run()`
orchestrator (`src/component.py`), the datadir + VCR test harness, sanitizers, CI, Dockerfile.
API-specific rewrites: `src/client/lightspeed_client.py` (R-Series uses a different host, auth, and the
legacy leaky-bucket rate limiting — NOT the version cursor), the endpoint/stream list, and pagination
(R-Series is offset/`limit`-based, not version-cursor).

---

## KEY FILES
- Spec: `docs/superpowers/specs/2026-07-15-ex-lightspeed-x-series-design.md`
- Plan: `docs/superpowers/plans/2026-07-15-ex-lightspeed-x-series.md`
- Research: `docs/superpowers/2026-07-15-ex-lightspeed-x-series-research.md`
- Code: `src/component.py`, `src/client/lightspeed_client.py`, `src/configuration.py`, `src/streams.py`, `src/flattener.py`
- Tests: `tests/` (unit) + `tests/functional/` (datadir/VCR) + `tests/setup/configs.json`
- Local build tracker (gitignored, not in repo): `docs/superpowers/keboola.ex-lightspeed-x-series-lifecycle.md`

## SESSION ARTIFACTS THAT DID NOT SURVIVE
- The Component Factory heartbeat cron loop was session-only and is gone.
- No PR is open yet (Phase 8 opens it).
