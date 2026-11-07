"""Multi-asset paper trading portfolio manager.
Tracks simulated cash, multiple concurrent open positions across Crypto and Stocks,
trade history, and portfolio equity.
Persists state atomically to data/paper_portfolio.json with automatic backup.
"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_FILE = PROJECT_ROOT / "data" / "paper_portfolio.json"
BACKUP_FILE = PROJECT_ROOT / "data" / "paper_portfolio.json.bak"


class PaperAccount:
    def __init__(self, initial_capital: float = 10000.0, portfolio_file: Optional[Path] = None):
        self.initial_capital = initial_capital
        self.portfolio_file = Path(portfolio_file) if portfolio_file is not None else PORTFOLIO_FILE
        self.backup_file = self.portfolio_file.with_suffix(".json.bak")
        self.data_dir = self.portfolio_file.parent
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state = self._load_or_initialize()

    def _load_or_initialize(self) -> Dict[str, Any]:
        """Load state from disk, attempt backup recovery if corrupted, or initialize fresh."""
        candidates = [self.portfolio_file, self.backup_file]
        for file_path in candidates:
            if file_path.exists():
                try:
                    with open(file_path, "r") as f:
                        state = json.load(f)

                    # Migration: convert legacy single "position" key to "positions" dict
                    if "position" in state and "positions" not in state:
                        state["positions"] = {}
                        if state["position"]:
                            sym = state["position"]["symbol"]
                            state["positions"][sym] = state["position"]
                        del state["position"]
                        self._save(state)

                    if "positions" not in state:
                        state["positions"] = {}

                    return state
                except Exception as e:
                    print(f"[PaperAccount] Warning: Could not load state from {file_path.name} ({e}).")

        # Initialize fresh paper account
        initial_state = {
            "initial_capital": self.initial_capital,
            "cash": self.initial_capital,
            "positions": {},  # symbol -> position dict
            "closed_trades": [],
            "equity_history": [
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "total_equity": self.initial_capital,
                    "cash": self.initial_capital,
                    "positions_value": 0.0,
                }
            ],
        }
        self._save(initial_state)
        return initial_state

    def _save(self, state: Optional[Dict[str, Any]] = None):
        """Save current state to disk atomically with backup."""
        if state is None:
            state = self.state

        # Ensure directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 1. Create backup copy if main file exists and is valid
        if self.portfolio_file.exists() and self.portfolio_file.stat().st_size > 0:
            try:
                shutil.copyfile(self.portfolio_file, self.backup_file)
            except Exception:
                pass

        # 2. Write atomically to temporary file, then replace
        tmp_file = self.portfolio_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "w") as f:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, self.portfolio_file)
        except Exception as e:
            if tmp_file.exists():
                tmp_file.unlink()
            raise IOError(f"Failed to atomically persist portfolio: {e}")

    def reset(self, capital: float = 10000.0):
        """Reset account to fresh state."""
        self.initial_capital = capital
        self.state = {
            "initial_capital": capital,
            "cash": capital,
            "positions": {},
            "closed_trades": [],
            "equity_history": [
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "total_equity": capital,
                    "cash": capital,
                    "positions_value": 0.0,
                }
            ],
        }
        self._save()
        print(f"[PaperAccount] Reset portfolio to ${capital:,.2f} cash.")

    @property
    def cash(self) -> float:
        return float(self.state.get("cash", 0.0))

    @property
    def positions(self) -> Dict[str, Any]:
        return self.state.get("positions", {})

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self.positions.get(symbol)

    def get_total_equity(self, current_prices: Optional[Dict[str, float]] = None) -> float:
        """Calculate total account equity (cash + all open positions value).

        For LONG: position value = units * current_price
        For SHORT: position value = units * (2 * entry_price - current_price)
        """
        total = self.cash
        if not current_prices:
            current_prices = {}

        for sym, pos in self.positions.items():
            price = current_prices.get(sym, pos["entry_price"])
            side = pos.get("side", "LONG")
            if side == "SHORT":
                pos_val = pos["units"] * (2 * pos["entry_price"] - price)
            else:
                pos_val = pos["units"] * price
            total += pos_val
        return total

    def open_position(
        self,
        symbol: str,
        entry_price: float,
        units: float,
        stop_loss: float,
        dollar_risk: float,
        max_concurrent_positions: int = 3,
        fee_pct: float = 0.0006,
        side: str = "LONG",
        strategy_id: str = "default",
    ) -> Dict[str, Any]:
        """Execute paper buy or short sell order for a specific symbol."""
        if self.has_position(symbol):
            raise RuntimeError(f"Cannot open position: an active position for {symbol} already exists.")

        if len(self.positions) >= max_concurrent_positions:
            raise RuntimeError(f"Position limit reached: already holding {len(self.positions)} / {max_concurrent_positions} positions.")

        side = side.upper()
        if side not in ("LONG", "SHORT"):
            raise ValueError(f"Invalid position side: {side}. Must be 'LONG' or 'SHORT'.")

        order_cost = units * entry_price
        fee = order_cost * fee_pct
        total_cash_needed = order_cost + fee

        if total_cash_needed > self.cash:
            raise ValueError(f"Insufficient cash (${self.cash:.2f}) for order (${total_cash_needed:.2f})")

        self.state["cash"] -= total_cash_needed

        pos = {
            "symbol": symbol,
            "side": side,
            "strategy_id": strategy_id,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "entry_price": entry_price,
            "units": units,
            "dollar_risk": dollar_risk,
            "trailing_stop": stop_loss,
            "highest_price": entry_price,
            "lowest_price": entry_price,
            "fees_paid": fee,
        }
        self.state["positions"][symbol] = pos
        self._save()
        return pos

    def update_trailing_stop(self, symbol: str, new_stop: float, current_price: float):
        """Update ratcheted trailing stop level for a specific symbol.
        For LONG positions: stop only ratchets upward.
        For SHORT positions: stop only ratchets downward.
        """
        pos = self.get_position(symbol)
        if not pos:
            return
        side = pos.get("side", "LONG")
        if side == "SHORT":
            if new_stop < pos["trailing_stop"]:
                pos["trailing_stop"] = new_stop
            if current_price < pos.get("lowest_price", pos["entry_price"]):
                pos["lowest_price"] = current_price
        else:
            if new_stop > pos["trailing_stop"]:
                pos["trailing_stop"] = new_stop
            if current_price > pos.get("highest_price", pos["entry_price"]):
                pos["highest_price"] = current_price
        self._save()

    def set_position_fields(self, symbol: str, **fields: Any) -> None:
        """Attach strategy bookkeeping (e.g. tp_target, tp_hit) to an open position and persist it.
        Accounting fields (units, prices, cash, fees) cannot be changed through this method.
        """
        protected = {"symbol", "units", "entry_price", "fees_paid", "dollar_risk", "side", "entry_time"}
        bad = protected.intersection(fields)
        if bad:
            raise ValueError(f"Cannot modify accounting fields via set_position_fields: {sorted(bad)}")
        pos = self.get_position(symbol)
        if not pos:
            return
        pos.update(fields)
        self._save()

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str = "Trailing Stop",
        fee_pct: float = 0.0006,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Execute paper closing order for an active position."""
        pos = self.get_position(symbol)
        if not pos:
            raise RuntimeError(f"Cannot close position: No open position for {symbol}.")

        units = pos["units"]
        entry_price = pos["entry_price"]
        side = pos.get("side", "LONG")

        if side == "SHORT":
            gross_pnl = (entry_price - exit_price) * units
            exit_fee = (units * exit_price) * fee_pct
            total_fees = pos["fees_paid"] + exit_fee
            net_pnl = gross_pnl - total_fees
            return_pct = (entry_price - exit_price) / entry_price
            net_cash_received = (units * entry_price) + gross_pnl - exit_fee
        else:
            gross_value = units * exit_price
            exit_fee = gross_value * fee_pct
            total_fees = pos["fees_paid"] + exit_fee
            gross_pnl = (exit_price - entry_price) * units
            net_pnl = gross_pnl - total_fees
            return_pct = (exit_price - entry_price) / entry_price
            net_cash_received = gross_value - exit_fee

        self.state["cash"] += net_cash_received

        exit_ts = (
            current_time.isoformat()
            if current_time is not None
            else datetime.now(timezone.utc).isoformat()
        )

        trade_record = {
            "symbol": symbol,
            "side": side,
            "strategy_id": pos.get("strategy_id", "default"),
            "entry_time": pos["entry_time"],
            "exit_time": exit_ts,
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
        del self.state["positions"][symbol]
        self._save()
        return trade_record

    def partial_close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_units: float,
        exit_reason: str = "Partial Take Profit",
        fee_pct: float = 0.0006,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Execute a partial scale-out for an open position."""
        pos = self.get_position(symbol)
        if not pos:
            raise RuntimeError(f"Cannot partially close position: No open position for {symbol}.")

        orig_units = pos["units"]
        exit_units = min(exit_units, orig_units)
        entry_price = pos["entry_price"]
        side = pos.get("side", "LONG")

        fraction = exit_units / orig_units
        portion_fee = pos["fees_paid"] * fraction

        if side == "SHORT":
            gross_pnl = (entry_price - exit_price) * exit_units
            exit_fee = (exit_units * exit_price) * fee_pct
            total_fees = portion_fee + exit_fee
            net_pnl = gross_pnl - total_fees
            return_pct = (entry_price - exit_price) / entry_price
            net_cash_received = (exit_units * entry_price) + gross_pnl - exit_fee
        else:
            gross_value = exit_units * exit_price
            exit_fee = gross_value * fee_pct
            total_fees = portion_fee + exit_fee
            gross_pnl = (exit_price - entry_price) * exit_units
            net_pnl = gross_pnl - total_fees
            return_pct = (exit_price - entry_price) / entry_price
            net_cash_received = gross_value - exit_fee

        self.state["cash"] += net_cash_received
        pos["fees_paid"] -= portion_fee
        pos["units"] -= exit_units

        exit_ts = (
            current_time.isoformat()
            if current_time is not None
            else datetime.now(timezone.utc).isoformat()
        )

        trade_record = {
            "symbol": symbol,
            "side": side,
            "strategy_id": pos.get("strategy_id", "default"),
            "entry_time": pos["entry_time"],
            "exit_time": exit_ts,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "units": exit_units,
            "dollar_risk": round(pos["dollar_risk"] * fraction, 2),
            "pnl": round(net_pnl, 2),
            "return_pct": round(return_pct * 100, 2),
            "exit_reason": exit_reason,
            "fees": round(total_fees, 2),
        }

        self.state["closed_trades"].append(trade_record)
        self._save()
        return trade_record
