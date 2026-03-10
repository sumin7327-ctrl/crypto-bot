"""
AI 분석기 - 바이낸스 선물거래용
Claude API를 사용해 롱/숏 신호 생성
"""

import os
import json
import aiohttp
import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """당신은 바이낸스 선물거래 전문 AI 트레이더입니다.
주어진 시장 데이터를 분석해서 롱(매수)/숏(매도)/홀드 신호를 생성합니다.

반드시 아래 JSON 형식으로만 응답하세요:
{
  "action": "LONG" | "SHORT" | "HOLD",
  "confidence": 0~100,
  "reason": "근거를 번호로 줄바꿈해서 설명\n1) 첫번째 이유\n2) 두번째 이유\n3) 세번째 이유",
  "target_price": 목표가격(숫자),
  "stop_loss": 손절가격(숫자),
  "leverage": 10
}

━━━ LONG 조건 ━━━
필수:
  1. 봉 마감 기준 MA20이 MA50을 상향 돌파 (골든크로스)
  2. 현재 종가가 MA50 위에 있음
  3. MA50이 최근 3봉 기준 상승 기울기
  4. 현재 거래량이 최근 20봉 평균의 1.5배 이상
최상 (추가 확인 시 confidence 상향):
  - 직전 20봉 고점 돌파 확인

━━━ SHORT 조건 ━━━
필수:
  1. 봉 마감 기준 MA20이 MA50을 하향 돌파 (데드크로스)
  2. 현재 종가가 MA50 아래에 있음
  3. MA50이 최근 3봉 기준 하락 기울기
  4. 현재 거래량이 최근 20봉 평균의 1.5배 이상
최상 (추가 확인 시 confidence 상향):
  - 직전 20봉 저점 이탈 확인

━━━ HOLD 조건 ━━━
  - 크로스 발생했으나 거래량 1.5배 미달
  - MA50 기울기가 평평함 (횡보)
  - 고점/저점 돌파 없음
  - 횡보장으로 판단되는 구간
  - 필수 조건 중 하나라도 미충족

confidence 80% 미만이면 HOLD 권장
선물거래 특성상 보수적으로 판단할 것"""


class BinanceAIAnalyzer:
    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.model   = "claude-sonnet-4-20250514"

    async def analyze(self, symbol: str, ticker: dict, klines: list, orderbook: dict) -> dict:
        closes  = [k["close"] for k in klines[-50:]]
        volumes = [k["volume"] for k in klines[-50:]]

        # RSI 계산
        rsi = self._rsi(closes)

        # 이동평균 (20일, 50일)
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else closes[-1]
        ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else closes[-1]

        # 골든크로스/데드크로스 판단
        # 이전 시점 MA
        prev_ma20 = sum(closes[-21:-1]) / 20 if len(closes) >= 21 else ma20
        prev_ma50 = sum(closes[-51:-1]) / 50 if len(closes) >= 51 else ma50
        golden_cross = prev_ma20 < prev_ma50 and ma20 >= ma50  # 골든크로스 발생
        death_cross  = prev_ma20 > prev_ma50 and ma20 <= ma50  # 데드크로스 발생

        # 추세 판단
        if ma20 > ma50:
            trend = "상승 (MA20 > MA50)"
        else:
            trend = "하락 (MA20 < MA50)"

        cross_msg = ""
        if golden_cross:
            cross_msg = "🟡 골든크로스 발생! (강한 매수 신호)"
        elif death_cross:
            cross_msg = "💀 데드크로스 발생! (강한 매도 신호)"

        # 거래량 평균 대비
        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else volumes[-1]
        cur_vol = volumes[-1]
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0

        # 직전 20봉 고점/저점
        recent_high = max(k["high"] for k in klines[-21:-1])
        recent_low  = min(k["low"]  for k in klines[-21:-1])
        cur_close   = closes[-1]
        high_break  = cur_close > recent_high
        low_break   = cur_close < recent_low

        prompt = f"""심볼: {symbol}
현재가: ${ticker['price']:,.2f}
24h 변동: {ticker['change_pct']:+.2f}%
24h 고가: ${ticker['high']:,.2f}
24h 저가: ${ticker['low']:,.2f}
거래량(USDT): ${ticker['quote_volume']:,.0f}

기술적 지표:
- RSI(14): {rsi:.1f}
- MA20: ${ma20:,.2f}
- MA50: ${ma50:,.2f}
- 추세: {trend}
- 크로스 신호: {cross_msg if cross_msg else "없음"}
- MA50 기울기 (최근 3봉): {"상승" if closes[-1] > closes[-4] and ma50 > sum(closes[-53:-50])/3 else "하락" if ma50 < sum(closes[-53:-50])/3 else "평평"}
- 거래량: 20봉 평균 대비 {vol_ratio:.1f}배 (기준: 1.5배)

고점/저점 돌파:
- 직전 20봉 고점: ${recent_high:,.2f} → {"✅ 돌파!" if high_break else "❌ 미돌파"}
- 직전 20봉 저점: ${recent_low:,.2f} → {"✅ 이탈!" if low_break else "❌ 미이탈"}

호가 분석:
- 매수/매도 불균형: {orderbook['imbalance']:.3f} (0.5=균형, >0.6=매수우세)
- 매수벽: ${orderbook['bid_volume']:,.1f}
- 매도벽: ${orderbook['ask_volume']:,.1f}

위 조건들을 기준으로 LONG/SHORT/HOLD 신호를 JSON으로 생성해주세요."""

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key":         self.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type":      "application/json",
                    },
                    json={
                        "model":      self.model,
                        "max_tokens": 500,
                        "system":     SYSTEM_PROMPT,
                        "messages":   [{"role": "user", "content": prompt}],
                    }
                ) as resp:
                    data = await resp.json()
                    text = data["content"][0]["text"]
                    start = text.find("{")
                    end   = text.rfind("}") + 1
                    return json.loads(text[start:end])
        except Exception as e:
            logger.error(f"AI 분석 오류: {e}")
            return {"action": "HOLD", "confidence": 0, "reason": f"분석 오류: {e}",
                    "target_price": 0, "stop_loss": 0, "leverage": 10}

    async def chat(self, text: str) -> str:
        """일반 대화"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key":         self.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type":      "application/json",
                    },
                    json={
                        "model":      self.model,
                        "max_tokens": 500,
                        "system":     "당신은 바이낸스 선물거래 전문 AI 어시스턴트입니다. 한국어로 답변하세요.",
                        "messages":   [{"role": "user", "content": text}],
                    }
                ) as resp:
                    data = await resp.json()
                    return data["content"][0]["text"]
        except Exception as e:
            return f"❌ 오류: {e}"

    def _rsi(self, closes: list, period=14) -> float:
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period
        if al == 0:
            return 100.0
        rs = ag / al
        return 100 - (100 / (1 + rs))
