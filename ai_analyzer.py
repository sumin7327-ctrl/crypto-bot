"""
Claude AI 매매 신호 분석기 v2
"""

from dotenv import load_dotenv
load_dotenv()

import os
import json
import aiohttp
from upbit_client import UpbitClient

SYSTEM_PROMPT = """당신은 암호화폐 트레이딩 전문 AI 어시스턴트입니다.
제공된 기술적 지표를 바탕으로 매매 신호를 분석하고, 명확하고 간결한 한국어로 답변합니다.

분석 시 반드시 포함할 항목:
1. 📊 현재 시장 상황 요약
2. 🎯 매매 신호: 매수 / 매도 / 관망 중 하나
3. 💡 근거 (RSI, 이동평균 등 지표 기반)
4. ⚠️ 리스크 주의사항
5. 🎯 단기 목표가 / 손절가 제안 (원화 기준)

면책: 이 분석은 참고용이며 투자 손실에 대한 책임은 본인에게 있습니다."""


class AIAnalyzer:
    def __init__(self):
        self.upbit = UpbitClient()
        self.url   = "https://api.anthropic.com/v1/messages"

    def _headers(self):
        return {
            "x-api-key":         os.getenv("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        }

    async def _call_claude(self, user_msg: str, system: str = SYSTEM_PROMPT) -> str:
        payload = {
            "model":      "claude-sonnet-4-20250514",
            "max_tokens": 1000,
            "system":     system,
            "messages":   [{"role": "user", "content": user_msg}],
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(self.url, headers=self._headers(), json=payload) as r:
                r.raise_for_status()
                data = await r.json()
                return data["content"][0]["text"]

    async def analyze_symbol(self, market: str) -> str:
        try:
            indicators = await self.upbit.get_indicators(market)
            ticker     = await self.upbit.get_ticker(market)
            change_rate = ticker.get("signed_change_rate", 0) * 100
            prompt = (
                f"다음 {market} 데이터를 분석해 매매 신호를 제시해주세요:\n\n"
                f"📌 현재가: ₩{indicators['current_price']:,.0f}\n"
                f"📈 24h 변동: {change_rate:+.2f}%\n"
                f"📊 RSI(14): {indicators['rsi']}\n"
                f"📉 SMA20: ₩{indicators['sma20']:,.0f} ({'현재가 위' if indicators['above_sma20'] else '현재가 아래'})\n"
                f"📉 SMA50: ₩{indicators['sma50']:,.0f} ({'현재가 위' if indicators['above_sma50'] else '현재가 아래'})\n"
                f"📦 24h 거래대금: ₩{ticker.get('acc_trade_price_24h', 0):,.0f}"
            )
            return await self._call_claude(prompt)
        except Exception as e:
            return f"❌ 분석 실패: {e}"

    async def should_trade(self, market: str, trade_amount_krw: float) -> dict:
        try:
            indicators  = await self.upbit.get_indicators(market)
            ticker      = await self.upbit.get_ticker(market)
            change_rate = ticker.get("signed_change_rate", 0) * 100
            prompt = (
                f"{market} 자동매매 판단이 필요합니다. JSON으로만 응답하세요.\n\n"
                f"현재가: {indicators['current_price']}\n"
                f"RSI(14): {indicators['rsi']}\n"
                f"SMA20: {indicators['sma20']} ({'위' if indicators['above_sma20'] else '아래'})\n"
                f"SMA50: {indicators['sma50']} ({'위' if indicators['above_sma50'] else '아래'})\n"
                f"24h 변동률: {change_rate:.2f}%\n\n"
                f'{{"action":"BUY"|"SELL"|"HOLD","confidence":0~100,"reason":"한줄이유","target_price":숫자,"stop_loss":숫자}}'
            )
            raw   = await self._call_claude(prompt, system="Reply only with valid JSON. No explanation.")
            clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            return json.loads(clean)
        except Exception as e:
            return {"action": "HOLD", "confidence": 0, "reason": f"오류: {e}"}

    async def chat(self, user_message: str) -> str:
        return await self._call_claude(user_message)
