from datetime import datetime, timezone

import httpx
import yaml

from daily_pilot import news
from daily_pilot.llm import LLMError

from .conftest import ROOT, SAMPLES, FakeLLM

FEEDS = SAMPLES / "feeds"
CFG = yaml.safe_load((ROOT / "config" / "news.yaml").read_text())


def test_parse_rss_and_atom():
    rss = news.parse_feed((FEEDS / "hn.xml").read_bytes(), "HN")
    atom = news.parse_feed((FEEDS / "simonw.xml").read_bytes(), "Simon")
    assert len(rss) == 5 and len(atom) == 2
    assert rss[0].published == datetime(2026, 11, 17, 21, 0, tzinfo=timezone.utc)
    assert atom[0].link == "https://example.com/simonw/drafts-only"


def test_dedupe_keeps_higher_weight_and_filters_old():
    items = news.fetch_sources(CFG["sources"], sample_dir=FEEDS)
    titles = [i.title for i in news.dedupe_and_filter(items, 24 * 365 * 50)]
    assert titles.count("Running a 9B model at 60 tok/s on an M5 MacBook with MLX") == 1
    recent = news.dedupe_and_filter(items, 6, now=datetime(2026, 11, 18, 0, 0, tzinfo=timezone.utc))
    assert {i.title for i in recent} <= {
        "Running a 9B model at 60 tok/s on an M5 MacBook with MLX",
        "Prompt injection in MCP tool descriptions: a field study",
        "Agents that only draft: a safer pattern for email assistants",
    }


def test_digest_with_llm_ranks_by_score():
    def scorer(system, user):
        # Score by keyword so the test is deterministic.
        out = []
        for line in user.splitlines():
            if line.startswith("["):
                idx = int(line[1 : line.index("]")])
                score = 9 if ("MLX" in line or "MCP" in line) else (1 if "sourdough" in line or "keyboard" in line else 5)
                out.append({"id": idx, "score": score, "summary": f"summary {idx}"})
        return {"items": out}

    text, top = news.build_digest(CFG, FakeLLM(scorer), sample_dir=FEEDS)
    assert top[0].score >= top[-1].score
    assert all("sourdough" not in t.title.lower() for t in top)  # below min_score
    assert "MLX" in top[0].title or "MCP" in top[0].title
    assert text.startswith("<b>📰 AI news digest</b>")
    assert '<a href="https://example.com/' in text


def test_digest_survives_llm_failure():
    def broken(system, user):
        raise LLMError("ollama down")

    text, top = news.build_digest(CFG, FakeLLM(broken), sample_dir=FEEDS)
    assert top, "keyword fallback should still produce items"
    assert all(t.summary for t in top)


def test_digest_without_llm():
    text, top = news.build_digest(CFG, None, sample_dir=FEEDS)
    assert 0 < len(top) <= CFG["top_n"]


def test_fetch_over_http_and_skip_broken_feed():
    hn = (FEEDS / "hn.xml").read_bytes()

    def handler(request):
        if "good" in str(request.url):
            return httpx.Response(200, content=hn)
        return httpx.Response(500)

    sources = [{"name": "good", "url": "https://good.example/rss"}, {"name": "bad", "url": "https://bad.example/rss"}]
    items = news.fetch_sources(sources, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(items) == 5 and {i.source for i in items} == {"good"}


def test_format_escapes_html():
    item = news.NewsItem("A <b> & co", 'https://x.example/?a=1&b="2"', "S<", None, "", summary="1 < 2")
    text = news.format_digest([item], "T")
    assert "A &lt;b&gt; &amp; co" in text and "1 &lt; 2" in text
    assert 'href="https://x.example/?a=1&amp;b=&quot;2&quot;"' in text


def test_interest_terms_and_keyword_score():
    terms = news.interest_terms(["local LLMs / Ollama / MLX", "Apple Silicon ML"])
    assert {"llm", "ollama", "mlx", "apple", "silicon"} <= terms
    hit = news.NewsItem("Ollama on Apple Silicon", "u", "s", None, "fast local LLMs")
    miss = news.NewsItem("Sourdough", "u", "s", None, "bread")
    assert news.keyword_score(hit, ["local LLMs / Ollama", "Apple Silicon"]) > news.keyword_score(miss, ["local LLMs / Ollama"])
