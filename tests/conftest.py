"""Shared pytest configuration for all test types (unit, datadir, VCR functional).

Simulates the KBC platform runtime for the whole test session. `keboola-component`
only emits the authoritative typed `schema` manifest (instead of the legacy
`column_metadata` one) once `_running_in_kbc` is true (`KBC_STACKID` set) *and*
`KBC_DATA_TYPE_SUPPORT` says so — mirroring the Dev Portal's `dataTypeSupport`
property (design spec §6/§7; `KBC_PROJECT_FEATURE_GATES=queuev2` is required
alongside it, otherwise the legacy-queue code path strips `write_always` from
the manifest). Without this, local datadir/VCR tests would assert the wrong
manifest shape compared to what actually runs on the platform.
"""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _simulate_kbc_runtime():
    os.environ["KBC_STACKID"] = "connection.keboola.com"
    os.environ["KBC_PROJECT_FEATURE_GATES"] = "queuev2"
    os.environ["KBC_DATA_TYPE_SUPPORT"] = "authoritative"
    yield
