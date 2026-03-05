"""
🤖 업비트 자동매매 텔레그램 봇 v2
- 완전 자동매매
- AI 매매 신호 분석
- 거래량 급등 감지
- 포트폴리오 수익률 추적
- 뉴스/공시 알림
- 버튼 + 텍스트 명령어 모두 지원
"""

from dotenv import load_dotenv
load_dotenv()

import os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from upbit_client import UpbitClient
from ai_analyzer import AIAnalyzer
from scheduler import TradingScheduler
from news_watcher import NewsWatcher

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
ALLOWED_USER_IDS = list(map(int, os.getenv("ALLOWED_USER_IDS", "0").split(",")))

upbit     = UpbitClient()
analyzer  = AIAnalyzer()
scheduler = TradingScheduler(upbit, analyzer)
news      = NewsWatcher()


# ── 인증 데코레이터 ───────────────────────────────────────────
def authorized(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ALLOWED_USER_IDS:
            await update.message.reply_text("⛔ 접근 권한이 없습니다.")
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 시세 조회",        callback_data="price"),
         InlineKeyboardButton("🤖 AI 분석",          callback_data="analyze")],
        [InlineKeyboardButton("💼 잔고/수익률",       callback_data="portfolio"),
         InlineKeyboardButton("📋 미체결 주문",       callback_data="orders")],
        [InlineKeyboardButton("📊 호가/체결강도",    callback_data="orderbook"),
         InlineKeyboardButton("🚨 거래량 급등 감지",  callback_data="hotcoins")],
        [InlineKeyboardButton("🟢 자동매매 ON",       callback_data="auto_on"),
         InlineKeyboardButton("🔴 자동매매 OFF",      callback_data="auto_off")],
        [InlineKeyboardButton("⚙️ 설정",             callback_data="settings")],
    ])


