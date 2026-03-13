"""
=============================================================================
시장 상태 분류기 (Market Regime Filter)
=============================================================================
현재 시장이 추세장/횡보장/과변동장인지 판단하여
전략 모듈의 활성화/비활성화를 결정
=============================================================================
"""

import logging
from enum import Enum

import pandas as pd

import config
from utils.indicators import to_dataframe, adx, bollinger_bands, atr

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    TRENDING = "trending"          # 추세장 → 전략 A, C 활성화
    RANGING = "ranging"            # 횡보장 → 전략 B 활성화
    VOLATILE = "volatile"          # 과변동장 → 진입 제한
    UNKNOWN = "unknown"


class RegimeClassifier:
    """시장 상태 분류기"""

    def __init__(self, exchange):
        self.exchange = exchange
        self.cfg = config.REGIME_FILTER

    def classify(self, symbol: str) -> MarketRegime:
        """
        심볼의 현재 시장 상태를 분류

        판단 기준:
        1. ADX > 25 → 추세장
        2. ADX < 20 + 볼밴 폭 수축 → 횡보장
        3. ATR 급등 + 볼밴 폭 급격 확대 → 과변동장
        """
        try:
            klines = self.exchange.get_klines(
                symbol, self.cfg["regime_timeframe"], limit=100
            )
            if not klines:
                return MarketRegime.UNKNOWN

            df = to_dataframe(klines)

            # ADX 계산
            adx_val = adx(df, self.cfg["adx_period"])
            current_adx = adx_val.iloc[-1]

            # 볼린저밴드 폭
            bb = bollinger_bands(df["close"], period=20, std_mult=2.0)
            current_bw = bb["bandwidth"].iloc[-1]
            avg_bw = bb["bandwidth"].rolling(
                window=self.cfg["bb_bandwidth_lookback"]
            ).mean().iloc[-1]

            # ATR 변동성 체크
            atr_val = atr(df, period=14)
            current_atr = atr_val.iloc[-1]
            avg_atr = atr_val.rolling(window=20).mean().iloc[-1]

            # --- 분류 로직 ---

            # 과변동장: ATR이 평균의 2.5배 이상이고 볼밴 폭도 평균의 2배 이상
            if current_atr > avg_atr * 2.5 and current_bw > avg_bw * 2.0:
                regime = MarketRegime.VOLATILE
            # 추세장: ADX > 25
            elif current_adx > self.cfg["adx_trending"]:
                regime = MarketRegime.TRENDING
            # 횡보장: ADX < 20
            elif current_adx < self.cfg["adx_ranging"]:
                regime = MarketRegime.RANGING
            # 중간 영역 (20~25): 이전 상태 유지하되 기본은 횡보로
            else:
                regime = MarketRegime.RANGING

            logger.info(
                f"[{symbol}] 시장 상태: {regime.value} "
                f"(ADX={current_adx:.1f}, BW={current_bw:.2f}, "
                f"ATR_ratio={current_atr/avg_atr:.2f})"
            )
            return regime

        except Exception as e:
            logger.error(f"[{symbol}] 시장 상태 분류 실패: {e}")
            return MarketRegime.UNKNOWN

    def get_allowed_strategies(self, regime: MarketRegime) -> list:
        """시장 상태에 따라 허용되는 전략 목록 반환"""
        mapping = {
            MarketRegime.TRENDING: ["trend_following", "breakout"],
            MarketRegime.RANGING: ["mean_reversion"],
            MarketRegime.VOLATILE: [],         # 진입 제한
            MarketRegime.UNKNOWN: [],           # 안전 모드
        }
        return mapping.get(regime, [])
