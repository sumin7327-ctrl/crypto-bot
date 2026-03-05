"""
뉴스/공시 알림 모듈
- 업비트 공시 RSS 파싱
- Claude AI 요약
"""

from dotenv import load_dotenv
load_dotenv()

import os
import aiohttp
from datetime import datetime


class NewsWatcher:
    def __init__(self):
        self.url = "https://api.anthropic.com/v1/messages"

    def _headers(self):
        return {
            "x-api-key":         os.getenv("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        }

    async def get_latest(self) -> str:
        """업비트 공지 + 암호화폐 뉴스 요약"""
        try:
            notices   = await self._fetch_upbit_notices()
            ai_summary = await self._summarize(notices)
            return ai_summary
        except Exception as e:
            return f"❌ 뉴스 조회 실패: {e}"

    async def _fetch_upbit_notices(self) -> str:
        """업비트 공지사항 가져오기"""
        url = "https://api.upbit.com/v1/market/all"
        try:
            async with aiohttp.ClientSession() as session:
                # 업비트 공지 API (실제 공지 엔드포인트)
                notice_url = "https://api-manager.upbit.com/api/v1/notices?page=1&per_page=5"
                async with session.get(notice_url) as r:
                    if r.status == 200:
                        data = await r.json()
                        items = data.get("data", {}).get("list", [])
                        lines = []
                        for item in items[:5]:
                            lines.append(f"- {item.get('title', '')} ({item.get('created_at', '')[:10]})")
                        return "\n".join(lines) if lines else "최신 공지 없음"
                    return "공지 조회 실패"
        except Exception:
            return "공지 조회 실패"

    async def _summarize(self, notices: str) -> str:
        """Claude AI로 뉴스 요약"""
        prompt = (
            f"다음은 업비트 최신 공지사항입니다:\n\n{notices}\n\n"
            f"투자자 관점에서 중요한 내용을 한국어로 간결하게 요약해주세요. "
            f"주의해야 할 상장폐지, 거래지원 종료, 입출금 중단 등을 강조해주세요."
        )
        payload = {
            "model":      "claude-sonnet-4-20250514",
            "max_tokens": 500,
            "messages":   [{"role": "user", "content": prompt}],
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(self.url, headers=self._headers(), json=payload) as r:
                r.raise_for_status()
                data = await r.json()
                result = data["content"][0]["text"]
                return f"📰 *업비트 최신 공지 요약*\n\n{result}\n\n🕐 {datetime.now().strftime('%H:%M:%S')}"
