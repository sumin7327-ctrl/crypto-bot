"""
업비트 API 클라이언트 v2
"""

from dotenv import load_dotenv
load_dotenv()

import os
import uuid
import hashlib
import asyncio
import aiohttp
import jwt
from urllib.parse import urlencode
from typing import Dict, Any, List

BASE_URL = "https://api.upbit.com/v1"


class UpbitClient:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None

    def _keys(self):
        return os.getenv("UPBIT_ACCESS_KEY", ""), os.getenv("UPBIT_SECRET_KEY", "")

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    def _make_token(self, query_params: dict = None) -> str:
        access_key, secret_key = self._keys()
        payload = {"access_key": access_key, "nonce": str(uuid.uuid4())}
        if query_params:
            qs = urlencode(query_params).encode()
            m  = hashlib.sha512()
            m.update(qs)
            payload["query_hash"]     = m.hexdigest()
            payload["query_hash_alg"] = "SHA512"
        return jwt.encode(payload, secret_key, algorithm="HS256")

    def _auth_header(self, query_params: dict = None) -> dict:
        return {"Authorization": f"Bearer {self._make_token(query_params)}"}

    async def _get(self, path: str, params: dict = None, signed=False) -> Any:
        session = await self._get_session()
        headers = self._auth_header(params) if signed else {}
        async with session.get(f"{BASE_URL}{path}", params=params, headers=headers) as r:
            r.raise_for_status()
            return await r.json()

    async def _post(self, path: str, body: dict) -> Any:
        session = await self._get_session()
        headers = {**self._auth_header(body), "Content-Type": "application/json"}
        async with session.post(f"{BASE_URL}{path}", json=body, headers=headers) as r:
            r.raise_for_status()
            return await r.json()

    async def _delete(self, path: str, params: dict) -> Any:
        session = await self._get_session()
        headers = self._auth_header(params)
        async with session.delete(f"{BASE_URL}{path}", params=params, headers=headers) as r:
            r.raise_for_status()
            return await r.json()

    async def get_ticker(self, market: str) -> Dict[str, Any]:
        data = await self._get("/ticker", {"markets": market})
        return data[0]

    async def get_tickers(self, markets: List[str]) -> List[Dict]:
        return await self._get("/ticker", {"markets": ",".join(markets)})

    async def get_candles(self, market: str, unit=60, count=50) -> List[Dict]:
        return await self._get(f"/candles/minutes/{unit}", {"market": market, "count": count})

    async def get_orderbook(self, market: str) -> Dict:
        data = await self._get("/orderbook", {"markets": market})
        return data[0]

    async def get_market_list(self) -> List[Dict]:
        return await self._get("/market/all")

    async def get_balances(self) -> Dict[str, Dict]:
        data = await self._get("/accounts", signed=True)
        return {
            item["currency"]: {
                "balance":       float(item["balance"]),
                "locked":        float(item["locked"]),
                "avg_buy_price": float(item["avg_buy_price"]),
            }
            for item in data
            if float(item["balance"]) > 0 or float(item["locked"]) > 0
        }

    async def get_open_orders(self, market: str = None) -> List[Dict]:
        params = {"state": "wait"}
        if market:
            params["market"] = market
        return await self._get("/orders", params, signed=True)

    async def market_order_buy(self, market: str, price_krw: float) -> Dict:
        return await self._post("/orders", {
            "market": market, "side": "bid",
            "price": str(price_krw), "ord_type": "price",
        })

    async def market_order_sell(self, market: str, volume: float) -> Dict:
        return await self._post("/orders", {
            "market": market, "side": "ask",
            "volume": str(volume), "ord_type": "market",
        })

    async def limit_order(self, market: str, side: str, volume: float, price: float) -> Dict:
        return await self._post("/orders", {
            "market": market, "side": side,
            "volume": str(volume), "price": str(int(price)), "ord_type": "limit",
        })

    async def cancel_order(self, uuid_str: str) -> Dict:
        return await self._delete("/order", {"uuid": uuid_str})

    async def get_rsi(self, market: str, period=14) -> float:
        candles = await self.get_candles(market, unit=60, count=period + 1)
        closes  = [c["trade_price"] for c in reversed(candles)]
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    async def get_indicators(self, market: str) -> Dict[str, Any]:
        candles = await self.get_candles(market, unit=60, count=50)
        closes  = [c["trade_price"] for c in reversed(candles)]
        rsi     = await self.get_rsi(market)
        sma20   = sum(closes[-20:]) / 20
        sma50   = sum(closes[-50:]) / 50
        current = closes[-1]
        return {
            "current_price": current,
            "rsi":           rsi,
            "sma20":         round(sma20, 0),
            "sma50":         round(sma50, 0),
            "above_sma20":   current > sma20,
            "above_sma50":   current > sma50,
        }

    async def get_volume_surge(self, multiplier: float = 3.0) -> List[Dict]:
        markets     = await self.get_market_list()
        krw_markets = [m["market"] for m in markets if m["market"].startswith("KRW-")]
        all_tickers = []
        for i in range(0, len(krw_markets), 100):
            chunk   = krw_markets[i:i+100]
            tickers = await self._get("/ticker", {"markets": ",".join(chunk)})
            all_tickers.extend(tickers)
            await asyncio.sleep(0.2)

        surge_list = []
        for t in all_tickers:
            try:
                acc_24h      = float(t.get("acc_trade_price_24h", 0))
                avg_per_hour = acc_24h / 24
                if avg_per_hour <= 0:
                    continue
                candles       = await self.get_candles(t["market"], unit=60, count=2)
                await asyncio.sleep(0.1)
                recent_volume = float(candles[0].get("candle_acc_trade_price", 0))
                ratio         = recent_volume / avg_per_hour
                if ratio >= multiplier:
                    surge_list.append({
                        "market":        t["market"],
                        "current_price": t["trade_price"],
                        "change_rate":   round(t.get("signed_change_rate", 0) * 100, 2),
                        "volume_ratio":  round(ratio, 1),
                    })
            except Exception:
                continue

        surge_list.sort(key=lambda x: x["volume_ratio"], reverse=True)
        return surge_list[:10]
