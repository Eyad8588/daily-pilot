import base64
import inspect
from datetime import date
from unittest.mock import MagicMock

from daily_pilot import google_api, mail
from daily_pilot.google_api import Email, GmailClient, SampleMail
from daily_pilot.llm import LLMError
from daily_pilot.tasks import Store

from .conftest import SAMPLES, FakeLLM

TODAY = date(2026, 11, 18)  # a Wednesday

CFG = {
    "create_drafts": True,
    "create_drafts_for": ["urgent", "action"],
    "signature_name": "Eyad",
    "rules": {"vip_senders": ["*@client-co.com"], "low_senders": ["noreply@medium.com"]},
}


def scripted_llm():
    """Classify sample emails by id the way a good model would."""
    answers = {
        "Production API is down": {"category": "urgent", "reason": "prod outage", "needs_reply": False,
                                   "tasks": [{"title": "Investigate API 503 outage", "due": "2026-11-18", "priority": 1, "est_minutes": 60}]},
        "Proposal review": {"category": "action", "reason": "client asks for review", "needs_reply": True,
                            "tasks": [{"title": "Review RAG proposal for Sara", "due": "2026-11-18", "priority": 1, "est_minutes": 45},
                                      {"title": "Confirm local-model setup on M-series", "due": "", "priority": 2, "est_minutes": 15}]},
        "Dependabot": {"category": "action", "reason": "critical dependency", "needs_reply": False,
                       "tasks": [{"title": "Upgrade pyyaml in rag-vault", "due": "", "priority": 1, "est_minutes": 20}]},
        "Slides": {"category": "fyi", "reason": "sharing slides", "needs_reply": False, "tasks": []},
        "Invoice": {"category": "action", "reason": "bill due", "needs_reply": False,
                    "tasks": [{"title": "Pay invoice INV-2291", "due": "2026-11-23", "priority": 2, "est_minutes": 10}]},
        "Dinner": {"category": "action", "reason": "RSVP", "needs_reply": True,
                   "tasks": [{"title": "Reply to Mona about dinner", "due": "3026-01-01", "priority": 3, "est_minutes": 5}]},
    }

    def fn(system, user):
        for key, ans in answers.items():
            if key in user:
                return ans
        raise AssertionError(f"unexpected email: {user[:200]}")

    return FakeLLM(fn, text="Hi Sara,\n\nThanks — I'll send comments by Wednesday.\n\nBest,\nEyad")


def run(tg, llm=None, cfg=CFG):
    store = Store(":memory:")
    src = SampleMail(SAMPLES / "emails.json")
    a = mail.MailAssistant(cfg, src, store, tg, llm if llm is not None else scripted_llm(), today=TODAY)
    return a, store, src, a.run()


def test_full_run_classifies_alerts_and_creates_tasks(tg):
    a, store, src, summary = run(tg)
    assert summary["urgent"] == 1 and summary["low"] == 1 and summary["fyi"] == 1 and summary["action"] == 4
    # exactly one Telegram alert, for the outage
    assert len(tg.sent) == 1 and "Production API is down" in tg.sent[0]
    titles = {t.title: t for t in store.open_tasks()}
    assert titles["Investigate API 503 outage"].priority == 1
    assert titles["Pay invoice INV-2291"].due == date(2026, 11, 23)
    assert titles["Reply to Mona about dinner"].due is None  # hallucinated far-future date dropped
    assert all(t.source == "gmail" for t in titles.values())


def test_drafts_only_for_needs_reply(tg):
    a, store, src, summary = run(tg)
    assert summary["drafts"] == 2
    assert {m for m, _ in src.drafts} == {"s2", "s7"}


def test_no_drafts_when_disabled(tg):
    a, store, src, summary = run(tg, cfg=CFG | {"create_drafts": False})
    assert summary["drafts"] == 0 and src.drafts == []


def test_second_run_skips_processed(tg):
    a, store, src, summary = run(tg)
    again = a.run()
    assert again["skipped"] == 7 and again["tasks"] == 0
    assert len(tg.sent) == 1


def test_low_sender_rule_skips_llm(tg):
    llm = scripted_llm()
    a, *_ = run(tg, llm=llm)
    assert not any("Medium Daily Digest" in u for _, u in llm.calls)


