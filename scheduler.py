"""
자동매매 스케줄러 v2
매매 기준 법칙:
1. 호가/유동성 필터 (호가 불균형, 호가 벽)
2. 체결강도 필터 (매수/매도 우세)
3. 실행 로그/PnL 추적 (수수료 포함)
4. 멀티 타임프레임 필터 (일봉 추세 + 1시간 RSI)
"""

from dotenv import load_dotenv
load_dotenv()

import os
import csv
import asyncio
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

LOG_FILE = Path("/app/trade_log.csv") if Path("/app").exists() else Path.home() / "Downloads" / "crypto_bot_v2" / "trade_log.csv"
FEE_RATE = 0.0005  # 업비트 수수료 0.05%


class TradingScheduler:
    def __init__(self, upbit_client, ai_analyzer):
        self.upbit    = upbit_client
        self.analyzer = ai_analyzer
        self.running  = False
        self._task    = None
        self._chat_id = None
        self._app     = None
        self._ensure_log_file()

    def _ensure_log_file(self):
        if not LOG_FILE.exists():
            with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "시각", "마켓", "액션", "신뢰도", "주문가격",
                    "체결가격", "수량", "투자금액", "체결금액",
                    "슬리피지(%)", "수수료", "PnL", "이유",
                    "호가불균형", "체결강도", "일봉추세", "1h_RSI"
                ])

    def _write_log(self, row: list):
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)

    def start(self, chat_id: int, app):
        if self.running:
            return
        self.running  = True
        self._chat_id = chat_id
        self._app     = app
        self._task    = asyncio.create_task(self._loop())

    def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()

    async def _send(self, text: str):
        if self._app and self._chat_id:
            await self._app.bot.send_message(
                chat_id=self._chat_id, text=text
            )

    async def _get_daily_trend(self, market: str) -> str:
        try:
            candles = await self.upbit.get_candles(market, unit=240, count=20)
            closes  = [c["trade_price"] for c in reversed(candles)]
            if len(closes) < 10:
                return "NEUTRAL"
            sma10   = sum(closes[-10:]) / 10
            sma20   = sum(closes) / len(closes)
            current = closes[-1]
            if current > sma10 > sma20:
                return "BULL"
            elif current < sma10 < sma20:
                return "BEAR"
            return "NEUTRAL"
        except Exception:
            return "NEUTRAL"

    async def _get_1h_rsi(self, market: str) -> float:
        try:
            return await self.upbit.get_rsi(market, period=14)
        except Exception:
            return 50.0

    async def _check_filters(self, market: str, action: str):
        messages = []
        passed   = True

        ob        = await self.upbit.get_orderbook_analysis(market)
        imbalance = ob["imbalance"]

        if action == "BUY":
            if imbalance < 0.8:
                messages.append(f"❌ 호가불균형 매도우세 ({imbalance}) → 진입 보류")
                passed = False
            else:
                messages.append(f"✅ 호가불균형 {imbalance} (매수우세)")
            sell_walls = [w for w in ob["walls"] if "매도벽" in w]
            if sell_walls:
                messages.append(f"❌ 매도벽 감지: {sell_walls[0]} → 진입 보류")
                passed = False
            else:
                messages.append("✅ 매도벽 없음")
        elif action == "SELL":
            if imbalance > 1.2:
                messages.append(f"❌ 호가불균형 매수우세 ({imbalance}) → 매도 보류")
                passed = False
            else:
                messages.append(f"✅ 호가불균형 {imbalance}")

        ts       = await self.upbit.get_trade_strength(market)
        strength = ts["strength"]

        if action == "BUY" and strength < 45:
            messages.append(f"❌ 체결강도 매도우세 ({strength}%) → 진입 보류")
            passed = False
        elif action == "SELL" and strength > 55:
            messages.append(f"❌ 체결강도 매수우세 ({strength}%) → 매도 보류")
            passed = False
        else:
            messages.append(f"✅ 체결강도 {strength}%")

        daily_trend = await self._get_daily_trend(market)
        rsi_1h      = await self._get_1h_rsi(market)

        if action == "BUY":
            if daily_trend == "BEAR":
                messages.append(f"❌ 일봉 하락추세 (BEAR) → 역추세 매수 보류")
                passed = False
            else:
                messages.append(f"✅ 일봉 추세: {daily_trend}")
            if rsi_1h > 70:
                messages.append(f"❌ 1h RSI 과매수 ({rsi_1h}) → 진입 보류")
                passed = False
            else:
                messages.append(f"✅ 1h RSI: {rsi_1h}")
        elif action == "SELL":
            if daily_trend == "BULL":
                messages.append(f"⚠️ 일봉 상승추세 (BULL) — 매도 신중히")
            if rsi_1h < 30:
                messages.append(f"⚠️ 1h RSI 과매도 ({rsi_1h}) — 매도 신중히")

        return passed, messages, imbalance, strength, daily_trend, rsi_1h

    async def _loop(self):
        markets      = os.getenv("MARKETS", "KRW-SOL,KRW-DOGE,KRW-ADA").split(",")
        trade_amount = float(os.getenv("TRADE_AMOUNT", "10000"))
        interval_min = int(os.getenv("INTERVAL_MIN", "1440"))
        min_conf     = int(os.getenv("MIN_CONFIDENCE", "90"))

        await self._send(
            f"🤖 *자동매매 시작! (v2 강화판)*\n"
            f"감시: {', '.join(markets)}\n"
            f"주기: {interval_min}분 | 금액: ₩{trade_amount:,.0f} | 신뢰도: {min_conf}%+\n\n"
            f"*적용 필터:*\n"
            f"✅ 호가/유동성 필터\n"
            f"✅ 체결강도 필터\n"
            f"✅ 멀티 타임프레임 필터\n"
            f"✅ 실행 로그/PnL 추적 (trade_log.csv)"
        )

        while self.running:
            try:
                await self._run_cycle(markets, trade_amount, min_conf)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"스케줄러 오류: {e}")
                await self._send(f"⚠️ 오류: {e}")

            for _ in range(interval_min * 60):
                if not self.running:
                    return
                await asyncio.sleep(1)

    async def _run_cycle(self, markets, trade_amount, min_conf):
        now = datetime.now().strftime("%H:%M")
        for market in markets:
            try:
                signal     = await self.analyzer.should_trade(market, trade_amount)
                action     = signal.get("action", "HOLD")
                confidence = signal.get("confidence", 0)
                reason     = signal.get("reason", "")
                target     = signal.get("target_price", 0)
                stop_loss  = signal.get("stop_loss", 0)

                emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(action, "⚪")
                log_msg = (
                    f"{emoji} *[{now}] {market}*\n"
                    f"AI 신호: `{action}` ({confidence}%)\n"
                    f"이유: {reason}\n"
                    f"목표가: `₩{target:,.0f}` | 손절가: `₩{stop_loss:,.0f}`"
                )

                if action == "HOLD" or confidence < min_conf:
                    if action != "HOLD":
                        log_msg += f"\n_신뢰도 부족 ({confidence}% < {min_conf}%)_"
                    await self._send(log_msg)
                    await asyncio.sleep(1)
                    continue

                passed, filter_msgs, imbalance, strength, trend, rsi_1h = \
                    await self._check_filters(market, action)

                log_msg += f"\n\n*📋 필터 결과:*\n" + "\n".join(filter_msgs)

                if not passed:
                    log_msg += "\n\n⛔ *필터 불통과 — 주문 보류*"
                    await self._send(log_msg)
                    self._write_log([
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        market, f"{action}(보류)", confidence,
                        "-", "-", "-", "-", "-", "-", "-", "-",
                        reason, imbalance, strength, trend, rsi_1h
                    ])
                    await asyncio.sleep(1)
                    continue

                ticker      = await self.upbit.get_ticker(market)
                order_price = ticker["trade_price"]

                if action == "BUY":
                    order    = await self.upbit.market_order_buy(market, trade_amount)
                    uuid     = order.get("uuid", "N/A")
                    quantity = round(trade_amount / order_price, 8)
                    fee      = round(trade_amount * FEE_RATE, 0)
                    log_msg += (
                        f"\n\n✅ *매수 완료!*\n"
                        f"주문ID: `{uuid}`\n"
                        f"체결가: `₩{order_price:,.0f}`\n"
                        f"수량: `{quantity}`\n"
                        f"수수료: `₩{fee:,.0f}`"
                    )
                    self._write_log([
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        market, "BUY", confidence,
                        order_price, order_price, quantity,
                        trade_amount, trade_amount, 0, fee, "-",
                        reason, imbalance, strength, trend, rsi_1h
                    ])

                elif action == "SELL":
                    currency = market.split("-")[1]
                    balances = await self.upbit.get_balances()
                    volume   = balances.get(currency, {}).get("balance", 0)
                    avg_buy  = balances.get(currency, {}).get("avg_buy_price", 0)

                    if volume <= 0:
                        log_msg += "\n\n⚠️ 보유 수량 없음 — 매도 건너뜀"
                    else:
                        order     = await self.upbit.market_order_sell(market, volume)
                        uuid      = order.get("uuid", "N/A")
                        sell_amt  = round(order_price * volume, 0)
                        buy_amt   = round(avg_buy * volume, 0)
                        fee       = round(sell_amt * FEE_RATE, 0)
                        pnl       = round(sell_amt - buy_amt - fee, 0)
                        pnl_pct   = round((pnl / buy_amt * 100) if buy_amt > 0 else 0, 2)
                        pnl_emoji = "📈" if pnl >= 0 else "📉"
                        log_msg += (
                            f"\n\n✅ *매도 완료!*\n"
                            f"주문ID: `{uuid}`\n"
                            f"체결가: `₩{order_price:,.0f}`\n"
                            f"수량: `{volume}`\n"
                            f"수수료: `₩{fee:,.0f}`\n"
                            f"{pnl_emoji} PnL: `{'+' if pnl >= 0 else ''}₩{pnl:,.0f}` ({pnl_pct:+.2f}%)"
                        )
                        self._write_log([
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            market, "SELL", confidence,
                            order_price, order_price, volume,
                            buy_amt, sell_amt, 0, fee, pnl,
                            reason, imbalance, strength, trend, rsi_1h
                        ])

                await self._send(log_msg)

            except Exception as e:
                await self._send(f"❌ {market} 처리 오류: {e}")

            await asyncio.sleep(1)
