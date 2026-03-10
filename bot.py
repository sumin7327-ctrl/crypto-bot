"""
바이낸스 선물거래 텔레그램 봇
- 실시간 가격 조회
- AI 매매 신호
- 자동 롱/숏 매매
- 포지션/잔고 조회
- 뉴스 요약
"""

import os
import logging
from dotenv import load_dotenv
load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

from binance_client import BinanceFuturesClient
from ai_analyzer import BinanceAIAnalyzer
from scheduler import BinanceScheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
ALLOWED_USER_IDS  = set(int(x) for x in os.getenv("ALLOWED_USER_IDS", "0").split(","))

client    = BinanceFuturesClient()
analyzer  = BinanceAIAnalyzer()
scheduler = BinanceScheduler(client, analyzer)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]


def authorized(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid not in ALLOWED_USER_IDS:
            await update.message.reply_text("❌ 권한 없음")
            return
        return await func(update, context)
    return wrapper


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 가격 조회", callback_data="price"),
         InlineKeyboardButton("📊 AI 신호", callback_data="signal")],
        [InlineKeyboardButton("📂 포지션", callback_data="position"),
         InlineKeyboardButton("💼 잔고", callback_data="balance")],
        [InlineKeyboardButton("🟢 자동매매 ON", callback_data="auto_on"),
         InlineKeyboardButton("🔴 자동매매 OFF", callback_data="auto_off")],
        [InlineKeyboardButton("📰 뉴스", callback_data="news"),
         InlineKeyboardButton("⚙️ 설정", callback_data="settings")],
    ])


def symbol_keyboard(action: str):
    buttons = []
    row = []
    for s in SYMBOLS:
        row.append(InlineKeyboardButton(s.replace("USDT",""), callback_data=f"{action}_{s}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 메인으로", callback_data="back")])
    return InlineKeyboardMarkup(buttons)


