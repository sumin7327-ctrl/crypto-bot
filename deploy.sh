#!/bin/bash
# =============================================================================
# DigitalOcean 서버 배포 스크립트
# =============================================================================
# 사용법: bash deploy.sh
# =============================================================================

set -e

echo "========================================="
echo "  코인 선물거래 자동매매봇 배포"
echo "========================================="

# 1. 시스템 패키지 업데이트
echo "[1/6] 시스템 업데이트..."
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv

# 2. 프로젝트 디렉토리 설정
echo "[2/6] 프로젝트 설정..."
PROJECT_DIR="/root/trading-bot"
mkdir -p $PROJECT_DIR
cp -r ./* $PROJECT_DIR/ 2>/dev/null || true
cd $PROJECT_DIR

# 3. 가상환경 생성 및 패키지 설치
echo "[3/6] Python 환경 설정..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. 환경변수 설정
echo "[4/6] 환경변수 확인..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "⚠️  .env 파일이 생성되었습니다."
    echo "    nano /root/trading-bot/.env 로 API 키를 설정하세요."
    echo ""
fi

# 5. systemd 서비스 등록
echo "[5/6] 서비스 등록..."
# ExecStart를 venv python으로 업데이트
cat > /etc/systemd/system/trading-bot.service << EOF
[Unit]
Description=Crypto Futures Trading Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/trading-bot
EnvironmentFile=/root/trading-bot/.env
ExecStart=/root/trading-bot/venv/bin/python /root/trading-bot/main.py
Restart=always
RestartSec=30
StandardOutput=append:/var/log/trading-bot.log
StandardError=append:/var/log/trading-bot-error.log
StartLimitIntervalSec=600
StartLimitBurst=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable trading-bot

# 6. 로그 로테이션 설정
echo "[6/6] 로그 로테이션 설정..."
cat > /etc/logrotate.d/trading-bot << EOF
/var/log/trading-bot.log
/var/log/trading-bot-error.log
/root/trading-bot/trading_bot.log
{
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    copytruncate
}
EOF

echo ""
echo "========================================="
echo "  배포 완료!"
echo "========================================="
echo ""
echo "다음 단계:"
echo "  1. API 키 설정: nano /root/trading-bot/.env"
echo "  2. 서비스 시작: systemctl start trading-bot"
echo "  3. 상태 확인:   systemctl status trading-bot"
echo "  4. 로그 확인:   tail -f /var/log/trading-bot.log"
echo "  5. 서비스 중지: systemctl stop trading-bot"
echo ""
echo "백테스트 실행:"
echo "  cd /root/trading-bot && source venv/bin/activate"
echo "  python backtest.py --symbol BTCUSDT --days 90 --strategy all"
echo ""
