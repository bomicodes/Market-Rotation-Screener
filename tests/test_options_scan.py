import app as appmod


def _row(symbol, dte, liquidity="Liquid", moneyness=2.0, iv=25.0):
    return {
        "symbol": symbol,
        "type": "call",
        "expiration": "2026-10-01",
        "dte": dte,
        "strike": 102.0,
        "bid": 1.0,
        "ask": 1.1,
        "mid": 1.05,
        "last": 1.05,
        "volume": 150,
        "open_interest": 800,
        "iv": iv,
        "delta": 0.35,
        "gamma": 0.02,
        "spread_pct": 9.5,
        "moneyness_pct": moneyness,
        "liquidity": liquidity,
        "execution_label": liquidity,
    }


def test_options_scan_reuses_one_chain_for_liquidity_and_gex(monkeypatch):
    calls = {"contracts": 0, "chain": 0}
    rows = {
        "NEAR": _row("NEAR", 3),
        "SWING1": _row("SWING1", 10),
        "SWING2": _row("SWING2", 18),
        "SWING3": _row("SWING3", 28),
    }

    monkeypatch.setattr(appmod, "realized_vol_20d", lambda ticker: (0.20, 100.0))

    def contracts(*args):
        calls["contracts"] += 1
        return [{"symbol": symbol} for symbol in rows]

    def chain(*args):
        calls["chain"] += 1
        return {symbol: {} for symbol in rows}

    monkeypatch.setattr(appmod, "alpaca_option_contracts", contracts)
    monkeypatch.setattr(appmod, "alpaca_option_chain", chain)
    monkeypatch.setattr(appmod, "option_contract_row", lambda symbol, snap, meta, spot: dict(rows[symbol]))
    monkeypatch.setattr(appmod, "modeled_dealer_positioning", lambda selected, spot: {"available": True, "count": len(selected)})

    payload = appmod.options_scan_payload("TEST", "0-30", 35, 7)

    assert calls == {"contracts": 1, "chain": 1}
    assert payload["contracts_checked"] == 3
    assert payload["gex_contracts_checked"] == 4
    assert payload["positioning"]["count"] == 4
    assert payload["liquidity"] == "Liquid"
    assert payload["scan_optimized"] is True


def test_front_month_scan_does_not_fall_through_to_leaps(monkeypatch):
    front = {"ticker": "TEST", "liquidity": "Thin", "contracts_checked": 2}
    monkeypatch.setattr(appmod, "options_scan_payload", lambda *args, **kwargs: dict(front))
    monkeypatch.setattr(appmod, "options_quality_payload", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LEAPS fetch should not run")))
    appmod.CACHE.clear()

    payload = appmod.contract_liquidity_gate("TEST", allow_leaps=False)

    assert payload["liquidity"] == "Thin"
    assert payload["leaps_checked"] is False
