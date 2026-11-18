"""News digest: fetch RSS/Atom feeds → local LLM scores + summarizes → Telegram."""

from __future__ import annotations

import calendar
import html
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import feedparser
import httpx

from .llm import LLM, LLMError
from .telegram import esc

log = logging.getLogger(__name__)

USER_AGENT = "daily-pilot/0.1 (+personal RSS digest)"


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published: datetime | None
    snippet: str
    weight: float = 1.0
    score: float = 0.0
    summary: str = ""

    @property
    def key(self) -> str:
        norm = re.sub(r"\W+", " ", self.title.lower()).strip()
        return hashlib.sha1(norm.encode()).hexdigest()[:16]


def _clean(text: str, limit: int = 500) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def parse_feed(content: bytes | str, source: str, weight: float = 1.0) -> list[NewsItem]:
    parsed = feedparser.parse(content)
    items: list[NewsItem] = []
    for e in parsed.entries:
        title = _clean(e.get("title", ""), 300)
        link = e.get("link", "")
        if not title or not link:
            continue
        ts = e.get("published_parsed") or e.get("updated_parsed")
        published = datetime.fromtimestamp(calendar.timegm(ts), tz=timezone.utc) if ts else None
        snippet = _clean(e.get("summary", "") or e.get("description", ""))
        items.append(NewsItem(title, link, source, published, snippet, weight))
    return items


def fetch_sources(
    sources: list[dict[str, Any]],
    client: httpx.Client | None = None,
    sample_dir: Path | None = None,
) -> list[NewsItem]:
    """Fetch every enabled source. With `sample_dir`, read `<sample>` files offline."""
    client = client or httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    items: list[NewsItem] = []
    for src in sources:
        if not src.get("enabled", True):
            continue
        name, weight = src["name"], float(src.get("weight", 1.0))
        try:
            if sample_dir is not None:
                sample = src.get("sample")
                if not sample or not (sample_dir / sample).exists():
                    continue
                content: bytes = (sample_dir / sample).read_bytes()
            else:
                r = client.get(src["url"])
                r.raise_for_status()
                content = r.content
            got = parse_feed(content, name, weight)[: int(src.get("max_items", 30))]
            log.info("%s: %d items", name, len(got))
            items.extend(got)
        except Exception as e:  # one bad feed must not kill the digest
            log.warning("Skipping source %s: %s", name, e)
    return items


def dedupe_and_filter(
    items: list[NewsItem], lookback_hours: float, now: datetime | None = None
) -> list[NewsItem]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.astimezone()  # treat naive times as local time
    cutoff = now - timedelta(hours=lookback_hours)
    seen: dict[str, NewsItem] = {}
    for it in items:
        if it.published and it.published < cutoff:
            continue
        prev = seen.get(it.key)
        if prev is None:
            seen[it.key] = it
        elif it.weight > prev.weight or (it.weight == prev.weight and len(it.snippet) > len(prev.snippet)):
            if len(prev.snippet) > len(it.snippet):
                it.snippet = prev.snippet  # keep the richer description
            seen[it.key] = it
    return list(seen.values())


_STOP = {"and", "the", "for", "with", "from", "into", "model", "models", "open", "use", "tool"}


def interest_terms(interests: list[str]) -> set[str]:
    """Split interest phrases into distinctive words ("local LLMs / Ollama" → llms, ollama, ...)."""
    terms = set()
    for phrase in interests:
        for w in re.split(r"[^a-z0-9.+#-]+", phrase.lower()):
            w = w.strip(".")
            if len(w) >= 3 and w not in _STOP:
                terms.add(w.rstrip("s") if len(w) > 3 else w)
    return terms


def keyword_score(item: NewsItem, interests: list[str]) -> float:
    """Cheap relevance score 0-10 (used to pre-rank, and as fallback without an LLM)."""
    words = set(re.findall(r"[a-z0-9.+#-]+", f"{item.title} {item.snippet}".lower()))
    words |= {w.rstrip("s") for w in words if len(w) > 3}
    hits = len(interest_terms(interests) & words)
    return min(10.0, 2.0 + 1.5 * hits) * item.weight


SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 10},
                    "summary": {"type": "string"},
                },
                "required": ["id", "score", "summary"],
            },
        }
    },
    "required": ["items"],
}


def llm_score(items: list[NewsItem], interests: list[str], llm: LLM, batch_size: int = 8) -> None:
    """Fill `score` and `summary` on each item in place using the local model."""
    system = (
        "You are a news editor for an AI developer. For each article, give a relevance score "
        "0-10 for the reader's interests (10 = must read, 0 = irrelevant or clickbait) and a "
        "factual one-sentence summary (max 30 words) based only on the given text. "
        "Reply in JSON only."
    )
    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        listing = "\n".join(
            f"[{i}] ({it.source}) {it.title}\n    {it.snippet[:350]}" for i, it in enumerate(batch)
        )
        user = f"Reader interests: {', '.join(interests)}\n\nArticles:\n{listing}"
        try:
            result = llm.json(system, user, SCORE_SCHEMA)
            by_id = {int(r["id"]): r for r in result.get("items", []) if "id" in r}
        except (LLMError, KeyError, TypeError, ValueError) as e:
            log.warning("LLM scoring failed for a batch, using keyword fallback: %s", e)
            by_id = {}
        for i, it in enumerate(batch):
            r = by_id.get(i)
            if r:
                it.score = float(max(0, min(10, int(r.get("score", 0))))) * it.weight
                it.summary = str(r.get("summary", "")).strip() or it.snippet[:200]
            else:
                it.score = keyword_score(it, interests)
                it.summary = it.snippet[:200]


def build_digest(
    cfg: dict[str, Any],
    llm: LLM | None,
    sample_dir: Path | None = None,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> tuple[str, list[NewsItem]]:
    """Return (telegram_html, top_items)."""
    interests = list(cfg.get("interests", []))
    items = fetch_sources(cfg.get("sources", []), client=client, sample_dir=sample_dir)
    lookback = float(cfg.get("lookback_hours", 30))
    if sample_dir is not None:
        lookback = 24 * 365 * 50  # sample files have fixed dates
    items = dedupe_and_filter(items, lookback, now)
    # Pre-rank cheaply so the LLM only reads the most promising candidates.
    items.sort(key=lambda it: (keyword_score(it, interests), it.published or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    candidates = items[: int(cfg.get("max_candidates", 24))]
    if llm is not None and candidates:
        llm_score(candidates, interests, llm, int(cfg.get("batch_size", 8)))
    else:
        for it in candidates:
            it.score = keyword_score(it, interests)
            it.summary = it.snippet[:200]
    min_score = float(cfg.get("min_score", 4))
    top = [it for it in sorted(candidates, key=lambda it: it.score, reverse=True) if it.score >= min_score]
    top = top[: int(cfg.get("top_n", 8))]
    return format_digest(top, cfg.get("title", "AI news digest"), now), top


def format_digest(items: list[NewsItem], title: str, now: datetime | None = None) -> str:
    date = (now or datetime.now()).strftime("%a %d %b %Y")
    lines = [f"<b>📰 {esc(title)}</b> — {date}", ""]
    if not items:
        lines.append("Nothing important today. 🎉")
    for n, it in enumerate(items, 1):
        lines.append(f'{n}. <a href="{html.escape(it.link, quote=True)}">{esc(it.title)}</a> <i>({esc(it.source)})</i>')
        if it.summary:
            lines.append(f"   {esc(it.summary)}")
        lines.append("")
    return "\n".join(lines).strip()
