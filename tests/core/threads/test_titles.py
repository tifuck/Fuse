"""Title generation queue: a chat is named once from its first real prompt,
a user rename always wins, failures back off and stop until the next
message, and a lease keeps two sweeps from naming one chat."""
import asyncio
from unittest.mock import patch

from app import chat_runtime, chat_store, chat_titles, chats, db
from tests.core.helpers import make_user, run_async

from ._support import pk


def _chat_with_prompt(uid, text="Plan a three day trip to Lisbon in spring"):
    thread = chats.create_thread(uid, model="offline")
    chat_store.start_turn(uid, thread["id"], text, thread["revision"], None)
    return chats.get_thread(uid, thread["id"])


def _sweep(generate):
    """One sweep with the title model faked; waits for its jobs."""
    async def body():
        await chat_titles.sweep()
        await asyncio.gather(*list(chat_titles._tasks))

    with patch.object(chat_runtime, "generate_thread_title", new=generate):
        run_async(body())


def _title_row(uid, thread_id):
    return db.fetchone("SELECT title,title_state,title_attempts,title_job_token,updated_at,revision FROM chat_threads WHERE id=?",
                       (pk(uid, thread_id),))


def test_the_first_real_prompt_names_the_chat_once(fresh_db):
    uid = make_user()
    thread = _chat_with_prompt(uid)
    prompts = []

    async def generate(request, user, model, prompt):
        prompts.append(prompt)
        return "Lisbon spring trip"

    _sweep(generate)
    _sweep(generate)

    row = _title_row(uid, thread["id"])
    assert (row["title"], row["title_state"], row["title_job_token"]) == ("Lisbon spring trip", "generated", "")
    assert len(prompts) == 1 and "Lisbon" in prompts[0]
    # Naming is not activity: the chat keeps its place in the list.
    assert row["updated_at"] == thread["updated_at"]
    assert row["revision"] == thread["revision"] + 1


def test_a_rename_while_generating_wins(fresh_db):
    uid = make_user()
    thread = _chat_with_prompt(uid)

    async def generate(request, user, model, prompt):
        await asyncio.to_thread(chats.update_thread, uid, thread["id"], title="My own name")
        return "Generated name"

    _sweep(generate)

    row = _title_row(uid, thread["id"])
    assert (row["title"], row["title_state"]) == ("My own name", "user")
    assert chat_titles.pending_title_threads(attempted_before=chat_titles._backoff_cutoff()) == []


def test_failures_back_off_then_stop_until_the_next_message(fresh_db):
    uid = make_user()
    thread = _chat_with_prompt(uid)

    async def nothing(request, user, model, prompt):
        return ""

    _sweep(nothing)
    row = _title_row(uid, thread["id"])
    assert row["title_attempts"] == 1 and row["title_state"] == ""
    # Within the backoff window the sweep leaves it alone.
    assert chat_titles.pending_title_threads(attempted_before=chat_titles._backoff_cutoff()) == []
    # After the last allowed attempt only a new message makes it due again.
    db.execute("UPDATE chat_threads SET title_attempts=?, title_attempted_at='2020-01-01T00:00:00Z' WHERE id=?",
               (chat_titles.TITLE_SWEEP_ATTEMPTS, pk(uid, thread["id"])))
    assert chat_titles.pending_title_threads(attempted_before=chat_titles._backoff_cutoff()) == []
    due = chat_titles.pending_title_threads(attempted_before=chat_titles._backoff_cutoff(), due=[pk(uid, thread["id"])])
    assert [item["id"] for item in due] == [pk(uid, thread["id"])]


def test_prompts_too_short_or_commands_wait(fresh_db):
    uid = make_user()
    _chat_with_prompt(uid, "hi")
    _chat_with_prompt(uid, "/compact now please")

    assert chat_titles.pending_title_threads(attempted_before=chat_titles._backoff_cutoff()) == []


def test_an_unqualified_chat_is_not_reread_until_the_next_message(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    first = chat_store.start_turn(uid, thread["id"], "hi", thread["revision"], None)
    numeric = pk(uid, thread["id"])
    assert chat_titles.pending_title_threads(attempted_before=chat_titles._backoff_cutoff()) == []

    # Long after the backoff the sweep still leaves it alone.
    with patch.object(chats, "parse_messages", wraps=chats.parse_messages) as parsed:
        assert chat_titles.pending_title_threads(attempted_before="3000-01-01T00:00:00Z") == []
    assert not parsed.called

    chat_store.finish_run(first["run"]["id"], {"content": "hello"}, "completed")
    current = chats.get_thread(uid, thread["id"])
    chat_store.start_turn(uid, thread["id"], "Plan a three day trip to Lisbon in spring", current["revision"], None)
    due = chat_titles.pending_title_threads(attempted_before=chat_titles._backoff_cutoff(), due=[numeric])
    assert [item["id"] for item in due] == [numeric]


def test_a_lease_lets_only_one_job_name_the_chat(fresh_db):
    uid = make_user()
    thread = _chat_with_prompt(uid)
    numeric = pk(uid, thread["id"])

    token = chat_titles.claim(uid, numeric)
    assert token and chat_titles.claim(uid, numeric) == ""
    # A job holding a stale token cannot apply its title.
    assert chat_titles.apply_generated_title(uid, numeric, "Stale job", token="not-the-lease") is None
    assert chat_titles.apply_generated_title(uid, numeric, "Lease holder", token=token)["title"] == "Lease holder"
    chat_titles.release(numeric, token)
    assert chat_titles.claim(uid, numeric) == ""  # named now: nothing left to claim
