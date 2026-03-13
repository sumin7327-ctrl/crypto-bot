"""
=============================================================================
백테스트 엔진
=============================================================================
과거 데이터로 전략 성과를 검증하는 모듈

사용법:
    python backtest.py --symbol BTCUSDT --days 90 --strategy all
    python backtest.py --symbol ETHUSDT --days 30 --strategy mean_reversion
=============================================================================
"""

import argparse
import logging
import json
import os
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import numpy as np

from binance.client import Client

import config
from utils.indicators import (
    to_dataframe, ema, rsi, macd, bollinger_bands,
    atr, adx, stochastic, volume_ratio,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("BACKTEST")


# =============================================================================
# 모의 거래소 (백테스트용)
# =============================================================================
class MockExchange:
    """백테스트용 거래소 모의 객체"""

    def __init__(self):
        self.client = Client(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)

    def fetch_historical_klines(
        self, symbol: str, interval: str, start_str: str, end_str: str = None
    ) -> pd.DataFrame:
        """과거 캔들 데이터 일괄 조회"""
        logger.info(f"[{symbol}] {interval} 데이터 다운로드 중... ({start_str} ~)")
        klines = self.client.futures_historical_klines(
            symbol=symbol,
            interval=interval,
            start_str=start_str,
            end_str=end_str,
        )
        data = []
        for k in klines:
            data.append({
                "timestamp": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        df = pd.DataFrame(data)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        logger.info(f"[{symbol}] {len(df)}개 캔들 로드 완료")
        return df


# =============================================================================
# 백테스트 트레이드 기록
# =============================================================================
class BacktestTrade:
    def __init__(
        self,
        symbol: str,
        side: str,
        strategy: str,
        entry_price: float,
        sl_price: float,
        tp1_price: Optional[float],
        tp1_close_pct: float,
        trailing_stop: Optional[float],
        entry_time: datetime,
        leverage: int,
    ):
        self.symbol = symbol
        self.side = side
        self.strategy = strategy
        self.entry_price = entry_price
        self.sl_price = sl_price
        self.tp1_price = tp1_price
        self.tp1_close_pct = tp1_close_pct
        self.trailing_stop = trailing_stop
        self.entry_time = entry_time
        self.leverage = leverage
        self.exit_price = None
        self.exit_time = None
        self.exit_reason = ""
        self.pnl_pct = 0.0
        self.tp1_hit = False
        self.trailing_high = entry_price
        self.trailing_low = entry_price


# =============================================================================
# 백테스트 엔진
# =============================================================================
class BacktestEngine:
    """전략 백테스트 실행"""

    def __init__(self, symbol: str, days: int):
        self.symbol = symbol
        self.days = days
        self.mock = MockExchange()
        self.initial_balance = 10000.0  # 초기 자본 (USDT)
        self.balance = self.initial_balance
        self.trades: list[BacktestTrade] = []
        self.equity_curve = []

    def run(self, strategies: list[str]) -> dict:
        """백테스트 실행"""
        start_date = (datetime.utcnow() - timedelta(days=self.days)).strftime("%Y-%m-%d")
        results = {}

        for strategy_name in strategies:
            logger.info(f"\n{'='*60}")
            logger.info(f"전략: {strategy_name} | 심볼: {self.symbol} | 기간: {self.days}일")
            logger.info(f"{'='*60}")

            self.balance = self.initial_balance
            self.trades = []
            self.equity_curve = []

            if strategy_name == "trend_following":
                self._backtest_trend_following(start_date)
            elif strategy_name == "mean_reversion":
                self._backtest_mean_reversion(start_date)
            elif strategy_name == "breakout":
                self._backtest_breakout(start_date)

            results[strategy_name] = self._calculate_stats()
            self._print_stats(strategy_name, results[strategy_name])

        return results

    # =========================================================================
    # 전략 A: 추세추종 백테스트
    # =========================================================================
    def _backtest_trend_following(self, start_date: str):
        cfg = config.TREND_FOLLOWING

        # 4H 데이터 (추세)
        df_4h = self.mock.fetch_historical_klines(
            self.symbol, Client.KLINE_INTERVAL_4HOUR, start_date
        )
        # 1H 데이터 (진입)
        df_1h = self.mock.fetch_historical_klines(
            self.symbol, Client.KLINE_INTERVAL_1HOUR, start_date
        )

        if df_1h.empty or df_4h.empty:
            logger.warning("데이터 부족")
            return

        # 4H 지표
        df_4h["ema_fast"] = ema(df_4h["close"], cfg["ema_fast"])
        df_4h["ema_slow"] = ema(df_4h["close"], cfg["ema_slow"])

        # 1H 지표
        df_1h["rsi"] = rsi(df_1h["close"], cfg["rsi_period"])
        macd_data = macd(df_1h["close"], cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"])
        df_1h["macd_hist"] = macd_data["histogram"]
        df_1h["atr"] = atr(df_1h, cfg["atr_period"])
        df_1h["vol_ratio"] = volume_ratio(df_1h)

        open_trade = None
        hold_bars = 0

        for i in range(60, len(df_1h)):
            row = df_1h.iloc[i]
            ts = df_1h.index[i]
            price = row["close"]

            # 4H 추세 확인 (현재 시간에 가장 가까운 4H 캔들)
            mask_4h = df_4h.index <= ts
            if mask_4h.sum() < 2:
                continue
            row_4h = df_4h[mask_4h].iloc[-1]
            trend_up = row_4h["ema_fast"] > row_4h["ema_slow"]
            trend_down = row_4h["ema_fast"] < row_4h["ema_slow"]

            # --- 포지션 관리 ---
            if open_trade:
                hold_bars += 1
                closed = self._manage_trade_bar(
                    open_trade, df_1h.iloc[i], ts, hold_bars,
                    cfg["max_hold_hours"]
                )
                if closed:
                    open_trade = None
                    hold_bars = 0
                continue

            # --- 시그널 체크 ---
            current_rsi = row["rsi"]
            hist_curr = row["macd_hist"]
            hist_prev = df_1h.iloc[i - 1]["macd_hist"]
            vol = row["vol_ratio"]
            current_atr = row["atr"]

            if pd.isna(current_rsi) or pd.isna(current_atr) or current_atr == 0:
                continue

            # 롱
            if (
                trend_up
                and cfg["rsi_entry_low"] <= current_rsi <= cfg["rsi_entry_high"]
                and hist_prev < 0 and hist_curr > 0
                and vol >= cfg["volume_mult"]
            ):
                sl = price - current_atr * cfg["sl_atr_mult"]
                tp1 = price + (price - sl) * cfg["tp1_rr"]
                open_trade = BacktestTrade(
                    self.symbol, "LONG", "trend_following", price, sl, tp1,
                    cfg["tp1_close_pct"], current_atr * cfg["trailing_atr_mult"],
                    ts, cfg["leverage"],
                )

            # 숏
            elif (
                trend_down
                and cfg["rsi_entry_low"] <= current_rsi <= cfg["rsi_entry_high"]
                and hist_prev > 0 and hist_curr < 0
                and vol >= cfg["volume_mult"]
            ):
                sl = price + current_atr * cfg["sl_atr_mult"]
                tp1 = price - (sl - price) * cfg["tp1_rr"]
                open_trade = BacktestTrade(
                    self.symbol, "SHORT", "trend_following", price, sl, tp1,
                    cfg["tp1_close_pct"], current_atr * cfg["trailing_atr_mult"],
                    ts, cfg["leverage"],
                )

    # =========================================================================
    # 전략 B: 평균회귀 백테스트
    # =========================================================================
    def _backtest_mean_reversion(self, start_date: str):
        cfg = config.MEAN_REVERSION

        df = self.mock.fetch_historical_klines(
            self.symbol, Client.KLINE_INTERVAL_15MINUTE, start_date
        )
        if df.empty:
            return

        # 지표
        df["rsi"] = rsi(df["close"], cfg["rsi_period"])
        bb = bollinger_bands(df["close"], cfg["bb_period"], cfg["bb_std"])
        df["bb_upper"] = bb["upper"]
        df["bb_lower"] = bb["lower"]
        df["bb_middle"] = bb["middle"]
        stoch = stochastic(df, cfg["stoch_k"], cfg["stoch_d"], cfg["stoch_smooth"])
        df["stoch_k"] = stoch["k"]
        df["stoch_d"] = stoch["d"]
        df["atr"] = atr(df, cfg["atr_period"])

        # ADX (시장 상태 필터)
        df["adx"] = adx(df, config.REGIME_FILTER["adx_period"])

        open_trade = None
        hold_bars = 0

        for i in range(60, len(df)):
            row = df.iloc[i]
            ts = df.index[i]
            price = row["close"]

            if open_trade:
                hold_bars += 1
                closed = self._manage_trade_bar(
                    open_trade, row, ts, hold_bars,
                    cfg["max_hold_hours"] * 4  # 15분봉이므로 ×4
                )
                if closed:
                    open_trade = None
                    hold_bars = 0
                continue

            # 횡보장에서만 실행 (ADX < 20)
            if pd.notna(row["adx"]) and row["adx"] >= config.REGIME_FILTER["adx_ranging"]:
                continue

            current_rsi = row["rsi"]
            current_atr = row["atr"]
            stoch_k_curr = row["stoch_k"]
            stoch_d_curr = row["stoch_d"]
            stoch_k_prev = df.iloc[i - 1]["stoch_k"]
            stoch_d_prev = df.iloc[i - 1]["stoch_d"]

            if pd.isna(current_rsi) or pd.isna(current_atr) or current_atr == 0:
                continue

            # 롱 (과매도)
            if (
                current_rsi < cfg["rsi_oversold"]
                and price <= row["bb_lower"]
                and stoch_k_prev < stoch_d_prev and stoch_k_curr > stoch_d_curr
            ):
                sl = row["bb_lower"] - current_atr * cfg["sl_atr_mult"]
                tp = row["bb_middle"]
                open_trade = BacktestTrade(
                    self.symbol, "LONG", "mean_reversion", price, sl, tp,
                    1.0, None, ts, cfg["leverage"],
                )

            # 숏 (과매수)
            elif (
                current_rsi > cfg["rsi_overbought"]
                and price >= row["bb_upper"]
                and stoch_k_prev > stoch_d_prev and stoch_k_curr < stoch_d_curr
            ):
                sl = row["bb_upper"] + current_atr * cfg["sl_atr_mult"]
                tp = row["bb_middle"]
                open_trade = BacktestTrade(
                    self.symbol, "SHORT", "mean_reversion", price, sl, tp,
                    1.0, None, ts, cfg["leverage"],
                )

    # =========================================================================
    # 전략 C: 브레이크아웃 백테스트
    # =========================================================================
    def _backtest_breakout(self, start_date: str):
        cfg = config.BREAKOUT

        df = self.mock.fetch_historical_klines(
            self.symbol, Client.KLINE_INTERVAL_1HOUR, start_date
        )
        if df.empty:
            return

        bb = bollinger_bands(df["close"], cfg["bb_period"], cfg["bb_std"])
        df["bb_upper"] = bb["upper"]
        df["bb_lower"] = bb["lower"]
        df["bb_bandwidth"] = bb["bandwidth"]
        df["atr"] = atr(df, cfg["atr_period"])
        df["vol_ratio"] = volume_ratio(df)

        open_trade = None
        hold_bars = 0

        for i in range(60, len(df)):
            row = df.iloc[i]
            ts = df.index[i]
            price = row["close"]

            if open_trade:
                hold_bars += 1
                closed = self._manage_trade_bar(
                    open_trade, row, ts, hold_bars,
                    cfg["max_hold_hours"]
                )
                if closed:
                    open_trade = None
                    hold_bars = 0
                continue

            current_atr = row["atr"]
            bw_window = df["bb_bandwidth"].iloc[max(0, i - cfg["bb_squeeze_lookback"]):i]
            if len(bw_window) == 0 or pd.isna(current_atr) or current_atr == 0:
                continue

            prev_bw = df.iloc[i - 1]["bb_bandwidth"]
            bw_min = bw_window.min()
            is_squeeze = prev_bw <= bw_min * 1.1

            recent_atr = df["atr"].iloc[max(0, i - cfg["atr_surge_lookback"]):i].mean()
            atr_surge = current_atr > recent_atr * 1.5 if recent_atr > 0 else False
            vol_surge = row["vol_ratio"] >= cfg["volume_mult"]

            candle_body = abs(row["close"] - row["open"])
            candle_range = row["high"] - row["low"]
            strong = candle_body > candle_range * 0.6 if candle_range > 0 else False

            # 상방 브레이크아웃
            if (
                price > row["bb_upper"]
                and is_squeeze and vol_surge and atr_surge and strong
            ):
                sl = row["low"] - current_atr * cfg["sl_atr_mult"]
                open_trade = BacktestTrade(
                    self.symbol, "LONG", "breakout", price, sl, None,
                    0.0, current_atr * cfg["trailing_atr_mult"],
                    ts, cfg["leverage"],
                )

            # 하방 브레이크아웃
            elif (
                price < row["bb_lower"]
                and is_squeeze and vol_surge and atr_surge and strong
            ):
                sl = row["high"] + current_atr * cfg["sl_atr_mult"]
                open_trade = BacktestTrade(
                    self.symbol, "SHORT", "breakout", price, sl, None,
                    0.0, current_atr * cfg["trailing_atr_mult"],
                    ts, cfg["leverage"],
                )

    # =========================================================================
    # 공통: 봉별 포지션 관리
    # =========================================================================
    def _manage_trade_bar(
        self,
        trade: BacktestTrade,
        bar,
        ts,
        hold_bars: int,
        max_bars: int,
    ) -> bool:
        """
        한 봉에서 손절/익절/트레일링/시간초과 체크.
        Returns True if trade closed.
        """
        high = bar["high"]
        low = bar["low"]
        close = bar["close"]

        # --- 손절 체크 ---
        if trade.side == "LONG" and low <= trade.sl_price:
            self._close_trade(trade, trade.sl_price, ts, "손절")
            return True
        if trade.side == "SHORT" and high >= trade.sl_price:
            self._close_trade(trade, trade.sl_price, ts, "손절")
            return True

        # --- 1차 익절 체크 ---
        if trade.tp1_price and not trade.tp1_hit:
            if trade.side == "LONG" and high >= trade.tp1_price:
                trade.tp1_hit = True
            elif trade.side == "SHORT" and low <= trade.tp1_price:
                trade.tp1_hit = True

        # --- 트레일링 스탑 (TP1 이후) ---
        if trade.trailing_stop and trade.tp1_hit:
            if trade.side == "LONG":
                trade.trailing_high = max(trade.trailing_high, high)
                trailing_sl = trade.trailing_high - trade.trailing_stop
                if low <= trailing_sl:
                    self._close_trade(trade, trailing_sl, ts, "트레일링 스탑")
                    return True
            else:
                trade.trailing_low = min(trade.trailing_low, low)
                trailing_sl = trade.trailing_low + trade.trailing_stop
                if high >= trailing_sl:
                    self._close_trade(trade, trailing_sl, ts, "트레일링 스탑")
                    return True

        # --- TP1 100% 청산 (평균회귀 등) ---
        if trade.tp1_price and trade.tp1_close_pct >= 1.0:
            if trade.side == "LONG" and high >= trade.tp1_price:
                self._close_trade(trade, trade.tp1_price, ts, "익절")
                return True
            elif trade.side == "SHORT" and low <= trade.tp1_price:
                self._close_trade(trade, trade.tp1_price, ts, "익절")
                return True

        # --- 시간 초과 ---
        if hold_bars >= max_bars:
            self._close_trade(trade, close, ts, "시간초과")
            return True

        return False

    def _close_trade(
        self, trade: BacktestTrade, exit_price: float, exit_time, reason: str
    ):
        """거래 종료 및 PnL 계산"""
        trade.exit_price = exit_price
        trade.exit_time = exit_time
        trade.exit_reason = reason

        if trade.side == "LONG":
            trade.pnl_pct = (exit_price - trade.entry_price) / trade.entry_price * 100
        else:
            trade.pnl_pct = (trade.entry_price - exit_price) / trade.entry_price * 100

        # 레버리지 적용 PnL
        leveraged_pnl_pct = trade.pnl_pct * trade.leverage

        # 리스크 기반이므로 실제 계좌 손익은 리스크% 기준으로 계산
        risk_pct = config.RISK_PER_TRADE_PCT
        if trade.pnl_pct > 0:
            # 수익: 손익비에 따라 스케일
            sl_distance = abs(trade.entry_price - trade.sl_price) / trade.entry_price * 100
            if sl_distance > 0:
                rr = trade.pnl_pct / sl_distance
                account_pnl_pct = risk_pct * rr
            else:
                account_pnl_pct = 0
        else:
            account_pnl_pct = -risk_pct

        self.balance *= (1 + account_pnl_pct / 100)
        self.equity_curve.append(self.balance)
        self.trades.append(trade)

    # =========================================================================
    # 통계 계산
    # =========================================================================
    def _calculate_stats(self) -> dict:
        if not self.trades:
            return {"total_trades": 0}

        wins = [t for t in self.trades if t.pnl_pct > 0]
        losses = [t for t in self.trades if t.pnl_pct <= 0]

        win_rate = len(wins) / len(self.trades) * 100
        avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
        avg_loss = np.mean([abs(t.pnl_pct) for t in losses]) if losses else 0
        profit_factor = (
            sum(t.pnl_pct for t in wins) / sum(abs(t.pnl_pct) for t in losses)
            if losses and sum(abs(t.pnl_pct) for t in losses) > 0
            else float("inf")
        )

        # 최대 낙폭 (MDD)
        if self.equity_curve:
            equity = np.array(self.equity_curve)
            peak = np.maximum.accumulate(equity)
            drawdown = (equity - peak) / peak * 100
            max_drawdown = abs(drawdown.min())
        else:
            max_drawdown = 0

        total_return = (self.balance - self.initial_balance) / self.initial_balance * 100

        # 연속 손실 최대
        max_consecutive_loss = 0
        current_streak = 0
        for t in self.trades:
            if t.pnl_pct <= 0:
                current_streak += 1
                max_consecutive_loss = max(max_consecutive_loss, current_streak)
            else:
                current_streak = 0

        # 청산 사유 분포
        exit_reasons = {}
        for t in self.trades:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

        return {
            "total_trades": len(self.trades),
            "win_rate": round(win_rate, 1),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "total_return_pct": round(total_return, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "final_balance": round(self.balance, 2),
            "max_consecutive_loss": max_consecutive_loss,
            "avg_rr": round(avg_win / avg_loss, 2) if avg_loss > 0 else 0,
            "exit_reasons": exit_reasons,
        }

    def _print_stats(self, strategy_name: str, stats: dict):
        """결과 출력"""
        logger.info(f"\n{'─'*50}")
        logger.info(f"📊 백테스트 결과: {strategy_name}")
        logger.info(f"{'─'*50}")
        if stats["total_trades"] == 0:
            logger.info("거래 없음")
            return
        logger.info(f"총 거래: {stats['total_trades']}회")
        logger.info(f"승률: {stats['win_rate']}%")
        logger.info(f"평균 수익: +{stats['avg_win_pct']}%")
        logger.info(f"평균 손실: -{stats['avg_loss_pct']}%")
        logger.info(f"평균 손익비: 1:{stats['avg_rr']}")
        logger.info(f"Profit Factor: {stats['profit_factor']}")
        logger.info(f"총 수익률: {stats['total_return_pct']}%")
        logger.info(f"최대 낙폭(MDD): {stats['max_drawdown_pct']}%")
        logger.info(f"최종 잔고: {stats['final_balance']} USDT")
        logger.info(f"최대 연패: {stats['max_consecutive_loss']}회")
        logger.info(f"청산 사유: {stats['exit_reasons']}")
        logger.info(f"{'─'*50}")


# =============================================================================
# CLI 실행
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="선물거래 전략 백테스트")
    parser.add_argument("--symbol", default="BTCUSDT", help="심볼 (default: BTCUSDT)")
    parser.add_argument("--days", type=int, default=90, help="기간 (일, default: 90)")
    parser.add_argument(
        "--strategy", default="all",
        help="전략 (trend_following, mean_reversion, breakout, all)"
    )
    args = parser.parse_args()

    if args.strategy == "all":
        strategies = ["trend_following", "mean_reversion", "breakout"]
    else:
        strategies = [args.strategy]

    engine = BacktestEngine(args.symbol, args.days)
    results = engine.run(strategies)

    # 결과 JSON 저장
    output_file = f"backtest_{args.symbol}_{args.days}d.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"\n결과 저장: {output_file}")


if __name__ == "__main__":
    main()
