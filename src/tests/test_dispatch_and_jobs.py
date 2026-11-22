"""Dispatcher ordering (Property 18), gate (Properties 12, 13), per-Session usage (11),
failed first request (15), and delete outcomes (17)."""

import asyncio
from pathlib import Path

import hypothesis.strategies as st
from hypothesis import given, settings

from bot.context_display import is_full
from bot.dispatcher import DeleteJob, TextJob, UserDispatcher
from bot.gate import ContextGate
from bot.dispatcher import SummaryJob
from bot.jobs import DELETE_FAILED_TEXT, SUMMARY_FAILED_TEXT, JobProcessor
from bot.kiro_parser import RunResult
from bot.models import SessionRecord
from bot.session_store import SessionStore
from bot.summary import SUMMARY_PROMPT
from bot.persona import first_message

OK_RESULT = RunResult("sess_11111111", "reply", None, None)


class FakeRunner:
    def __init__(self, workdir, results=(), delete_error=None):
        self.workdir = workdir
        self.results = list(results)
        self.delete_error = delete_error
        self.prompts = []
        self.deleted = []

    def workdir_for(self, user_id):
        return self.workdir

    async def run_prompt(self, workdir, session_id, text):
        self.prompts.append((session_id, text))
        return self.results.pop(0)

    async def delete_session(self, workdir, session_id):
        self.deleted.append(session_id)
        return self.delete_error


class FakeNotifier:
    def __init__(self):
        self.texts = []
        self.replies = []
        self.choices = []
        self.summaries = []

    async def send_text(self, user_id, text, reply_to=None):
        self.texts.append((user_id, text))

    async def send_reply(self, user_id, chunks, display, choice=None, reply_to=None, group=False):
        self.replies.append((user_id, chunks, display))
        self.choices.append(choice)

    async def send_summary(self, user_id, text, old_sid):
        self.summaries.append((user_id, text, old_sid))

    async def send_blocked(self, job):
        self.texts.append((job.user_id, "full"))


def test_summary_success_stores_old_usage_and_hands_text_over(tmp_path):
    store = _store(tmp_path)
    runner = FakeRunner(tmp_path, results=[RunResult("sess_00000001", "выжимка", 4.0, None)])
    notifier = FakeNotifier()
    processor = JobProcessor(store, runner, notifier, now=lambda: "t1")

    async def run():
        await store.add_session(1, _session("aaaaaaaa"))
        await processor.summary(SummaryJob(1, "aaaaaaaa"))

    asyncio.run(run())
    assert runner.prompts == [("sess_00000001", SUMMARY_PROMPT)]
    assert store.find_session(1, "aaaaaaaa").context_usage == 4.0
    assert notifier.summaries == [(1, "выжимка", "aaaaaaaa")]
    assert len(store.get_user(1).sessions) == 1  # no new Session is created here


def test_summary_failure_changes_nothing(tmp_path):
    store = _store(tmp_path)
    runner = FakeRunner(tmp_path, results=[RunResult(None, None, None, "boom")])
    notifier = FakeNotifier()
    processor = JobProcessor(store, runner, notifier, now=lambda: "t1")

    async def run():
        await store.add_session(1, _session("aaaaaaaa", usage=2.0))
        await store.set_active(1, "aaaaaaaa")
        await processor.summary(SummaryJob(1, "aaaaaaaa"))

    asyncio.run(run())
    assert store.find_session(1, "aaaaaaaa").context_usage == 2.0
    assert store.get_user(1).active_sid == "aaaaaaaa"
    assert notifier.summaries == []
    assert notifier.texts == [(1, SUMMARY_FAILED_TEXT)]


def test_first_reply_of_summary_session_offers_choice(tmp_path):
    store = _store(tmp_path)
    runner = FakeRunner(tmp_path, results=[RunResult("sess_99999999", "hi", 1.0, None)])
    notifier = FakeNotifier()
    processor = JobProcessor(store, runner, notifier, now=lambda: "t1")
    job = TextJob(1, "cccccccc", "summary text", pending_name="Новая", summary_of="aaaaaaaa")

    asyncio.run(processor.text(job))
    assert notifier.choices == [("cccccccc", "aaaaaaaa")]
    assert store.get_user(1).active_sid == "cccccccc"


