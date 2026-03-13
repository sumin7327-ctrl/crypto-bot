"""
=============================================================================
전략 A: 추세추종 (Trend Following)
=============================================================================
추세장에서 되돌림 후 재진입을 노리는 전략
승률 40~45%, 손익비 1:2.5~3.0 목표
=============================================================================
"""

import logging
from typing import Optional

import pandas as pd

import config
from utils.indicators import (
    to_dataframe, ema, rsi, macd, atr, volume_ratio,
)

logger = logging.getLogger(__name__)


class TrendFollowingStrategy:
    """추세추종 전략"""

    def __init__(self, exchange):
        self.exchange = exchange
        self.cfg = config.TREND_FOLLOWING
        self.name = "trend_following"

    def analyze(self, symbol: str) -> Optional[dict]:
        """
        시그널 분석

        Returns:
            dict with keys: signal, entry_price, sl_price, tp1_price,
                           quantity_pct, leverage, reason
            or None if no signal
        """
        if not self.cfg["enabled"]:
            return None

        try:
            # 상위 타임프레임 데이터 (추세 확인)
            klines_trend = self.exchange.get_klines(
                symbol, self.cfg["timeframes"]["trend"], limit=100
            )
            # 진입 타임프레임 데이터
            klines_entry = self.exchange.get_klines(
                symbol, self.cfg["timeframes"]["entry"], limit=100
            )

            if not klines_trend or not klines_entry:
                return None

            df_trend = to_dataframe(klines_trend)
            df_entry = to_dataframe(klines_entry)

            signal = self._check_signal(df_trend, df_entry, symbol)
            return signal

        except Exception as e:
            logger.error(f"[{symbol}] 추세추종 분석 실패: {e}")
            return None

    def _check_signal(
        self, df_trend: pd.DataFrame, df_entry: pd.DataFrame, symbol: str
    ) -> Optional[dict]:
        """롱/숏 시그널 체크"""
        cfg = self.cfg

        # === 상위 추세 확인 (4H) ===
        ema_fast = ema(df_trend["close"], cfg["ema_fast"])
        ema_slow = ema(df_trend["close"], cfg["ema_slow"])

        trend_up = ema_fast.iloc[-1] > ema_slow.iloc[-1]
        trend_down = ema_fast.iloc[-1] < ema_slow.iloc[-1]

        # === 진입 타이밍 확인 (1H) ===
        entry_rsi = rsi(df_entry["close"], cfg["rsi_period"])
        current_rsi = entry_rsi.iloc[-1]

        entry_macd = macd(
            df_entry["close"],
            cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"],
        )
        hist_current = entry_macd["histogram"].iloc[-1]
        hist_prev = entry_macd["histogram"].iloc[-2]

        vol_ratio = volume_ratio(df_entry)
        current_vol = vol_ratio.iloc[-1]

        atr_val = atr(df_entry, cfg["atr_period"])
        current_atr = atr_val.iloc[-1]
        current_price = df_entry["close"].iloc[-1]

        # === 롱 시그널 ===
        if trend_up:
            rsi_pullback = cfg["rsi_entry_low"] <= current_rsi <= cfg["rsi_entry_high"]
            macd_turn = hist_prev < 0 and hist_current > 0
            vol_ok = current_vol >= cfg["volume_mult"]

            if rsi_pullback and macd_turn and vol_ok:
                sl_price = current_price - (current_atr * cfg["sl_atr_mult"])
                tp1_price = current_price + (
                    (current_price - sl_price) * cfg["tp1_rr"]
                )

                return {
                    "signal": "LONG",
                    "strategy": self.name,
                    "symbol": symbol,
                    "entry_price": current_price,
                    "sl_price": sl_price,
                    "tp1_price": tp1_price,
                    "tp1_close_pct": cfg["tp1_close_pct"],
                    "trailing_stop": current_atr * cfg["trailing_atr_mult"],
                    "leverage": cfg["leverage"],
                    "max_hold_hours": cfg["max_hold_hours"],
                    "reason": (
                        f"LONG: EMA{cfg['ema_fast']}>{cfg['ema_slow']}, "
                        f"RSI={current_rsi:.1f} 되돌림, "
                        f"MACD 히스토그램 전환, Vol={current_vol:.1f}x"
                    ),
                }

        # === 숏 시그널 ===
        if trend_down:
            rsi_pullback = cfg["rsi_entry_low"] <= current_rsi <= cfg["rsi_entry_high"]
            macd_turn = hist_prev > 0 and hist_current < 0
            vol_ok = current_vol >= cfg["volume_mult"]

            if rsi_pullback and macd_turn and vol_ok:
                sl_price = current_price + (current_atr * cfg["sl_atr_mult"])
                tp1_price = current_price - (
                    (sl_price - current_price) * cfg["tp1_rr"]
                )

                return {
                    "signal": "SHORT",
                    "strategy": self.name,
                    "symbol": symbol,
                    "entry_price": current_price,
                    "sl_price": sl_price,
                    "tp1_price": tp1_price,
                    "tp1_close_pct": cfg["tp1_close_pct"],
                    "trailing_stop": current_atr * cfg["trailing_atr_mult"],
                    "leverage": cfg["leverage"],
                    "max_hold_hours": cfg["max_hold_hours"],
                    "reason": (
                        f"SHORT: EMA{cfg['ema_fast']}<{cfg['ema_slow']}, "
                        f"RSI={current_rsi:.1f} 되돌림, "
                        f"MACD 히스토그램 전환, Vol={current_vol:.1f}x"
                    ),
                }

        return None
