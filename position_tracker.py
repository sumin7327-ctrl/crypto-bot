"""
=============================================================================
포지션 트래커
=============================================================================
열린 포지션의 트레일링 스탑, 시간 기반 청산, 부분 익절 관리
=============================================================================
"""

import logging
import json
import os
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

POSITIONS_FILE = "positions.json"


class Position:
    """개별 포지션 정보"""

    def __init__(
        self,
        symbol: str,
        side: str,
        strategy: str,
        entry_price: float,
        quantity: float,
        sl_price: float,
        tp1_price: Optional[float],
        tp1_close_pct: float,
        trailing_stop: Optional[float],
        max_hold_hours: int,
        leverage: int,
    ):
        self.symbol = symbol
        self.side = side  # "LONG" or "SHORT"
        self.strategy = strategy
        self.entry_price = entry_price
        self.quantity = quantity
        self.remaining_quantity = quantity
        self.sl_price = sl_price
        self.tp1_price = tp1_price
        self.tp1_close_pct = tp1_close_pct
        self.trailing_stop = trailing_stop
        self.trailing_high = entry_price if side == "LONG" else entry_price
        self.trailing_low = entry_price if side == "SHORT" else entry_price
        self.max_hold_hours = max_hold_hours
        self.leverage = leverage
        self.entry_time = datetime.utcnow()
        self.tp1_hit = False
        self.fakeout_candles_remaining = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "strategy": self.strategy,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "remaining_quantity": self.remaining_quantity,
            "sl_price": self.sl_price,
            "tp1_price": self.tp1_price,
            "tp1_close_pct": self.tp1_close_pct,
            "trailing_stop": self.trailing_stop,
            "trailing_high": self.trailing_high,
            "trailing_low": self.trailing_low,
            "max_hold_hours": self.max_hold_hours,
            "leverage": self.leverage,
            "entry_time": self.entry_time.isoformat(),
            "tp1_hit": self.tp1_hit,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        pos = cls(
            symbol=data["symbol"],
            side=data["side"],
            strategy=data["strategy"],
            entry_price=data["entry_price"],
            quantity=data["quantity"],
            sl_price=data["sl_price"],
            tp1_price=data.get("tp1_price"),
            tp1_close_pct=data.get("tp1_close_pct", 1.0),
            trailing_stop=data.get("trailing_stop"),
            max_hold_hours=data.get("max_hold_hours", 48),
            leverage=data.get("leverage", 5),
        )
        pos.remaining_quantity = data.get("remaining_quantity", data["quantity"])
        pos.trailing_high = data.get("trailing_high", data["entry_price"])
        pos.trailing_low = data.get("trailing_low", data["entry_price"])
        pos.entry_time = datetime.fromisoformat(data["entry_time"])
        pos.tp1_hit = data.get("tp1_hit", False)
        return pos


