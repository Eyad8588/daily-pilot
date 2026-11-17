"""Gmail + Google Calendar access (OAuth desktop flow) and offline sample stand-ins.

Safety: the Gmail wrapper exposes *read* and *create draft* only. There is no
send method anywhere in this project — drafts must be reviewed and sent by you.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",  # needed to create drafts
    "https://www.googleapis.com/auth/calendar.readonly",
]


# --------------------------------------------------------------------------- models
@dataclass
class Email:
    id: str
    thread_id: str
    sender: str
    subject: str
    date: datetime | None
    snippet: str
    body: str = ""
    to: str = ""
    message_id_header: str = ""
    labels: list[str] = field(default_factory=list)

    @property
    def sender_email(self) -> str:
        return parseaddr(self.sender)[1] or self.sender


@dataclass
class Event:
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    location: str = ""


class MailSource(Protocol):
    def list_new(self, query: str, max_results: int) -> list[Email]: ...

    def create_draft(self, reply_to: Email, body: str) -> str: ...


class CalendarSource(Protocol):
    def events_for_day(self, day: date, tz: tzinfo) -> list[Event]: ...


# --------------------------------------------------------------------------- auth
def get_credentials(credentials_file: Path, token_file: Path, interactive: bool = False):
    """Load cached OAuth credentials, refreshing them; run the browser flow if `interactive`."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif interactive:
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not credentials_file.exists():
            raise FileNotFoundError(
                f"OAuth client file not found: {credentials_file}. Download it from Google Cloud "
                "Console (APIs & Services → Credentials → OAuth client ID → Desktop app)."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
        creds = flow.run_local_server(port=0)
    else:
        raise RuntimeError("No valid Google token. Run `uv run daily-pilot auth google` first.")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    token_file.chmod(0o600)
    return creds


# --------------------------------------------------------------------------- Gmail
def _b64decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def extract_body(payload: dict[str, Any]) -> str:
    """Prefer text/plain; fall back to tag-stripped text/html."""
    plain: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and mime == "text/plain":
            plain.append(_b64decode(data))
        elif data and mime == "text/html":
            html_parts.append(_b64decode(data))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain).strip()
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", "\n".join(html_parts), flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def email_from_api(msg: dict[str, Any]) -> Email:
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    try:
        when = parsedate_to_datetime(headers["date"]) if "date" in headers else None
    except (TypeError, ValueError):
        when = None
    return Email(
        id=msg["id"],
        thread_id=msg.get("threadId", msg["id"]),
        sender=headers.get("from", ""),
        to=headers.get("to", ""),
        subject=headers.get("subject", "(no subject)"),
        date=when,
        snippet=msg.get("snippet", ""),
        body=extract_body(msg.get("payload", {})),
        message_id_header=headers.get("message-id", ""),
        labels=msg.get("labelIds", []),
    )


def build_reply(reply_to: Email, body: str, from_addr: str = "") -> EmailMessage:
    m = EmailMessage()
    m["To"] = reply_to.sender
    if from_addr:
        m["From"] = from_addr
    subj = reply_to.subject or ""
    m["Subject"] = subj if subj.lower().startswith("re:") else f"Re: {subj}"
    if reply_to.message_id_header:
        m["In-Reply-To"] = reply_to.message_id_header
        m["References"] = reply_to.message_id_header
    m.set_content(body)
    return m


class GmailClient:
    def __init__(self, service: Any) -> None:
        self.service = service

    @classmethod
    def from_settings(cls, settings) -> GmailClient:  # noqa: ANN001
        from googleapiclient.discovery import build

        creds = get_credentials(settings.google_credentials_file, settings.google_token_file)
        return cls(build("gmail", "v1", credentials=creds, cache_discovery=False))

    def list_new(self, query: str, max_results: int = 25) -> list[Email]:
        users = self.service.users()
        resp = users.messages().list(userId="me", q=query, maxResults=max_results).execute()
        out = []
        for ref in resp.get("messages", []):
            msg = users.messages().get(userId="me", id=ref["id"], format="full").execute()
            out.append(email_from_api(msg))
        return out

    def create_draft(self, reply_to: Email, body: str) -> str:
        raw = base64.urlsafe_b64encode(build_reply(reply_to, body).as_bytes()).decode()
        draft = (
            self.service.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": raw, "threadId": reply_to.thread_id}})
            .execute()
        )
        return draft["id"]


