"""
=============================================================================
메인 봇 엔진
=============================================================================
전체 흐름을 조율하는 오케스트레이터

실행 흐름:
1. 시장 상태 분류 (Regime Filter)
2. 허용된 전략에 대해 시그널 분석
3. 바이낸스 필터 검증
4. 리스크 관리 확인
5. 포지션 사이징 및 진입
6. 열린 포지션 관리 (트레일링, 시간초과 등)
=============================================================================
"""

import logging
import time
import signal
import sys
from datetime import datetime

import config
from core.exchange import BinanceExchange
from core.regime import RegimeClassifier, MarketRegime
from core.risk_manager import RiskManager
from core.binance_filter import BinanceFilter
from core.position_tracker import PositionTracker, Position
from strategies.trend_following import TrendFollowingStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.breakout import BreakoutStrategy
from utils.notifier import TelegramNotifier

# =============================================================================
# 로깅 설정
# =============================================================================
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("BOT")


class TradingBot:
    """메인 트레이딩 봇"""

    def __init__(self):
        logger.info("=" * 60)
        logger.info("코인 선물거래 자동매매봇 시작")
        logger.info("=" * 60)

        # 코어 모듈 초기화
        self.exchange = BinanceExchange()
        self.regime = RegimeClassifier(self.exchange)
        self.risk = RiskManager(self.exchange)
        self.tracker = PositionTracker(self.exchange)
        self.binance_filter = BinanceFilter(self.exchange)
        self.notifier = TelegramNotifier()

        # 전략 모듈 초기화
        self.strategies = {
            "trend_following": TrendFollowingStrategy(self.exchange),
            "mean_reversion": MeanReversionStrategy(self.exchange),
            "breakout": BreakoutStrategy(self.exchange),
        }

        # 각 전략의 마지막 체크 시각
        self._last_check = {
            "trend_following": 0,
            "mean_reversion": 0,
            "breakout": 0,
        }
        self._intervals = {
            "trend_following": config.TREND_FOLLOWING["check_interval_minutes"] * 60,
            "mean_reversion": config.MEAN_REVERSION["check_interval_minutes"] * 60,
            "breakout": config.BREAKOUT["check_interval_minutes"] * 60,
        }

        self.running = True
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        # 시작 알림
        balance = self.exchange.get_balance()
        self.notifier.send(
            f"🤖 <b>봇 시작</b>\n"
            f"잔고: {balance:.2f} USDT\n"
            f"심볼: {', '.join(config.SYMBOLS)}\n"
            f"전략: 추세추종, 평균회귀, 브레이크아웃"
        )

    def _shutdown(self, signum, frame):
        logger.info("종료 신호 수신 - 안전하게 종료합니다")
        self.running = False

    # =========================================================================
    # 메인 루프
    # =========================================================================
    def run(self):
        """메인 실행 루프"""
        logger.info("메인 루프 시작")
        position_check_interval = 60  # 포지션 관리는 1분마다

        while self.running:
            try:
                now = time.time()

                # --- 1. 열린 포지션 관리 (매 분) ---
                self._manage_positions()

                # --- 2. 전략별 시그널 체크 (각자 주기에 따라) ---
                for strategy_name, interval in self._intervals.items():
                    if now - self._last_check[strategy_name] >= interval:
                        self._run_strategy_cycle(strategy_name)
                        self._last_check[strategy_name] = now

                # --- 3. 정기 상태 리포트 (매 4시간) ---
                current_hour = datetime.utcnow().hour
                current_minute = datetime.utcnow().minute
                if current_minute < 1 and current_hour % 4 == 0:
                    stats = self.risk.get_stats()
                    self.notifier.notify_status(stats)

                # 다음 체크까지 대기
                time.sleep(position_check_interval)

            except Exception as e:
                logger.critical(f"메인 루프 에러: {e}", exc_info=True)
                self.notifier.notify_error(str(e))
                time.sleep(60)

        logger.info("봇 종료 완료")

    # =========================================================================
    # 전략 실행 사이클
    # =========================================================================
    def _run_strategy_cycle(self, strategy_name: str):
        """특정 전략의 전체 심볼 분석 사이클"""
        strategy = self.strategies[strategy_name]

        for symbol in config.SYMBOLS:
            try:
                # 이미 해당 전략으로 포지션이 열려있으면 스킵
                if self.tracker.has_position(symbol, strategy_name):
                    continue

                # 1. 시장 상태 확인
                regime = self.regime.classify(symbol)
                allowed = self.regime.get_allowed_strategies(regime)

                if strategy_name not in allowed:
                    logger.debug(
                        f"[{symbol}] {strategy_name} 비활성 "
                        f"(시장={regime.value})"
                    )
                    continue

                # 2. 시그널 분석
                signal_data = strategy.analyze(symbol)
                if not signal_data:
                    continue

                logger.info(
                    f"[{symbol}] 시그널 감지: {signal_data['reason']}"
                )

                # 3. 바이낸스 필터 검증
                passed, filter_info, confidence = (
                    self.binance_filter.validate_signal(signal_data)
                )
                if not passed:
                    logger.info(
                        f"[{symbol}] 필터 거부: {filter_info}"
                    )
                    continue

                # 4. 리스크 확인
                can_trade, risk_reason = self.risk.can_open_position()
                if not can_trade:
                    logger.info(f"리스크 거부: {risk_reason}")
                    continue

                # 5. 진입 실행
                self._execute_entry(signal_data, filter_info)

            except Exception as e:
                logger.error(
                    f"[{symbol}] {strategy_name} 사이클 에러: {e}",
                    exc_info=True,
                )

    # =========================================================================
    # 진입 실행
    # =========================================================================
    def _execute_entry(self, signal_data: dict, filter_info: str):
        """포지션 진입 실행"""
        symbol = signal_data["symbol"]
        direction = signal_data["signal"]
        entry_price = signal_data["entry_price"]
        sl_price = signal_data["sl_price"]
        leverage = signal_data["leverage"]

        # 포지션 사이징
        quantity = self.risk.calculate_position_size(
            symbol, entry_price, sl_price, leverage
        )
        if quantity <= 0:
            logger.warning(f"[{symbol}] 포지션 사이즈 0 - 진입 취소")
            return

        # 시그널 알림
        self.notifier.notify_signal(signal_data, filter_info)

        # 시장가 주문
        side = "BUY" if direction == "LONG" else "SELL"
        order = self.exchange.market_order(symbol, side, quantity)

        if not order:
            logger.error(f"[{symbol}] 주문 실패")
            return

        # 손절 주문 설정
        self.exchange.set_stop_loss(symbol, side, sl_price, quantity)

        # 1차 익절 주문 설정 (있는 경우)
        if signal_data.get("tp1_price") and signal_data["tp1_close_pct"] > 0:
            tp_qty = quantity * signal_data["tp1_close_pct"]
            self.exchange.set_take_profit(
                symbol, side, signal_data["tp1_price"], tp_qty
            )

        # 포지션 트래커에 등록
        pos = Position(
            symbol=symbol,
            side=direction,
            strategy=signal_data["strategy"],
            entry_price=entry_price,
            quantity=quantity,
            sl_price=sl_price,
            tp1_price=signal_data.get("tp1_price"),
            tp1_close_pct=signal_data.get("tp1_close_pct", 1.0),
            trailing_stop=signal_data.get("trailing_stop"),
            max_hold_hours=signal_data.get("max_hold_hours", 48),
            leverage=leverage,
        )
        self.tracker.add_position(pos)

        # 진입 알림
        self.notifier.notify_entry(signal_data, quantity)
        logger.info(
            f"[{symbol}] ✅ 진입 완료: {direction} {quantity:.6f} "
            f"@ {entry_price:.4f}"
        )

    # =========================================================================
    # 포지션 관리
    # =========================================================================
    def _manage_positions(self):
        """열린 포지션 관리 (트레일링 스탑, 시간초과 등)"""
        actions = self.tracker.manage_all()

        for action in actions:
            try:
                pos = action["position"]
                reason = action["reason"]
                qty = action["quantity"]

                # 청산 실행
                close_side = "SELL" if pos.side == "LONG" else "BUY"
                order = self.exchange.market_order(
                    pos.symbol, close_side, qty, reduce_only=True
                )

                if order:
                    # 기존 주문 취소
                    self.exchange.cancel_all_orders(pos.symbol)

                    # PnL 계산 (근사치)
                    current_price = float(order.get("avgPrice", pos.entry_price))
                    if pos.side == "LONG":
                        pnl = (current_price - pos.entry_price) * qty
                    else:
                        pnl = (pos.entry_price - current_price) * qty

                    # 리스크 매니저에 기록
                    self.risk.record_trade(pnl, pos.symbol, pos.strategy)

                    # 알림
                    self.notifier.notify_exit(
                        pos.symbol, pos.side, reason, pnl
                    )

                    # 완전 청산이면 포지션 제거
                    if action["action"] == "close":
                        self.tracker.remove_position(
                            pos.symbol, pos.strategy
                        )

                    logger.info(
                        f"[{pos.symbol}] 청산: {reason} "
                        f"(PnL: {pnl:+.2f} USDT)"
                    )

            except Exception as e:
                logger.error(f"청산 실행 실패: {e}", exc_info=True)


# =============================================================================
# 엔트리포인트
# =============================================================================
if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
