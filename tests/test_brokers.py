# -*- coding: utf-8 -*-
"""
tests/test_brokers.py

Tests para los conectores de brokers. Usa mocks de requests para no
tocar APIs reales.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Cocos OMS
# =============================================================================

def test_cocos_normalize_ticker():
    from engine.brokers.cocos import _normalize_ticker
    assert _normalize_ticker("AL30D-24hs") == "AL30D"
    assert _normalize_ticker("GD30C-CI") == "GD30C"
    assert _normalize_ticker("AAPL-T1") == "AAPL"
    assert _normalize_ticker("BTC") == "BTC"
    assert _normalize_ticker("") == ""


def test_cocos_classify():
    from engine.brokers.cocos import _classify
    assert _classify("AL30D") == "BOND_AR"
    assert _classify("GD30C") == "BOND_AR"
    assert _classify("AE38D") == "BOND_AR"
    assert _classify("GGAL") == "EQUITY_AR"
    assert _classify("YPFD") == "EQUITY_AR"
    assert _classify("AAPL") == "EQUITY_US"
    assert _classify("MELI") == "EQUITY_US"
    assert _classify("ARS") == "CASH"
    assert _classify("USDT") == "CASH"
    assert _classify("UNKNOWN_XYZ") == "OTHER"


def test_cocos_fetch_positions_parses():
    """Mock del flow login + GET /rest/risk/position con un response típico."""
    from engine.brokers import cocos

    fake_session = MagicMock()
    # Login: post devuelve 200
    fake_session.post.return_value.raise_for_status.return_value = None
    # GET de posiciones: el primer endpoint devuelve OK con JSON
    json_response = MagicMock()
    json_response.status_code = 200
    json_response.headers = {"content-type": "application/json"}
    json_response.json.return_value = [
        {"symbol": "AL30D-24hs", "quantity": 1500, "averagePrice": 65.5,
         "currency": "USB", "description": "Bonar 2030 USD"},
        {"symbol": "GGAL", "quantity": 100, "averagePrice": 2500.0,
         "currency": "ARS"},
        {"symbol": "ARS", "quantity": 50000, "currency": "ARS"},
        {"symbol": "ZERO", "quantity": 0},  # se filtra
    ]
    fake_session.get.return_value = json_response
    with patch("engine.brokers.cocos._login", return_value=fake_session):
        result = cocos.fetch_positions(
            {"byma_user": "u", "byma_pass": "p"}
        )
    assert result["broker"] == "cocos"
    pos = result["positions"]
    tickers = [p["ticker"] for p in pos]
    assert "AL30D" in tickers
    assert "GGAL" in tickers
    assert "ARS" in tickers
    assert "ZERO" not in tickers
    al30 = next(p for p in pos if p["ticker"] == "AL30D")
    assert al30["asset_class"] == "BOND_AR"
    assert al30["currency"] == "USB"
    assert al30["qty"] == 1500
    assert al30["avg_price"] == 65.5
    assert al30["is_cash"] is False
    ars = next(p for p in pos if p["ticker"] == "ARS")
    assert ars["is_cash"] is True
    assert ars["asset_class"] == "CASH"


def test_cocos_missing_creds():
    from engine.brokers import cocos
    with pytest.raises(ValueError, match="credenciales BYMA"):
        cocos.fetch_positions({})


# =============================================================================
# Binance
# =============================================================================

def test_binance_signature_deterministic():
    from engine.brokers.binance import _sign
    s = _sign("secret123", "timestamp=1000&recvWindow=5000")
    # Sig hex es 64 chars
    assert len(s) == 64
    assert all(c in "0123456789abcdef" for c in s)
    # Determinístico
    assert s == _sign("secret123", "timestamp=1000&recvWindow=5000")
    # Cambia con diferente input
    assert s != _sign("secret123", "timestamp=1001&recvWindow=5000")


def test_binance_fetch_positions_parses():
    from engine.brokers import binance
    fake_response = {
        "balances": [
            {"asset": "BTC", "free": "0.5", "locked": "0"},
            {"asset": "ETH", "free": "2.0", "locked": "0.5"},
            {"asset": "USDT", "free": "1000", "locked": "0"},
            {"asset": "ARS", "free": "100000", "locked": "0"},
            {"asset": "DOGE", "free": "0", "locked": "0"},      # filtrado
            {"asset": "LDBTC", "free": "0.1", "locked": "0"},   # LD-prefix
        ]
    }
    fake_prices = [
        {"symbol": "BTCUSDT", "price": "95000.00"},
        {"symbol": "ETHUSDT", "price": "3500.00"},
        {"symbol": "OTHERUSDT", "price": "1.0"},
    ]
    with patch("engine.brokers.binance._signed_get",
                return_value=fake_response), \
         patch("engine.brokers.binance._public_get",
                return_value=fake_prices):
        r = binance.fetch_positions({
            "binance_api_key": "k", "binance_api_secret": "s"
        })
    assert r["broker"] == "binance"
    tickers = [p["ticker"] for p in r["positions"]]
    assert "BTC" in tickers
    assert "ETH" in tickers
    assert "USDT" in tickers
    assert "ARS" in tickers
    assert "DOGE" not in tickers
    assert "LDBTC" not in tickers

    btc = next(p for p in r["positions"] if p["ticker"] == "BTC")
    assert btc["qty"] == 0.5
    assert btc["asset_class"] == "CRYPTO"
    assert btc["is_cash"] is False
    assert btc["avg_price"] == 95000.0   # bajado del ticker

    eth = next(p for p in r["positions"] if p["ticker"] == "ETH")
    assert eth["qty"] == 2.5
    assert eth["avg_price"] == 3500.0

    usdt = next(p for p in r["positions"] if p["ticker"] == "USDT")
    assert usdt["asset_class"] == "STABLECOIN"
    # NUEVO behavior: stables son ASSETS, no cash
    assert usdt["is_cash"] is False
    assert usdt["avg_price"] == 1.0
    assert usdt["currency"] == "USD"

    ars = next(p for p in r["positions"] if p["ticker"] == "ARS")
    assert ars["asset_class"] == "CASH"
    assert ars["is_cash"] is True
    assert ars["avg_price"] == 1.0
    assert ars["currency"] == "ARS"


def test_binance_missing_creds():
    from engine.brokers import binance
    with pytest.raises(ValueError, match="credenciales Binance"):
        binance.fetch_positions({})


# =============================================================================
# IBKR Flex
# =============================================================================

def test_ibkr_classify():
    from engine.brokers.ibkr_flex import _classify_secid
    assert _classify_secid("ISIN", "US0378331005", "STK") == "EQUITY_US"
    assert _classify_secid("", "", "BOND") == "BOND_US"
    assert _classify_secid("", "", "ETF") == "ETF"
    assert _classify_secid("", "", "OPT") == "DERIVATIVE"
    assert _classify_secid("", "", "FUT") == "DERIVATIVE"
    assert _classify_secid("", "", "CASH") == "CASH"
    assert _classify_secid("", "", "FUND") == "FCI"
    assert _classify_secid("", "", "WEIRD") == "OTHER"


def test_ibkr_parse_xml_open_positions():
    from engine.brokers.ibkr_flex import _parse_positions_xml
    xml = """<FlexQueryResponse>
      <FlexStatements>
        <FlexStatement>
          <OpenPositions>
            <OpenPosition symbol="AAPL" position="50" costBasisPrice="150.00"
                          currency="USD" assetCategory="STK"
                          description="Apple Inc"/>
            <OpenPosition symbol="SPY" position="100" costBasisPrice="450.50"
                          currency="USD" assetCategory="ETF"/>
            <OpenPosition symbol="ZERO" position="0" currency="USD" assetCategory="STK"/>
          </OpenPositions>
          <CashReport>
            <CashReportCurrency currency="USD" endingCash="5000"/>
            <CashReportCurrency currency="EUR" endingCash="200.50"/>
            <CashReportCurrency currency="BASE_SUMMARY" endingCash="9999"/>
          </CashReport>
        </FlexStatement>
      </FlexStatements>
    </FlexQueryResponse>"""
    out = _parse_positions_xml(xml)
    tickers = [p["ticker"] for p in out]
    assert "AAPL" in tickers
    assert "SPY" in tickers
    assert "USD" in tickers
    assert "EUR" in tickers
    assert "BASE_SUMMARY" not in tickers
    assert "ZERO" not in tickers
    aapl = next(p for p in out if p["ticker"] == "AAPL")
    assert aapl["qty"] == 50
    assert aapl["avg_price"] == 150.0
    assert aapl["asset_class"] == "EQUITY_US"
    spy = next(p for p in out if p["ticker"] == "SPY")
    assert spy["asset_class"] == "ETF"
    usd_cash = next(p for p in out if p["ticker"] == "USD")
    assert usd_cash["is_cash"] is True
    assert usd_cash["qty"] == 5000


def test_ibkr_missing_creds():
    from engine.brokers import ibkr_flex
    with pytest.raises(ValueError, match="credenciales IBKR"):
        ibkr_flex.fetch_positions({})


# =============================================================================
# Registry
# =============================================================================

def test_registry_has_all_brokers():
    from engine import brokers
    for k in ("cocos", "binance", "ibkr"):
        assert brokers.get(k) is not None
    meta = brokers.list_brokers()
    assert len(meta) == 3
    for b in meta:
        assert "name" in b and "needs" in b and "icon" in b
