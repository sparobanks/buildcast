import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def small_company(tmp_path_factory):
    """A 60-job simulated company, run through the pipeline once for all tests."""
    from buildcast.pipeline import run
    from buildcast.simulate import Config, generate

    d = tmp_path_factory.mktemp("company")
    generate(d, Config(n_jobs=60, seed=7))
    res = run(d, d / "warehouse.db", mapping={})
    return d, res
