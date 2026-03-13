"""
=============================================================================
전략 C: 브레이크아웃 (Breakout)
=============================================================================
변동성 수축(스퀴즈) 후 확장을 노리는 전략
승률 35~40%, 손익비 1:3.0~5.0 목표
=============================================================================
"""

import logging
from typing import Optional

import pandas as pd

import config
from utils.indicators import (
    to_dataframe, bollinger_bands, atr, volume_ratio,
)

logger = logging.getLogger(__name__)


class BreakoutStrategy:
    """브레이크아웃 전략"""

    def __init__(self, exchange):
        self.exchange = exchange
        self.cfg = config.BREAKOUT
        self.name = "breakout"

    def analyze(self, symbol: str) -> Optional[dict]:
        """시그널 분석"""
        if not self.cfg["enabled"]:
            return None

        try:
            klines = self.exchange.get_klines(
                symbol, self.cfg["timeframes"]["entry"], limit=100
            )
            if not klines:
                return None

            df = to_dataframe(klines)
            signal = self._check_signal(df, symbol)
            return signal

        except Exception as e:
            logger.error(f"[{symbol}] 브레이크아웃 분석 실패: {e}")
            return None

    def _check_signal(
        self, df: pd.DataFrame, symbol: str
    ) -> Optional[dict]:
        """브레이크아웃 시그널 체크"""
        cfg = self.cfg
        current_price = df["close"].iloc[-1]

        # === 지표 계산 ===
        bb = bollinger_bands(df["close"], cfg["bb_period"], cfg["bb_std"])
        bb_upper = bb["upper"].iloc[-1]
        bb_lower = bb["lower"].iloc[-1]
        current_bw = bb["bandwidth"].iloc[-1]

        # 볼밴 폭이 최근 N기간 중 최저인지 (스퀴즈)
        bw_min = bb["bandwidth"].iloc[-cfg["bb_squeeze_lookback"]:].min()
        # 직전 캔들까지의 밴드폭이 최저 근처였는지 확인
        prev_bw = bb["bandwidth"].iloc[-2]
        is_squeeze_release = prev_bw <= bw_min * 1.1 and current_bw > prev_bw * 1.3

        atr_val = atr(df, cfg["atr_period"])
        current_atr = atr_val.iloc[-1]
        recent_atr_avg = atr_val.iloc[-cfg["atr_surge_lookback"]-1:-1].mean()
        atr_surging = current_atr > recent_atr_avg * 1.5

        vol = volume_ratio(df)
        current_vol = vol.iloc[-1]
        volume_surge = current_vol >= cfg["volume_mult"]

        # 캔들 분석
        candle_body = abs(df["close"].iloc[-1] - df["open"].iloc[-1])
        candle_range = df["high"].iloc[-1] - df["low"].iloc[-1]
        strong_candle = candle_body > candle_range * 0.6 if candle_range > 0 else False

        # === 상방 브레이크아웃 ===
        if (
            current_price > bb_upper
            and is_squeeze_release
            and volume_surge
            and atr_surging
            and strong_candle
        ):
            sl_price = df["low"].iloc[-1] - (current_atr * cfg["sl_atr_mult"])

            return {
                "signal": "LONG",
                "strategy": self.name,
                "symbol": symbol,
                "entry_price": current_price,
                "sl_price": sl_price,
                "tp1_price": None,  # 목표가 없이 트레일링만
                "tp1_close_pct": 0.0,
                "trailing_stop": current_atr * cfg["trailing_atr_mult"],
                "leverage": cfg["leverage"],
                "max_hold_hours": cfg["max_hold_hours"],
                "fakeout_candles": cfg["fakeout_candles"],
                "reason": (
                    f"LONG 브레이크아웃: BB상단 돌파, "
                    f"스퀴즈 해제, Vol={current_vol:.1f}x, "
                    f"ATR 급등={current_atr/recent_atr_avg:.1f}x"
                ),
            }

        # === 하방 브레이크아웃 ===
        if (
            current_price < bb_lower
            and is_squeeze_release
            and volume_surge
            and atr_surging
            and strong_candle
        ):
            sl_price = df["high"].iloc[-1] + (current_atr * cfg["sl_atr_mult"])

            return {
                "signal": "SHORT",
                "strategy": self.name,
                "symbol": symbol,
                "entry_price": current_price,
                "sl_price": sl_price,
                "tp1_price": None,
                "tp1_close_pct": 0.0,
                "trailing_stop": current_atr * cfg["trailing_atr_mult"],
                "leverage": cfg["leverage"],
                "max_hold_hours": cfg["max_hold_hours"],
                "fakeout_candles": cfg["fakeout_candles"],
                "reason": (
                    f"SHORT 브레이크아웃: BB하단 돌파, "
                    f"스퀴즈 해제, Vol={current_vol:.1f}x, "
                    f"ATR 급등={current_atr/recent_atr_avg:.1f}x"
                ),
            }

        return None
