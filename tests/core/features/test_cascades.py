"""Deleting a project, machine, chat or account relies on foreign-key cascades
for this area's rows (memories, logos, schedules and fires, artifacts). The
core fixture schema is built with CREATE TABLE ... LIKE, which drops foreign
keys, so the cascades are checked in the schema fresh installs load."""
from pathlib import Path

import pytest

SCHEMA = (Path(__file__).resolve().parents[3] / "scripts/sql/schema.sql").read_text()


def table(name):
    body = SCHEMA[SCHEMA.index(f"CREATE TABLE `{name}`"):]
    return body[:body.index(";")]


@pytest.mark.parametrize("name, column, parent, rule", [
    # A project delete takes its memories and logo; its schedules lose the target.
    ("memories", "project_id", "user_projects", "CASCADE"),
    ("project_logos", "project_id", "user_projects", "CASCADE"),
    ("chat_schedules", "project_id", "user_projects", "SET NULL"),
    # A machine delete takes its projects.
    ("user_projects", "host_id", "user_hosts", "CASCADE"),
    # A chat delete keeps memories and artifacts, unlinked.
    ("memories", "thread_id", "chat_threads", "SET NULL"),
    ("artifacts", "thread_id", "chat_threads", "SET NULL"),
    ("artifact_threads", "thread_id", "chat_threads", "CASCADE"),
    ("chat_schedules", "thread_id", "chat_threads", "SET NULL"),
    ("skill_proposals", "thread_id", "chat_threads", "CASCADE"),
    # An artifact or schedule delete takes its versions or fires.
    ("artifact_versions", "artifact_id", "artifacts", "CASCADE"),
    ("schedule_fires", "schedule_id", "chat_schedules", "CASCADE"),
    # An account delete takes everything it owns here.
    *[(t, "user_id", "users", "CASCADE") for t in (
        "memories", "user_hosts", "user_projects", "project_logos", "chat_schedules", "schedule_fires",
        "artifacts", "push_subscriptions", "user_skills", "skill_proposals")],
])
def test_owned_rows_follow_their_parent(name, column, parent, rule):
    assert f"FOREIGN KEY (`{column}`) REFERENCES `{parent}` (`id`) ON DELETE {rule}" in table(name)
