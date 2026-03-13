"""
=============================================================================
텔레그램 알림 모듈
=============================================================================
"""

import logging
import requests

import config

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """텔레그램 알림 전송"""

    def __init__(self):
        self.token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)

    def send(self, message: str):
        """메시지 전송"""
        if not self.enabled:
            return

        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.warning(f"텔레그램 전송 실패: {response.text}")
        except Exception as e:
            logger.warning(f"텔레그램 전송 오류: {e}")

    # =========================================================================
    # 포맷팅된 알림
    # =========================================================================
    def notify_signal(self, signal: dict, filter_info: str = ""):
        """시그널 발생 알림"""
        msg = (
            f"🔔 <b>시그널 발생</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"심볼: <b>{signal['symbol']}</b>\n"
            f"방향: <b>{signal['signal']}</b>\n"
            f"전략: {signal['strategy']}\n"
            f"진입가: {signal['entry_price']:.4f}\n"
            f"손절가: {signal['sl_price']:.4f}\n"
        )
        if signal.get("tp1_price"):
            msg += f"익절가: {signal['tp1_price']:.4f}\n"
        msg += f"레버리지: {signal['leverage']}x\n"
        msg += f"사유: {signal['reason']}\n"
        if filter_info:
            msg += f"필터: {filter_info}\n"
        self.send(msg)

    def notify_entry(self, signal: dict, quantity: float):
        """진입 완료 알림"""
        msg = (
            f"✅ <b>포지션 진입</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"심볼: <b>{signal['symbol']}</b>\n"
            f"방향: <b>{signal['signal']}</b>\n"
            f"수량: {quantity:.6f}\n"
            f"진입가: {signal['entry_price']:.4f}\n"
            f"손절가: {signal['sl_price']:.4f}\n"
        )
        self.send(msg)

    def notify_exit(self, symbol: str, side: str, reason: str, pnl: float):
        """청산 알림"""
        emoji = "💰" if pnl >= 0 else "💸"
        msg = (
            f"{emoji} <b>포지션 청산</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"심볼: <b>{symbol}</b>\n"
            f"방향: {side}\n"
            f"사유: {reason}\n"
            f"손익: <b>{pnl:+.2f} USDT</b>\n"
        )
        self.send(msg)

    def notify_status(self, stats: dict):
        """상태 요약 알림"""
        msg = (
            f"📊 <b>봇 상태</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"잔고: {stats['balance']:.2f} USDT\n"
            f"포지션: {stats['open_positions']}개\n"
            f"일일 PnL: {stats['daily_pnl']:+.2f}\n"
            f"주간 PnL: {stats['weekly_pnl']:+.2f}\n"
            f"연패: {stats['consecutive_losses']}회\n"
            f"총 거래: {stats['total_trades']}회\n"
        )
        if stats["is_halted"]:
            msg += "⚠️ <b>매매 중단 상태</b>\n"
        self.send(msg)

    def notify_error(self, error: str):
        """에러 알림"""
        msg = f"🚨 <b>에러 발생</b>\n{error}"
        self.send(msg)
