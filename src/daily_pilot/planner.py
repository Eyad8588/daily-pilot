"""Daily routine: fit open tasks into free time between calendar events.

Scheduling is deterministic (so it's testable and never hallucinates times);
the local LLM only writes a short motivational intro on top.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from .google_api import Event
from .llm import LLM, LLMError
from .tasks import Task
from .telegram import esc

log = logging.getLogger(__name__)


@dataclass
class Block:
    start: datetime
    end: datetime
    title: str
    kind: str  # event | task | routine
    task_id: int | None = None
    location: str = ""


def _at(day: date, hhmm: str, tz: tzinfo) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tz)


def task_sort_key(t: Task, today: date) -> tuple:
    overdue_or_today = 0 if (t.due and t.due <= today) else 1
    return (overdue_or_today, t.priority, t.due or date.max, t.id)


def free_slots(busy: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    slots, cursor = [], start
    for s, e in sorted(busy):
        if e <= cursor:
            continue
        if s > cursor:
            slots.append((cursor, min(s, end)))
        cursor = max(cursor, e)
        if cursor >= end:
            break
    if cursor < end:
        slots.append((cursor, end))
    return [(s, e) for s, e in slots if e > s]


def build_plan(
    day: date,
    tz: tzinfo,
    events: list[Event],
    tasks: list[Task],
    cfg: dict[str, Any],
    now: datetime | None = None,
) -> tuple[list[Block], list[Task]]:
    """Return (timeline blocks, tasks that didn't fit)."""
    day_start = _at(day, cfg.get("day_start", "09:00"), tz)
    day_end = _at(day, cfg.get("day_end", "18:00"), tz)
    if now and now.date() == day and now > day_start:
        # Round up to the next 15 minutes when planning mid-day.
        q = (now.minute // 15 + 1) * 15
        day_start = now.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=q)
    buffer = timedelta(minutes=int(cfg.get("buffer_minutes", 10)))
    min_slot = timedelta(minutes=int(cfg.get("min_slot_minutes", 15)))

    blocks: list[Block] = []
    busy: list[tuple[datetime, datetime]] = []
    for ev in events:
        if ev.all_day:
            continue
        blocks.append(Block(ev.start, ev.end, ev.title, "event", location=ev.location))
        busy.append((ev.start - buffer, ev.end + buffer))
    for r in cfg.get("routine", []) or []:  # e.g. lunch, gym
        s, e = _at(day, r["start"], tz), _at(day, r["end"], tz)
        blocks.append(Block(s, e, r["title"], "routine"))
        busy.append((s, e))

    slots = [list(s) for s in free_slots(busy, day_start, day_end)]
    unscheduled: list[Task] = []
    for t in sorted(tasks, key=lambda t: task_sort_key(t, day)):
        need = timedelta(minutes=t.est_minutes)
        for slot in slots:
            if slot[1] - slot[0] >= need:
                blocks.append(Block(slot[0], slot[0] + need, t.title, "task", t.id))
                slot[0] = slot[0] + need + buffer
                break
        else:
            unscheduled.append(t)
        slots = [s for s in slots if s[1] - s[0] >= min_slot]
    blocks.sort(key=lambda b: b.start)
    return blocks, unscheduled


ICONS = {"event": "📅", "task": "✅", "routine": "☕"}


def format_plan(
    day: date,
    blocks: list[Block],
    unscheduled: list[Task],
    all_day: list[Event],
    intro: str = "",
    mail_counts: dict[str, int] | None = None,
    greeting: str = "",
) -> str:
    lines = []
    if greeting:
        lines.append(f"<b>{esc(greeting)}</b>")
    lines.append(f"<b>🗓 Plan for {day:%A %d %B}</b>")
    if intro:
        lines += ["", f"<i>{esc(intro)}</i>"]
    if all_day:
        lines += ["", "All day: " + ", ".join(esc(e.title) for e in all_day)]
    lines.append("")
    if not blocks:
        lines.append("Nothing scheduled — a free day!")
    for b in blocks:
        loc = f" @ {esc(b.location)}" if b.location else ""
        lines.append(f"{b.start:%H:%M}–{b.end:%H:%M} {ICONS.get(b.kind, '•')} {esc(b.title)}{loc}")
    if unscheduled:
        lines += ["", "<b>Didn't fit today:</b>"]
        for t in unscheduled[:10]:
            due = f" (due {t.due:%a %d %b})" if t.due else ""
            lines.append(f"• {esc(t.title)}{due}")
    if mail_counts:
        parts = [f"{mail_counts.get(c, 0)} {c}" for c in ("urgent", "action", "fyi", "low")]
        lines += ["", "📬 Mail since yesterday: " + ", ".join(parts)]
    return "\n".join(lines)


def llm_intro(llm: LLM | None, day: date, blocks: list[Block], unscheduled: list[Task]) -> str:
    if llm is None:
        return ""
    timeline = "\n".join(f"{b.start:%H:%M}-{b.end:%H:%M} [{b.kind}] {b.title}" for b in blocks)
    user = f"Day: {day:%A %d %B}\nTimeline:\n{timeline or '(empty)'}\nUnscheduled tasks: {len(unscheduled)}"
    try:
        return llm.text(
            "You are a concise, upbeat personal assistant. In at most 2 sentences (max 40 words), "
            "summarize the shape of the day and name the single most important thing to focus on. "
            "No greetings, no emojis, no lists.",
            user,
        )[:400]
    except LLMError as e:
        log.warning("Plan intro skipped: %s", e)
        return ""
