# -*- coding: utf-8 -*-
"""
자동매매 스케줄러 v2 (안정형 강화)
매매 기준 법칙:
1. 호가/유동성 필터 (호가 불균형, 호가 벽)
2. 체결강도 필터 (매수/매도 우세)
3. 실행 로그/PnL 추적 (수수료 포함)
4. 멀티 타임프레임 필터 (일봉 추세 + 1시간 RSI)

추가된 안정형 규칙:
A) 일일 손실 한도(실현손익) 도달 시 자동매매 중지
B) 연속 손실 N회 시 쿨다운
C) 하루 최대 거래 횟수 제한
D) 동일 마켓 재진입 쿨다운
E) (BUY) 최소 손익비(RR) 필터
"""

from dotenv import load_dotenv
load_dotenv()

import os
import csv
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

logger = logging.getLogger(__name__)

BASE_DIR_APP = Path("/app")
LOG_FILE = BASE_DIR_APP / "trade_log.csv" if BASE_DIR_APP.exists() else Path.home() / "Downloads" / "crypto_bot_v2" / "trade_log.csv"
FEE_RATE = 0.0005  # 업비트 수수료 0.05%
WON = "\u20A9"     # ₩ (원화 기호) - 직접 '₩' 사용을 피해서 SyntaxError 방지

# -------------------------
# 안정형 리스크 설정 (env로 조절)
# -------------------------
DAILY_MAX_LOSS_KRW = float(os.getenv("DAILY_MAX_LOSS_KRW", "50000"))     # 일일 실현손익 -5만원이면 중지
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "6"))           # 하루 최대 거래 횟수(주문 성공 기준)
LOSS_STREAK_LIMIT = int(os.getenv("LOSS_STREAK_LIMIT", "2"))             # 연속 손실 횟수
LOSS_STREAK_COOLDOWN_MIN = int(os.getenv("LOSS_STREAK_COOLDOWN_MIN", "180"))  # 연속손실 쿨다운(분)
MARKET_COOLDOWN_MIN = int(os.getenv("MARKET_COOLDOWN_MIN", "60"))        # 동일 마켓 재진입 쿨다운(분)
MIN_RR = float(os.getenv("MIN_RR", "1.5"))                               # (BUY) 최소 손익비
REFRESH_PRICE_BEFORE_ORDER = os.getenv("REFRESH_PRICE_BEFORE_ORDER", "0") == "1"  # 주문 직전 티커 재조회(정확도↑, 호출↑)