@authorized
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = "🟢 실행 중" if scheduler.running else "🔴 중지됨"
    await update.message.reply_text(
        f"⚡ *바이낸스 선물거래 봇*\n\n"
        f"자동매매: {status}\n"
        f"레버리지: `10x` | 교차마진\n\n"
        f"원하는 기능을 선택하세요:",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = str(context.error)
    if "Query is too old" in error or "query id is invalid" in error:
        return
    logger.error(f"오류: {context.error}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "price":
        await query.edit_message_text(
            "📊 *가격 조회할 코인 선택:*",
            parse_mode="Markdown",
            reply_markup=symbol_keyboard("price")
        )

    elif data.startswith("price_"):
        symbol = data.split("_")[1]
        try:
            ticker = await client.get_ticker(symbol)
            await query.edit_message_text(
                f"💰 *{symbol}*\n\n"
                f"현재가: `${ticker['price']:,.4f}`\n"
                f"24h 변동: `{ticker['change_pct']:+.2f}%`\n"
                f"24h 고가: `${ticker['high']:,.4f}`\n"
                f"24h 저가: `${ticker['low']:,.4f}`\n"
                f"거래량: `${ticker['quote_volume']:,.0f} USDT`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 메인으로", callback_data="back")
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 오류: {e}")

    elif data == "signal":
        await query.edit_message_text(
            "🤖 *AI 신호 분석할 코인 선택:*",
            parse_mode="Markdown",
            reply_markup=symbol_keyboard("signal")
        )

    elif data.startswith("signal_"):
        symbol = data.split("_")[1]
        await query.edit_message_text(f"🔍 `{symbol}` 분석 중...", parse_mode="Markdown")
        try:
            ticker    = await client.get_ticker(symbol)
            klines    = await client.get_klines(symbol, "1h", 100)
            orderbook = await client.get_orderbook(symbol)
            signal    = await analyzer.analyze(symbol, ticker, klines, orderbook)

            action     = signal.get("action", "HOLD")
            confidence = signal.get("confidence", 0)
            reason     = signal.get("reason", "")
            target     = signal.get("target_price", 0)
            stop_loss  = signal.get("stop_loss", 0)
            emoji = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "⚪"}.get(action, "⚪")

            buttons = []
            if action == "LONG":
                buttons.append([InlineKeyboardButton("🟢 롱 진입", callback_data=f"exec_long_{symbol}")])
            elif action == "SHORT":
                buttons.append([InlineKeyboardButton("🔴 숏 진입", callback_data=f"exec_short_{symbol}")])
            buttons.append([InlineKeyboardButton("🔙 메인으로", callback_data="back")])

            await query.edit_message_text(
                f"{emoji} *{symbol} AI 신호*\n\n"
                f"현재가: `${ticker['price']:,.4f}`\n"
                f"신호: `{action}` ({confidence}%)\n"
                f"근거: {reason}\n"
                f"목표가: `${target:,.4f}`\n"
                f"손절가: `${stop_loss:,.4f}`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 오류: {e}")

    elif data.startswith("exec_long_") or data.startswith("exec_short_"):
        parts  = data.split("_")
        side   = parts[1]
        symbol = parts[2]
        amount = float(os.getenv("TRADE_AMOUNT", "10000")) / 1400
        try:
            if side == "long":
                result = await client.open_long(symbol, amount)
            else:
                result = await client.open_short(symbol, amount)

            if result.get("orderId"):
                await query.edit_message_text(
                    f"✅ *{'롱' if side == 'long' else '숏'} 진입 완료*\n"
                    f"심볼: `{symbol}`\n"
                    f"주문ID: `{result['orderId']}`",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 메인으로", callback_data="back")
                    ]])
                )
            else:
                await query.edit_message_text(f"❌ 주문 실패: {result}")
        except Exception as e:
            await query.edit_message_text(f"❌ 오류: {e}")

    elif data == "position":
        try:
            positions = await client.get_positions()
            if not positions:
                msg = "📂 *현재 포지션 없음*"
            else:
                msg = "📂 *현재 포지션*\n\n"
                for p in positions:
                    pnl_emoji = "🟢" if p["pnl"] >= 0 else "🔴"
                    msg += (
                        f"*{p['symbol']}* `{p['side']}` {p['leverage']}x\n"
                        f"수량: `{p['size']}`\n"
                        f"진입가: `${p['entry_price']:,.4f}`\n"
                        f"현재가: `${p['mark_price']:,.4f}`\n"
                        f"손익: {pnl_emoji} `${p['pnl']:,.4f}` ({p['pnl_pct']:+.2f}%)\n\n"
                    )
            await query.edit_message_text(
                msg, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 메인으로", callback_data="back")
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 오류: {e}")

    elif data == "balance":
        try:
            bal = await client.get_balance()
            pnl_emoji = "🟢" if bal["pnl"] >= 0 else "🔴"
            await query.edit_message_text(
                f"💼 *계좌 잔고*\n\n"
                f"총 자산: `${bal['total']:,.2f} USDT`\n"
                f"사용 가능: `${bal['available']:,.2f} USDT`\n"
                f"미실현 손익: {pnl_emoji} `${bal['pnl']:,.4f} USDT`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 메인으로", callback_data="back")
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 오류: {e}")

    elif data == "auto_on":
        scheduler.start(query.message.chat_id, context.application)
        markets      = os.getenv("MARKETS", "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT")
        trade_amount = os.getenv("TRADE_AMOUNT", "10000")
        interval_min = os.getenv("INTERVAL_MIN", "15")
        min_conf     = os.getenv("MIN_CONFIDENCE", "85")
        await query.edit_message_text(
            f"🟢 *자동매매 시작!*\n\n"
            f"📌 감시: `{markets}`\n"
            f"⏱ 주기: `{interval_min}분마다`\n"
            f"💰 1회 금액: `₩{float(trade_amount):,.0f}`\n"
            f"🎯 신뢰도: `{min_conf}%+`\n"
            f"⚡ 레버리지: `10x` | 교차마진\n\n"
            f"_AI 신호 감지 시 텔레그램으로 알림!_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 메인으로", callback_data="back")
            ]])
        )

    elif data == "auto_off":
        scheduler.stop()
        await query.edit_message_text(
            "🔴 자동매매 중지됨",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 메인으로", callback_data="back")
            ]])
        )

    elif data == "news":
        await query.edit_message_text("📰 뉴스 분석 중...")
        try:
            reply = await analyzer.chat(
                "최근 비트코인, 이더리움, 솔라나, 리플 관련 주요 뉴스와 시장 동향을 한국어로 요약해줘. "
                "선물거래 관점에서 롱/숏 포지션에 영향을 줄 수 있는 내용 위주로."
            )
            await query.edit_message_text(
                f"📰 *시장 뉴스 요약*\n\n{reply}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 메인으로", callback_data="back")
                ]])
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 오류: {e}")

    elif data == "settings":
        markets  = os.getenv("MARKETS", "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT")
        amount   = os.getenv("TRADE_AMOUNT", "10000")
        interval = os.getenv("INTERVAL_MIN", "15")
        conf     = os.getenv("MIN_CONFIDENCE", "85")
        await query.edit_message_text(
            f"⚙️ *현재 설정*\n\n"
            f"감시 코인: `{markets}`\n"
            f"1회 금액: `₩{float(amount):,.0f}`\n"
            f"분석 주기: `{interval}분`\n"
            f"최소 신뢰도: `{conf}%`\n"
            f"레버리지: `10x`\n"
            f"마진: `교차마진`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 메인으로", callback_data="back")
            ]])
        )

    elif data == "back":
        status = "🟢 실행 중" if scheduler.running else "🔴 중지됨"
        await query.edit_message_text(
            f"⚡ *바이낸스 선물거래 봇*\n\n자동매매: {status}\n\n원하는 기능을 선택하세요:",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )


@authorized
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = await analyzer.chat(update.message.text)
    await update.message.reply_text(reply, parse_mode="Markdown")


async def post_init(application):
    chat_id = os.getenv("ALLOWED_USER_IDS", "").split(",")[0].strip()
    if chat_id:
        scheduler.start(int(chat_id), application)
        await application.bot.send_message(
            chat_id=int(chat_id),
            text="⚡ *바이낸스 선물봇 시작! 자동매매 ON* ✅",
            parse_mode="Markdown"
        )


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_error_handler(error_handler)
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    logger.info("⚡ 바이낸스 선물거래 봇 시작!")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
