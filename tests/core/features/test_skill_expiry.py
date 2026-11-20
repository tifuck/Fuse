"""A skill draft goes stale for its own user only: expiring drafts is scoped
to the user whose drafts are being read."""
from app import chats, db, skill_proposals
from tests.core.helpers import quick_user


def _draft(uid, name):
    thread = chats.create_thread(uid, model="offline", tools=[])
    pk = chats.resolve_thread_pk(uid, thread["id"])
    return skill_proposals.create(uid, thread_id=pk, name=name, description="Takes notes.", body="Write notes.")


def test_reading_drafts_expires_only_that_users_stale_drafts(fresh_db, skills_dir):
    mine, theirs = quick_user(), quick_user("other@core.test")
    old, other = _draft(mine, "old-one"), _draft(theirs, "their-one")
    db.execute("UPDATE skill_proposals SET created_at='2020-01-01T00:00:00Z'")

    assert skill_proposals.get_proposal(mine, old["id"])["status"] == "expired"
    assert db.fetchone("SELECT status FROM skill_proposals WHERE id=?", (other["id"],))["status"] == "pending"
    assert skill_proposals.get_proposal(theirs, other["id"])["status"] == "expired"  # its own read expires it
