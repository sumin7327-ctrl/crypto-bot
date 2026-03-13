"""
=============================================================================
기술적 지표 계산 모듈
=============================================================================
pandas + numpy 기반, 외부 라이브러리 의존 최소화
=============================================================================
"""

import numpy as np
import pandas as pd


def to_dataframe(klines: list) -> pd.DataFrame:
    """캔들 데이터를 DataFrame으로 변환"""
    df = pd.DataFrame(klines)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df


# =============================================================================
# 이동평균
# =============================================================================
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


# =============================================================================
# RSI
# =============================================================================
def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# =============================================================================
# MACD
# =============================================================================
def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict:
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
    }


# =============================================================================
# 볼린저 밴드
# =============================================================================
def bollinger_bands(
    series: pd.Series, period: int = 20, std_mult: float = 2.0
) -> dict:
    middle = sma(series, period)
    std = series.rolling(window=period).std()
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    bandwidth = (upper - lower) / middle * 100
    return {
        "upper": upper,
        "middle": middle,
        "lower": lower,
        "bandwidth": bandwidth,
    }


# =============================================================================
# ATR (Average True Range)
# =============================================================================
def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"].shift(1)
    tr1 = high - low
    tr2 = (high - close).abs()
    tr3 = (low - close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, min_periods=period).mean()


# =============================================================================
# ADX (Average Directional Index)
# =============================================================================
def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    atr_val = atr(df, period)

    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr_val)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr_val)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).abs()
    adx_val = dx.ewm(alpha=1 / period, min_periods=period).mean()

    return adx_val


# =============================================================================
# 스토캐스틱
# =============================================================================
def stochastic(
    df: pd.DataFrame,
    k_period: int = 14,
    d_period: int = 3,
    smooth: int = 3,
) -> dict:
    low_min = df["low"].rolling(window=k_period).min()
    high_max = df["high"].rolling(window=k_period).max()

    fast_k = 100 * (df["close"] - low_min) / (high_max - low_min)
    slow_k = fast_k.rolling(window=smooth).mean()
    slow_d = slow_k.rolling(window=d_period).mean()

    return {"k": slow_k, "d": slow_d}


# =============================================================================
# 거래량 분석
# =============================================================================
def volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """현재 거래량 / 평균 거래량 비율"""
    avg_vol = df["volume"].rolling(window=period).mean()
    return df["volume"] / avg_vol
