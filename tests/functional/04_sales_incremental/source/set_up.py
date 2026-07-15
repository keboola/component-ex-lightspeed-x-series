"""Seeds `in/state.json` for the incremental-load replay of this test.

`keboola.datadirtest.TestDataDir.setUp()` unconditionally resets a standalone
(non-chained) test folder's `source/data/in/state.json` to `{}` before running
the component (confirmed empirically — see the Phase 5 test-tester report).
`.gitignore` also excludes `tests/functional/*/source/data/in/`, so a
committed state.json there would neither survive `setUp()` nor even be
tracked. `set_up.py` is the supported extension point: it runs right after
that reset, so it is the only place a fixed, replayable `last_version` can be
guaranteed for this test.

IMPORTANT — the `last_version` below MUST equal whatever value was actually
used as the `after=` query param when the real cassette was recorded, or VCR
replay will fail to match the request (no matching interaction in the
cassette). When recording this test for real:

1. Record `03_sales_first_run` first and read the real `last_version` it
   produced from its `source/data/out/state.json` (or use
   `scaffold --chain-state` to forward it automatically into this test's
   `in/state.json` at record time).
2. Copy that exact same integer into `LAST_VERSION` below before recording
   `04_sales_incremental`, so the committed replay fixture and the recorded
   cassette agree on the `after=` cursor.
"""

import json
import os

# Placeholder until the real cassette is recorded (design spec §7/§9 risk #1) —
# a live personal token is required to know the real store's version cursor.
LAST_VERSION = 0


def run(test_case):
    state_path = os.path.join(test_case.source_data_dir, "in", "state.json")
    with open(state_path, "w") as f:
        json.dump({"last_version": LAST_VERSION}, f)
