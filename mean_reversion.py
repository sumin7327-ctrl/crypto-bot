"""
=============================================================================
전략 B: 평균회귀 (Mean Reversion)
=============================================================================
횡보장에서 과매수/과매도 되돌림을 짧게 잡는 전략
승률 60~70%, 손익비 1:1.0~1.5 목표
=============================================================================
"""

import logging
from typing import Optional

import pandas as pd

import config
from utils.indicators import (
    to_dataframe, rsi, bollinger_bands, stochastic, atr, volume_ratio,
)

logger = logging.getLogger(__name__)


class MeanReversionStrategy:
    """평균회귀 전략"""

    def __init__(self, exchange):
        self.exchange = exchange
        self.cfg = config.MEAN_REVERSION
        self.name = "mean_reversion"

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
            logger.error(f"[{symbol}] 평균회귀 분석 실패: {e}")
            return None

    def _check_signal(
        self, df: pd.DataFrame, symbol: str
    ) -> Optional[dict]:
        """롱/숏 시그널 체크"""
        cfg = self.cfg
        current_price = df["close"].iloc[-1]

        # === 지표 계산 ===
        entry_rsi = rsi(df["close"], cfg["rsi_period"])
        current_rsi = entry_rsi.iloc[-1]

        bb = bollinger_bands(df["close"], cfg["bb_period"], cfg["bb_std"])
        bb_upper = bb["upper"].iloc[-1]
        bb_lower = bb["lower"].iloc[-1]
        bb_middle = bb["middle"].iloc[-1]

        stoch = stochastic(df, cfg["stoch_k"], cfg["stoch_d"], cfg["stoch_smooth"])
        stoch_k = stoch["k"].iloc[-1]
        stoch_k_prev = stoch["k"].iloc[-2]
        stoch_d = stoch["d"].iloc[-1]
        stoch_d_prev = stoch["d"].iloc[-2]

        atr_val = atr(df, cfg["atr_period"])
        current_atr = atr_val.iloc[-1]

        # 오더북 비대칭 확인
        orderbook = self.exchange.get_orderbook(symbol)
        imbalance = orderbook["imbalance_ratio"]

        # === 롱 시그널 (과매도 반등) ===
        rsi_oversold = current_rsi < cfg["rsi_oversold"]
        price_at_lower_bb = current_price <= bb_lower
        stoch_golden = stoch_k_prev < stoch_d_prev and stoch_k > stoch_d
        bid_heavy = imbalance >= cfg["orderbook_imbalance"]

        # 조건: RSI 과매도 + 볼밴 하단 + (스토캐스틱 골든크로스 또는 오더북 매수우위)
        if rsi_oversold and price_at_lower_bb and (stoch_golden or bid_heavy):
            sl_price = bb_lower - (current_atr * cfg["sl_atr_mult"])
            tp_price = bb_middle  # 볼밴 중심선까지

            return {
                "signal": "LONG",
                "strategy": self.name,
                "symbol": symbol,
                "entry_price": current_price,
                "sl_price": sl_price,
                "tp1_price": tp_price,
                "tp1_close_pct": 1.0,  # 전량 청산
                "trailing_stop": None,
                "leverage": cfg["leverage"],
                "max_hold_hours": cfg["max_hold_hours"],
                "reason": (
                    f"LONG 평균회귀: RSI={current_rsi:.1f}, "
                    f"BB하단 터치, Stoch={'GC' if stoch_golden else '-'}, "
                    f"오더북 비대칭={imbalance:.2f}"
                ),
            }

        # === 숏 시그널 (과매수 하락) ===
        rsi_overbought = current_rsi > cfg["rsi_overbought"]
        price_at_upper_bb = current_price >= bb_upper
        stoch_dead = stoch_k_prev > stoch_d_prev and stoch_k < stoch_d
        ask_heavy = imbalance <= (1 / cfg["orderbook_imbalance"])

        if rsi_overbought and price_at_upper_bb and (stoch_dead or ask_heavy):
            sl_price = bb_upper + (current_atr * cfg["sl_atr_mult"])
            tp_price = bb_middle

            return {
                "signal": "SHORT",
                "strategy": self.name,
                "symbol": symbol,
                "entry_price": current_price,
                "sl_price": sl_price,
                "tp1_price": tp_price,
                "tp1_close_pct": 1.0,
                "trailing_stop": None,
                "leverage": cfg["leverage"],
                "max_hold_hours": cfg["max_hold_hours"],
                "reason": (
                    f"SHORT 평균회귀: RSI={current_rsi:.1f}, "
                    f"BB상단 터치, Stoch={'DC' if stoch_dead else '-'}, "
                    f"오더북 비대칭={imbalance:.2f}"
                ),
            }

        return None
