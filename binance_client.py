"""
바이낸스 선물거래 클라이언트
- 실시간 가격 조회
- 포지션 관리
- 선물 주문 (롱/숏)
- 호가/체결강도 분석
"""

import aiohttp
import hashlib
import hmac
import time
from urllib.parse import urlencode
import os


class BinanceFuturesClient:
    BASE_URL = "https://fapi.binance.com"

    def __init__(self):
        self.api_key    = os.getenv("BINANCE_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_SECRET_KEY", "")

    def _sign(self, params: dict) -> str:
        query = urlencode(params)
        return hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()

    def _headers(self):
        return {"X-MBX-APIKEY": self.api_key}

    async def _get(self, path, params=None, signed=False):
        if params is None:
            params = {}
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._sign(params)
        async with aiohttp.ClientSession() as s:
            async with s.get(self.BASE_URL + path, params=params, headers=self._headers()) as r:
                return await r.json()

    async def _post(self, path, params):
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)
        async with aiohttp.ClientSession() as s:
            async with s.post(self.BASE_URL + path, params=params, headers=self._headers()) as r:
                return await r.json()

    async def get_ticker(self, symbol: str) -> dict:
        """실시간 가격 조회"""
        data = await self._get("/fapi/v1/ticker/24hr", {"symbol": symbol})
        return {
            "symbol":        symbol,
            "price":         float(data.get("lastPrice", 0)),
            "change_pct":    float(data.get("priceChangePercent", 0)),
            "high":          float(data.get("highPrice", 0)),
            "low":           float(data.get("lowPrice", 0)),
            "volume":        float(data.get("volume", 0)),
            "quote_volume":  float(data.get("quoteVolume", 0)),
        }

    async def get_klines(self, symbol: str, interval="1h", limit=100) -> list:
        """캔들 데이터"""
        data = await self._get("/fapi/v1/klines", {
            "symbol": symbol, "interval": interval, "limit": limit
        })
        return [{
            "open":   float(k[1]),
            "high":   float(k[2]),
            "low":    float(k[3]),
            "close":  float(k[4]),
            "volume": float(k[5]),
        } for k in data]

    async def get_orderbook(self, symbol: str, limit=20) -> dict:
        """호가 분석"""
        data = await self._get("/fapi/v1/depth", {"symbol": symbol, "limit": limit})
        bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
        asks = [(float(p), float(q)) for p, q in data.get("asks", [])]
        bid_vol = sum(q for _, q in bids)
        ask_vol = sum(q for _, q in asks)
        total = bid_vol + ask_vol
        imbalance = bid_vol / total if total > 0 else 0.5
        return {
            "bids": bids, "asks": asks,
            "bid_volume": bid_vol, "ask_volume": ask_vol,
            "imbalance": round(imbalance, 3),
        }

    async def get_positions(self) -> list:
        """현재 포지션 조회"""
        data = await self._get("/fapi/v2/positionRisk", signed=True)
        positions = []
        for p in data:
            amt = float(p.get("positionAmt", 0))
            if amt != 0:
                positions.append({
                    "symbol":      p["symbol"],
                    "side":        "LONG" if amt > 0 else "SHORT",
                    "size":        abs(amt),
                    "entry_price": float(p.get("entryPrice", 0)),
                    "mark_price":  float(p.get("markPrice", 0)),
                    "pnl":         float(p.get("unRealizedProfit", 0)),
                    "pnl_pct":     float(p.get("unRealizedProfit", 0)) / (abs(amt) * float(p.get("entryPrice", 1))) * 100,
                    "leverage":    int(p.get("leverage", 1)),
                })
        return positions

    async def get_balance(self) -> dict:
        """계좌 잔고"""
        data = await self._get("/fapi/v2/balance", signed=True)
        for b in data:
            if b.get("asset") == "USDT":
                return {
                    "total":     float(b.get("balance", 0)),
                    "available": float(b.get("availableBalance", 0)),
                    "pnl":       float(b.get("crossUnPnl", 0)),
                }
        return {"total": 0, "available": 0, "pnl": 0}

    async def set_leverage(self, symbol: str, leverage: int):
        """레버리지 설정"""
        return await self._post("/fapi/v1/leverage", {
            "symbol": symbol, "leverage": leverage
        })

    async def set_margin_type(self, symbol: str, margin_type="CROSSED"):
        """마진 타입 설정 (CROSSED=교차, ISOLATED=격리)"""
        try:
            return await self._post("/fapi/v1/marginType", {
                "symbol": symbol, "marginType": margin_type
            })
        except Exception:
            pass  # 이미 설정된 경우 오류 무시

    async def open_long(self, symbol: str, usdt_amount: float) -> dict:
        """롱 포지션 오픈"""
        ticker = await self.get_ticker(symbol)
        price  = ticker["price"]
        qty    = round(usdt_amount / price, 3)
        await self.set_leverage(symbol, 10)
        await self.set_margin_type(symbol, "CROSSED")
        return await self._post("/fapi/v1/order", {
            "symbol":   symbol,
            "side":     "BUY",
            "type":     "MARKET",
            "quantity": qty,
        })

    async def open_short(self, symbol: str, usdt_amount: float) -> dict:
        """숏 포지션 오픈"""
        ticker = await self.get_ticker(symbol)
        price  = ticker["price"]
        qty    = round(usdt_amount / price, 3)
        await self.set_leverage(symbol, 10)
        await self.set_margin_type(symbol, "CROSSED")
        return await self._post("/fapi/v1/order", {
            "symbol":   symbol,
            "side":     "SELL",
            "type":     "MARKET",
            "quantity": qty,
        })

    async def close_position(self, symbol: str, side: str, size: float) -> dict:
        """포지션 청산"""
        close_side = "SELL" if side == "LONG" else "BUY"
        return await self._post("/fapi/v1/order", {
            "symbol":         symbol,
            "side":           close_side,
            "type":           "MARKET",
            "quantity":       round(size, 3),
            "reduceOnly":     "true",
        })
