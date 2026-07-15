"""VCR-based functional tests for keboola.ex-lightspeed-x-series.

Discovers only test cases under `tests/functional/` that already have a
recorded cassette (`get_test_cases`) — folders scaffolded with `--no-record`
(no live credentials available yet, design spec §7/§9 risk #1) are skipped
rather than failing, so this file collects zero tests until cassettes exist.
"""

from pathlib import Path

import pytest
from keboola.datadirtest.vcr import VCRDataDirTester, get_test_cases

FUNCTIONAL_DIR = str(Path(__file__).parent / "functional")
COMPONENT_SCRIPT = str(Path(__file__).parent.parent / "src" / "component.py")


@pytest.mark.parametrize("test_name", get_test_cases(FUNCTIONAL_DIR))
def test_functional(test_name):
    """Run a single VCR functional test case."""
    tester = VCRDataDirTester(
        data_dir=FUNCTIONAL_DIR,
        component_script=COMPONENT_SCRIPT,
        selected_tests=[test_name],
    )
    tester.run()