class PositionTracker:
    """포지션 추적 및 관리"""

    def __init__(self, exchange):
        self.exchange = exchange
        self.positions: dict[str, Position] = {}
        self._load_positions()

    def _load_positions(self):
        """파일에서 포지션 복원"""
        if os.path.exists(POSITIONS_FILE):
            try:
                with open(POSITIONS_FILE, "r") as f:
                    data = json.load(f)
                for key, pos_data in data.items():
                    self.positions[key] = Position.from_dict(pos_data)
                logger.info(f"포지션 {len(self.positions)}개 복원 완료")
            except Exception as e:
                logger.error(f"포지션 복원 실패: {e}")

    def _save_positions(self):
        """포지션을 파일에 저장"""
        try:
            data = {k: v.to_dict() for k, v in self.positions.items()}
            with open(POSITIONS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"포지션 저장 실패: {e}")

    def add_position(self, pos: Position):
        """새 포지션 등록"""
        key = f"{pos.symbol}_{pos.strategy}"
        self.positions[key] = pos
        self._save_positions()
        logger.info(f"포지션 등록: {key} ({pos.side} @ {pos.entry_price})")

    def remove_position(self, symbol: str, strategy: str):
        """포지션 제거"""
        key = f"{symbol}_{strategy}"
        if key in self.positions:
            del self.positions[key]
            self._save_positions()
            logger.info(f"포지션 제거: {key}")

    def has_position(self, symbol: str, strategy: str) -> bool:
        key = f"{symbol}_{strategy}"
        return key in self.positions

    def get_position(self, symbol: str, strategy: str) -> Optional[Position]:
        key = f"{symbol}_{strategy}"
        return self.positions.get(key)

    # =========================================================================
    # 포지션 관리 루프
    # =========================================================================
    def manage_all(self) -> list[dict]:
        """
        모든 열린 포지션을 체크하여 청산 조건 확인

        Returns:
            청산 실행해야 할 액션 리스트
        """
        actions = []

        for key, pos in list(self.positions.items()):
            try:
                klines = self.exchange.get_klines(pos.symbol, "1m", limit=5)
                if not klines:
                    continue
                current_price = klines[-1]["close"]

                # 1. 시간 기반 청산
                if self._check_time_exit(pos):
                    actions.append({
                        "action": "close",
                        "position": pos,
                        "reason": f"최대 보유시간 {pos.max_hold_hours}h 초과",
                        "quantity": pos.remaining_quantity,
                    })
                    continue

                # 2. 트레일링 스탑 업데이트 및 체크
                if pos.trailing_stop and pos.tp1_hit:
                    trailing_action = self._update_trailing_stop(
                        pos, current_price
                    )
                    if trailing_action:
                        actions.append(trailing_action)
                        continue

                # 3. 1차 익절 체크
                if not pos.tp1_hit and pos.tp1_price:
                    tp1_action = self._check_tp1(pos, current_price)
                    if tp1_action:
                        actions.append(tp1_action)

            except Exception as e:
                logger.error(f"포지션 관리 실패 [{key}]: {e}")

        return actions

    def _check_time_exit(self, pos: Position) -> bool:
        """보유시간 초과 체크"""
        elapsed = datetime.utcnow() - pos.entry_time
        return elapsed > timedelta(hours=pos.max_hold_hours)

    def _check_tp1(self, pos: Position, current_price: float) -> Optional[dict]:
        """1차 익절 체크"""
        if pos.side == "LONG" and current_price >= pos.tp1_price:
            close_qty = pos.quantity * pos.tp1_close_pct
            pos.tp1_hit = True
            pos.remaining_quantity -= close_qty
            self._save_positions()
            return {
                "action": "partial_close",
                "position": pos,
                "reason": f"1차 익절 도달 (TP1={pos.tp1_price:.2f})",
                "quantity": close_qty,
            }
        elif pos.side == "SHORT" and current_price <= pos.tp1_price:
            close_qty = pos.quantity * pos.tp1_close_pct
            pos.tp1_hit = True
            pos.remaining_quantity -= close_qty
            self._save_positions()
            return {
                "action": "partial_close",
                "position": pos,
                "reason": f"1차 익절 도달 (TP1={pos.tp1_price:.2f})",
                "quantity": close_qty,
            }
        return None

    def _update_trailing_stop(
        self, pos: Position, current_price: float
    ) -> Optional[dict]:
        """트레일링 스탑 업데이트"""
        if pos.side == "LONG":
            if current_price > pos.trailing_high:
                pos.trailing_high = current_price
                self._save_positions()

            trailing_sl = pos.trailing_high - pos.trailing_stop
            if current_price <= trailing_sl:
                return {
                    "action": "close",
                    "position": pos,
                    "reason": (
                        f"트레일링 스탑 ({pos.trailing_high:.2f} → "
                        f"{trailing_sl:.2f})"
                    ),
                    "quantity": pos.remaining_quantity,
                }

        elif pos.side == "SHORT":
            if current_price < pos.trailing_low:
                pos.trailing_low = current_price
                self._save_positions()

            trailing_sl = pos.trailing_low + pos.trailing_stop
            if current_price >= trailing_sl:
                return {
                    "action": "close",
                    "position": pos,
                    "reason": (
                        f"트레일링 스탑 ({pos.trailing_low:.2f} → "
                        f"{trailing_sl:.2f})"
                    ),
                    "quantity": pos.remaining_quantity,
                }

        return None
