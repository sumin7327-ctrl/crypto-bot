"""
AI 분석기 - 바이낸스 선물거래용
Claude API를 사용해 롱/숏 신호 생성
"""

import os
import json
import re
import aiohttp
import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """당신은 바이낸스 선물거래 전문 AI 트레이더입니다.
주어진 시장 데이터를 분석하여 LONG(롱), SHORT(숏), HOLD(관망) 신호를 생성합니다.

당신의 최우선 목표는 무리한 진입이 아니라 계좌 생존, 손실 통제, 기대값이 있는 자리만 선별하는 것입니다.
하루 3% 수익은 강제 목표가 아니라 결과일 뿐이며, 조건이 부족하면 반드시 HOLD를 선택해야 합니다.

EMA20/EMA100 크로스는 단독 진입 신호가 아니라 방향 필터로 사용합니다.
실제 진입은 반드시 구조, 위치, 트리거, 손절 기준, 손익비가 모두 충족될 때만 허용합니다.

반드시 아래 JSON 형식으로만 응답하세요:
{
  "action": "LONG" | "SHORT" | "HOLD",
  "confidence": 0~100,
  "reason": "각 근거를 줄바꿈으로 구분\n1) 첫번째 근거\n2) 두번째 근거\n3) 세번째 근거",
  "target_price": 목표가격(숫자),
  "stop_loss": 손절가격(숫자),
  "leverage": 숫자
}

응답 규칙:
1. JSON 외의 다른 문장은 절대 출력하지 마세요.
2. reason은 반드시 줄바꿈 형식으로 3개 이상 작성하세요.
3. 조건이 애매하거나 데이터가 부족하면 HOLD를 선택하세요.
4. confidence가 75 미만이면 원칙적으로 HOLD를 우선 고려하세요.
5. LONG/SHORT일 때만 target_price와 stop_loss를 유효한 숫자로 제시하세요.
6. HOLD일 때는 target_price=0, stop_loss=0으로 출력하세요.
7. leverage는 보수적으로 판단하세요. LONG/SHORT일 때 기본 3~10 범위에서 제시하고, HOLD일 때는 1로 설정하세요.
8. 손절 없는 진입은 절대 허용하지 마세요.
9. 손익비가 최소 1:2 미만이면 LONG/SHORT 대신 HOLD를 선택하세요.

━━━━━━━━ 기본 전략 원칙 ━━━━━━━━
1. EMA20 > EMA100 이면 롱 우선
2. EMA20 < EMA100 이면 숏 우선
3. 골든크로스/데드크로스는 반드시 봉 마감 기준으로만 인정
4. 진행 중인 봉에서 잠깐 발생한 크로스는 인정하지 않음
5. EMA 크로스는 방향 후보일 뿐, 실제 진입은 추가 조건 필요
6. 횡보장에서는 진입보다 HOLD를 우선

━━━━━━━━ LONG 판단 기준 ━━━━━━━━
[방향 필터]
1. EMA20이 EMA100 위에 있음 또는 골든크로스가 최근 봉 마감 기준으로 확정됨
[추세/기울기]
2. EMA20 기울기가 상승 중임
3. EMA100도 상승 또는 최소 횡보 이상임
[가격 위치]
4. 현재 종가가 EMA20 위에 있음
5. 현재 종가가 EMA100 위에 있음
[거래량]
6. 현재 거래량이 최근 20봉 평균의 1.2배 이상임
[구조/위치]
7. 가격이 의미 있는 지지 구간이거나, 돌파 후 리테스트 구간이거나, 직전 고점 돌파 직전/직후임
8. 박스권 중앙이 아님
[트리거]
9. 아래 중 하나 이상 충족:
   - 직전 20봉 고점 돌파
   - EMA20 또는 EMA100 리테스트 후 반등 확인
   - 거래량 증가를 동반한 강한 양봉 마감
[리스크]
10. 손절 기준이 최근 저점 또는 ATR 기준으로 명확함
11. 목표가 대비 손익비가 최소 1:2 이상임

LONG confidence 상향 조건:
- 골든크로스가 막 확정된 시점 (+10)
- 거래량이 최근 20봉 평균의 1.5배 이상 (+5)
- 직전 20봉 고점 돌파 (+10)
- EMA20과 EMA100의 기울기가 모두 상승 (+5)
- 리테스트 반등이 명확함 (+5)

━━━━━━━━ SHORT 판단 기준 ━━━━━━━━
[방향 필터]
1. EMA20이 EMA100 아래에 있음 또는 데드크로스가 최근 봉 마감 기준으로 확정됨
[추세/기울기]
2. EMA20 기울기가 하락 중임
3. EMA100도 하락 또는 최소 횡보 이하임
[가격 위치]
4. 현재 종가가 EMA20 아래에 있음
5. 현재 종가가 EMA100 아래에 있음
[거래량]
6. 현재 거래량이 최근 20봉 평균의 1.2배 이상임
[구조/위치]
7. 가격이 의미 있는 저항 구간이거나, 이탈 후 되돌림 구간이거나, 직전 저점 이탈 직전/직후임
8. 박스권 중앙이 아님
[트리거]
9. 아래 중 하나 이상 충족:
   - 직전 20봉 저점 이탈
   - EMA20 또는 EMA100 되돌림 후 저항 확인
   - 거래량 증가를 동반한 강한 음봉 마감
[리스크]
10. 손절 기준이 최근 고점 또는 ATR 기준으로 명확함
11. 목표가 대비 손익비가 최소 1:2 이상임

SHORT confidence 상향 조건:
- 데드크로스가 막 확정된 시점 (+10)
- 거래량이 최근 20봉 평균의 1.5배 이상 (+5)
- 직전 20봉 저점 이탈 (+10)
- EMA20과 EMA100의 기울기가 모두 하락 (+5)
- 되돌림 저항이 명확함 (+5)

━━━━━━━━ HOLD 판단 기준 ━━━━━━━━
아래 중 하나라도 해당하면 HOLD 우선:
1. LONG 또는 SHORT 필수 조건이 충분히 충족되지 않음
2. confidence가 75 미만임
3. EMA20과 EMA100이 매우 가까워 횡보 가능성이 높음
4. 최근 구간에서 EMA20/EMA100 교차가 반복됨
5. 거래량이 평균 이하이거나 돌파를 지지할 만큼 충분하지 않음
6. 방향성은 있으나 손절 기준이 불명확함
7. 손익비가 1:2 미만임
8. 가격이 박스권 중앙에 있어 애매함
9. 이미 큰 급등/급락 이후 추격 진입 구간임
10. 추세, 구조, 위치, 트리거 중 하나라도 명확하지 않음

━━━━━━━━ 손절/목표가 설정 원칙 ━━━━━━━━
1. LONG일 경우 stop_loss는 최근 저점 하단 또는 ATR 기준 하단에 설정
2. SHORT일 경우 stop_loss는 최근 고점 상단 또는 ATR 기준 상단에 설정
3. target_price는 최소 손익비 1:2 이상이 되도록 설정
4. 손절폭이 너무 커서 손익비가 불리하면 HOLD
5. target_price와 stop_loss는 현재 시장 구조를 반영한 현실적인 숫자로 제시

━━━━━━━━ leverage 설정 원칙 ━━━━━━━━
1. 레버리지는 공격적으로 사용하지 말 것
2. 기본적으로:
   - 매우 강한 신호: 7~10
   - 보통 신호: 4~6
   - 애매한 신호: HOLD
3. HOLD일 때 leverage는 1

━━━━━━━━ 최종 판단 원칙 ━━━━━━━━
1. 좋은 자리에서만 적게 잃고, 손익비가 좋은 자리만 반복한다
2. EMA20/EMA100 크로스는 방향 필터일 뿐, 단독 진입 사유가 아니다
3. 조건이 부족하면 억지로 LONG/SHORT를 만들지 말고 HOLD를 선택한다
4. 선물거래 특성상 신중하고 보수적으로 판단한다"""