class TradingScheduler:
    def __init__(self, upbit_client, ai_analyzer):
        self.upbit = upbit_client
        self.analyzer = ai_analyzer
        self.running = False
        self._task = None
        self._chat_id = None
        self._app = None
        self._ensure_log_file()

        # ---- 안정형 상태값 ----
        self._day = datetime.now().date()
        self._daily_realized_pnl = 0.0   # SELL에서만 누적(실현손익)
        self._trades_today = 0           # BUY/SELL 주문 "성공" 횟수 누적
        self._loss_streak = 0            # 연속 손실 횟수
        self._paused_until = None        # 쿨다운 종료 시간(datetime)
        self._market_cooldown_until = {} # market -> datetime

    def _ensure_log_file(self):
        if not LOG_FILE.exists():
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
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
        self.running = True
        self._chat_id = chat_id
        self._app = app
        self._task = asyncio.create_task(self._loop())

    def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()

    async def _send(self, text: str):
        # parse_mode를 쓰면 Markdown 파싱 오류가 자주 나서 안정형은 기본 plain text로 보냄
        if self._app and self._chat_id:
            await self._app.bot.send_message(chat_id=self._chat_id, text=text)

    # -------------------------
    # 안정형 헬퍼
    # -------------------------
    def _reset_daily_if_needed(self):
        today = datetime.now().date()
        if today != self._day:
            self._day = today
            self._daily_realized_pnl = 0.0
            self._trades_today = 0
            self._loss_streak = 0
            self._paused_until = None
            self._market_cooldown_until.clear()

    def _is_paused(self) -> bool:
        if self._paused_until and datetime.now() < self._paused_until:
            return True
        self._paused_until = None
        return False

    def _fmt_won(self, x: float) -> str:
        try:
            return f"{WON}{float(x):,.0f}"
        except Exception:
            return f"{WON}{x}"

    def _calc_rr(self, current: float, target: float, stop: float) -> float | None:
        # RR = (target-current) / (current-stop)
        try:
            current = float(current)
            target = float(target)
            stop = float(stop)
            if current <= 0 or target <= 0 or stop <= 0:
                return None
            if not (stop < current < target):
                return None
            reward = target - current
            risk = current - stop
            if risk <= 0:
                return None
            return reward / risk
        except Exception:
            return None

    # -------------------------
    # 기존 필터(유지)
    # -------------------------
    async def _get_daily_trend(self, market: str) -> str:
        try:
            candles = await self.upbit.get_candles(market, unit=240, count=20)
            closes = [c["trade_price"] for c in reversed(candles)]
            if len(closes) < 10:
                return "NEUTRAL"
            sma10 = sum(closes[-10:]) / 10
            sma20 = sum(closes) / len(closes)
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
        passed = True

        # 이 함수는 프로젝트에 맞게 구현되어 있다는 가정(기존 유지)
        ob = await self.upbit.get_orderbook_analysis(market)
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

        ts = await self.upbit.get_trade_strength(market)
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
        rsi_1h = await self._get_1h_rsi(market)

        if action == "BUY":
            if daily_trend == "BEAR":
                messages.append("❌ 일봉 하락추세 (BEAR) → 역추세 매수 보류")
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
                messages.append("⚠️ 일봉 상승추세 (BULL) — 매도 신중히")
            if rsi_1h < 30:
                messages.append(f"⚠️ 1h RSI 과매도 ({rsi_1h}) — 매도 신중히")

        return passed, messages, imbalance, strength, daily_trend, rsi_1h

    # -------------------------
    # 메인 루프
    # -------------------------
    async def _loop(self):
        markets = [m.strip() for m in os.getenv("MARKETS", "KRW-SOL,KRW-DOGE,KRW-ADA").split(",") if m.strip()]
        trade_amount = float(os.getenv("TRADE_AMOUNT", "10000"))
        interval_min = int(os.getenv("INTERVAL_MIN", "60"))   # 안정형 기본 60분 추천
        min_conf = int(os.getenv("MIN_CONFIDENCE", "90"))

        await self._send(
            f"🤖 자동매매 시작 (안정형)\n"
            f"감시: {', '.join(markets)}\n"
            f"주기: {interval_min}분 | 금액: {self._fmt_won(trade_amount)} | 신뢰도: {min_conf}%+\n\n"
            f"적용 필터:\n"
            f"✅ 호가/유동성\n"
            f"✅ 체결강도\n"
            f"✅ 멀티 타임프레임(일봉+1h RSI)\n"
            f"✅ 실행 로그/PnL(trade_log.csv)\n\n"
            f"안정형 규칙:\n"
            f"🛑 일일 손실 한도: -{self._fmt_won(DAILY_MAX_LOSS_KRW)}\n"
            f"⛔ 하루 최대 거래: {MAX_TRADES_PER_DAY}회\n"
            f"⏸ 연속 손실 {LOSS_STREAK_LIMIT}회 → {LOSS_STREAK_COOLDOWN_MIN}분 쿨다운\n"
            f"🧊 동일 마켓 쿨다운: {MARKET_COOLDOWN_MIN}분\n"
            f"📏 (BUY) 최소 RR: {MIN_RR}"
        )

        while self.running:
            try:
                await self._run_cycle(markets, trade_amount, min_conf)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"스케줄러 오류: {e}")
                await self._send(f"⚠️ 스케줄러 오류: {e}")

            for _ in range(interval_min * 60):
                if not self.running:
                    return
                await asyncio.sleep(1)

    async def _run_cycle(self, markets, trade_amount, min_conf):
        self._reset_daily_if_needed()

        # 일일 손실 한도 도달 시 중지
        if self._daily_realized_pnl <= -DAILY_MAX_LOSS_KRW:
            await self._send(f"🛑 일일 손실 한도 도달: {self._fmt_won(self._daily_realized_pnl)} → 자동매매 중지")
            self.stop()
            return

        # 쿨다운 중이면 스킵
        if self._is_paused():
            return

        # 하루 거래 횟수 제한
        if self._trades_today >= MAX_TRADES_PER_DAY:
            await self._send(f"⛔ 오늘 거래 횟수 제한({MAX_TRADES_PER_DAY}회) 도달 → 자동매매 중지")
            self.stop()
            return

        now = datetime.now(KST).strftime("%H:%M")

        for market in markets:
            try:
                # 마켓 쿨다운 체크
                cd_until = self._market_cooldown_until.get(market)
                if cd_until and datetime.now() < cd_until:
                    continue

                # 1) AI 신호
                signal = await self.analyzer.should_trade(market, trade_amount)
                action = signal.get("action", "HOLD")
                confidence = signal.get("confidence", 0)
                reason = signal.get("reason", "")
                target = signal.get("target_price", 0) or 0
                stop_loss = signal.get("stop_loss", 0) or 0

                # 2) 현재가(항상 표시)
                ticker = await self.upbit.get_ticker(market)
                current_price = float(ticker["trade_price"])

                # 3) 메시지(현재가 | 목표 | 손절)
                emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(action, "⚪")
                log_msg = (
                    f"{emoji} [{now}] {market}\n"
                    f"가격: {WON}{current_price:,.0f} | 목표: {WON}{target:,.0f} | 손절: {WON}{stop_loss:,.0f}\n"
                    f"신호: {action} ({confidence}%)\n"
                    f"이유: {reason}"
                )

                # 4) HOLD/신뢰도 미달이면 종료
                if action == "HOLD" or confidence < min_conf:
                    if action != "HOLD":
                        log_msg += f"\n(신뢰도 부족: {confidence}% < {min_conf}%)"
                    await self._send(log_msg)
                    await asyncio.sleep(1)
                    continue

                # 5) (BUY) 최소 RR 필터
                if action == "BUY":
                    rr = self._calc_rr(current_price, target, stop_loss)
                    if rr is None or rr < MIN_RR:
                        log_msg += f"\n\n⛔ RR 미달 → 주문 보류 (RR={rr if rr is not None else 'N/A'} / 기준 {MIN_RR})"
                        await self._send(log_msg)
                        await asyncio.sleep(1)
                        continue

                # 6) 기존 필터
                passed, filter_msgs, imbalance, strength, trend, rsi_1h = await self._check_filters(market, action)
                log_msg += "\n\n[필터 결과]\n" + "\n".join(filter_msgs)

                if not passed:
                    log_msg += "\n\n⛔ 필터 불통과 — 주문 보류"
                    await self._send(log_msg)
                    self._write_log([
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        market, f"{action}(보류)", confidence,
                        "-", "-", "-", "-", "-", "-", "-", "-",
                        reason, imbalance, strength, trend, rsi_1h
                    ])
                    await asyncio.sleep(1)
                    continue

                # 7) 주문가격 결정(기본: 위에서 구한 현재가)
                order_price = current_price
                if REFRESH_PRICE_BEFORE_ORDER:
                    t2 = await self.upbit.get_ticker(market)
                    order_price = float(t2["trade_price"])

                # 8) 실제 주문
                if action == "BUY":
                    # (안정형) 이미 보유 중이면 추가 매수 방지(선택적)
                    try:
                        currency = market.split("-")[1]
                        balances = await self.upbit.get_balances()
                        if balances.get(currency, {}).get("balance", 0) > 0 or balances.get(currency, {}).get("locked", 0) > 0:
                            log_msg += "\n\n⚠️ 이미 보유 중 — 추가 매수 보류"
                            await self._send(log_msg)
                            await asyncio.sleep(1)
                            continue
                    except Exception:
                        pass

                    order = await self.upbit.market_order_buy(market, trade_amount)
                    uuid_str = order.get("uuid", "N/A")
                    quantity = round(trade_amount / order_price, 8)
                    fee = round(trade_amount * FEE_RATE, 0)

                    self._trades_today += 1
                    self._market_cooldown_until[market] = datetime.now() + timedelta(minutes=MARKET_COOLDOWN_MIN)

                    log_msg += (
                        f"\n\n✅ 매수 완료\n"
                        f"주문ID: {uuid_str}\n"
                        f"체결가: {self._fmt_won(order_price)}\n"
                        f"수량: {quantity}\n"
                        f"수수료: {self._fmt_won(fee)}"
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
                    volume = float(balances.get(currency, {}).get("balance", 0))
                    avg_buy = float(balances.get(currency, {}).get("avg_buy_price", 0))

                    if volume <= 0:
                        log_msg += "\n\n⚠️ 보유 수량 없음 — 매도 건너뜀"
                    else:
                        order = await self.upbit.market_order_sell(market, volume)
                        uuid_str = order.get("uuid", "N/A")

                        sell_amt = round(order_price * volume, 0)
                        buy_amt = round(avg_buy * volume, 0)
                        fee = round(sell_amt * FEE_RATE, 0)
                        pnl = round(sell_amt - buy_amt - fee, 0)
                        pnl_pct = round((pnl / buy_amt * 100) if buy_amt > 0 else 0, 2)
                        pnl_emoji = "📈" if pnl >= 0 else "📉"

                        # 안정형 상태 업데이트(SELL에서 실현손익 반영)
                        self._daily_realized_pnl += pnl
                        self._trades_today += 1
                        self._market_cooldown_until[market] = datetime.now() + timedelta(minutes=MARKET_COOLDOWN_MIN)

                        if pnl < 0:
                            self._loss_streak += 1
                            if self._loss_streak >= LOSS_STREAK_LIMIT:
                                self._paused_until = datetime.now() + timedelta(minutes=LOSS_STREAK_COOLDOWN_MIN)
                        else:
                            self._loss_streak = 0

                        log_msg += (
                            f"\n\n✅ 매도 완료\n"
                            f"주문ID: {uuid_str}\n"
                            f"체결가: {self._fmt_won(order_price)}\n"
                            f"수량: {volume}\n"
                            f"수수료: {self._fmt_won(fee)}\n"
                            f"{pnl_emoji} PnL: {('+' if pnl >= 0 else '')}{self._fmt_won(pnl)} ({pnl_pct:+.2f}%)\n"
                            f"오늘 실현손익: {self._fmt_won(self._daily_realized_pnl)}"
                        )

                        if self._paused_until:
                            left_min = int((self._paused_until - datetime.now()).total_seconds() // 60)
                            if left_min > 0:
                                log_msg += f"\n⏸ 연속 손실 {self._loss_streak}회 → {left_min}분 쿨다운"

                        self._write_log([
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            market, "SELL", confidence,
                            order_price, order_price, volume,
                            buy_amt, sell_amt, 0, fee, pnl,
                            reason, imbalance, strength, trend, rsi_1h
                        ])

                        # 일일 손실 한도 즉시 체크(SELL 후)
                        if self._daily_realized_pnl <= -DAILY_MAX_LOSS_KRW:
                            log_msg += f"\n\n🛑 일일 손실 한도 도달 → 자동매매 중지"
                            await self._send(log_msg)
                            self.stop()
                            return

                await self._send(log_msg)

            except Exception as e:
                await self._send(f"❌ {market} 처리 오류: {e}")

            await asyncio.sleep(1)
