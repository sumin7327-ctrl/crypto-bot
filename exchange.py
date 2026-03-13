"""
=============================================================================
Binance Futures 거래소 인터페이스
=============================================================================
"""

import logging
import time
from typing import Optional

from binance.client import Client
from binance.enums import *

import config

logger = logging.getLogger(__name__)


class BinanceExchange:
    """바이낸스 선물 API 래퍼"""

    def __init__(self):
        self.client = Client(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)
        self._setup_symbols()

    def _setup_symbols(self):
        """각 심볼별 마진 타입 및 레버리지 초기 설정"""
        strategies = {
            "trend": config.TREND_FOLLOWING,
            "mean_reversion": config.MEAN_REVERSION,
            "breakout": config.BREAKOUT,
        }
        max_leverage = max(s["leverage"] for s in strategies.values() if s["enabled"])

        for symbol in config.SYMBOLS:
            try:
                # 마진 타입 설정
                try:
                    self.client.futures_change_margin_type(
                        symbol=symbol, marginType=config.MARGIN_TYPE
                    )
                except Exception as e:
                    if "No need to change margin type" not in str(e):
                        logger.warning(f"[{symbol}] 마진 타입 설정 실패: {e}")

                # 레버리지 설정 (전략 중 최대값으로 설정)
                self.client.futures_change_leverage(
                    symbol=symbol, leverage=max_leverage
                )
                logger.info(f"[{symbol}] 설정 완료 - {config.MARGIN_TYPE}, 레버리지 {max_leverage}x")
            except Exception as e:
                logger.error(f"[{symbol}] 초기 설정 실패: {e}")

    # =========================================================================
    # 시세 데이터
    # =========================================================================
    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list:
        """캔들스틱 데이터 조회"""
        try:
            klines = self.client.futures_klines(
                symbol=symbol, interval=interval, limit=limit
            )
            return [
                {
                    "timestamp": k[0],
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "close_time": k[6],
                }
                for k in klines
            ]
        except Exception as e:
            logger.error(f"[{symbol}] 캔들 데이터 조회 실패: {e}")
            return []

    def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        """오더북 조회"""
        try:
            ob = self.client.futures_order_book(symbol=symbol, limit=limit)
            bid_volume = sum(float(b[1]) for b in ob["bids"])
            ask_volume = sum(float(a[1]) for a in ob["asks"])
            return {
                "bid_volume": bid_volume,
                "ask_volume": ask_volume,
                "imbalance_ratio": bid_volume / ask_volume if ask_volume > 0 else 1.0,
            }
        except Exception as e:
            logger.error(f"[{symbol}] 오더북 조회 실패: {e}")
            return {"bid_volume": 0, "ask_volume": 0, "imbalance_ratio": 1.0}

    def get_funding_rate(self, symbol: str) -> float:
        """현재 펀딩비 조회"""
        try:
            info = self.client.futures_funding_rate(symbol=symbol, limit=1)
            return float(info[-1]["fundingRate"]) * 100 if info else 0.0
        except Exception as e:
            logger.error(f"[{symbol}] 펀딩비 조회 실패: {e}")
            return 0.0

    def get_open_interest(self, symbol: str) -> float:
        """미결제약정 조회"""
        try:
            oi = self.client.futures_open_interest(symbol=symbol)
            return float(oi["openInterest"])
        except Exception as e:
            logger.error(f"[{symbol}] OI 조회 실패: {e}")
            return 0.0

    def get_long_short_ratio(self, symbol: str) -> float:
        """롱숏 비율 조회 (롱 %)"""
        try:
            ratio = self.client.futures_top_longshort_account_ratio(
                symbol=symbol, period="1h", limit=1
            )
            if ratio:
                long_pct = float(ratio[-1]["longAccount"]) * 100
                return long_pct
            return 50.0
        except Exception as e:
            logger.error(f"[{symbol}] 롱숏비 조회 실패: {e}")
            return 50.0

    # =========================================================================
    # 계좌 정보
    # =========================================================================
    def get_balance(self) -> float:
        """USDT 잔고 조회"""
        try:
            account = self.client.futures_account()
            for asset in account["assets"]:
                if asset["asset"] == "USDT":
                    return float(asset["walletBalance"])
            return 0.0
        except Exception as e:
            logger.error(f"잔고 조회 실패: {e}")
            return 0.0

    def get_open_positions(self) -> list:
        """현재 열려있는 포지션 목록"""
        try:
            account = self.client.futures_account()
            positions = []
            for pos in account["positions"]:
                amt = float(pos["positionAmt"])
                if amt != 0:
                    positions.append({
                        "symbol": pos["symbol"],
                        "side": "LONG" if amt > 0 else "SHORT",
                        "amount": abs(amt),
                        "entry_price": float(pos["entryPrice"]),
                        "unrealized_pnl": float(pos["unrealizedProfit"]),
                        "leverage": int(pos["leverage"]),
                        "margin_type": pos["marginType"],
                    })
            return positions
        except Exception as e:
            logger.error(f"포지션 조회 실패: {e}")
            return []

    # =========================================================================
    # 주문 실행
    # =========================================================================
    def market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        reduce_only: bool = False,
    ) -> Optional[dict]:
        """시장가 주문"""
        try:
            params = {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": self._format_quantity(symbol, quantity),
            }
            if reduce_only:
                params["reduceOnly"] = "true"

            order = self.client.futures_create_order(**params)
            logger.info(
                f"[{symbol}] 시장가 주문 체결 - {side} {quantity} @ market"
            )
            return order
        except Exception as e:
            logger.error(f"[{symbol}] 시장가 주문 실패: {e}")
            return None

    def set_stop_loss(
        self, symbol: str, side: str, stop_price: float, quantity: float
    ) -> Optional[dict]:
        """손절 주문 (스탑마켓)"""
        try:
            close_side = "SELL" if side == "BUY" else "BUY"
            order = self.client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type="STOP_MARKET",
                stopPrice=self._format_price(symbol, stop_price),
                quantity=self._format_quantity(symbol, quantity),
                reduceOnly="true",
            )
            logger.info(f"[{symbol}] 손절 설정 - {stop_price}")
            return order
        except Exception as e:
            logger.error(f"[{symbol}] 손절 설정 실패: {e}")
            return None

    def set_take_profit(
        self, symbol: str, side: str, tp_price: float, quantity: float
    ) -> Optional[dict]:
        """익절 주문 (TP 마켓)"""
        try:
            close_side = "SELL" if side == "BUY" else "BUY"
            order = self.client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type="TAKE_PROFIT_MARKET",
                stopPrice=self._format_price(symbol, tp_price),
                quantity=self._format_quantity(symbol, quantity),
                reduceOnly="true",
            )
            logger.info(f"[{symbol}] 익절 설정 - {tp_price}")
            return order
        except Exception as e:
            logger.error(f"[{symbol}] 익절 설정 실패: {e}")
            return None

    def cancel_all_orders(self, symbol: str):
        """특정 심볼의 모든 미체결 주문 취소"""
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info(f"[{symbol}] 모든 미체결 주문 취소")
        except Exception as e:
            logger.error(f"[{symbol}] 주문 취소 실패: {e}")

    # =========================================================================
    # 유틸리티
    # =========================================================================
    def _get_symbol_info(self, symbol: str) -> dict:
        """심볼 거래 정보 조회 (정밀도 등)"""
        try:
            info = self.client.futures_exchange_info()
            for s in info["symbols"]:
                if s["symbol"] == symbol:
                    return s
        except Exception:
            pass
        return {}

    def _format_quantity(self, symbol: str, quantity: float) -> str:
        """수량 정밀도 포맷팅"""
        info = self._get_symbol_info(symbol)
        for f in info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                step = float(f["stepSize"])
                precision = len(str(step).rstrip("0").split(".")[-1])
                return f"{quantity:.{precision}f}"
        return f"{quantity:.3f}"

    def _format_price(self, symbol: str, price: float) -> str:
        """가격 정밀도 포맷팅"""
        info = self._get_symbol_info(symbol)
        for f in info.get("filters", []):
            if f["filterType"] == "PRICE_FILTER":
                tick = float(f["tickSize"])
                precision = len(str(tick).rstrip("0").split(".")[-1])
                return f"{price:.{precision}f}"
        return f"{price:.2f}"