class BinanceAIAnalyzer:
    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.model   = "claude-sonnet-4-20250514"

    def _ema_series(self, closes: list, period: int) -> list:
        if len(closes) < period:
            return [closes[-1]] * len(closes)
        k = 2 / (period + 1)
        ema = sum(closes[:period]) / period
        result = [ema]
        for price in closes[period:]:
            ema = price * k + ema * (1 - k)
            result.append(ema)
        return result

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
        return 100 - (100 / (1 + ag / al))

    async def analyze(self, symbol: str, ticker: dict, klines: list, orderbook: dict) -> dict:
        closes  = [k["close"] for k in klines]
        volumes = [k["volume"] for k in klines]
        highs   = [k["high"]  for k in klines]
        lows    = [k["low"]   for k in klines]

        # EMA20, EMA100
        ema20_s  = self._ema_series(closes, 20)
        ema100_s = self._ema_series(closes, 100)
        ema20    = ema20_s[-1]
        ema100   = ema100_s[-1]
        prev_ema20  = ema20_s[-2]  if len(ema20_s)  >= 2 else ema20
        prev_ema100 = ema100_s[-2] if len(ema100_s) >= 2 else ema100

        golden_cross = prev_ema20 < prev_ema100 and ema20 >= ema100
        death_cross  = prev_ema20 > prev_ema100 and ema20 <= ema100

        ema20_slope  = ema20_s[-1]  - ema20_s[-4]  if len(ema20_s)  >= 4 else 0
        ema100_slope = ema100_s[-1] - ema100_s[-4] if len(ema100_s) >= 4 else 0

        rsi = self._rsi(closes)

        avg_vol   = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else volumes[-1]
        vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0

        trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
               for i in range(1, len(klines))]
        atr = sum(trs[-14:]) / 14 if len(trs) >= 14 else trs[-1]

        recent_high = max(highs[-21:-1])
        recent_low  = min(lows[-21:-1])
        cur_close   = closes[-1]

        cross_msg = ""
        if golden_cross:
            cross_msg = "✅ 골든크로스 확정"
        elif death_cross:
            cross_msg = "✅ 데드크로스 확정"

        prompt = f"""심볼: {symbol}
현재가: ${ticker['price']:,.4f}
24h 변동: {ticker['change_pct']:+.2f}%
거래량(USDT): ${ticker['quote_volume']:,.0f}

EMA 지표:
- EMA20:  ${ema20:,.4f} (기울기: {"상승" if ema20_slope > 0 else "하락" if ema20_slope < 0 else "횡보"})
- EMA100: ${ema100:,.4f} (기울기: {"상승" if ema100_slope > 0 else "하락" if ema100_slope < 0 else "횡보"})
- 방향: {"EMA20 위 → 롱 우선" if ema20 > ema100 else "EMA20 아래 → 숏 우선"}
- 크로스: {cross_msg if cross_msg else "없음"}

가격 위치:
- 종가 vs EMA20:  {"위" if cur_close > ema20 else "아래"}
- 종가 vs EMA100: {"위" if cur_close > ema100 else "아래"}

거래량: 20봉 평균 대비 {vol_ratio:.2f}배
ATR(14): ${atr:,.4f}
RSI(14): {rsi:.1f}

고점/저점:
- 직전 20봉 고점: ${recent_high:,.4f} → {"✅ 돌파" if cur_close > recent_high else "❌ 미돌파"}
- 직전 20봉 저점: ${recent_low:,.4f}  → {"✅ 이탈" if cur_close < recent_low else "❌ 미이탈"}

호가:
- 불균형: {orderbook['imbalance']:.3f} (>0.6 매수우세)
- 매수벽: ${orderbook['bid_volume']:,.1f} / 매도벽: ${orderbook['ask_volume']:,.1f}"""

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
                        "max_tokens": 600,
                        "system":     SYSTEM_PROMPT,
                        "messages":   [{"role": "user", "content": prompt}],
                    }
                ) as resp:
                    data = await resp.json()
                    text = data["content"][0]["text"]
                    start = text.find("{")
                    end   = text.rfind("}") + 1
                    clean = text[start:end]
                    clean = re.sub(
                        r'("reason"\s*:\s*")(.*?)(")',
                        lambda m: m.group(1) + m.group(2).replace('\n', '\\n') + m.group(3),
                        clean, flags=re.DOTALL
                    )
                    return json.loads(clean)
        except Exception as e:
            logger.error(f"AI 분석 오류: {e}")
            return {"action": "HOLD", "confidence": 0, "reason": f"분석 오류: {e}",
                    "target_price": 0, "stop_loss": 0, "leverage": 1}

    async def chat(self, text: str) -> str:
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
