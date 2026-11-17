from datetime import date, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from daily_pilot import planner
from daily_pilot.google_api import CalendarClient, SampleCalendar
from daily_pilot.tasks import Store

from .conftest import SAMPLES, FakeLLM

TZ = ZoneInfo("Africa/Cairo")
DAY = date(2026, 11, 18)
CFG = {"day_start": "09:00", "day_end": "18:00", "buffer_minutes": 10, "routine": [{"title": "Lunch", "start": "13:00", "end": "13:45"}]}


def dt(h, m=0):
    return datetime(2026, 11, 18, h, m, tzinfo=TZ)


def test_store_add_dedupe_complete():
    s = Store(":memory:")
    a = s.add_task("Write report", date(2026, 11, 20), 1, 60, source="gmail", source_id="m1:x")
    assert a is not None
    assert s.add_task("Write report", None, source="gmail", source_id="m1:x") is None  # same source item
    b = s.add_task("No due date")
    c = s.add_task("Due earlier", date(2026, 11, 19))
    assert [t.id for t in s.open_tasks()] == [c, a, b]  # due first, undated last
    assert s.complete(a) and not s.complete(a)
    assert len(s.open_tasks()) == 2


def test_mail_counts():
    s = Store(":memory:")
    s.mark_processed("1", "urgent", "x", "y")
    s.mark_processed("2", "low", "x", "y")
    s.mark_processed("3", "low", "x", "y")
    assert s.mail_counts_since(datetime.now() - timedelta(hours=1)) == {"urgent": 1, "low": 2}


def test_free_slots():
    busy = [(dt(10), dt(11)), (dt(10, 30), dt(12)), (dt(15), dt(16))]
    assert planner.free_slots(busy, dt(9), dt(18)) == [(dt(9), dt(10)), (dt(12), dt(15)), (dt(16), dt(18))]


def test_plan_places_tasks_around_events():
    store = Store(":memory:")
    store.add_task("Urgent fix", DAY, 1, 60)
    store.add_task("Review proposal", DAY + timedelta(days=1), 1, 45)
    store.add_task("Huge task", None, 3, 600)  # can't fit
    events = SampleCalendar(SAMPLES / "calendar.json").events_for_day(DAY, TZ)
    blocks, unscheduled = planner.build_plan(DAY, TZ, events, store.open_tasks(), CFG)

    assert [t.title for t in unscheduled] == ["Huge task"]
    tasks = [b for b in blocks if b.kind == "task"]
    meetings = [b for b in blocks if b.kind in ("event", "routine")]
    for t in tasks:
        assert dt(9) <= t.start and t.end <= dt(18)
        for m in meetings:
            assert t.end <= m.start or t.start >= m.end, f"{t.title} overlaps {m.title}"
    by_title = {b.title: b for b in tasks}
    # Due-today task is placed first, in the earliest free slot long enough (60 min):
    assert by_title["Urgent fix"].start == dt(11, 55)
    # the shorter task then fills the earlier morning gap
    assert by_title["Review proposal"].start == dt(9, 55)
    assert blocks == sorted(blocks, key=lambda b: b.start)


def test_plan_midday_starts_from_now():
    blocks, _ = planner.build_plan(DAY, TZ, [], [], CFG)
    assert blocks[0].title == "Lunch"
    store = Store(":memory:")
    store.add_task("Later task", None, 2, 30)
    blocks, _ = planner.build_plan(DAY, TZ, [], store.open_tasks(), CFG, now=dt(14, 7))
    task = next(b for b in blocks if b.kind == "task")
    assert task.start == dt(14, 15)


def test_format_plan_and_intro():
    events = SampleCalendar(SAMPLES / "calendar.json").events_for_day(DAY, TZ)
    blocks, unscheduled = planner.build_plan(DAY, TZ, events, [], CFG)
    intro = planner.llm_intro(FakeLLM(text="Busy morning; protect the afternoon."), DAY, blocks, unscheduled)
    text = planner.format_plan(DAY, blocks, unscheduled, [e for e in events if e.all_day], intro, {"urgent": 1}, "Good morning!")
    assert text.startswith("<b>Good morning!</b>")
    assert "09:30–09:45 📅 Team standup @ Google Meet" in text
    assert "All day: World Kindness Day" in text
    assert "1 urgent" in text and "Busy morning" in text


def test_calendar_client_parses_events():
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"summary": "Standup", "start": {"dateTime": "2026-11-18T07:30:00Z"}, "end": {"dateTime": "2026-11-18T07:45:00Z"}},
            {"summary": "Holiday", "start": {"date": "2026-11-18"}, "end": {"date": "2026-11-19"}},
            {"summary": "Gone", "status": "cancelled", "start": {"date": "2026-11-18"}},
        ]
    }
    evs = CalendarClient(service).events_for_day(DAY, TZ)
    assert [e.title for e in evs] == ["Holiday", "Standup"]
    assert evs[1].start == dt(9, 30)  # 07:30Z == 09:30 Cairo
    assert evs[0].all_day
