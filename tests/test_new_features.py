# -*- coding: utf-8 -*-
"""
test_new_features.py

Tests para los Sprints A-E:
  A) Trade stats (winners/losers, winrate, expectancy, profit factor)
  B) Investible flag y filtros
  C) Snapshots históricos + equity curve
  D) Buying power (BYMA aforos + IBKR margin)
  E) Funding/leverage (cauciones con linked trade)

Y un smoke test del exporter (Excel + HTML) con todas las features.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_master import build_master
from engine.importer import import_all
from engine.holdings import (
    calculate_holdings, total_pn, filter_investible, filter_non_investible,
    by_cash_purpose,
)
from engine.pnl import calculate_realized_pnl
from engine.trade_stats import (
    calculate_trade_stats, trade_stats_by_asset, trade_stats_by_account,
)
from engine.snapshots import (
    record_snapshots, get_equity_curve, get_equity_curves_by_account,
    calculate_returns, TOTAL_KEY, TOTAL_INV_KEY,
)
from engine.buying_power import (
    buying_power_byma, buying_power_margin, buying_power_summary,
    get_aforo_for, _load_aforos, DEFAULT_AFOROS_BY_CLASS,
)
from engine.exporter import export_excel, export_html


def _setup_db(tmp, fecha_corte=date(2026, 5, 2)):
    """Helper: crea master + importa, devuelve (conn, db_path)."""
    xlsx = tmp / "test.xlsx"
    db = tmp / "test.db"
    build_master(xlsx)
    import_all(db, xlsx, fecha_corte=fecha_corte)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn, db


# =============================================================================
# Sprint A: trade stats
# =============================================================================

def test_a1_trade_stats_basic():
    """TXMJ9 trade gana +8.75M ARS → 1 winner, winrate 100%, expectancy positivo."""
    print("\n[A1] trade stats sobre TXMJ9 winning trade:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        fills = calculate_realized_pnl(conn)
        assert len(fills) >= 1, f"esperaba ≥1 fill, got {len(fills)}"
        stats = calculate_trade_stats(fills)
        assert "ARS" in stats, f"esperaba ARS en stats, got {list(stats.keys())}"
        s = stats["ARS"]
        assert s.n_trades >= 1
        assert s.n_winners >= 1
        assert s.n_losers == 0
        assert s.winrate == 1.0
        assert s.gross_profit > 0
        assert s.net_pnl > 0
        assert s.expectancy > 0
        # Profit factor cuando no hay losers = inf
        assert s.profit_factor == float("inf")
        print(f"  ✓ ARS: {s.n_trades}t, {s.n_winners}w/{s.n_losers}l, "
              f"winrate {s.winrate*100:.0f}%, net {s.net_pnl:,.0f}")
        conn.close()


def test_a2_trade_stats_by_asset():
    """trade_stats_by_asset agrega por ticker."""
    print("\n[A2] trade stats por activo:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        fills = calculate_realized_pnl(conn)
        rows = trade_stats_by_asset(fills)
        assert any(r["asset"] == "TXMJ9" for r in rows), \
            f"TXMJ9 no aparece: {[r['asset'] for r in rows]}"
        txmj9 = next(r for r in rows if r["asset"] == "TXMJ9")
        assert txmj9["winrate"] == 1.0
        assert txmj9["net_pnl"] > 0
        print(f"  ✓ TXMJ9 net PnL: {txmj9['net_pnl']:,.0f}")
        conn.close()


def test_a3_trade_stats_with_loser():
    """Con un trade perdedor: profit factor finito, expectancy correcto."""
    from engine.pnl import Fill
    fills = [
        Fill(account="x", asset="A", fecha_compra="2026-01-01",
             fecha_venta="2026-01-10", qty=10, precio_compra=100,
             precio_venta=110, currency="USD", pnl_realizado=100,
             pnl_pct=0.10, holding_period_days=9),
        Fill(account="x", asset="A", fecha_compra="2026-01-15",
             fecha_venta="2026-01-20", qty=10, precio_compra=110,
             precio_venta=105, currency="USD", pnl_realizado=-50,
             pnl_pct=-0.045, holding_period_days=5),
        Fill(account="x", asset="A", fecha_compra="2026-01-21",
             fecha_venta="2026-01-30", qty=5, precio_compra=105,
             precio_venta=120, currency="USD", pnl_realizado=75,
             pnl_pct=0.142, holding_period_days=9),
    ]
    print("\n[A3] trade stats con winner+loser+winner:")
    stats = calculate_trade_stats(fills)
    s = stats["USD"]
    assert s.n_trades == 3
    assert s.n_winners == 2
    assert s.n_losers == 1
    assert abs(s.winrate - 2/3) < 1e-6
    assert abs(s.gross_profit - 175) < 1e-6
    assert abs(s.gross_loss - (-50)) < 1e-6
    assert abs(s.net_pnl - 125) < 1e-6
    assert abs(s.profit_factor - 175/50) < 1e-6
    # racha de wins máxima en estos fills ordenados ASC: el primero solo (1),
    # luego loss, luego 1 win → racha máx = 1
    assert s.largest_streak_wins == 1
    assert s.largest_streak_losses == 1
    print(f"  ✓ winrate {s.winrate*100:.1f}%, PF {s.profit_factor:.2f}, "
          f"net {s.net_pnl}")


# =============================================================================
# Sprint B: investible flag
# =============================================================================

def test_b1_investible_flag_loaded():
    """cash_reserva (Investible=NO) viene marcado como no-invertible."""
    print("\n[B1] flag investible cargado desde Excel:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        cur = conn.execute(
            "SELECT code, investible, cash_purpose FROM accounts WHERE code='cash_reserva'"
        )
        row = cur.fetchone()
        assert row is not None
        assert row["investible"] == 0, f"cash_reserva debería ser no-invertible, got investible={row['investible']}"
        assert row["cash_purpose"] == "RESERVA_NO_DECLARADO"
        print(f"  ✓ cash_reserva: investible={row['investible']}, purpose={row['cash_purpose']}")

        # external_* siempre no-invertible (override automático)
        for code in ("external_income", "external_expense", "opening_balance"):
            cur = conn.execute(
                "SELECT investible FROM accounts WHERE code=?", (code,)
            )
            row = cur.fetchone()
            assert row is not None
            assert row["investible"] == 0, f"{code} debería ser no-invertible"
        print(f"  ✓ external_* y opening_balance: forzados a investible=0")
        conn.close()


def test_b2_holdings_carry_investible():
    """holdings expone investible y cash_purpose por posición."""
    print("\n[B2] holdings expone investible:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        holdings = calculate_holdings(conn, fecha=date(2026, 5, 2),
                                       anchor_currency="ARS")
        # cash_reserva tiene 800k ARS por la transferencia
        cr = [h for h in holdings if h["account"] == "cash_reserva"]
        assert cr, "cash_reserva no aparece en holdings"
        for h in cr:
            assert h["investible"] is False, f"cash_reserva debería ser no-invertible: {h}"
            assert h["cash_purpose"] == "RESERVA_NO_DECLARADO"
        print(f"  ✓ cash_reserva ({len(cr)} pos) marcadas como no-invertibles")

        # Otras cuentas reales son invertibles
        cocos = [h for h in holdings if h["account"] == "cocos"]
        for h in cocos:
            assert h["investible"] is True
        print(f"  ✓ cocos ({len(cocos)} pos) marcadas como invertibles")
        conn.close()


def test_b3_total_pn_split():
    """total_pn separa total / invertible / no-invertible y resta pasivos."""
    print("\n[B3] total_pn separa invertible y resta pasivos:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        holdings = calculate_holdings(conn, fecha=date(2026, 5, 2),
                                       anchor_currency="ARS")
        tp = total_pn(holdings, "ARS")
        assert "total_investible" in tp
        assert "total_non_investible" in tp
        assert "total_assets" in tp
        assert "total_liabilities" in tp
        # Sanity: investible + non_invertible == total
        assert abs(tp["total_investible"] + tp["total_non_investible"]
                   - tp["total_anchor"]) < 1e-3
        # cash_reserva (800k ARS, investible=0) → contribuye 800k a non_invertible
        assert abs(tp["total_non_investible"] - 800000) < 1, \
            f"non_invertible debería ser exactamente 800k (cash_reserva), got {tp['total_non_investible']}"
        # La caución TOMA del master ejemplo (200M ARS) crea un LIABILITY
        # que se suma a total_liabilities y se resta de total_anchor
        assert tp["total_liabilities"] >= 200_000_000 - 1, \
            f"total_liabilities debería incluir la caución 200M, got {tp['total_liabilities']}"
        # PN total = activos - pasivos
        assert abs(tp["total_anchor"] - (tp["total_assets"] - tp["total_liabilities"])) < 1
        print(f"  ✓ Activos: {tp['total_assets']:,.0f}, "
              f"Pasivos: {tp['total_liabilities']:,.0f}")
        print(f"  ✓ PN Total NETO: {tp['total_anchor']:,.0f} = "
              f"Inv: {tp['total_investible']:,.0f} + "
              f"NoInv: {tp['total_non_investible']:,.0f}")

        # by_cash_purpose
        purposes = by_cash_purpose(holdings)
        assert "RESERVA_NO_DECLARADO" in purposes
        assert "OPERATIVO" in purposes
        print(f"  ✓ Cash por propósito: {list(purposes.keys())}")
        conn.close()


# =============================================================================
# Sprint C: snapshots / equity curve
# =============================================================================

def test_c1_snapshot_record_and_query():
    """record_snapshots crea entradas y get_equity_curve las recupera."""
    print("\n[C1] record y query de snapshots:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        holdings = calculate_holdings(conn, fecha=date(2026, 5, 2),
                                       anchor_currency="ARS")
        n = record_snapshots(conn, holdings, date(2026, 5, 2), anchor_currency="ARS")
        assert n >= 3, f"esperaba ≥3 snapshots, got {n}"
        print(f"  ✓ {n} snapshots escritos")

        curve = get_equity_curve(conn, anchor_currency="ARS")
        assert len(curve) == 1
        assert curve[0]["fecha"] == "2026-05-02"
        print(f"  ✓ Total curve: {len(curve)} puntos, valor={curve[0]['mv_anchor']:,.0f}")

        inv_curve = get_equity_curve(conn, anchor_currency="ARS",
                                       investible_only=True)
        assert len(inv_curve) == 1
        # invertible debería ser menor (excluye cash_reserva)
        assert inv_curve[0]["mv_anchor"] < curve[0]["mv_anchor"]
        print(f"  ✓ Invertible curve: {inv_curve[0]['mv_anchor']:,.0f} < "
              f"Total {curve[0]['mv_anchor']:,.0f}")
        conn.close()


def test_c2_snapshot_idempotent():
    """Llamar record_snapshots dos veces para misma fecha → no duplica."""
    print("\n[C2] record_snapshots idempotente:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        holdings = calculate_holdings(conn, fecha=date(2026, 5, 2),
                                       anchor_currency="ARS")
        n1 = record_snapshots(conn, holdings, date(2026, 5, 2), "ARS")
        n2 = record_snapshots(conn, holdings, date(2026, 5, 2), "ARS")
        cur = conn.execute("SELECT COUNT(*) AS n FROM pn_snapshots")
        total = cur.fetchone()["n"]
        # Mismo número en ambas corridas (sobreescribe)
        assert total == n1, f"esperaba {n1}, got {total}"
        print(f"  ✓ Re-correr no duplica ({total} snapshots)")
        conn.close()


def test_c2b_snapshot_uses_pn_net_of_liabilities():
    """record_snapshots debe usar mv_pn_anchor (signed): pasivos restan.
    Si usa mv_anchor crudo, el __TOTAL__ queda inflado por las deudas."""
    print("\n[C2b] snapshots usan PN net (pasivos restan):")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        holdings = calculate_holdings(conn, fecha=date(2026, 5, 2),
                                       anchor_currency="ARS")
        from engine.holdings import total_pn
        pn = total_pn(holdings)
        record_snapshots(conn, holdings, date(2026, 5, 2), anchor_currency="ARS")
        # __TOTAL__ snapshot debe igualar el PN total real (assets - liab),
        # no la suma cruda de mv_anchor.
        cur = conn.execute(
            "SELECT mv_anchor FROM pn_snapshots WHERE account='__TOTAL__' AND anchor_currency='ARS'"
        )
        snap_total = cur.fetchone()["mv_anchor"]
        # Tolerance de 1 ARS por rounding
        assert abs(snap_total - pn["total_anchor"]) < 1, \
            f"Snapshot {snap_total} ≠ PN real {pn['total_anchor']} (diff {snap_total - pn['total_anchor']})"
        print(f"  ✓ __TOTAL__ snapshot = PN real = {pn['total_anchor']:,.2f} ARS")
        # Si el master de test tiene pasivos, verificá que el fix los hace restar
        if pn.get("total_liabilities", 0) > 0:
            print(f"  ✓ pasivos restados correctamente ({pn['total_liabilities']:,.0f} de deuda)")
        conn.close()


def test_c3_equity_curve_multiple_dates():
    """Snapshots en 3 fechas → equity curve con 3 puntos + métricas."""
    print("\n[C3] equity curve con 3 fechas:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        holdings = calculate_holdings(conn, fecha=date(2026, 5, 2),
                                       anchor_currency="ARS")
        # Simulamos 3 snapshots con valores creciendo, decreciendo, volviendo a subir
        from engine.schema import insert_pn_snapshot
        insert_pn_snapshot(conn, "2026-04-30", TOTAL_KEY, "ARS", 1000.0)
        insert_pn_snapshot(conn, "2026-05-01", TOTAL_KEY, "ARS", 1100.0)
        insert_pn_snapshot(conn, "2026-05-02", TOTAL_KEY, "ARS", 950.0)
        insert_pn_snapshot(conn, "2026-05-03", TOTAL_KEY, "ARS", 1200.0)
        conn.commit()

        curve = get_equity_curve(conn, anchor_currency="ARS")
        assert len(curve) == 4
        rets = calculate_returns(curve)
        assert abs(rets["first_value"] - 1000.0) < 1e-6
        assert abs(rets["last_value"] - 1200.0) < 1e-6
        assert abs(rets["total_return_abs"] - 200.0) < 1e-6
        assert abs(rets["total_return_pct"] - 0.20) < 1e-6
        # Drawdown: peak 1100, trough 950 → -150 = -13.6%
        assert rets["max_drawdown_abs"] < 0
        assert abs(rets["max_drawdown_pct"] - (-150/1100)) < 1e-6
        print(f"  ✓ Retorno: {rets['total_return_pct']*100:+.2f}%, "
              f"DD: {rets['max_drawdown_pct']*100:.2f}%")
        conn.close()


# =============================================================================
# Sprint D: buying power
# =============================================================================

def test_d1_aforos_loaded():
    """Hoja aforos importa correctamente."""
    print("\n[D1] aforos cargados desde Excel:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        aforos = _load_aforos(conn)
        assert "BOND_AR" in aforos["CLASS"], f"BOND_AR no cargado: {aforos}"
        assert "AL30D" in aforos["TICKER"], f"AL30D no cargado: {aforos}"
        # AL30D override = 0.90 (más alto que default 0.85)
        assert aforos["TICKER"]["AL30D"] == 0.90
        # CLASS BOND_AR = 0.85
        assert aforos["CLASS"]["BOND_AR"] == 0.85
        print(f"  ✓ {len(aforos['CLASS'])} aforos por clase, "
              f"{len(aforos['TICKER'])} por ticker")

        # get_aforo_for: prioriza ticker
        a1 = get_aforo_for("BOND_AR", "AL30D", aforos)
        a2 = get_aforo_for("BOND_AR", "OTROBOND", aforos)
        assert a1 == 0.90, f"AL30D specific: {a1}"
        assert a2 == 0.85, f"BOND_AR fallback: {a2}"
        print(f"  ✓ AL30D aforo = {a1*100}% (override), otros bonos = {a2*100}%")
        conn.close()


def test_d2_buying_power_byma_cocos():
    """Cocos: cash + holdings → garantía calculada con aforos.

    Nota: en el master de test no hay FX USB→ARS cargado, así que AL30D
    puede no tener mv_anchor en ARS. Validamos con holdings sintéticos
    para chequear la mecánica del aforo.
    """
    print("\n[D2] buying_power BYMA cocos:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))

        # Holdings sintéticos para tener determinismo
        fake_holdings = [
            {"account": "cocos", "asset": "ARS", "is_cash": True,
             "asset_class": "CASH", "mv_anchor": 1000.0, "mv_anchor_ok": True,
             "investible": True},
            {"account": "cocos", "asset": "AL30D", "is_cash": False,
             "asset_class": "BOND_AR", "mv_anchor": 5000.0, "mv_anchor_ok": True,
             "investible": True},
            {"account": "cocos", "asset": "GGAL", "is_cash": False,
             "asset_class": "EQUITY_AR", "mv_anchor": 2000.0, "mv_anchor_ok": True,
             "investible": True},
        ]
        bp = buying_power_byma(conn, fake_holdings, account="cocos",
                                anchor_currency="ARS")
        assert bp.cash_total == 1000.0
        assert bp.holdings_mv == 7000.0  # 5000 + 2000
        # Garantía: 5000 * 0.90 (AL30D override) + 2000 * 0.70 (EQUITY_AR) + 1000 * 1.00 (cash)
        expected_garantia_holdings = 5000 * 0.90 + 2000 * 0.70
        expected_garantia_total = expected_garantia_holdings + 1000 * 1.0
        assert abs(bp.garantia_holdings - expected_garantia_holdings) < 1e-3, \
            f"got {bp.garantia_holdings}"
        assert abs(bp.garantia_total - expected_garantia_total) < 1e-3
        # BP = cash + garantia_holdings
        assert abs(bp.poder_de_compra - (1000 + expected_garantia_holdings)) < 1e-3
        print(f"  ✓ Cash: {bp.cash_total:,.0f} | Holdings MV: {bp.holdings_mv:,.0f}")
        print(f"  ✓ Garantía holdings: {bp.garantia_holdings:,.0f} "
              f"(AL30D 90% + GGAL 70%)")
        print(f"  ✓ Poder de compra: {bp.poder_de_compra:,.0f} "
              f"(leverage {bp.leverage_ratio:.2f}x)")

        # AL30D debe usar aforo 90% (override por ticker), GGAL debe usar 70% (class)
        al30d = next(d for d in bp.detalle_por_holding if d["asset"] == "AL30D")
        ggal = next(d for d in bp.detalle_por_holding if d["asset"] == "GGAL")
        assert abs(al30d["aforo_pct"] - 0.90) < 1e-6
        assert abs(ggal["aforo_pct"] - 0.70) < 1e-6
        print(f"  ✓ AL30D aforo {al30d['aforo_pct']*100}% (override) · "
              f"GGAL aforo {ggal['aforo_pct']*100}% (class)")
        conn.close()


def test_d3_buying_power_margin_ibkr():
    """IBKR cargado en margin_config: x2 overnight, x4 intraday."""
    print("\n[D3] buying_power MARGIN ibkr:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        # IBKR no tiene holdings reales en el master ejemplo, pero la config
        # se debe haber cargado.
        cur = conn.execute("SELECT * FROM margin_config WHERE account='ibkr'")
        row = cur.fetchone()
        assert row is not None, "ibkr margin config no cargado"
        assert row["multiplier_overnight"] == 2.0
        assert row["multiplier_intraday"] == 4.0
        assert abs(row["funding_rate_annual"] - 0.06) < 1e-6
        print(f"  ✓ Margin config: x{row['multiplier_overnight']} ON / x{row['multiplier_intraday']} ID, "
              f"funding {row['funding_rate_annual']*100:.2f}%")

        # Llamar buying_power_margin con holdings vacíos: equity 0, BP 0
        bp = buying_power_margin(conn, [], account="ibkr",
                                  anchor_currency="USD", mode="overnight")
        assert bp.equity == 0
        assert bp.multiplier == 2.0
        assert bp.poder_de_compra == 0

        # Simular holdings: 1000 USD cash en ibkr
        fake_holdings = [{
            "account": "ibkr", "asset": "USD", "is_cash": True,
            "mv_anchor": 1000.0, "mv_anchor_ok": True,
            "investible": True, "asset_class": "CASH",
        }]
        bp = buying_power_margin(conn, fake_holdings, account="ibkr",
                                  anchor_currency="USD", mode="overnight")
        assert bp.equity == 1000
        assert bp.poder_de_compra == 2000
        assert bp.margin_disponible == 1000
        # Funding cost diario = 1000 * 0.06 / 365
        expected_cost = 1000 * 0.06 / 365
        assert abs(bp.funding_cost_per_day - expected_cost) < 1e-6
        print(f"  ✓ ON: equity 1000 → BP 2000, cost/día {bp.funding_cost_per_day:.4f} USD")

        bp_i = buying_power_margin(conn, fake_holdings, account="ibkr",
                                    anchor_currency="USD", mode="intraday")
        assert bp_i.poder_de_compra == 4000
        print(f"  ✓ ID: BP 4000 (x4)")
        conn.close()


def test_d4_buying_power_summary():
    """buying_power_summary devuelve cuentas con BYMA + MARGIN."""
    print("\n[D4] buying_power_summary:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        holdings = calculate_holdings(conn, fecha=date(2026, 5, 2),
                                       anchor_currency="ARS")
        summary = buying_power_summary(conn, holdings, "ARS")
        assert len(summary) > 0, "summary vacío"
        types = {item["type"] for item in summary}
        # Debería haber al menos BYMA (cocos)
        assert "BYMA" in types, f"types: {types}"
        cocos = [i for i in summary if i.get("account") == "cocos"]
        assert cocos
        print(f"  ✓ summary: {len(summary)} cuentas evaluadas, types={types}")
        conn.close()


# =============================================================================
# Sprint E: funding / leverage tracking
# =============================================================================

def test_e1_funding_imported():
    """Hoja funding genera FUNDING_OPEN events con linked trade y pasivo real."""
    print("\n[E1] funding import + pasivo real:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        cur = conn.execute(
            "SELECT * FROM events WHERE event_type='FUNDING_OPEN'"
        )
        rows = list(cur.fetchall())
        assert len(rows) >= 1, f"esperaba ≥1 FUNDING_OPEN, got {len(rows)}"
        ev = rows[0]
        assert ev["external_id"] == "F0001"
        assert "T0001-A" in (ev["notes"] or "")
        print(f"  ✓ {len(rows)} FUNDING_OPEN, primero linked a T0001-A")

        # Verificar movements: cocos +200M ARS, caucion_pasivo_ars +200M ARS
        cur = conn.execute(
            """SELECT account, asset, qty FROM movements
               WHERE event_id=? ORDER BY account""",
            (ev["event_id"],),
        )
        movs = list(cur.fetchall())
        assert len(movs) == 2
        cocos_mov = [m for m in movs if m["account"] == "cocos"][0]
        assert cocos_mov["qty"] == 200000000
        # Contracuenta: caucion_pasivo_ars (NO external_expense ya)
        liab_mov = [m for m in movs if m["account"] == "caucion_pasivo_ars"][0]
        assert liab_mov["qty"] == 200000000, \
            f"caucion_pasivo_ars debería +200M (saldo deudor), got {liab_mov['qty']}"
        print(f"  ✓ cocos: +{cocos_mov['qty']:,.0f} ARS")
        print(f"  ✓ caucion_pasivo_ars: +{liab_mov['qty']:,.0f} ARS (deuda)")

        # Verificar que la cuenta caucion_pasivo_ars existe con kind=LIABILITY
        cur = conn.execute(
            "SELECT kind FROM accounts WHERE code='caucion_pasivo_ars'"
        )
        row = cur.fetchone()
        assert row is not None and row["kind"] == "LIABILITY", \
            f"caucion_pasivo_ars debería ser LIABILITY, got {row}"
        print(f"  ✓ caucion_pasivo_ars existe con kind=LIABILITY")
        conn.close()


def test_f1_caucion_neteo_pn():
    """Caución TOMA abierta: PN delta = 0 (cash compensa con deuda)."""
    print("\n[F1] caución TOMA neteo PN:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        holdings = calculate_holdings(conn, fecha=date(2026, 5, 2),
                                       anchor_currency="ARS")
        # Buscar el pasivo
        liabs = [h for h in holdings if h["account"] == "caucion_pasivo_ars"]
        assert liabs, "caucion_pasivo_ars no aparece en holdings"
        liab = liabs[0]
        assert liab["is_liability"] is True
        assert liab["mv_anchor"] == 200_000_000   # saldo deudor positivo
        assert liab["mv_pn_anchor"] == -200_000_000  # impacta PN restando
        print(f"  ✓ caucion_pasivo_ars: balance +{liab['mv_anchor']:,.0f}, "
              f"PN impact {liab['mv_pn_anchor']:,.0f}")

        # En total_pn, los 200M deudores netean los 200M cash que entraron a cocos
        # Sin la caución, cocos tendría algún saldo X. Con la caución cocos tiene
        # X+200M cash + (-200M) deuda = X PN neto. Sin inflar.
        tp = total_pn(holdings, "ARS")
        assert tp["total_liabilities"] >= 200_000_000
        print(f"  ✓ Pasivos totales: {tp['total_liabilities']:,.0f} ARS")
        print(f"  ✓ PN neto (Activos - Pasivos): {tp['total_anchor']:,.0f} ARS")


def test_f2_card_credit_resta_pn():
    """Tarjeta de crédito con saldo deudor resta del PN (no inflarlo)."""
    print("\n[F2] tarjeta de crédito resta PN:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        # En el master ejemplo hay un gasto de 35k ARS con galicia_visa_ars
        holdings = calculate_holdings(conn, fecha=date(2026, 5, 2),
                                       anchor_currency="ARS")
        cards = [h for h in holdings if h["account_kind"] == "CARD_CREDIT"
                 and h.get("mv_anchor") is not None
                 and abs(h["mv_anchor"]) > 1e-6]
        if not cards:
            print(f"  (no hay saldo de tarjeta en el ejemplo, skip)")
            return
        for c in cards:
            assert c["is_liability"] is True
            assert c["mv_pn_anchor"] == -c["mv_anchor"], \
                f"PN impact tarjeta debería ser negativo del balance"
            print(f"  ✓ {c['account']}: saldo +{c['mv_anchor']:,.0f}, "
                  f"PN impact {c['mv_pn_anchor']:,.0f}")


def test_f3_filter_helpers():
    """filter_assets y filter_liabilities particionan correctamente."""
    print("\n[F3] filter_assets / filter_liabilities:")
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup_db(Path(tmp))
        from engine.holdings import filter_assets, filter_liabilities
        holdings = calculate_holdings(conn, fecha=date(2026, 5, 2),
                                       anchor_currency="ARS")
        assets = filter_assets(holdings)
        liabs = filter_liabilities(holdings)
        assert len(assets) + len(liabs) == len(holdings)
        for h in assets:
            assert not h.get("is_liability")
        for h in liabs:
            assert h.get("is_liability")
        print(f"  ✓ {len(holdings)} holdings = {len(assets)} activos + {len(liabs)} pasivos")


# =============================================================================
# Smoke test: exporter completo
# =============================================================================

def test_exporter_excel_html_smoke():
    """Smoke: export_excel y export_html no levantan excepciones y crean archivos."""
    print("\n[SMOKE] export_excel + export_html:")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        conn, _ = _setup_db(tmp)

        xlsx_out = tmp / "report.xlsx"
        html_out = tmp / "report.html"

        export_excel(conn, xlsx_out, fecha=date(2026, 5, 2),
                     anchor_currency="ARS", record_snapshot=True)
        assert xlsx_out.is_file()
        assert xlsx_out.stat().st_size > 5000
        print(f"  ✓ Excel generado ({xlsx_out.stat().st_size:,} bytes)")

        export_html(conn, html_out, fecha=date(2026, 5, 2),
                    anchor_currency="ARS", record_snapshot=True)
        assert html_out.is_file()
        html = html_out.read_text(encoding="utf-8")
        # Validar contenido: títulos clave de Sprints
        assert "PN Invertible" in html, "Sprint B no aparece en HTML"
        assert "Equity Curve" in html, "Sprint C no aparece en HTML"
        assert "Métricas de Trading" in html, "Sprint A no aparece en HTML"
        assert "Poder de Compra" in html, "Sprint D no aparece en HTML"
        assert "RESERVA_NO_DECLARADO" in html, "cash_purpose no se muestra"
        print(f"  ✓ HTML generado ({html_out.stat().st_size:,} bytes), todos los bloques presentes")

        # Verificar que se creó al menos 1 snapshot
        cur = conn.execute("SELECT COUNT(*) AS n FROM pn_snapshots")
        n_snap = cur.fetchone()["n"]
        assert n_snap > 0
        print(f"  ✓ Snapshots persistidos: {n_snap}")

        conn.close()


def test_html_view_toggle():
    """HTML report incluye toggle JS funcional con ambas vistas embebidas."""
    print("\n[TOGGLE] HTML toggle entre 'Todo' y 'Solo invertible':")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        conn, _ = _setup_db(tmp)
        html_out = tmp / "report.html"
        export_html(conn, html_out, fecha=date(2026, 5, 2),
                    anchor_currency="ARS", default_view="all")
        html = html_out.read_text(encoding="utf-8")

        # UI elements
        assert 'class="view-toggle"' in html
        assert 'data-view="all"' in html
        assert 'data-view="investible"' in html
        # JS data
        assert 'const VIEWS = ' in html
        assert '"all"' in html and '"investible"' in html
        assert 'DEFAULT_VIEW = "all"' in html
        # Containers vacíos a llenar por JS
        assert 'id="topHoldingsBody"' in html
        assert 'id="byAccountBody"' in html
        # Función renderView debe estar
        assert 'function renderView' in html
        print(f"  ✓ Toggle UI + JS data + render function presentes")

        # Default view = investible
        export_html(conn, html_out, fecha=date(2026, 5, 2),
                    anchor_currency="ARS", default_view="investible")
        html2 = html_out.read_text(encoding="utf-8")
        assert 'DEFAULT_VIEW = "investible"' in html2
        print(f"  ✓ default_view='investible' respetado")

        # Excel con --investible-only
        from engine.exporter import export_excel
        xlsx_inv = tmp / "rep_inv.xlsx"
        export_excel(conn, xlsx_inv, fecha=date(2026, 5, 2),
                     anchor_currency="ARS", investible_only=True,
                     record_snapshot=False)
        assert xlsx_inv.is_file()
        print(f"  ✓ Excel investible_only ({xlsx_inv.stat().st_size:,} bytes)")
        conn.close()


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    tests = [
        test_a1_trade_stats_basic,
        test_a2_trade_stats_by_asset,
        test_a3_trade_stats_with_loser,
        test_b1_investible_flag_loaded,
        test_b2_holdings_carry_investible,
        test_b3_total_pn_split,
        test_c1_snapshot_record_and_query,
        test_c2_snapshot_idempotent,
        test_c2b_snapshot_uses_pn_net_of_liabilities,
        test_c3_equity_curve_multiple_dates,
        test_d1_aforos_loaded,
        test_d2_buying_power_byma_cocos,
        test_d3_buying_power_margin_ibkr,
        test_d4_buying_power_summary,
        test_e1_funding_imported,
        test_f1_caucion_neteo_pn,
        test_f2_card_credit_resta_pn,
        test_f3_filter_helpers,
        test_exporter_excel_html_smoke,
        test_html_view_toggle,
    ]
    failed = []
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"  ✗ FAIL: {e}")
            import traceback; traceback.print_exc()
            failed.append(t.__name__)
    print("\n" + "=" * 70)
    if failed:
        print(f"✗ {len(failed)}/{len(tests)} tests FALLARON: {failed}")
        sys.exit(1)
    else:
        print(f"✓ Todos los {len(tests)} tests pasaron")
    print("=" * 70)
