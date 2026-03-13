"""
=============================================================================
바이낸스 특화 필터
=============================================================================
펀딩비, 미결제약정, 롱숏비율 등 바이낸스 고유 데이터를 활용한
시그널 확인/거부 필터
=============================================================================
"""

import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)


class BinanceFilter:
    """바이낸스 특화 데이터 필터"""

    def __init__(self, exchange):
        self.exchange = exchange
        self.cfg = config.BINANCE_FILTERS

    def validate_signal(self, signal: dict) -> tuple[bool, str, float]:
        """
        시그널에 대한 바이낸스 특화 필터 검증

        Returns:
            (통과 여부, 사유, 신뢰도 가중치 0.0~1.0)
        """
        symbol = signal["symbol"]
        direction = signal["signal"]  # "LONG" or "SHORT"

        score = 0.0
        reasons = []
        max_score = 3.0  # 최대 점수

        # === 1. 펀딩비 필터 ===
        funding = self.exchange.get_funding_rate(symbol)
        funding_score, funding_reason = self._check_funding(funding, direction)
        score += funding_score
        if funding_reason:
            reasons.append(funding_reason)

        # === 2. 미결제약정 변화 ===
        # (현재는 단순 조회, 과거 대비 변화는 별도 저장 필요)
        oi = self.exchange.get_open_interest(symbol)
        if oi > 0:
            reasons.append(f"OI={oi:.2f}")

        # === 3. 롱숏 비율 ===
        ls_ratio = self.exchange.get_long_short_ratio(symbol)
        ls_score, ls_reason = self._check_long_short(ls_ratio, direction)
        score += ls_score
        if ls_reason:
            reasons.append(ls_reason)

        # 정규화 (0.0 ~ 1.0)
        confidence = max(0.0, min(1.0, (score + max_score) / (2 * max_score)))

        # 강한 반대 시그널이 있으면 거부
        if score <= -2.0:
            reason_str = ", ".join(reasons)
            logger.warning(
                f"[{symbol}] 필터 거부: {reason_str} (점수={score:.1f})"
            )
            return False, reason_str, confidence

        reason_str = ", ".join(reasons)
        logger.info(
            f"[{symbol}] 필터 통과: {reason_str} "
            f"(점수={score:.1f}, 신뢰도={confidence:.2f})"
        )
        return True, reason_str, confidence

    def _check_funding(self, funding_rate: float, direction: str) -> tuple[float, str]:
        """
        펀딩비 분석

        - 극단적 양수 (>0.05%): 롱 과열 → 숏 유리
        - 극단적 음수 (<-0.03%): 숏 과열 → 롱 유리
        """
        if funding_rate > self.cfg["funding_rate_extreme_short"]:
            # 롱 과열 상태
            if direction == "SHORT":
                return 1.0, f"펀딩비 {funding_rate:.4f}% (숏 유리)"
            else:
                return -1.0, f"펀딩비 {funding_rate:.4f}% (롱 불리)"

        elif funding_rate < self.cfg["funding_rate_extreme_long"]:
            # 숏 과열 상태
            if direction == "LONG":
                return 1.0, f"펀딩비 {funding_rate:.4f}% (롱 유리)"
            else:
                return -1.0, f"펀딩비 {funding_rate:.4f}% (숏 불리)"

        return 0.0, f"펀딩비 {funding_rate:.4f}% (중립)"

    def _check_long_short(
        self, long_pct: float, direction: str
    ) -> tuple[float, str]:
        """
        롱숏 비율 분석

        극단적 쏠림 시 반대 방향에 가중치
        """
        extreme = self.cfg["long_short_extreme"]

        if long_pct >= extreme:
            # 롱 극단 쏠림 → 숏 유리
            if direction == "SHORT":
                return 1.0, f"롱숏비 {long_pct:.1f}% (숏 유리)"
            else:
                return -0.5, f"롱숏비 {long_pct:.1f}% (롱 주의)"

        elif long_pct <= (100 - extreme):
            # 숏 극단 쏠림 → 롱 유리
            if direction == "LONG":
                return 1.0, f"롱숏비 {long_pct:.1f}% (롱 유리)"
            else:
                return -0.5, f"롱숏비 {long_pct:.1f}% (숏 주의)"

        return 0.0, f"롱숏비 {long_pct:.1f}% (중립)"