def test_vip_sender_never_fyi(tg):
    llm = FakeLLM(lambda s, u: {"category": "fyi", "reason": "meh", "needs_reply": False, "tasks": []})
    a = mail.MailAssistant(CFG, SampleMail(SAMPLES / "emails.json"), Store(":memory:"), tg, llm, today=TODAY)
    email = Email("x", "x", "Sara <sara@client-co.com>", "hello", None, "hi")
    assert a.triage(email).category == "action"


def test_llm_failure_falls_back_to_heuristics(tg):
    def broken(s, u):
        raise LLMError("down")

    a, store, src, summary = run(tg, llm=FakeLLM(broken))
    assert summary["urgent"] >= 1  # "immediately"/"down" keywords
    assert any("Production API" in m for m in tg.sent)


def test_prompt_marks_email_as_untrusted(tg):
    llm = scripted_llm()
    run(tg, llm=llm)
    system, user = llm.calls[0]
    assert "untrusted DATA" in system and "<email>" in user and "TODAY: 2026-11-18" in user


# ---------------------------------------------------------------- Gmail API wrapper (mocked)
def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def test_gmail_client_parses_messages():
    service = MagicMock()
    msgs = service.users.return_value.messages.return_value
    msgs.list.return_value.execute.return_value = {"messages": [{"id": "m1"}]}
    msgs.get.return_value.execute.return_value = {
        "id": "m1",
        "threadId": "t1",
        "snippet": "snip",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "Bob <bob@x.com>"},
                {"name": "Subject", "value": "Hi"},
                {"name": "Date", "value": "Tue, 17 Nov 2026 10:00:00 +0000"},
                {"name": "Message-ID", "value": "<abc@x.com>"},
            ],
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64("<p>html body</p>")}},
                {"mimeType": "text/plain", "body": {"data": _b64("plain body")}},
            ],
        },
    }
    [e] = GmailClient(service).list_new("in:inbox", 5)
    assert (e.id, e.thread_id, e.sender_email, e.subject, e.body) == ("m1", "t1", "bob@x.com", "Hi", "plain body")
    msgs.list.assert_called_once_with(userId="me", q="in:inbox", maxResults=5)


def test_html_only_body_is_stripped():
    payload = {"mimeType": "text/html", "body": {"data": _b64("<style>x{}</style><b>Hello</b> <i>world</i>")}}
    assert google_api.extract_body(payload) == "Hello world"


def test_gmail_create_draft_is_a_draft_reply():
    service = MagicMock()
    drafts = service.users.return_value.drafts.return_value
    drafts.create.return_value.execute.return_value = {"id": "d1"}
    e = Email("m1", "t1", "Bob <bob@x.com>", "Question", None, "", message_id_header="<abc@x.com>")
    assert GmailClient(service).create_draft(e, "Thanks!") == "d1"
    kwargs = drafts.create.call_args.kwargs
    assert kwargs["body"]["message"]["threadId"] == "t1"
    raw = base64.urlsafe_b64decode(kwargs["body"]["message"]["raw"]).decode()
    assert "Subject: Re: Question" in raw and "In-Reply-To: <abc@x.com>" in raw and "To: Bob <bob@x.com>" in raw
    service.users.return_value.messages.return_value.send.assert_not_called()


def test_project_never_sends_email():
    """Guardrail: no Gmail send call and no send scope anywhere."""
    for module in (google_api, mail):
        src = inspect.getsource(module)
        assert "messages().send" not in src and ".send(userId" not in src
        assert "drafts().send" not in src
    assert "https://www.googleapis.com/auth/gmail.send" not in google_api.SCOPES
    assert "https://mail.google.com/" not in google_api.SCOPES


def test_never_drafts_to_noreply_senders(tg):
    llm = FakeLLM(lambda s, u: {"category": "action", "reason": "x", "needs_reply": True, "tasks": []}, text="ok")
    a = mail.MailAssistant(CFG, SampleMail(SAMPLES / "emails.json"), Store(":memory:"), tg, llm, today=TODAY)
    a.run()
    drafted = {m for m, _ in a.mail.drafts}
    assert "s1" not in drafted and "s3" not in drafted  # alerts@pagerduty.com, noreply@github.com
    assert "s2" in drafted
