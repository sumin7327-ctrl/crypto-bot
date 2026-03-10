"""
바이낸스 선물거래 스케줄러
- 15분마다 AI 분석 및 자동매매
- 롱/숏 포지션 관리
"""

import os
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class BinanceScheduler:
    def __init__(self, client, analyzer):
        self.client   = client
        self.analyzer = analyzer
        self.running  = False
        self.task     = None
        self.chat_id  = None
        self.app      = None

    async def _send(self, text: str):
        if self.chat_id and self.app:
            try:
                await self.app.bot.send_message(
                    chat_id=self.chat_id, text=text, parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"메시지 전송 오류: {e}")

    def start(self, chat_id: int, app):
        if self.running:
            return
        self.chat_id = chat_id
        self.app     = app
        self.running = True
        self.task    = asyncio.create_task(self._loop())

    def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()

    async def _loop(self):
        symbols      = os.getenv("MARKETS", "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT").split(",")
        trade_amount = float(os.getenv("TRADE_AMOUNT", "10000")) / 1400  # 원 → USDT
        interval_min = int(os.getenv("INTERVAL_MIN", "15"))
        min_conf     = int(os.getenv("MIN_CONFIDENCE", "85"))

        await self._send(
            f"🤖 *바이낸스 선물 자동매매 시작!*\n\n"
            f"📌 감시: `{', '.join(symbols)}`\n"
            f"⏱ 주기: `{interval_min}분`\n"
            f"💰 1회 금액: `${trade_amount:.1f} USDT`\n"
            f"🎯 최소 신뢰도: `{min_conf}%`\n"
            f"⚡ 레버리지: `10x` | 교차마진"
        )

        while self.running:
            try:
                await self._run_cycle(symbols, trade_amount, min_conf)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"스케줄러 오류: {e}")
                await self._send(f"⚠️ 오류: {e}")

            for _ in range(interval_min * 60):
                if not self.running:
                    return
                await asyncio.sleep(1)

    async def _run_cycle(self, symbols, trade_amount, min_conf):
        now = datetime.now().strftime("%H:%M")

        for symbol in symbols:
            try:
                ticker    = await self.client.get_ticker(symbol)
                klines    = await self.client.get_klines(symbol, "1h", 100)
                orderbook = await self.client.get_orderbook(symbol)
                signal    = await self.analyzer.analyze(symbol, ticker, klines, orderbook)

                action     = signal.get("action", "HOLD")
                confidence = signal.get("confidence", 0)
                reason     = signal.get("reason", "")
                target     = signal.get("target_price", 0)
                stop_loss  = signal.get("stop_loss", 0)

                emoji = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "⚪"}.get(action, "⚪")

                msg = (
                    f"{emoji} *[{now}] {symbol}*\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"💰 현재가: `${ticker['price']:,.2f}` ({ticker['change_pct']:+.2f}%)\n"
                    f"🤖 AI 신호: `{action}` ({confidence}%)\n\n"
                    f"📋 *분석 근거*\n{reason}\n\n"
                    f"🎯 목표가: `${target:,.2f}`\n"
                    f"🛑 손절가: `${stop_loss:,.2f}`"
                )

                if action == "HOLD" or confidence < min_conf:
                    if action != "HOLD":
                        msg += f"\n_신뢰도 부족 ({confidence}% < {min_conf}%)_"
                    await self._send(msg)
                    await asyncio.sleep(1)
                    continue

                # 기존 포지션 확인
                positions = await self.client.get_positions()
                existing  = next((p for p in positions if p["symbol"] == symbol), None)

                if existing:
                    # 반대 신호면 청산
                    if (action == "LONG" and existing["side"] == "SHORT") or \
                       (action == "SHORT" and existing["side"] == "LONG"):
                        await self.client.close_position(symbol, existing["side"], existing["size"])
                        msg += f"\n\n🔄 *기존 {existing['side']} 포지션 청산 후 진입*"
                    else:
                        msg += f"\n\n⏸ 이미 {existing['side']} 포지션 보유 중"
                        await self._send(msg)
                        continue

                # 포지션 오픈
                if action == "LONG":
                    result = await self.client.open_long(symbol, trade_amount)
                else:
                    result = await self.client.open_short(symbol, trade_amount)

                if result.get("orderId"):
                    msg += f"\n\n✅ *{action} 진입 완료*\n주문ID: `{result['orderId']}`"
                else:
                    msg += f"\n\n❌ 주문 실패: {result}"

                await self._send(msg)
                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"{symbol} 분석 오류: {e}")
                await self._send(f"⚠️ {symbol} 오류: {e}")
