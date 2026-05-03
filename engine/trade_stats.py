# -*- coding: utf-8 -*-
"""
engine/trade_stats.py

Métricas de performance del trading sobre los Fills de PnL realizado FIFO.

Para cada moneda (los fills no son comparables entre USD/USB/ARS):
  - n_trades (fills)
  - n_winners, n_losers, n_scratch
  - winrate
  - gross_profit, gross_loss, net_pnl
  - avg_winner, avg_loser
  - profit_factor (gross_profit / |gross_loss|)
  - expectancy (winrate * avg_winner + (1-winrate) * avg_loser)
  - best_trade, worst_trade
  - avg_holding_days
  - largest_streak_wins, largest_streak_losses

USO:
    from engine.trade_stats import calculate_trade_stats
    stats = calculate_trade_stats(fills)  # dict por moneda
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import Optional


SCRATCH_THRESHOLD = 1e-6  # PnL absoluto debajo de esto = "scratch" (ni ganador ni perdedor)


@dataclass
class TradeStats:
    """Métricas agregadas de trading para una moneda."""
    currency: str
    n_trades: int
    n_winners: int
    n_losers: int
    n_scratch: int
    winrate: float                  # n_winners / (n_winners + n_losers)
    gross_profit: float             # suma PnL de winners
    gross_loss: float               # suma PnL de losers (negativo)
    net_pnl: float                  # gross_profit + gross_loss
    avg_winner: float               # gross_profit / n_winners
    avg_loser: float                # gross_loss / n_losers (negativo)
    profit_factor: float            # gross_profit / |gross_loss|
    expectancy: float               # winrate*avg_winner + (1-winrate)*avg_loser
    best_trade: float               # mejor PnL individual
    worst_trade: float              # peor PnL individual
    avg_holding_days: float         # promedio de holding period
    largest_streak_wins: int        # racha máxima de winners consecutivos
    largest_streak_losses: int      # racha máxima de losers consecutivos

    def to_dict(self):
        return asdict(self)


def _classify(pnl: float) -> str:
    if abs(pnl) < SCRATCH_THRESHOLD:
        return "scratch"
    return "win" if pnl > 0 else "loss"


def calculate_trade_stats(fills) -> dict:
    """Calcula métricas de trading agrupadas por moneda.

    Devuelve dict {currency: TradeStats}.
    """
    by_ccy = defaultdict(list)
    for f in fills:
        by_ccy[f.currency or "?"].append(f)

    out = {}
    for ccy, group in by_ccy.items():
        # Ordenar por fecha de venta ASC para calcular rachas correctamente
        ordered = sorted(group, key=lambda f: f.fecha_venta)

        n_trades = len(ordered)
        winners = [f for f in ordered if f.pnl_realizado > SCRATCH_THRESHOLD]
        losers = [f for f in ordered if f.pnl_realizado < -SCRATCH_THRESHOLD]
        scratch = [f for f in ordered if abs(f.pnl_realizado) <= SCRATCH_THRESHOLD]

        n_winners = len(winners)
        n_losers = len(losers)
        n_scratch = len(scratch)

        decisive = n_winners + n_losers
        winrate = (n_winners / decisive) if decisive > 0 else 0.0

        gross_profit = sum(f.pnl_realizado for f in winners)
        gross_loss = sum(f.pnl_realizado for f in losers)  # negativo
        net_pnl = gross_profit + gross_loss

        avg_winner = (gross_profit / n_winners) if n_winners else 0.0
        avg_loser = (gross_loss / n_losers) if n_losers else 0.0

        # Profit factor: ratio de ganancias brutas vs pérdidas brutas
        if abs(gross_loss) > SCRATCH_THRESHOLD:
            profit_factor = gross_profit / abs(gross_loss)
        else:
            profit_factor = float("inf") if gross_profit > 0 else 0.0

        expectancy = (winrate * avg_winner) + ((1 - winrate) * avg_loser)

        best_trade = max((f.pnl_realizado for f in ordered), default=0.0)
        worst_trade = min((f.pnl_realizado for f in ordered), default=0.0)

        avg_holding_days = (
            sum(f.holding_period_days for f in ordered) / n_trades
            if n_trades else 0.0
        )

        # Rachas de wins / losses consecutivos
        max_w_streak = 0
        max_l_streak = 0
        cur_w = 0
        cur_l = 0
        for f in ordered:
            klass = _classify(f.pnl_realizado)
            if klass == "win":
                cur_w += 1
                cur_l = 0
                max_w_streak = max(max_w_streak, cur_w)
            elif klass == "loss":
                cur_l += 1
                cur_w = 0
                max_l_streak = max(max_l_streak, cur_l)
            else:
                cur_w = 0
                cur_l = 0

        out[ccy] = TradeStats(
            currency=ccy,
            n_trades=n_trades,
            n_winners=n_winners,
            n_losers=n_losers,
            n_scratch=n_scratch,
            winrate=winrate,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            net_pnl=net_pnl,
            avg_winner=avg_winner,
            avg_loser=avg_loser,
            profit_factor=profit_factor,
            expectancy=expectancy,
            best_trade=best_trade,
            worst_trade=worst_trade,
            avg_holding_days=avg_holding_days,
            largest_streak_wins=max_w_streak,
            largest_streak_losses=max_l_streak,
        )

    return out


def trade_stats_by_asset(fills) -> dict:
    """Métricas básicas (n_trades, winrate, net_pnl) por (asset, currency).

    Útil para identificar tickers más rentables / problemáticos.
    Devuelve list de dicts ordenada por net_pnl desc.
    """
    by_pair = defaultdict(list)
    for f in fills:
        by_pair[(f.asset, f.currency or "?")].append(f)

    rows = []
    for (asset, ccy), group in by_pair.items():
        winners = [f for f in group if f.pnl_realizado > SCRATCH_THRESHOLD]
        losers = [f for f in group if f.pnl_realizado < -SCRATCH_THRESHOLD]
        decisive = len(winners) + len(losers)
        winrate = (len(winners) / decisive) if decisive else 0.0
        net_pnl = sum(f.pnl_realizado for f in group)
        rows.append({
            "asset": asset,
            "currency": ccy,
            "n_trades": len(group),
            "n_winners": len(winners),
            "n_losers": len(losers),
            "winrate": winrate,
            "net_pnl": net_pnl,
            "avg_pnl": (net_pnl / len(group)) if group else 0.0,
        })
    rows.sort(key=lambda r: -r["net_pnl"])
    return rows


def trade_stats_by_account(fills) -> dict:
    """Métricas básicas por (cuenta, moneda)."""
    by_pair = defaultdict(list)
    for f in fills:
        by_pair[(f.account, f.currency or "?")].append(f)

    rows = []
    for (account, ccy), group in by_pair.items():
        winners = [f for f in group if f.pnl_realizado > SCRATCH_THRESHOLD]
        losers = [f for f in group if f.pnl_realizado < -SCRATCH_THRESHOLD]
        decisive = len(winners) + len(losers)
        winrate = (len(winners) / decisive) if decisive else 0.0
        net_pnl = sum(f.pnl_realizado for f in group)
        rows.append({
            "account": account,
            "currency": ccy,
            "n_trades": len(group),
            "n_winners": len(winners),
            "n_losers": len(losers),
            "winrate": winrate,
            "net_pnl": net_pnl,
        })
    rows.sort(key=lambda r: -r["net_pnl"])
    return rows
