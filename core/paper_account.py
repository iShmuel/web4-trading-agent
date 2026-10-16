"""Paper trading portfolio manager.
Tracks simulated cash, open positions, trade history, and portfolio equity.
Persists state to data/paper_portfolio.json.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_FILE = PROJECT_ROOT / "data" / "paper_portfolio.json"


class PaperAccount:
    def __init__(self, initial_capital: float = 10000.0):
        self.initial_capital = initial_capital
        self.data_dir = PROJECT_ROOT / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state = self._load_or_initialize()

    def _load_or_initialize(self) -> Dict[str, Any]:
        """Load state from disk or initialize fresh paper account."""
        if PORTFOLIO_FILE.exists():
            try:
                with open(PORTFOLIO_FILE, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[PaperAccount] Notice: Corrupt portfolio file ({e}). Initializing fresh account.")

        initial_state = {
            "initial_capital": self.initial_capital,
            "cash": self.initial_capital,
            "position": None,  # Active open position dict
            "closed_trades": [],
            "equity_history": [
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "total_equity": self.initial_capital,
                    "cash": self.initial_capital,
                    "position_value": 0.0,
                }
            ],
        }
        self._save(initial_state)
        return initial_state

    def _save(self, state: Optional[Dict[str, Any]] = None):
        """Save current state to disk."""
        if state is None:
            state = self.state
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(state, f, indent=2)

    def reset(self, capital: float = 10000.0):
        """Reset account to fresh state."""
        self.initial_capital = capital
        self.state = {
            "initial_capital": capital,
            "cash": capital,
            "position": None,
            "closed_trades": [],
            "equity_history": [
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "total_equity": capital,
                    "cash": capital,
                    "position_value": 0.0,
                }
            ],
        }
        self._save()
        print(f"[PaperAccount] Reset portfolio to ${capital:,.2f} cash.")

    @property
    def cash(self) -> float:
        return self.state["cash"]

    @property
    def position(self) -> Optional[Dict[str, Any]]:
        return self.state.get("position")

    def get_total_equity(self, current_price: float) -> float:
        """Calculate total account equity (cash + open position value)."""
        pos = self.position
        if not pos:
            return self.cash
        position_value = pos["units"] * current_price
        return self.cash + position_value

    def open_position(
        self,
        symbol: str,
        entry_price: float,
        units: float,
        stop_loss: float,
        dollar_risk: float,
        fee_pct: float = 0.0006,
    ) -> Dict[str, Any]:
        """Execute paper buy order."""
        if self.position is not None:
            raise RuntimeError("Cannot open position: an active position already exists.")

        order_cost = units * entry_price
        fee = order_cost * fee_pct
        total_cash_needed = order_cost + fee

        if total_cash_needed > self.cash:
            raise ValueError(f"Insufficient cash (${self.cash:.2f}) for order (${total_cash_needed:.2f})")

        self.state["cash"] -= total_cash_needed

        pos = {
            "symbol": symbol,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "entry_price": entry_price,
            "units": units,
            "dollar_risk": dollar_risk,
            "trailing_stop": stop_loss,
            "highest_price": entry_price,
            "fees_paid": fee,
        }
        self.state["position"] = pos
        self._save()
        return pos

    def update_trailing_stop(self, new_stop: float, current_price: float):
        """Update ratcheted trailing stop level."""
        pos = self.position
        if not pos:
            return
        if new_stop > pos["trailing_stop"]:
            pos["trailing_stop"] = new_stop
        if current_price > pos.get("highest_price", pos["entry_price"]):
            pos["highest_price"] = current_price
        self._save()

    def close_position(
        self,
        exit_price: float,
        exit_reason: str = "Trailing Stop",
        fee_pct: float = 0.0006,
    ) -> Dict[str, Any]:
        """Execute paper sell order."""
        pos = self.position
        if not pos:
            raise RuntimeError("Cannot close position: No open position.")

        units = pos["units"]
        entry_price = pos["entry_price"]
        gross_value = units * exit_price
        exit_fee = gross_value * fee_pct
        net_cash_received = gross_value - exit_fee

        self.state["cash"] += net_cash_received

        total_fees = pos["fees_paid"] + exit_fee
        gross_pnl = (exit_price - entry_price) * units
        net_pnl = gross_pnl - total_fees
        return_pct = (exit_price - entry_price) / entry_price

        trade_record = {
            "symbol": pos["symbol"],
            "entry_time": pos["entry_time"],
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "units": units,
            "dollar_risk": pos["dollar_risk"],
            "pnl": round(net_pnl, 2),
            "return_pct": round(return_pct * 100, 2),
            "exit_reason": exit_reason,
            "fees": round(total_fees, 2),
        }

        self.state["closed_trades"].append(trade_record)
        self.state["position"] = None
        self._save()
        return trade_record