# ── /start ────────────────────────────────────────────────────
@authorized
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = "🟢 실행 중" if scheduler.running else "🔴 중지됨"
    await update.message.reply_text(
        f"🤖 *업비트 자동매매 봇 v2*\n\n자동매매 상태: {status}\n\n원하는 기능을 선택하세요:",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


# ── /price [마켓] ─────────────────────────────────────────────
@authorized
async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    market = context.args[0].upper() if context.args else "KRW-BTC"
    await update.message.reply_text(f"⏳ {market} 시세 조회 중...")
    try:
        t = await upbit.get_ticker(market)
        ind = await upbit.get_indicators(market)
        change = t.get("signed_change_rate", 0) * 100
        msg = (
            f"📊 *{market} 시세*\n\n"
            f"💰 현재가: `₩{t['trade_price']:,.0f}`\n"
            f"📈 고가:   `₩{t['high_price']:,.0f}`\n"
            f"📉 저가:   `₩{t['low_price']:,.0f}`\n"
            f"🔄 변동률: `{change:+.2f}%`\n"
            f"📦 거래대금: `₩{t.get('acc_trade_price_24h', 0):,.0f}`\n\n"
            f"📊 *기술적 지표*\n"
            f"RSI(14): `{ind['rsi']}`\n"
            f"SMA20: `₩{ind['sma20']:,.0f}`\n"
            f"SMA50: `₩{ind['sma50']:,.0f}`\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")


# ── /analyze [마켓] ───────────────────────────────────────────
@authorized
async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    market = context.args[0].upper() if context.args else "KRW-BTC"
    msg = await update.message.reply_text(f"🤖 AI가 {market} 분석 중...")
    try:
        result = await analyzer.analyze_symbol(market)
        await msg.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ 분석 실패: {e}")


async def _get_orderbook_text(market: str) -> str:
    ob  = await upbit.get_orderbook_analysis(market)
    ts  = await upbit.get_trade_strength(market)

    imbalance     = ob["imbalance"]
    imbal_emoji   = "🟢 매수 우세" if imbalance > 1.2 else ("🔴 매도 우세" if imbalance < 0.8 else "⚪ 균형")
    strength_emoji = "🟢" if ts["strength"] > 55 else ("🔴" if ts["strength"] < 45 else "⚪")

    lines = [
        f"📊 *{market} 호가/유동성 분석*\n",
        f"💰 최우선 매도: `₩{ob['best_ask']:,.0f}`",
        f"💰 최우선 매수: `₩{ob['best_bid']:,.0f}`",
        f"📏 스프레드: `₩{ob['spread']:,.0f}` ({ob['spread_pct']:.4f}%)\n",
        f"📦 매도 잔량(10호가): `{ob['total_ask']:.4f}`",
        f"📦 매수 잔량(10호가): `{ob['total_bid']:.4f}`",
        f"⚖️ 호가 불균형: `{ob['imbalance']}` → {imbal_emoji}\n",
    ]

    if ob["walls"]:
        lines.append("🧱 *호가 벽 감지:*")
        for w in ob["walls"]:
            lines.append(f"  • {w}")
        lines.append("")

    lines += [
        f"🔥 *체결강도 (최근 100건)*",
        f"{strength_emoji} 매수 체결: `{ts['strength']}%` / 매도: `{100 - ts['strength']:.1f}%`",
        f"🐋 대량 체결: `{ts['big_trades']}건` ({ts['big_trade_vol']:.4f}개)",
    ]

    return "\n".join(lines)


# ── /orderbook [마켓] ─────────────────────────────────────────
@authorized
async def orderbook_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    market = context.args[0].upper() if context.args else "KRW-BTC"
    msg = await update.message.reply_text(f"⏳ {market} 호가 분석 중...")
    try:
        result = await _get_orderbook_text(market)
        await msg.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ 오류: {e}")


# ── /portfolio ────────────────────────────────────────────────
@authorized
async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 포트폴리오 조회 중...")
    try:
        result = await _get_portfolio_text()
        await update.message.reply_text(result, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ 조회 실패: {e}")


async def _get_portfolio_text() -> str:
    balances = await upbit.get_balances()
    lines = ["💼 *포트폴리오 현황*\n"]
    total_buy  = 0
    total_eval = 0

    for currency, info in balances.items():
        bal = info["balance"]
        avg = info["avg_buy_price"]
        if currency == "KRW":
            lines.append(f"💵 KRW: `₩{bal:,.0f}`")
            total_eval += bal
            continue
        try:
            market = f"KRW-{currency}"
            ticker = await upbit.get_ticker(market)
            current = ticker["trade_price"]
            buy_val  = avg * bal
            eval_val = current * bal
            pnl      = eval_val - buy_val
            pnl_pct  = (pnl / buy_val * 100) if buy_val > 0 else 0
            emoji    = "📈" if pnl >= 0 else "📉"
            lines.append(
                f"{emoji} *{currency}*: `{bal}`개\n"
                f"   평단 ₩{avg:,.0f} → 현재 ₩{current:,.0f}\n"
                f"   평가손익: `{'+' if pnl >= 0 else ''}₩{pnl:,.0f}` ({pnl_pct:+.2f}%)"
            )
            total_buy  += buy_val
            total_eval += eval_val
        except Exception:
            lines.append(f"• {currency}: `{bal}`")

    if total_buy > 0:
        total_pnl     = total_eval - total_buy
        total_pnl_pct = (total_pnl / total_buy * 100)
        lines.append(
            f"\n📊 *총 평가손익*\n"
            f"`{'+' if total_pnl >= 0 else ''}₩{total_pnl:,.0f}` ({total_pnl_pct:+.2f}%)"
        )
    return "\n".join(lines)


# ── /hotcoins ─────────────────────────────────────────────────
@authorized
async def hotcoins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 전체 마켓 스캔 중... (30초 정도 걸려요)")
    try:
        result = await _get_hotcoins_text()
        await msg.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ 스캔 실패: {e}")


async def _get_hotcoins_text() -> str:
    surge_list = await upbit.get_volume_surge(multiplier=3.0)
    if not surge_list:
        return "📊 현재 거래량 3배 이상 급등한 코인이 없어요."
    lines = ["🚨 *거래량 급등 코인 TOP 10*\n(평소 대비 3배 이상)\n"]
    for i, coin in enumerate(surge_list, 1):
        emoji = "📈" if coin["change_rate"] > 0 else "📉"
        lines.append(
            f"{i}. *{coin['market']}*\n"
            f"   💰 ₩{coin['current_price']:,.0f} {emoji} {coin['change_rate']:+.2f}%\n"
            f"   🔥 거래량: 평소의 *{coin['volume_ratio']}배*\n"
        )
    return "\n".join(lines)


# ── /news ─────────────────────────────────────────────────────
@authorized
async def news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📰 뉴스/공시 수집 중...")
    try:
        result = await news.get_latest()
        await msg.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ 뉴스 조회 실패: {e}")


# ── /buy /sell ────────────────────────────────────────────────
@authorized
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("사용법: /buy KRW-BTC 10000")
        return
    market, amount = context.args[0].upper(), context.args[1]
    keyboard = [[
        InlineKeyboardButton("✅ 확인 매수", callback_data=f"confirm_buy_{market}_{amount}"),
        InlineKeyboardButton("❌ 취소",      callback_data="cancel"),
    ]]
    await update.message.reply_text(
        f"⚠️ *매수 확인*\n\n마켓: `{market}`\n금액: `₩{float(amount):,.0f}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


@authorized
async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("사용법: /sell KRW-BTC 0.0001")
        return
    market, volume = context.args[0].upper(), context.args[1]
    keyboard = [[
        InlineKeyboardButton("✅ 확인 매도", callback_data=f"confirm_sell_{market}_{volume}"),
        InlineKeyboardButton("❌ 취소",      callback_data="cancel"),
    ]]
    await update.message.reply_text(
        f"⚠️ *매도 확인*\n\n마켓: `{market}`\n수량: `{volume}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── /auto ─────────────────────────────────────────────────────
@authorized
async def auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        status = "🟢 실행 중" if scheduler.running else "🔴 중지됨"
        await update.message.reply_text(f"자동매매 상태: {status}")
        return
    cmd = context.args[0].lower()
    if cmd == "on":
        scheduler.start(update.effective_chat.id, context.application)
        await update.message.reply_text("🟢 자동매매가 시작되었습니다!")
    elif cmd == "off":
        scheduler.stop()
        await update.message.reply_text("🔴 자동매매가 중지되었습니다.")


# ── /help ─────────────────────────────────────────────────────
@authorized
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *명령어 목록*\n\n"
        "/start — 메인 메뉴\n"
        "/price [마켓] — 시세 + 기술적 지표\n"
        "/analyze [마켓] — AI 매매 신호 분석\n"
        "/orderbook [마켓] — 호가/체결강도 분석\n"
        "/portfolio — 잔고 + 수익률\n"
        "/hotcoins — 거래량 급등 코인\n"
        "/news — 최신 뉴스/공시\n"
        "/buy [마켓] [금액] — 시장가 매수\n"
        "/sell [마켓] [수량] — 시장가 매도\n"
        "/auto on|off — 자동매매 제어\n"
        "/help — 도움말",
        parse_mode="Markdown",
    )


# ── 버튼 핸들러 ───────────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "price":
        markets = os.getenv("MARKETS", "KRW-SOL,KRW-DOGE,KRW-ADA").split(",")
        all_markets = list(dict.fromkeys(markets + ["KRW-BTC", "KRW-ETH", "KRW-XRP"]))
        buttons = []
        row = []
        for m in all_markets:
            coin = m.replace("KRW-", "")
            row.append(InlineKeyboardButton(coin, callback_data=f"price_{m}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 메인으로", callback_data="back")])
        await query.edit_message_text(
            "📈 *시세 조회할 코인을 선택하세요:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("price_"):
        market = data.replace("price_", "")
        await query.edit_message_text(f"⏳ {market} 시세 조회 중...")
        try:
            t = await upbit.get_ticker(market)
            ind = await upbit.get_indicators(market)
            change = t.get("signed_change_rate", 0) * 100
            await query.edit_message_text(
                f"📊 *{market} 시세*\n\n"
                f"💰 현재가: `₩{t['trade_price']:,.0f}`\n"
                f"📈 고가:   `₩{t['high_price']:,.0f}`\n"
                f"📉 저가:   `₩{t['low_price']:,.0f}`\n"
                f"🔄 변동률: `{change:+.2f}%`\n"
                f"📦 거래대금: `₩{t.get('acc_trade_price_24h', 0):,.0f}`\n\n"
                f"📊 *기술적 지표*\n"
                f"RSI(14): `{ind['rsi']}`\n"
                f"SMA20: `₩{ind['sma20']:,.0f}`\n"
                f"SMA50: `₩{ind['sma50']:,.0f}`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 코인 선택으로", callback_data="price"),
                    InlineKeyboardButton("🏠 메인으로", callback_data="back"),
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 오류: {e}")

    elif data == "analyze":
        markets = os.getenv("MARKETS", "KRW-SOL,KRW-DOGE,KRW-ADA").split(",")
        # 감시 마켓 + 주요 코인 버튼 생성
        all_markets = list(dict.fromkeys(markets + ["KRW-BTC", "KRW-ETH", "KRW-XRP"]))
        buttons = []
        row = []
        for m in all_markets:
            coin = m.replace("KRW-", "")
            row.append(InlineKeyboardButton(coin, callback_data=f"analyze_{m}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 메인으로", callback_data="back")])
        await query.edit_message_text(
            "🤖 *AI 분석할 코인을 선택하세요:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("analyze_"):
        market = data.replace("analyze_", "")
        await query.edit_message_text(f"🤖 {market} AI 분석 중... (잠시만요)")
        try:
            result = await analyzer.analyze_symbol(market)
            await query.edit_message_text(
                result, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 코인 선택으로", callback_data="analyze"),
                    InlineKeyboardButton("🏠 메인으로", callback_data="back"),
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 분석 실패: {e}")

    elif data == "portfolio":
        await query.edit_message_text("⏳ 포트폴리오 조회 중...")
        try:
            result = await _get_portfolio_text()
            await query.edit_message_text(
                result, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 메인으로", callback_data="back")
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 조회 실패: {e}")

    elif data == "orders":
        try:
            orders = await upbit.get_open_orders()
            if not orders:
                text = "📋 미체결 주문이 없습니다."
            else:
                lines = ["📋 *미체결 주문*\n"]
                for o in orders:
                    side = "매수" if o["side"] == "bid" else "매도"
                    lines.append(f"• {o['market']} {side} {o.get('volume','?')} @ ₩{float(o.get('price',0)):,.0f}")
                text = "\n".join(lines)
            await query.edit_message_text(
                text, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 메인으로", callback_data="back")
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 오류: {e}")

    elif data == "orderbook":
        markets = os.getenv("MARKETS", "KRW-SOL,KRW-DOGE,KRW-ADA").split(",")
        all_markets = list(dict.fromkeys(markets + ["KRW-BTC", "KRW-ETH", "KRW-XRP"]))
        buttons = []
        row = []
        for m in all_markets:
            coin = m.replace("KRW-", "")
            row.append(InlineKeyboardButton(coin, callback_data=f"ob_{m}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 메인으로", callback_data="back")])
        await query.edit_message_text(
            "📊 *호가/체결강도 분석할 코인을 선택하세요:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("ob_"):
        market = data.replace("ob_", "")
        await query.edit_message_text(f"⏳ {market} 호가 분석 중...")
        try:
            result = await _get_orderbook_text(market)
            await query.edit_message_text(
                result, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 코인 선택으로", callback_data="orderbook"),
                    InlineKeyboardButton("🏠 메인으로", callback_data="back"),
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 오류: {e}")

    elif data == "hotcoins":
        await query.edit_message_text("🔍 전체 마켓 스캔 중... (30초 정도 걸려요)")
        try:
            result = await _get_hotcoins_text()
            await query.edit_message_text(
                result, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 메인으로", callback_data="back")
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 스캔 실패: {e}")

    elif data == "news":
        await query.edit_message_text("📰 뉴스/공시 수집 중...")
        try:
            result = await news.get_latest()
            await query.edit_message_text(
                result, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 메인으로", callback_data="back")
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 뉴스 조회 실패: {e}")

    elif data == "auto_on":
        scheduler.start(query.message.chat_id, context.application)
        markets      = os.getenv("MARKETS", "KRW-SOL,KRW-DOGE,KRW-ADA")
        trade_amount = os.getenv("TRADE_AMOUNT", "10000")
        interval_min = os.getenv("INTERVAL_MIN", "1440")
        min_conf     = os.getenv("MIN_CONFIDENCE", "85")
        await query.edit_message_text(
            f"🟢 *자동매매 시작!*\n\n"
            f"📌 감시 마켓: `{markets}`\n"
            f"⏱ 분석 주기: `{interval_min}분마다`\n"
            f"💰 1회 매수금액: `₩{float(trade_amount):,.0f}`\n"
            f"🎯 최소 신뢰도: `{min_conf}%`\n\n"
            f"*적용 필터:*\n"
            f"✅ 호가/유동성 필터\n"
            f"✅ 체결강도 필터\n"
            f"✅ 멀티 타임프레임 필터\n"
            f"✅ PnL 로그 추적\n\n"
            f"_AI가 신호를 감지하면 텔레그램으로 알려드려요!_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 메인으로", callback_data="back")
            ]])
        )

    elif data == "auto_off":
        scheduler.stop()
        await query.edit_message_text(
            "🔴 자동매매가 중지되었습니다.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 메인으로", callback_data="back")
            ]])
        )

    elif data == "settings":
        markets = os.getenv("MARKETS", "KRW-SOL,KRW-DOGE,KRW-ADA")
        amount  = os.getenv("TRADE_AMOUNT", "10000")
        interval = os.getenv("INTERVAL_MIN", "1440")
        confidence = os.getenv("MIN_CONFIDENCE", "90")
        await query.edit_message_text(
            f"⚙️ *현재 설정*\n\n"
            f"감시 마켓: `{markets}`\n"
            f"1회 매수금액: `₩{float(amount):,.0f}`\n"
            f"분석 주기: `{interval}분`\n"
            f"최소 신뢰도: `{confidence}%`\n\n"
            f"변경하려면 `.env` 파일을 수정하세요.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 메인으로", callback_data="back")
            ]])
        )

    elif data.startswith("confirm_buy_"):
        parts  = data.split("_")
        market, amount = parts[2], float(parts[3])
        try:
            result = await upbit.market_order_buy(market, amount)
            await query.edit_message_text(
                f"✅ *매수 완료*\n마켓: `{market}`\n금액: `₩{amount:,.0f}`\n주문ID: `{result.get('uuid','N/A')}`",
                parse_mode="Markdown",
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 매수 실패: {e}")

    elif data.startswith("confirm_sell_"):
        parts  = data.split("_")
        market, volume = parts[2], float(parts[3])
        try:
            result = await upbit.market_order_sell(market, volume)
            await query.edit_message_text(
                f"✅ *매도 완료*\n마켓: `{market}`\n수량: `{volume}`\n주문ID: `{result.get('uuid','N/A')}`",
                parse_mode="Markdown",
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 매도 실패: {e}")

    elif data == "back":
        status = "🟢 실행 중" if scheduler.running else "🔴 중지됨"
        await query.edit_message_text(
            f"🤖 *업비트 자동매매 봇 v2*\n\n자동매매 상태: {status}\n\n원하는 기능을 선택하세요:",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )

    elif data == "cancel":
        await query.edit_message_text("❌ 취소되었습니다.")


# ── 텍스트 채팅 → AI ──────────────────────────────────────────
@authorized
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = await analyzer.chat(update.message.text)
    await update.message.reply_text(reply, parse_mode="Markdown")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """오래된 버튼 클릭 등 무시할 수 있는 오류 처리"""
    error = str(context.error)
    if "Query is too old" in error or "query id is invalid" in error:
        return  # 조용히 무시
    logger.error(f"오류 발생: {context.error}")


# ── 메인 ─────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",     start))
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("price",     price))
    app.add_handler(CommandHandler("analyze",   analyze))
    app.add_handler(CommandHandler("orderbook",  orderbook_cmd))
    app.add_handler(CommandHandler("portfolio", portfolio))
    app.add_handler(CommandHandler("hotcoins",  hotcoins))
    app.add_handler(CommandHandler("news",      news_cmd))
    app.add_handler(CommandHandler("buy",       buy))
    app.add_handler(CommandHandler("sell",      sell))
    app.add_handler(CommandHandler("auto",      auto))
    app.add_handler(CommandHandler("help",      help_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    logger.info("🤖 업비트 자동매매 봇 v2 시작!")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
