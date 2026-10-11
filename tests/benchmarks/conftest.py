"""The benchmarks need the CodSpeed runner, and only codspeed.yml installs it.

That is deliberate — the suite must not need a benchmark runner to check
correctness (see WORKFLOW_ONLY in tests/test_declared_imports.py) — so
`pytest`, as the README says to run it, would otherwise end in `fixture
'benchmark' not found` errors that say nothing about the code. Without the
runner they are skipped and say why; codspeed.yml runs them.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    if config.pluginmanager.hasplugin("codspeed"):
        return
    skip = pytest.mark.skip(reason="the CodSpeed runner is not installed: codspeed.yml runs the benchmarks")
    for item in items:
        if "benchmark" in getattr(item, "fixturenames", ()):
            item.add_marker(skip)
