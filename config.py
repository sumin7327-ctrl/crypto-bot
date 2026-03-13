"""
=============================================================================
코인 선물거래 자동매매봇 - 설정 파일
=============================================================================
Binance Futures | Multi-Strategy | BTC, ETH, SOL, XRP
=============================================================================
"""

import os

# =============================================================================
# API 설정
# =============================================================================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

# =============================================================================
# 거래 대상
# =============================================================================
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

# =============================================================================
# 리스크 관리 (글로벌)
# =============================================================================
RISK_PER_TRADE_PCT = 1.5          # 거래당 최대 리스크 (자본 대비 %)
MAX_CONCURRENT_POSITIONS = 3      # 동시 최대 포지션 수
DAILY_LOSS_LIMIT_PCT = 5.0        # 일일 최대 손실 %
WEEKLY_LOSS_LIMIT_PCT = 10.0      # 주간 최대 손실 %
CONSECUTIVE_LOSS_REDUCE = 3       # N연패 시 사이즈 50% 축소
CONSECUTIVE_LOSS_STOP = 5         # N연패 시 매매 중단
MARGIN_TYPE = "ISOLATED"          # ISOLATED 권장 (CROSSED도 가능)

# =============================================================================
# 전략별 설정
# =============================================================================

# --- 전략 A: 추세추종 (Trend Following) ---
TREND_FOLLOWING = {
    "enabled": True,
    "leverage": 5,
    "timeframes": {
        "trend": "4h",      # 상위 추세 확인
        "entry": "1h",      # 진입 타이밍
    },
    "check_interval_minutes": 60,   # 1시간마다 체크
    # 지표 파라미터
    "ema_fast": 21,
    "ema_slow": 55,
    "rsi_period": 14,
    "rsi_entry_low": 40,
    "rsi_entry_high": 60,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "volume_mult": 1.2,            # 거래량 배수 기준
    # 손절/익절
    "atr_period": 14,
    "sl_atr_mult": 1.5,            # 손절 = ATR × 1.5
    "tp1_rr": 2.0,                 # 1차 익절 R:R
    "tp1_close_pct": 0.5,          # 1차 익절 시 50% 청산
    "trailing_atr_mult": 2.0,      # 트레일링 스탑 ATR 배수
    "max_hold_hours": 72,          # 최대 보유 시간
}

# --- 전략 B: 평균회귀 (Mean Reversion) ---
MEAN_REVERSION = {
    "enabled": True,
    "leverage": 3,
    "timeframes": {
        "entry": "15m",
    },
    "check_interval_minutes": 15,   # 15분마다 체크
    # 지표 파라미터
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "bb_period": 20,
    "bb_std": 2.0,
    "stoch_k": 14,
    "stoch_d": 3,
    "stoch_smooth": 3,
    "orderbook_imbalance": 1.5,    # 오더북 비대칭 기준
    # 손절/익절
    "atr_period": 14,
    "sl_atr_mult": 0.5,            # 볼밴 하단 + ATR × 0.5
    "max_hold_hours": 8,
}

# --- 전략 C: 브레이크아웃 (Breakout) ---
BREAKOUT = {
    "enabled": True,
    "leverage": 4,
    "timeframes": {
        "entry": "1h",
    },
    "check_interval_minutes": 30,   # 30분마다 체크
    # 지표 파라미터
    "bb_period": 20,
    "bb_std": 2.0,
    "bb_squeeze_lookback": 50,     # 스퀴즈 판단 기간
    "volume_mult": 2.0,            # 돌파 시 거래량 배수
    "atr_period": 14,
    "atr_surge_lookback": 5,       # ATR 급등 비교 기간
    # 손절/익절
    "sl_atr_mult": 0.3,            # 브레이크아웃 캔들 반대 + ATR × 0.3
    "trailing_atr_mult": 2.5,
    "fakeout_candles": 3,          # 페이크아웃 판단 캔들 수
    "max_hold_hours": 48,
}

# =============================================================================
# 시장 상태 분류기 (Market Regime Filter)
# =============================================================================
REGIME_FILTER = {
    "adx_period": 14,
    "adx_trending": 25,           # ADX > 25 → 추세장
    "adx_ranging": 20,            # ADX < 20 → 횡보장
    "bb_bandwidth_lookback": 50,  # 볼밴 폭 비교 기간
    "regime_timeframe": "4h",     # 상태 판단 타임프레임
}

# =============================================================================
# 바이낸스 특화 필터
# =============================================================================
BINANCE_FILTERS = {
    "funding_rate_extreme_long": -0.03,    # 펀딩비 < -0.03% → 롱 유리
    "funding_rate_extreme_short": 0.05,    # 펀딩비 > 0.05% → 숏 유리
    "oi_surge_pct": 10,                    # OI 10% 급증 감지
    "long_short_extreme": 80,              # 롱숏비 80% 이상 = 극단 쏠림
}

# =============================================================================
# 로깅 및 알림
# =============================================================================
LOG_LEVEL = "INFO"
LOG_FILE = "trading_bot.log"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
