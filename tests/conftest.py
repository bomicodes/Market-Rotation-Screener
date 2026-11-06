import pytest

import app as appmod


@pytest.fixture(autouse=True)
def _isolate_l2_cache(monkeypatch):
    """The persistent L2 cache serves after-close payloads from the local
    SQLite store; tests must never see results saved by earlier runs."""
    monkeypatch.setattr(appmod, "L2_PREFIXES", ())
    appmod.L2_MEMORY.clear()
    yield
    appmod.L2_MEMORY.clear()


@pytest.fixture(autouse=True)
def _no_network_earnings(monkeypatch):
    monkeypatch.setattr(appmod, "_context_earnings_catalyst", lambda t: {"next_earnings": None, "days_to_earnings": None, "risk": "Unknown"})
