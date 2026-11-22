"""Who may create an account: the first-run setup token and first admin,
invites, open registration, and the email/password rules."""
import threading

import pytest

from app import accounts, db
from app.config import settings
from tests.core.auth._support import anonymous, read_session, cookie
from tests.core.helpers import quick_user


def _register(client, email, password="password12", **fields):
    form = {"email": email, "password": password, "csrf_token": anonymous(client)["csrf"], **fields}
    client.headers.pop("x-csrf-token", None)
    return client.post("/register", data=form, follow_redirects=False)


def _accounts():
    return db.fetchone("SELECT COUNT(*) AS n FROM users")["n"]


@pytest.fixture
def closed(monkeypatch):
    monkeypatch.setattr(settings, "allow_register", False)


def test_the_first_account_needs_the_setup_token_and_becomes_admin(web, closed):
    token = accounts.ensure_setup_token()
    assert token and accounts.ensure_setup_token() == token
    assert accounts.SETUP_TOKEN_FILE.stat().st_mode & 0o777 == 0o600

    for wrong in ("", "guess", token + "x"):
        assert _register(web, "admin@core.test", setup=wrong).status_code == 403, wrong
    assert _accounts() == 0 and accounts.needs_setup()

    made = _register(web, "admin@core.test", setup=token)
    assert made.status_code == 303 and made.headers["location"] == "/chat"
    user = accounts.user_by_email("admin@core.test")
    assert user["is_admin"] == 1
    # Signed in as the new account, and the one-time token is gone for good.
    assert read_session(cookie(web))["uid"] == user["id"]
    assert not accounts.SETUP_TOKEN_FILE.exists()
    assert accounts.ensure_setup_token() is None
    assert _register(web, "second@core.test", setup=token).status_code == 403
    assert _accounts() == 1


def test_later_accounts_are_members_and_need_an_invite_unless_registration_is_open(web, closed, monkeypatch):
    quick_user("root@core.test", admin=True)
    refused = _register(web, "walk-in@core.test")
    assert refused.status_code == 403
    assert accounts.user_by_email("walk-in@core.test") is None

    monkeypatch.setattr(settings, "allow_register", True)
    assert _register(web, "walk-in@core.test").status_code == 303
    assert accounts.user_by_email("walk-in@core.test")["is_admin"] == 0


def test_an_invite_needs_its_link_token_and_is_spent_once(fresh_db, closed):
    quick_user("root@core.test", admin=True)
    email, token = accounts.add_whitelist(" Invited@Core.test ")
    assert email == "invited@core.test"
    _other, other_token = accounts.add_whitelist("other@core.test")
    # Knowing an invited address is not an invitation, and one person's link
    # does not open another address.
    for wrong in ("", "guess", token + "x", other_token):
        with pytest.raises(ValueError, match="closed"):
            accounts.register("invited@core.test", "password12", wrong)
    assert accounts.user_by_email("invited@core.test") is None

    user = accounts.register("invited@core.test", "password12", token)
    assert user["is_admin"] == 0
    assert [row["email"] for row in accounts.list_whitelist()] == ["other@core.test"]
    with pytest.raises(ValueError):
        accounts.register("invited@core.test", "password12", token)


def test_an_expired_invite_is_refused_and_stays_listed(fresh_db, closed):
    quick_user("root@core.test", admin=True)
    _email, token = accounts.add_whitelist("late@core.test")
    db.execute("UPDATE registration_whitelist SET created_at='2000-01-01T00:00:00Z' WHERE email=?", ("late@core.test",))
    with pytest.raises(ValueError, match="closed"):
        accounts.register("late@core.test", "password12", token)
    assert [row["email"] for row in accounts.list_whitelist()] == ["late@core.test"]


def test_an_invite_cannot_be_listed_twice_or_for_an_existing_account(fresh_db):
    quick_user("member@core.test")
    with pytest.raises(ValueError, match="exists"):
        accounts.add_whitelist("MEMBER@core.test")
    accounts.add_whitelist("new@core.test")
    with pytest.raises(ValueError, match="listed"):
        accounts.add_whitelist("new@core.test")
    with pytest.raises(ValueError, match="email"):
        accounts.add_whitelist("not-an-email")