def _session(sid="aaaaaaaa", usage=None, session_id="sess_00000001"):
    return SessionRecord(sid, "name-" + sid, session_id, usage, "t0", "t0")


def _store(tmp: Path) -> SessionStore:
    return SessionStore(tmp / "store.json")


# Feature: telegram-kiro-bot, Property 18: Per-User requests run in order, one at a time
@settings(max_examples=60, deadline=None)
@given(users=st.lists(st.integers(min_value=1, max_value=3), min_size=1, max_size=25))
def test_per_user_order_and_no_overlap(users):
    order = []
    active = {}
    max_active = {}

    async def handle(job):
        uid = job.user_id
        active[uid] = active.get(uid, 0) + 1
        max_active[uid] = max(max_active.get(uid, 0), active[uid])
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        order.append((uid, job.text))
        active[uid] -= 1

    async def run():
        dispatcher = UserDispatcher(handle)
        for index, uid in enumerate(users):
            await dispatcher.submit(TextJob(uid, "aaaaaaaa", str(index)))
        await dispatcher.drain()
        assert not dispatcher._queues  # idle workers are removed

    asyncio.run(run())
    assert len(order) == len(users)
    assert all(count == 1 for count in max_active.values())
    for uid in set(users):
        expected = [str(i) for i, u in enumerate(users) if u == uid]
        actual = [text for u, text in order if u == uid]
        assert actual == expected


def test_full_session_discards_queued_text():
    blocked = []

    async def handle(job):
        raise AssertionError("must not run")

    async def on_blocked(job):
        blocked.append(job.text)

    async def run():
        dispatcher = UserDispatcher(
            handle, is_blocked=lambda job: True, on_blocked=on_blocked
        )
        await dispatcher.submit(TextJob(1, "aaaaaaaa", "hello"))
        await dispatcher.drain()

    asyncio.run(run())
    assert blocked == ["hello"]


# Feature: telegram-kiro-bot, Property 11: Each Session keeps its own raw usage
@settings(max_examples=60, deadline=None)
@given(
    ops=st.lists(
        st.tuples(
            st.sampled_from(["aaaaaaaa", "bbbbbbbb"]),
            st.one_of(st.none(), st.floats(min_value=0, max_value=100, allow_nan=False)),
        ),
        max_size=12,
    )
)
def test_each_session_keeps_its_own_last_reported_usage(tmp_path_factory, ops):
    tmp = tmp_path_factory.mktemp("usage")
    store = _store(tmp)
    runner = FakeRunner(tmp, results=[RunResult("sess_00000001", "r", usage, None) for _, usage in ops])
    processor = JobProcessor(store, runner, FakeNotifier(), now=lambda: "t1")

    async def run():
        await store.add_session(1, _session("aaaaaaaa"))
        await store.add_session(1, _session("bbbbbbbb"))
        for sid, _ in ops:
            await processor.text(TextJob(1, sid, "hi"))

    asyncio.run(run())

    expected = {"aaaaaaaa": None, "bbbbbbbb": None}
    for sid, usage in ops:
        if usage is not None:
            expected[sid] = usage
    actual = {s.sid: s.context_usage for s in store.get_user(1).sessions}
    assert actual == expected


# Feature: telegram-kiro-bot, Property 12: Full state blocks ordinary text
# Feature: telegram-kiro-bot, Property 13: Full state ends below 10
def test_gate_blocks_at_ten_and_clears_below_ten(tmp_path):
    store = _store(tmp_path)
    notifier = FakeNotifier()
    runner = FakeRunner(
        tmp_path,
        results=[
            RunResult("sess_00000001", "r1", 12.0, None),  # enters Full state
            RunResult("sess_00000001", "r2", 9.5, None),  # leaves it
        ],
    )
    processor = JobProcessor(store, runner, notifier, now=lambda: "t1")
    gate = ContextGate(store)

    async def run():
        await store.add_session(1, _session("aaaaaaaa"))
        await processor.text(TextJob(1, "aaaaaaaa", "first"))
        assert gate.blocks(TextJob(1, "aaaaaaaa", "second"))
        assert is_full(store.find_session(1, "aaaaaaaa").context_usage)

        # Summary and delete are never gated, so only text is checked here.
        await processor.text(TextJob(1, "aaaaaaaa", "second"))
        assert not gate.blocks(TextJob(1, "aaaaaaaa", "third"))

    asyncio.run(run())
    assert [sid for sid, _ in runner.prompts] == ["sess_00000001", "sess_00000001"]


