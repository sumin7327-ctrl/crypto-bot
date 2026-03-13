"""
=============================================================================
헬스체크 & 모니터링
=============================================================================
cron으로 주기적 실행하여 봇 상태 확인 및 비정상 시 재시작

사용법:
    python monitor.py              # 상태 확인
    python monitor.py --restart    # 비정상 시 재시작

crontab 등록 (5분마다 체크):
    */5 * * * * /root/trading-bot/venv/bin/python /root/trading-bot/monitor.py --restart >> /var/log/trading-bot-monitor.log 2>&1
=============================================================================
"""

import argparse
import subprocess
import sys
import os
import json
from datetime import datetime, timedelta

import requests

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config


def check_service_status() -> dict:
    """systemd 서비스 상태 확인"""
    result = subprocess.run(
        ["systemctl", "is-active", "trading-bot"],
        capture_output=True, text=True,
    )
    is_active = result.stdout.strip() == "active"

    # 최근 로그 확인
    log_result = subprocess.run(
        ["tail", "-5", "/var/log/trading-bot.log"],
        capture_output=True, text=True,
    )

    # 로그 파일 최종 수정 시간
    log_file = "/root/trading-bot/trading_bot.log"
    log_stale = True
    if os.path.exists(log_file):
        mtime = datetime.fromtimestamp(os.path.getmtime(log_file))
        log_stale = (datetime.now() - mtime) > timedelta(minutes=10)

    return {
        "service_active": is_active,
        "recent_logs": log_result.stdout,
        "log_stale": log_stale,
    }


def check_binance_connection() -> bool:
    """바이낸스 API 연결 확인"""
    try:
        response = requests.get(
            "https://fapi.binance.com/fapi/v1/ping", timeout=5
        )
        return response.status_code == 200
    except Exception:
        return False


def check_positions_file() -> dict:
    """포지션 파일 상태 확인"""
    pos_file = "/root/trading-bot/positions.json"
    if os.path.exists(pos_file):
        with open(pos_file, "r") as f:
            positions = json.load(f)
        return {"exists": True, "count": len(positions)}
    return {"exists": False, "count": 0}


def send_telegram_alert(message: str):
    """텔레그램 알림"""
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=10)
    except Exception:
        pass


def restart_service():
    """서비스 재시작"""
    subprocess.run(["systemctl", "restart", "trading-bot"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart", action="store_true", help="비정상 시 자동 재시작")
    args = parser.parse_args()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{now}] 헬스체크 시작")

    # 1. 서비스 상태
    status = check_service_status()
    print(f"  서비스: {'✅ 활성' if status['service_active'] else '❌ 비활성'}")
    print(f"  로그 정체: {'⚠️ 예' if status['log_stale'] else '✅ 아니오'}")

    # 2. 바이낸스 연결
    binance_ok = check_binance_connection()
    print(f"  바이낸스: {'✅ 연결' if binance_ok else '❌ 연결 실패'}")

    # 3. 포지션 파일
    pos = check_positions_file()
    print(f"  포지션: {pos['count']}개 열림")

    # 4. 문제 감지 시 조치
    problems = []
    if not status["service_active"]:
        problems.append("서비스 중단됨")
    if status["log_stale"] and status["service_active"]:
        problems.append("로그 10분 이상 정체 (응답없음 의심)")
    if not binance_ok:
        problems.append("바이낸스 API 연결 실패")

    if problems:
        alert = (
            f"🚨 <b>봇 이상 감지</b>\n"
            f"시각: {now}\n"
            f"문제: {', '.join(problems)}\n"
        )
        print(f"\n  ⚠️ 문제 감지: {', '.join(problems)}")

        if args.restart and ("서비스 중단됨" in problems or "응답없음" in problems):
            print("  🔄 서비스 재시작 중...")
            restart_service()
            alert += "조치: 자동 재시작 실행\n"

        send_telegram_alert(alert)
    else:
        print("\n  ✅ 모든 항목 정상")


if __name__ == "__main__":
    main()
