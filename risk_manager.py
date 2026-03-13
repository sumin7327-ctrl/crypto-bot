"""
=============================================================================
리스크 관리 모듈
=============================================================================
포지션 사이징, 손실 한도, 연속 손실 관리
=============================================================================
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import config

logger = logging.getLogger(__name__)


class RiskManager:
    """전역 리스크 관리"""

    def __init__(self, exchange):
        self.exchange = exchange
        self.trade_log = []          # 거래 기록
        self.daily_pnl = 0.0         # 당일 손익
        self.weekly_pnl = 0.0        # 주간 손익
        self.consecutive_losses = 0  # 연속 손실 횟수
        self.is_halted = False       # 매매 중단 여부
        self._day_start = datetime.utcnow().date()
        self._week_start = self._get_week_start()

    def _get_week_start(self):
        today = datetime.utcnow().date()
        return today - timedelta(days=today.weekday())

    def _reset_daily_if_needed(self):
        today = datetime.utcnow().date()
        if today != self._day_start:
            logger.info(f"일일 리셋 - 어제 PnL: {self.daily_pnl:.2f} USDT")
            self.daily_pnl = 0.0
            self._day_start = today

    def _reset_weekly_if_needed(self):
        week_start = self._get_week_start()
        if week_start != self._week_start:
            logger.info(f"주간 리셋 - 지난주 PnL: {self.weekly_pnl:.2f} USDT")
            self.weekly_pnl = 0.0
            self._week_start = week_start

    # =========================================================================
    # 진입 가능 여부 확인
    # =========================================================================
    def can_open_position(self) -> tuple[bool, str]:
        """새 포지션 진입 가능 여부 확인"""
        self._reset_daily_if_needed()
        self._reset_weekly_if_needed()

        # 매매 중단 상태
        if self.is_halted:
            return False, "매매 중단 상태 (연속 손실 한도 초과)"

        # 동시 포지션 수 확인
        positions = self.exchange.get_open_positions()
        if len(positions) >= config.MAX_CONCURRENT_POSITIONS:
            return False, f"최대 동시 포지션 수 초과 ({len(positions)}/{config.MAX_CONCURRENT_POSITIONS})"

        # 일일 손실 한도
        balance = self.exchange.get_balance()
        if balance > 0:
            daily_loss_pct = abs(min(self.daily_pnl, 0)) / balance * 100
            if daily_loss_pct >= config.DAILY_LOSS_LIMIT_PCT:
                return False, f"일일 손실 한도 도달 ({daily_loss_pct:.1f}%)"

            weekly_loss_pct = abs(min(self.weekly_pnl, 0)) / balance * 100
            if weekly_loss_pct >= config.WEEKLY_LOSS_LIMIT_PCT:
                return False, f"주간 손실 한도 도달 ({weekly_loss_pct:.1f}%)"

        return True, "OK"

    # =========================================================================
    # 포지션 사이징
    # =========================================================================
    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        leverage: int,
    ) -> float:
        """
        리스크 기반 포지션 사이징

        계산 로직:
        1. 자본 × 리스크% = 1회 최대 손실 금액
        2. |진입가 - 손절가| / 진입가 = 손절 거리 %
        3. 포지션 크기 = 최대 손실 금액 / 손절 거리
        4. 연속 손실 시 사이즈 축소 적용
        """
        balance = self.exchange.get_balance()
        if balance <= 0 or entry_price <= 0:
            return 0.0

        # 1. 최대 손실 금액
        risk_amount = balance * (config.RISK_PER_TRADE_PCT / 100)

        # 2. 손절 거리
        sl_distance_pct = abs(entry_price - stop_loss_price) / entry_price
        if sl_distance_pct == 0:
            return 0.0

        # 3. 포지션 크기 (USDT 기준)
        position_usdt = risk_amount / sl_distance_pct

        # 레버리지 고려한 실제 필요 마진
        margin_required = position_usdt / leverage

        # 마진이 잔고의 30%를 초과하지 않도록 제한
        max_margin = balance * 0.3
        if margin_required > max_margin:
            position_usdt = max_margin * leverage

        # 4. 연속 손실 시 사이즈 축소
        if self.consecutive_losses >= config.CONSECUTIVE_LOSS_REDUCE:
            position_usdt *= 0.5
            logger.warning(
                f"연속 {self.consecutive_losses}패 - 포지션 사이즈 50% 축소"
            )

        # 코인 수량으로 변환
        quantity = position_usdt / entry_price

        logger.info(
            f"[{symbol}] 포지션 사이징: "
            f"잔고={balance:.2f}, 리스크={risk_amount:.2f}, "
            f"포지션={position_usdt:.2f} USDT, 수량={quantity:.6f}"
        )
        return quantity

    # =========================================================================
    # 거래 기록 및 손실 추적
    # =========================================================================
    def record_trade(self, pnl: float, symbol: str, strategy: str):
        """거래 결과 기록"""
        self._reset_daily_if_needed()
        self._reset_weekly_if_needed()

        self.daily_pnl += pnl
        self.weekly_pnl += pnl

        trade = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "strategy": strategy,
            "pnl": pnl,
        }
        self.trade_log.append(trade)

        if pnl < 0:
            self.consecutive_losses += 1
            logger.info(f"손실 기록: {pnl:.2f} USDT (연속 {self.consecutive_losses}패)")

            if self.consecutive_losses >= config.CONSECUTIVE_LOSS_STOP:
                self.is_halted = True
                logger.critical(
                    f"⚠️ 매매 중단! 연속 {self.consecutive_losses}패 도달"
                )
        else:
            self.consecutive_losses = 0
            logger.info(f"수익 기록: +{pnl:.2f} USDT")

    def resume_trading(self):
        """매매 재개 (수동 호출)"""
        self.is_halted = False
        self.consecutive_losses = 0
        logger.info("매매 재개")

    def get_stats(self) -> dict:
        """현재 리스크 상태 요약"""
        balance = self.exchange.get_balance()
        positions = self.exchange.get_open_positions()
        return {
            "balance": balance,
            "open_positions": len(positions),
            "daily_pnl": self.daily_pnl,
            "weekly_pnl": self.weekly_pnl,
            "consecutive_losses": self.consecutive_losses,
            "is_halted": self.is_halted,
            "total_trades": len(self.trade_log),
        }
