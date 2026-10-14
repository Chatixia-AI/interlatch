"""people.py: roles, invites, per-computer tokens, browser sessions and sign-in codes on a hub."""

import pytest

from interlatch.sharing import people
from interlatch.storage.sqlite import connect

MACHINE = "11111111-2222-4333-8444-555555555555"
KEY = "k" * 43  # the key Bob's laptop keeps in its machine-key file


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "hub.db")
    c.execute("INSERT INTO machines(id, name) VALUES (?, 'Bob laptop')", (MACHINE,))
    c.commit()
    yield c
    c.close()


def test_invite_join_and_tokens(conn):
    assert not people.has_people(conn)
    admin = people.add(conn, "Ada", "ADA@example.com", "admin")
    assert admin["email"] == "ada@example.com" and people.has_people(conn)
    bob = people.add(conn, "Bob", "bob@example.com", "member", by=admin)
    with pytest.raises(people.PeopleError, match="already on this hub"):
        people.add(conn, "Bob again", "bob@example.com", "member")
    with pytest.raises(people.PeopleError, match="role must be"):
        people.add(conn, "X", None, "owner")

    code = people.invite(conn, bob["id"], by=admin)
    assert len(code) == 14 and code.count("-") == 2
    people.record_key(conn, MACHINE, KEY)  # Bob's laptop said hello before: the hub knows it, and its key
    who, token = people.join_computer(conn, code.lower().replace("-", ""), MACHINE, "Bob laptop", KEY)
    assert who["id"] == bob["id"]
    with pytest.raises(people.PeopleError, match="already used"):
        people.join_computer(conn, code, MACHINE)
    with pytest.raises(people.PeopleError, match="isn't known"):
        people.join_computer(conn, "AAAA-BBBB-CCCC", MACHINE)
    assert people.computer_person(conn, token, MACHINE)["id"] == bob["id"]
    assert people.computer_person(conn, token, "99999999-2222-4333-8444-555555555555") is None  # another computer
    assert people.computer_person(conn, "nope") is None
    assert conn.execute("SELECT person_id FROM machines WHERE id = ?", (MACHINE,)).fetchone()[0] == bob["id"]
    assert token not in str([dict(r) for r in conn.execute("SELECT * FROM people_tokens")])  # only its hash is kept

    listed = {p["name"]: p for p in people.listing(conn)}
    assert [c["name"] for c in listed["Bob"]["computers"]] == ["Bob laptop"]
    assert people.revoke(conn, bob["id"], listed["Bob"]["computers"][0]["id"], by=admin)
    assert people.computer_person(conn, token, MACHINE) is None


def test_browser_sessions_signin_and_removal(conn):
    admin = people.add(conn, "Ada", "ada@example.com", "admin")
    viewer = people.add(conn, "Vic", "vic@example.com", "readonly", by=admin)
    who, session = people.open_browser(conn, people.invite(conn, viewer["id"]), "Safari")
    assert who["role"] == "readonly" and people.browser_person(conn, session)["id"] == viewer["id"]
    who, again = people.open_browser(conn, people.signin_code(conn, viewer))
    assert people.browser_person(conn, again)["id"] == viewer["id"]
    people.sign_out(conn, again)
    assert people.browser_person(conn, again) is None and people.browser_person(conn, session)
    conn.execute("UPDATE people_codes SET expires_at = '2000-01-01T00:00:00.000Z'")
    with pytest.raises(people.PeopleError, match="expired"):
        people.open_browser(conn, people.invite(conn, viewer["id"], days=-1))

    assert people.allows(viewer, "readonly") and not people.allows(viewer, "member")
    assert people.allows(None, "admin") and people.public(None)["role"] == "admin"
    with pytest.raises(people.PeopleError, match="last admin"):
        people.set_role(conn, admin["id"], "member", by=admin)
    people.remove(conn, viewer["id"], by=admin)
    assert people.browser_person(conn, session) is None
    assert [p["name"] for p in people.listing(conn)] == ["Ada"]
    actions = [a["action"] for a in people.audit_log(conn)]
    assert actions[0] == "remove" and "add" in actions and "signin" in actions
    assert people.add(conn, "Vic", "vic@example.com", "member")["role"] == "member"  # can be added again


def test_wrong_codes_make_an_address_wait():
    t = [1000.0]
    a = people.CodeAttempts(clock=lambda: t[0])
    for _ in range(people.CodeAttempts.FREE):  # the first few cost nothing
        assert a.wait("203.0.113.7") == 0
        a.wrong("203.0.113.7")
    assert a.wait("203.0.113.7") == 1 and a.wait("198.51.100.9") == 0  # only that address waits
    t[0] += 1
    assert a.wait("203.0.113.7") == 0
    a.wrong("203.0.113.7")
    assert a.wait("203.0.113.7") == 2  # each wrong one doubles the wait
    for _ in range(10):
        a.wrong("203.0.113.7")
    assert a.wait("203.0.113.7") == people.CodeAttempts.MAX_WAIT  # up to a minute: never a lockout
    t[0] += people.CodeAttempts.MAX_WAIT
    assert a.wait("203.0.113.7") == 0
    t[0] += people.CodeAttempts.WINDOW + 1  # a quiet quarter of an hour forgets the address
    a.wrong("203.0.113.7")
    assert a.wait("203.0.113.7") == 0

    a.KEEP = 8  # many addresses at once: the ones that went quiet first are forgotten, the busy ones kept
    for i in range(20):
        t[0] += 1
        a.wrong(f"192.0.2.{i}")
    assert len(a._seen) <= 8 and "192.0.2.19" in a._seen and "192.0.2.0" not in a._seen


def test_only_an_unknown_code_counts_as_a_guess(conn):
    bob = people.add(conn, "Bob", "bob@example.com", "member")
    code = people.invite(conn, bob["id"])
    people.record_key(conn, MACHINE, KEY)  # the hub knows Bob's laptop: it joins with its key
    people.join_computer(conn, code, MACHINE, "Bob laptop", KEY)
    with pytest.raises(people.PeopleError) as used:
        people.open_browser(conn, code)
    assert not isinstance(used.value, people.UnknownCode)  # a real code, already used: not a guess
    with pytest.raises(people.UnknownCode):
        people.open_browser(conn, "AAAA-BBBB-CCCC")


def test_a_computer_the_hub_knows_is_claimed_only_with_its_key(conn):
    bob = people.add(conn, "Bob", "bob@example.com", "member")
    new = "22222222-2222-4333-8444-555555555555"
    code = people.invite(conn, bob["id"])
    assert people.may_claim(conn, code, new, None)  # one the hub never heard of joins as before
    assert not people.may_claim(conn, code, MACHINE, KEY)  # the fixture's laptop: known, with no key on record
    people.record_key(conn, MACHINE, KEY)
    people.record_key(conn, MACHINE, "o" * 43)  # the first key stays
    assert people.may_claim(conn, code, MACHINE, KEY)
    assert not people.may_claim(conn, code, MACHINE, "o" * 43) and not people.may_claim(conn, code, MACHINE, "short")
    with pytest.raises(people.PeopleError, match="doesn't know a computer"):
        people.invite(conn, bob["id"], machine=new)
    for_it = people.invite(conn, bob["id"], machine=MACHINE)  # an admin's word: this one is Bob's
    assert people.may_claim(conn, for_it, MACHINE, None)
    with pytest.raises(people.PeopleError, match="for another computer"):
        people.join_computer(conn, for_it, new)
    assert people.peek_invite(conn, for_it)  # refused before it was used