# --------------------------------------------------------------------------- Calendar
def _parse_event(item: dict[str, Any], tz: tzinfo) -> Event | None:
    start, end = item.get("start", {}), item.get("end", {})
    if "dateTime" in start:
        s = datetime.fromisoformat(start["dateTime"]).astimezone(tz)
        e = datetime.fromisoformat(end.get("dateTime", start["dateTime"])).astimezone(tz)
        return Event(item.get("summary", "(busy)"), s, e, False, item.get("location", ""))
    if "date" in start:
        d = date.fromisoformat(start["date"])
        s = datetime.combine(d, time.min, tz)
        return Event(item.get("summary", "(busy)"), s, s + timedelta(days=1), True, item.get("location", ""))
    return None


class CalendarClient:
    def __init__(self, service: Any, calendar_ids: list[str] | None = None) -> None:
        self.service = service
        self.calendar_ids = calendar_ids or ["primary"]

    @classmethod
    def from_settings(cls, settings, calendar_ids: list[str] | None = None) -> CalendarClient:  # noqa: ANN001
        from googleapiclient.discovery import build

        creds = get_credentials(settings.google_credentials_file, settings.google_token_file)
        return cls(build("calendar", "v3", credentials=creds, cache_discovery=False), calendar_ids)

    def events_for_day(self, day: date, tz: tzinfo) -> list[Event]:
        lo = datetime.combine(day, time.min, tz)
        hi = lo + timedelta(days=1)
        events: list[Event] = []
        for cal in self.calendar_ids:
            resp = (
                self.service.events()
                .list(
                    calendarId=cal,
                    timeMin=lo.isoformat(),
                    timeMax=hi.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            for item in resp.get("items", []):
                if item.get("status") == "cancelled":
                    continue
                ev = _parse_event(item, tz)
                if ev:
                    events.append(ev)
        return sorted(events, key=lambda e: e.start)


# --------------------------------------------------------------------------- samples
class SampleMail:
    """Reads `data/samples/emails.json`; drafts are kept in memory (and printed)."""

    def __init__(self, path: Path) -> None:
        self.items = json.loads(Path(path).read_text())
        self.drafts: list[tuple[str, str]] = []

    def list_new(self, query: str = "", max_results: int = 25) -> list[Email]:
        out = []
        for d in self.items[:max_results]:
            out.append(
                Email(
                    id=d["id"],
                    thread_id=d.get("thread_id", d["id"]),
                    sender=d["from"],
                    subject=d["subject"],
                    date=datetime.fromisoformat(d["date"]) if d.get("date") else None,
                    snippet=d.get("body", "")[:160],
                    body=d.get("body", ""),
                    message_id_header=f"<{d['id']}@sample>",
                )
            )
        return out

    def create_draft(self, reply_to: Email, body: str) -> str:
        draft_id = f"sample-draft-{len(self.drafts) + 1}"
        self.drafts.append((reply_to.id, body))
        return draft_id


class SampleCalendar:
    """Reads `data/samples/calendar.json` (times like "09:30") and places them on `day`."""

    def __init__(self, path: Path) -> None:
        self.items = json.loads(Path(path).read_text())

    def events_for_day(self, day: date, tz: tzinfo) -> list[Event]:
        out = []
        for d in self.items:
            if d.get("all_day"):
                s = datetime.combine(day, time.min, tz)
                out.append(Event(d["title"], s, s + timedelta(days=1), True, d.get("location", "")))
                continue
            s = datetime.combine(day, time.fromisoformat(d["start"]), tz)
            e = datetime.combine(day, time.fromisoformat(d["end"]), tz)
            out.append(Event(d["title"], s, e, False, d.get("location", "")))
        return sorted(out, key=lambda e: e.start)