def test_reply_without_usage_keeps_stored_value(tmp_path):
    store = _store(tmp_path)
    runner = FakeRunner(tmp_path, results=[RunResult("sess_00000001", "r", None, None)])
    notifier = FakeNotifier()
    processor = JobProcessor(store, runner, notifier, now=lambda: "t1")

    async def run():
        await store.add_session(1, _session("aaaaaaaa", usage=6.0))
        await processor.text(TextJob(1, "aaaaaaaa", "hi"))

    asyncio.run(run())
    assert store.find_session(1, "aaaaaaaa").context_usage == 6.0
    _, _, display = notifier.replies[0]
    assert display.text == "60.0%"  # the stored value still drives the display


# Feature: telegram-kiro-bot, Property 15: A failed first request leaves the store unchanged
def test_failed_first_request_leaves_store_unchanged(tmp_path):
    store = _store(tmp_path)
    runner = FakeRunner(tmp_path, results=[RunResult(None, None, None, "boom")])
    notifier = FakeNotifier()
    processor = JobProcessor(store, runner, notifier, now=lambda: "t1")

    async def run():
        await processor.text(TextJob(1, "cccccccc", "hi", pending_name="New one"))

    asyncio.run(run())
    assert store.get_user(1).sessions == []
    assert store.get_user(1).active_sid is None
    assert not (tmp_path / "store.json").exists()
    assert notifier.texts and notifier.replies == []


def test_first_reply_stores_pending_session_and_activates_it(tmp_path):
    store = _store(tmp_path)
    runner = FakeRunner(tmp_path, results=[RunResult("sess_99999999", "hello", 2.0, None)])
    notifier = FakeNotifier()
    processor = JobProcessor(store, runner, notifier, now=lambda: "t1")

    asyncio.run(processor.text(TextJob(1, "cccccccc", "hi", pending_name="New one")))

    user = store.get_user(1)
    assert [s.name for s in user.sessions] == ["New one"]
    assert user.sessions[0].session_id == "sess_99999999"
    assert user.active_sid == "cccccccc"
    assert runner.prompts == [(None, first_message("hi"))]  # a new Session has no --resume-id


# Feature: telegram-kiro-bot, Property 17: Delete changes the store only on confirmed success
def test_delete_success_removes_record_and_clears_active(tmp_path):
    store = _store(tmp_path)
    runner = FakeRunner(tmp_path)
    processor = JobProcessor(store, runner, FakeNotifier(), now=lambda: "t1")

    async def run():
        await store.add_session(1, _session("aaaaaaaa"))
        await store.set_active(1, "aaaaaaaa")
        await processor.delete(DeleteJob(1, "aaaaaaaa"))

    asyncio.run(run())
    assert store.get_user(1).sessions == []
    assert store.get_user(1).active_sid is None
    assert runner.deleted == ["sess_00000001"]


def test_delete_failure_keeps_record(tmp_path):
    store = _store(tmp_path)
    runner = FakeRunner(tmp_path, delete_error="kiro-cli delete exited with code 4")
    notifier = FakeNotifier()
    processor = JobProcessor(store, runner, notifier, now=lambda: "t1")

    async def run():
        await store.add_session(1, _session("aaaaaaaa"))
        await store.set_active(1, "aaaaaaaa")
        await processor.delete(DeleteJob(1, "aaaaaaaa"))

    asyncio.run(run())
    assert [s.sid for s in store.get_user(1).sessions] == ["aaaaaaaa"]
    assert store.get_user(1).active_sid == "aaaaaaaa"
    assert notifier.texts == [(1, DELETE_FAILED_TEXT)]


def test_pending_session_without_session_id_is_dropped_without_kiro(tmp_path):
    store = _store(tmp_path)
    runner = FakeRunner(tmp_path)
    processor = JobProcessor(store, runner, FakeNotifier(), now=lambda: "t1")

    async def run():
        await store.add_session(1, _session("aaaaaaaa", session_id=""))
        await processor.delete(DeleteJob(1, "aaaaaaaa"))

    asyncio.run(run())
    assert runner.deleted == []
    assert store.get_user(1).sessions == []

