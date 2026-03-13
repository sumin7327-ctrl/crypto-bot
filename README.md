# 코인 선물거래 자동매매봇

바이낸스 선물 멀티전략 자동매매봇 (BTC, ETH, SOL, XRP)

## 아키텍처

```
시장 상태 분류기 (4H ADX 기반)
     │
     ├── 추세장 (ADX>25) ──→ 전략A: 추세추종 + 전략C: 브레이크아웃
     ├── 횡보장 (ADX<20) ──→ 전략B: 평균회귀
     └── 과변동장 ─────────→ 진입 중단
     │
     ▼
바이낸스 필터 (펀딩비 / OI / 롱숏비)
     │
     ▼
리스크 관리 (포지션 사이징 / 손실한도 / 연패관리)
     │
     ▼
주문 실행 + 포지션 관리 (트레일링 / 부분익절 / 시간청산)
```

## 전략 요약

| 전략 | 시장상태 | 타임프레임 | 레버리지 | 목표 승률 | 목표 손익비 |
|------|---------|-----------|---------|----------|-----------|
| A. 추세추종 | 추세장 | 4H+1H | 5x | 40-45% | 1:2.5~3.0 |
| B. 평균회귀 | 횡보장 | 15M | 3x | 60-70% | 1:1.0~1.5 |
| C. 브레이크아웃 | 추세장 | 1H | 4x | 35-40% | 1:3.0~5.0 |

## 설치

```bash
# 서버에서 (DigitalOcean 등)
git clone <repo-url> && cd trading-bot
bash deploy/deploy.sh

# 또는 수동 설치
pip install -r requirements.txt
cp .env.example .env
nano .env  # API 키 입력
```

## 실행

```bash
# 직접 실행
python main.py

# 서비스로 실행 (백그라운드)
systemctl start trading-bot
systemctl status trading-bot
tail -f /var/log/trading-bot.log
```

## 백테스트

실전 투입 전 반드시 백테스트를 실행하세요.

```bash
# 전체 전략 90일 백테스트
python backtest.py --symbol BTCUSDT --days 90 --strategy all

# 특정 전략만
python backtest.py --symbol ETHUSDT --days 60 --strategy mean_reversion

# 결과는 backtest_BTCUSDT_90d.json 으로 저장됩니다
```

## 모니터링

```bash
# 수동 상태 확인
python monitor.py

# cron 등록 (5분마다 자동 체크 + 재시작)
crontab -e
# 추가: */5 * * * * /root/trading-bot/venv/bin/python /root/trading-bot/monitor.py --restart >> /var/log/trading-bot-monitor.log 2>&1
```

## 파일 구조

```
trading-bot/
├── config.py                ← 전체 설정
├── main.py                  ← 메인 엔진
├── backtest.py              ← 백테스트
├── monitor.py               ← 모니터링
├── requirements.txt
├── .env.example
├── core/
│   ├── exchange.py          ← 바이낸스 API
│   ├── regime.py            ← 시장 상태 분류기
│   ├── risk_manager.py      ← 리스크 관리
│   ├── binance_filter.py    ← 펀딩비/OI/롱숏비
│   └── position_tracker.py  ← 포지션 트래커
├── strategies/
│   ├── trend_following.py   ← 전략 A
│   ├── mean_reversion.py    ← 전략 B
│   └── breakout.py          ← 전략 C
├── utils/
│   ├── indicators.py        ← 기술적 지표
│   └── notifier.py          ← 텔레그램 알림
└── deploy/
    ├── deploy.sh            ← 배포 스크립트
    └── trading-bot.service  ← systemd 서비스
```

## 리스크 관리

- 거래당 리스크: 자본의 1.5%
- 동시 포지션: 최대 3개
- 일일 손실 한도: 5%
- 주간 손실 한도: 10%
- 3연패: 사이즈 50% 축소
- 5연패: 매매 자동 중단

## 주의사항

⚠️ **투자 위험 경고**: 이 봇은 참고용 도구이며, 모든 투자 결정과 손실에 대한 책임은 사용자에게 있습니다. 반드시 백테스트와 소액 테스트를 거친 후 사용하세요.