@pytest.mark.parametrize("email, password, error", [
    ("bad", "password12", "email"),
    ("two@@core.test", "password12", "email"),
    ("dot@core.", "password12", "email"),
    ("sp ace@core.test", "password12", "email"),
    ("ok@core.test", "short7!", "password"),
    ("ok@core.test", "x" * 257, "password"),
])
def test_email_and_password_rules(fresh_db, email, password, error):
    with pytest.raises(ValueError, match=error):
        accounts.create_user(email, password)
    assert _accounts() == 0


def test_emails_are_unique_whatever_their_case(fresh_db):
    first = accounts.create_user("Ada@Core.Test", "password12")
    assert first["email"] == "ada@core.test"
    with pytest.raises(ValueError, match="exists"):
        accounts.create_user("ADA@core.test ", "password12")
    assert _accounts() == 1


def test_concurrent_first_registrations_make_exactly_one_admin(fresh_db, closed):
    # Both see an empty users table; the schema_meta sentinel decides. The
    # loser is an ordinary closed-registration refusal, not a second admin.
    start = threading.Barrier(2)
    outcomes = []

    def register(email):
        start.wait()
        try:
            outcomes.append(accounts.create_user(email, "password12", require_invite=True)["is_admin"])
        except ValueError as exc:
            outcomes.append(str(exc))

    threads = [threading.Thread(target=register, args=(f"first{n}@core.test",)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes, key=str) == [1, "closed"]
    assert accounts.admin_count() == 1 and _accounts() == 1


def test_a_lost_first_admin_claim_makes_an_invited_member(fresh_db, closed):
    # The sentinel outlives the account that claimed it: an emptied users
    # table does not reopen first-admin registration.
    db.execute("INSERT INTO schema_meta(key, value) VALUES('first_user_claimed', ?)", (db.now(),))
    _email, token = accounts.add_whitelist("late@core.test")
    assert accounts.create_user("late@core.test", "password12", require_invite=True, invite=token)["is_admin"] == 0


def test_the_setup_token_still_works_after_the_users_table_was_emptied(web, closed, monkeypatch):
    # A manual wipe or partial restore keeps schema_meta: the first-admin claim
    # outlives the account that made it, and the boot still hands out a token.
    db.execute("INSERT INTO schema_meta(key, value) VALUES('first_user_claimed', ?)", (db.now(),))
    token = accounts.ensure_setup_token()
    assert token and accounts.needs_setup()

    for wrong in ("", "guess", token + "x"):
        assert _register(web, "admin@core.test", setup=wrong).status_code == 403, wrong
    # Open registration does not let a walk-in take the empty install either.
    monkeypatch.setattr(settings, "allow_register", True)
    assert _register(web, "walk-in@core.test").status_code == 403
    assert _accounts() == 0

    made = _register(web, "admin@core.test", setup=token)
    assert made.status_code == 303 and made.headers["location"] == "/chat"
    assert accounts.user_by_email("admin@core.test")["is_admin"] == 1
    assert not accounts.SETUP_TOKEN_FILE.exists()
    assert _register(web, "second@core.test", setup=token).status_code == 303
    assert accounts.user_by_email("second@core.test")["is_admin"] == 0


def test_concurrent_setup_token_claims_after_a_wipe_make_exactly_one_admin(fresh_db, closed):
    db.execute("INSERT INTO schema_meta(key, value) VALUES('first_user_claimed', ?)", (db.now(),))
    token = accounts.ensure_setup_token()
    start = threading.Barrier(2)
    outcomes = []

    def register(email):
        start.wait()
        try:
            outcomes.append(accounts.create_user(email, "password12", require_invite=True, setup=token)["is_admin"])
        except ValueError as exc:
            outcomes.append(str(exc))

    threads = [threading.Thread(target=register, args=(f"first{n}@core.test",)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes, key=str) == [1, "closed"]
    assert accounts.admin_count() == 1 and _accounts() == 1
