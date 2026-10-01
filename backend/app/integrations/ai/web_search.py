"""Bounded supplementary business search. Never fetch arbitrary model-supplied URLs."""
import os
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from backend.app.observability import event


async def search_business(query: str):
    key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if not key:
        return [], "网页搜索未配置；以下回答仅使用案例资料。"
    if not query.strip() or len(query) > 160:
        return [], "搜索词无效，未进行网页检索。"
    domains = [d.strip().lower() for d in os.getenv("TUTOR_SEARCH_DOMAINS", "stats.gov.cn,pbc.gov.cn,worldbank.org,imf.org,oecd.org").split(",") if d.strip()]
    if not domains:
        return [], "搜索来源白名单为空，已跳过搜索。"
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
            response = await client.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {key}"},
                json={"query": query, "search_depth": "basic", "max_results": 3, "include_domains": domains,
                      "include_answer": False, "include_raw_content": False, "auto_parameters": False})
            response.raise_for_status()
            payload = response.json()
        sources = []
        for row in payload.get("results", [])[:3]:
            url = str(row.get("url") or "")
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            if parsed.scheme != "https" or parsed.username or parsed.password or not any(host == d or host.endswith("." + d) for d in domains):
                continue
            sources.append({"id": f"web_{len(sources) + 1}", "title": str(row.get("title") or host)[:200],
                "url": url, "retrieved_at": datetime.now().isoformat(), "content": str(row.get("content") or "")[:1500]})
        event("答疑Agent：网页检索完成", result_count=len(sources))
        return sources, "" if sources else "未检索到符合来源要求的网页；不补造网络信息。"
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        event("答疑Agent：网页检索暂不可用")
        return [], "网页搜索暂不可用；以下回答仅使用案例资料。"