def test_typing_is_refreshed_while_a_text_job_runs_and_stops_after():
    calls = []

    async def handle(job):
        await asyncio.sleep(0.05)

    async def show(user_id):
        calls.append(user_id)

    async def run():
        dispatcher = UserDispatcher(handle, show_typing=show, typing_interval=0.01)
        await dispatcher.submit(TextJob(1, "aaaaaaaa", "hi"))
        await dispatcher.drain()
        stopped_at = len(calls)
        await asyncio.sleep(0.05)
        assert len(calls) == stopped_at  # no typing after the job finished

    asyncio.run(run())
    assert len(calls) >= 2
    assert set(calls) == {1}


def test_delete_job_shows_typing():
    calls = []

    async def handle(job):
        await asyncio.sleep(0.02)

    async def show(user_id):
        calls.append(user_id)

    async def run():
        dispatcher = UserDispatcher(handle, show_typing=show, typing_interval=0.005)
        await dispatcher.submit(DeleteJob(1, "aaaaaaaa"))
        await dispatcher.drain()

    asyncio.run(run())
    assert len(calls) >= 1
    assert set(calls) == {1}

def test_failed_reply_shows_the_reason_to_the_user(tmp_path):
    store = _store(tmp_path)
    runner = FakeRunner(tmp_path, results=[RunResult("sess_00000001", None, None, "модель отказалась")])
    notifier = FakeNotifier()
    processor = JobProcessor(store, runner, notifier, now=lambda: "t1")

    async def run():
        await store.add_session(1, _session("aaaaaaaa"))
        await processor.text(TextJob(1, "aaaaaaaa", "hi"))

    asyncio.run(run())
    assert notifier.texts[-1][1].endswith("Причина: модель отказалась")

def test_refusal_gets_one_of_the_fixed_replies_and_store_is_unchanged(tmp_path):
    from bot.refusal import REFUSAL_REPLIES

    store = _store(tmp_path)
    refusal = RunResult("sess_00000001", None, None, "модель отказалась", refused=True)
    runner = FakeRunner(tmp_path, results=[refusal])
    notifier = FakeNotifier()
    processor = JobProcessor(store, runner, notifier, now=lambda: "t1")

    async def run():
        await store.add_session(1, _session("aaaaaaaa", usage=2.0))
        await processor.text(TextJob(1, "aaaaaaaa", "how"))

    asyncio.run(run())
    assert len(notifier.texts) == 1
    sent = notifier.texts[0][1]
    assert sent in REFUSAL_REPLIES
    assert "Причина" not in sent and "Сессия не изменена" not in sent
    assert store.find_session(1, "aaaaaaaa").context_usage == 2.0


def test_refusal_of_summary_gets_a_fixed_reply(tmp_path):
    from bot.refusal import REFUSAL_REPLIES

    store = _store(tmp_path)
    runner = FakeRunner(tmp_path, results=[RunResult(None, None, None, "x", refused=True)])
    notifier = FakeNotifier()
    processor = JobProcessor(store, runner, notifier, now=lambda: "t1")

    async def run():
        await store.add_session(1, _session("aaaaaaaa"))
        await processor.summary(SummaryJob(1, "aaaaaaaa"))

    asyncio.run(run())
    assert notifier.summaries == []
    assert notifier.texts[0][1] in REFUSAL_REPLIES


def test_refusal_on_pending_session_keeps_it_pending_and_creates_nothing(tmp_path):
    from bot.refusal import REFUSAL_REPLIES

    store = _store(tmp_path)
    runner = FakeRunner(tmp_path, results=[RunResult("sess_00000002", None, None, "x", refused=True)])
    notifier = FakeNotifier()
    processor = JobProcessor(store, runner, notifier, now=lambda: "t1")

    asyncio.run(processor.text(TextJob(1, "cccccccc", "how", pending_name="Новая")))
    assert store.get_user(1).sessions == []
    assert notifier.texts[0][1] in REFUSAL_REPLIES