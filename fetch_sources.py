"""
오늘 발행된 경제/투자 관련 뉴스·커뮤니티 아이템을 수집한다.

주의: 이 스크립트는 실제 외부 사이트(뉴스 RSS, Reddit 등)에 네트워크로
접근해야 하므로, 외부 네트워크가 열려 있는 환경(자신의 PC, 서버,
Claude Code 등)에서 실행해야 한다.
"""
import time
import datetime as dt
from dataclasses import dataclass, asdict

import feedparser
import requests

from config import (
    NEWS_FEEDS,
    COMMUNITY_SOURCES,
    REQUEST_USER_AGENT,
    REQUEST_DELAY_SECONDS,
)


@dataclass
class Item:
    source: str
    source_type: str  # "news" | "community"
    region: str  # 소스의 지역 태그 (예: "Korea", "Middle East", "Global" 등)
    title: str
    summary: str  # 짧은 발췌 (본문 전체 금지)
    link: str
    published: str


def _today_str():
    return dt.datetime.utcnow().strftime("%Y-%m-%d")


def fetch_rss_feeds() -> list[Item]:
    items = []
    headers = {"User-Agent": REQUEST_USER_AGENT}
    for source_name, (url, region) in NEWS_FEEDS.items():
        try:
            # feedparser.parse(url)는 자체 타임아웃이 없어 응답이 느린
            # 서버를 만나면 무한정 멈출 수 있다. 반드시 requests로
            # 타임아웃을 걸어 먼저 받아온 뒤 그 내용만 파싱한다.
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            for entry in feed.entries[:20]:
                items.append(
                    Item(
                        source=source_name,
                        source_type="news",
                        region=region,
                        title=entry.get("title", ""),
                        # summary는 RSS가 제공하는 짧은 발췌만 사용 (본문 크롤링 금지)
                        summary=(entry.get("summary", "") or "")[:400],
                        link=entry.get("link", ""),
                        published=entry.get("published", ""),
                    )
                )
        except Exception as e:
            print(f"[WARN] {source_name} RSS 수집 실패: {e}")
        time.sleep(REQUEST_DELAY_SECONDS)
    return items


def fetch_community_sources() -> list[Item]:
    items = []
    headers = {"User-Agent": REQUEST_USER_AGENT}

    for source_name, url in COMMUNITY_SOURCES.items():
        try:
            if "reddit.com" in url:
                resp = requests.get(url, headers=headers, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                for child in data.get("data", {}).get("children", [])[:20]:
                    d = child.get("data", {})
                    items.append(
                        Item(
                            source=source_name,
                            source_type="community",
                            region="Global",
                            title=d.get("title", ""),
                            summary=(d.get("selftext", "") or "")[:400],
                            link="https://reddit.com" + d.get("permalink", ""),
                            published=dt.datetime.utcfromtimestamp(
                                d.get("created_utc", 0)
                            ).isoformat(),
                        )
                    )
            elif "hacker-news" in url:
                ids = requests.get(url, headers=headers, timeout=10).json()[:20]
                for story_id in ids:
                    item_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
                    d = requests.get(item_url, headers=headers, timeout=10).json()
                    items.append(
                        Item(
                            source=source_name,
                            source_type="community",
                            region="Global",
                            title=d.get("title", ""),
                            summary="",
                            link=d.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                            published=dt.datetime.utcfromtimestamp(
                                d.get("time", 0)
                            ).isoformat(),
                        )
                    )
        except Exception as e:
            print(f"[WARN] {source_name} 수집 실패: {e}")
        time.sleep(REQUEST_DELAY_SECONDS)
    return items


def fetch_today() -> list[dict]:
    """오늘 자 뉴스 + 커뮤니티 아이템을 모두 수집해서 dict 리스트로 반환."""
    all_items = fetch_rss_feeds() + fetch_community_sources()
    return [asdict(i) for i in all_items]


if __name__ == "__main__":
    import json

    items = fetch_today()
    out_path = f"raw_{_today_str()}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"{len(items)}개 아이템 수집 완료 → {out_path}")
