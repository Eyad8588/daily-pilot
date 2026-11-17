import io
import json

import httpx
import pytest

from daily_pilot.telegram import MAX_LEN, Telegram, TelegramError, esc, split_message


def test_esc_html():
    assert esc("a < b & <script>") == "a &lt; b &amp; &lt;script&gt;"


def test_split_short_message_unchanged():
    assert split_message("hello") == ["hello"]


def test_split_long_message_on_lines():
    text = "\n".join(f"line {i} " + "x" * 100 for i in range(200))
    chunks = split_message(text)
    assert len(chunks) > 1
    assert all(len(c) <= MAX_LEN for c in chunks)
    assert "".join(c.replace("\n", "") for c in chunks) == text.replace("\n", "")


def test_split_giant_single_line():
    chunks = split_message("y" * (MAX_LEN * 2 + 10))
    assert [len(c) for c in chunks] == [MAX_LEN, MAX_LEN, 10]


def test_dry_run_prints_and_sends_nothing():
    out = io.StringIO()

    def boom(request):  # any HTTP call is a failure
        raise AssertionError("network used in dry-run")

    t = Telegram("", "", dry_run=True, client=httpx.Client(transport=httpx.MockTransport(boom)), out=out)
    t.send("<b>hi</b>")
    assert "[dry-run]" in out.getvalue() and "<b>hi</b>" in out.getvalue()
    assert t.sent == ["<b>hi</b>"]


def test_live_requires_credentials():
    with pytest.raises(TelegramError):
        Telegram("", "", dry_run=False)


def test_live_posts_send_message():
    seen = []

    def handler(request: httpx.Request):
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {}})

    t = Telegram("123:ABC", "42", dry_run=False, client=httpx.Client(transport=httpx.MockTransport(handler)))
    t.send("hello", silent=True)
    url, body = seen[0]
    assert url == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert body["chat_id"] == "42" and body["text"] == "hello"
    assert body["parse_mode"] == "HTML" and body["disable_notification"] is True


def test_live_error_raises():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"ok": False, "description": "chat not found"}))
    )
    with pytest.raises(TelegramError, match="chat not found"):
        Telegram("t", "c", dry_run=False, client=client).send("x")


def test_discover_chat_ids():
    updates = {
        "ok": True,
        "result": [
            {"message": {"chat": {"id": 777, "first_name": "Eyad"}}},
            {"message": {"chat": {"id": 777, "first_name": "Eyad"}}},
        ],
    }
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=updates)))
    assert Telegram("t", "", client=client).discover_chat_ids() == [("777", "Eyad")]
