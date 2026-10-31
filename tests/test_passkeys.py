"""Passkey registration and sign-in, with a synthetic authenticator only (tests/passkey_fake.py)."""
import hashlib
import json
import sqlite3
from html.parser import HTMLParser
from contextlib import closing

import pyotp
import pytest

from tests.passkey_fake import ORIGIN, FakeAuthenticator, b64url, options_challenge, unb64url

PASSWORD = "synthetic owner password"


@pytest.fixture
def owner(tmp_path):
    from app.auth import AuthStore
    directory = tmp_path / "auth"
    directory.mkdir(mode=0o700)
    secret = pyotp.random_base32()
    store = AuthStore(directory)
    store.enroll(PASSWORD, secret, pyotp.TOTP(secret).at(3000), now=3000)
    return store, secret


def _code(secret, at):
    return pyotp.TOTP(secret).at(at)


def _rows(store, sql, args=()):
    with closing(sqlite3.connect(store.path)) as db:
        return db.execute(sql, args).fetchall()


def _execute(store, sql, args=()):
    with closing(sqlite3.connect(store.path)) as db:
        with db:
            db.execute(sql, args)


def _failures(store):
    return _rows(store, "SELECT failures FROM throttle WHERE id=1")[0][0]


def _challenges(store):
    return _rows(store, "SELECT digest,purpose,expires FROM passkey_challenges")


def _register(store, secret, at, fake=None, **answer):
    fake = fake or FakeAuthenticator()
    options = store.begin_registration(PASSWORD, _code(secret, at), origin=ORIGIN, now=at)
    assert options
    assert store.finish_registration(fake.register(options, **answer), origin=ORIGIN, now=at + 1) is True
    return fake


def _sign_in(store, fake, at, *, keep=False, **answer):
    options = store.begin_login(origin=ORIGIN, now=at)
    assert options
    return store.finish_login(fake.assertion(options, **answer), origin=ORIGIN, keep=keep, now=at + 1)


# --- the happy path -----------------------------------------------------------

def test_register_then_sign_in_returns_a_session(owner):
    store, secret = owner
    fake = _register(store, secret, 3030)
    assert [key_id for key_id, _ in store.passkeys()] == [fake.id]
    assert store.passkeys()[0][1] == 3031
    token = _sign_in(store, fake, 4000)
    assert token and store.session(token, now=4002)
    assert store.session_info(token, now=4002).kept is False
    kept = _sign_in(store, fake, 4100, keep=True)
    assert store.session_info(kept, now=4102).kept is True
    store.available()


def test_registration_options_follow_the_global_constraints(owner):
    store, secret = owner
    first = _register(store, secret, 3030)
    options = json.loads(store.begin_registration(PASSWORD, _code(secret, 3060), origin=ORIGIN, now=3060))
    assert options["rp"] == {"id": "localhost", "name": "OneOS"}
    assert options["user"] == {"id": b64url(b"oneos-owner"), "name": "owner", "displayName": "OneOS owner"}
    assert options["authenticatorSelection"]["residentKey"] == "required"
    assert options["authenticatorSelection"]["requireResidentKey"] is True
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["attestation"] == "none"
    assert [param["alg"] for param in options["pubKeyCredParams"]] == [-7, -257]
    assert [item["id"] for item in options["excludeCredentials"]] == [first.id]
    challenge = unb64url(options["challenge"])
    assert len(challenge) == 32
    assert _challenges(store) == [(hashlib.sha256(challenge).hexdigest(), "register", 3360)]
    assert options["challenge"].encode() not in store.path.read_bytes()


def test_login_options_are_discoverable_with_user_verification(owner):
    store, secret = owner
    _register(store, secret, 3030)
    options = json.loads(store.begin_login(origin=ORIGIN, now=4000))
    assert options["rpId"] == "localhost"
    assert options["allowCredentials"] == []
    assert options["userVerification"] == "required"
    challenge = unb64url(options["challenge"])
    assert len(challenge) == 32
    assert _challenges(store) == [(hashlib.sha256(challenge).hexdigest(), "login", 4300)]


def test_zero_counters_are_accepted_every_time(owner):
    store, secret = owner
    fake = _register(store, secret, 3030, sign_count=0)
    assert _sign_in(store, fake, 4000, sign_count=0)
    assert _sign_in(store, fake, 4100, sign_count=0)
    assert _rows(store, "SELECT sign_count FROM passkeys") == [(0,)]


def test_increasing_counter_is_accepted_and_stored(owner):
    store, secret = owner
    fake = _register(store, secret, 3030, sign_count=5)
    assert _sign_in(store, fake, 4000, sign_count=6)
    assert _rows(store, "SELECT sign_count FROM passkeys") == [(6,)]


def test_passkey_sign_in_resets_the_throttle(owner):
    store, secret = owner
    fake = _register(store, secret, 3030)
    for _ in range(3):
        assert store.login("wrong password", "000000", now=3100) is None
    assert _failures(store) == 3
    assert _sign_in(store, fake, 3200)
    assert _failures(store) == 0
    assert _rows(store, "SELECT blocked FROM throttle") == [(0,)]


# --- sign-in refusals: each one counts toward the throttle --------------------

def _refusal_uv_off(store, fake, secret):
    return _sign_in(store, fake, 4000, uv=False)


def _refusal_up_off(store, fake, secret):
    return _sign_in(store, fake, 4000, up=False)


def _refusal_wrong_origin(store, fake, secret):
    return _sign_in(store, fake, 4000, origin="https://evil.example")


def _refusal_wrong_port(store, fake, secret):
    return _sign_in(store, fake, 4000, origin="https://localhost:8444")


def _refusal_wrong_rp_id(store, fake, secret):
    return _sign_in(store, fake, 4000, rp_id="evil.example")


def _refusal_create_type(store, fake, secret):
    return _sign_in(store, fake, 4000, kind="webauthn.create")


def _refusal_expired_challenge(store, fake, secret):
    options = store.begin_login(origin=ORIGIN, now=4000)
    return store.finish_login(fake.assertion(options), origin=ORIGIN, keep=False, now=4301)


def _refusal_challenge_at_expiry(store, fake, secret):
    options = store.begin_login(origin=ORIGIN, now=4000)
    return store.finish_login(fake.assertion(options), origin=ORIGIN, keep=False, now=4300)


def _refusal_unknown_challenge(store, fake, secret):
    store.begin_login(origin=ORIGIN, now=4000)
    return store.finish_login(fake.assertion("{}", rp_id="localhost", challenge=bytes(32)), origin=ORIGIN, keep=False, now=4001)


def _refusal_unknown_credential(store, fake, secret):
    return _sign_in(store, FakeAuthenticator(), 4000)


def _refusal_other_key_signature(store, fake, secret):
    return _sign_in(store, fake, 4000, signer=FakeAuthenticator().key)


def _refusal_flipped_signature(store, fake, secret):
    return _sign_in(store, fake, 4000, flip_signature=True)


def _refusal_wrong_user_handle(store, fake, secret):
    return _sign_in(store, fake, 4000, user_handle=b"someone-else")


def _refusal_counter_not_increasing(store, fake, secret):
    _execute(store, "UPDATE passkeys SET sign_count=5")
    return _sign_in(store, fake, 4000, sign_count=5)


def _refusal_counter_going_back(store, fake, secret):
    _execute(store, "UPDATE passkeys SET sign_count=5")
    return _sign_in(store, fake, 4000, sign_count=4)


def _refusal_counter_reset_to_zero(store, fake, secret):
    _execute(store, "UPDATE passkeys SET sign_count=5")
    return _sign_in(store, fake, 4000, sign_count=0)


def _refusal_passkey_removed(store, fake, secret):
    options = store.begin_login(origin=ORIGIN, now=4000)
    assert store.remove_passkey(fake.id) is True
    assert store.passkeys() == []
    return store.finish_login(fake.assertion(options), origin=ORIGIN, keep=False, now=4001)


def _refusal_after_recover(store, fake, secret):
    options = store.begin_login(origin=ORIGIN, now=4000)
    store.enroll("another synthetic owner password", secret, _code(secret, 4010), now=4010, recover=True)
    assert store.passkeys() == [] and store.begin_login(origin=ORIGIN, now=4011) is None
    return store.finish_login(fake.assertion(options), origin=ORIGIN, keep=False, now=4012)


def _refusal_register_challenge(store, fake, secret):
    options = store.begin_registration(PASSWORD, _code(secret, 4000), origin=ORIGIN, now=4000)
    return store.finish_login(fake.assertion(options, rp_id="localhost"), origin=ORIGIN, keep=False, now=4001)


@pytest.mark.parametrize("refusal", [
    _refusal_uv_off, _refusal_up_off, _refusal_wrong_origin, _refusal_wrong_port, _refusal_wrong_rp_id,
    _refusal_create_type, _refusal_expired_challenge, _refusal_challenge_at_expiry,
    _refusal_unknown_challenge, _refusal_unknown_credential, _refusal_other_key_signature,
    _refusal_flipped_signature, _refusal_wrong_user_handle, _refusal_counter_not_increasing,
    _refusal_counter_going_back, _refusal_counter_reset_to_zero, _refusal_passkey_removed,
    _refusal_after_recover, _refusal_register_challenge,
], ids=lambda function: function.__name__.removeprefix("_refusal_"))
def test_sign_in_refusal_is_counted(owner, refusal):
    store, secret = owner
    fake = _register(store, secret, 3030)
    before = _failures(store)
    assert refusal(store, fake, secret) is None
    assert _failures(store) == before + 1
    assert _rows(store, "SELECT COUNT(*) FROM sessions WHERE created>=4000") == [(0,)]
    store.available()


def test_reused_challenge_is_refused_and_counted(owner):
    store, secret = owner
    fake = _register(store, secret, 3030)
    options = store.begin_login(origin=ORIGIN, now=4000)
    answer = fake.assertion(options)
    assert store.finish_login(answer, origin=ORIGIN, keep=False, now=4001)
    assert _challenges(store) == []
    # Zero counters are accepted, so only the single-use challenge stops this.
    assert store.finish_login(answer, origin=ORIGIN, keep=False, now=4002) is None
    assert store.finish_login(fake.assertion(options), origin=ORIGIN, keep=False, now=4003) is None
    assert _failures(store) == 2


def test_concurrent_answers_to_one_challenge_make_one_session(owner):
    from concurrent.futures import ThreadPoolExecutor
    store, secret = owner
    fake = _register(store, secret, 3030)
    answer = fake.assertion(store.begin_login(origin=ORIGIN, now=4000))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.finish_login(answer, origin=ORIGIN, keep=False, now=4001), range(4)))
    assert len([token for token in results if token]) == 1
    assert _failures(store) == 3


def test_failed_check_still_consumes_the_challenge(owner):
    store, secret = owner
    fake = _register(store, secret, 3030)
    options = store.begin_login(origin=ORIGIN, now=4000)
    assert store.finish_login(fake.assertion(options, uv=False), origin=ORIGIN, keep=False, now=4001) is None
    assert store.finish_login(fake.assertion(options), origin=ORIGIN, keep=False, now=4002) is None


def test_challenge_just_before_expiry_is_accepted(owner):
    store, secret = owner
    fake = _register(store, secret, 3030)
    options = store.begin_login(origin=ORIGIN, now=4000)
    assert store.finish_login(fake.assertion(options), origin=ORIGIN, keep=False, now=4299)


@pytest.mark.parametrize("answer", [
    None, 7, "", "not json", "[]", "{}", '{"id": "x"}', "x" * 200_000,
    json.dumps({"id": "A" * 22, "rawId": "A" * 22, "type": "public-key", "response": {"clientDataJSON": "!!!"}}),
    json.dumps({"response": {"clientDataJSON": b64url(b'{"challenge": 5}')}}),
    json.dumps({"response": {"clientDataJSON": b64url(b'{"challenge": "' + b64url(bytes(31)).encode() + b'"}')}}),
])
def test_malformed_sign_in_answer_is_refused_and_counted(owner, answer):
    store, secret = owner
    _register(store, secret, 3030)
    store.begin_login(origin=ORIGIN, now=4000)
    assert store.finish_login(answer, origin=ORIGIN, keep=False, now=4001) is None
    assert _failures(store) == 1


def test_five_passkey_refusals_block_both_sign_in_methods(owner):
    store, secret = owner
    fake = _register(store, secret, 3030)
    held = store.begin_login(origin=ORIGIN, now=4000)
    for attempt in range(5):
        assert _sign_in(store, fake, 4000 + attempt, uv=False) is None
    assert _failures(store) == 5
    assert store.begin_login(origin=ORIGIN, now=4010) is None
    assert store.finish_login(fake.assertion(held), origin=ORIGIN, keep=False, now=4010) is None
    assert store.login(PASSWORD, _code(secret, 4020), now=4020) is None
    assert store.begin_registration(PASSWORD, _code(secret, 4030), origin=ORIGIN, now=4030) is None
    # Once the block has passed, a good passkey signs in again and clears it.
    assert _sign_in(store, fake, 5000)
    assert _failures(store) == 0


def test_begin_login_without_passkeys_returns_none_and_writes_nothing(owner):
    store, _ = owner
    before = store.path.read_bytes()
    assert store.begin_login(origin=ORIGIN, now=4000) is None
    assert store.path.read_bytes() == before


# --- setup (begin_registration) needs a fresh password + code ------------------

def test_begin_registration_refuses_wrong_code_and_counts(owner):
    store, secret = owner
    wrong = f"{(int(_code(secret, 3030)) + 1) % 1_000_000:06d}"
    assert store.begin_registration(PASSWORD, wrong, origin=ORIGIN, now=3030) is None
    assert store.begin_registration("wrong password", _code(secret, 3030), origin=ORIGIN, now=3030) is None
    assert _failures(store) == 2
    assert _challenges(store) == []


def test_begin_registration_refuses_a_code_already_used_to_sign_in(owner):
    store, secret = owner
    code = _code(secret, 3030)
    assert store.login(PASSWORD, code, now=3030)
    assert store.begin_registration(PASSWORD, code, origin=ORIGIN, now=3040) is None
    assert _failures(store) == 1
    assert _challenges(store) == []
    assert store.begin_registration(PASSWORD, _code(secret, 3060), origin=ORIGIN, now=3060)


def test_begin_registration_refused_while_blocked_like_login(owner):
    store, secret = owner
    for _ in range(5):
        assert store.login("wrong password", "000000", now=3030) is None
    assert store.begin_registration(PASSWORD, _code(secret, 3040), origin=ORIGIN, now=3040) is None
    assert _challenges(store) == []
    # Exactly like a refused password sign-in while blocked: the count is not changed.
    assert _failures(store) == 5


def test_begin_registration_success_advances_the_code_and_resets_the_throttle(owner):
    store, secret = owner
    assert store.login("wrong password", "000000", now=3030) is None
    code = _code(secret, 3060)
    assert store.begin_registration(PASSWORD, code, origin=ORIGIN, now=3060)
    assert _failures(store) == 0
    assert store.login(PASSWORD, code, now=3061) is None
    assert _rows(store, "SELECT COUNT(*) FROM sessions") == [(0,)]


def test_five_failed_setups_block_both_sign_in_methods(owner):
    store, secret = owner
    fake = _register(store, secret, 3030)
    for _ in range(5):
        assert store.begin_registration("wrong password", "000000", origin=ORIGIN, now=3100) is None
    assert _failures(store) == 5
    assert store.login(PASSWORD, _code(secret, 3120), now=3120) is None
    assert store.begin_login(origin=ORIGIN, now=3120) is None
    assert len(store.passkeys()) == 1 and fake


@pytest.mark.parametrize("password,code", [(None, "123456"), ("x" * 1025, "123456"), (PASSWORD, None), (PASSWORD, "12345"), (PASSWORD, 123456)])
def test_begin_registration_malformed_input_is_refused(owner, password, code):
    store, _ = owner
    assert store.begin_registration(password, code, origin=ORIGIN, now=3030) is None
    assert _challenges(store) == []


# --- finish_registration -------------------------------------------------------

def _setup_answer(store, secret, at, fake=None, **answer):
    fake = fake or FakeAuthenticator()
    options = store.begin_registration(PASSWORD, _code(secret, at), origin=ORIGIN, now=at)
    return fake, fake.register(options, **answer)


@pytest.mark.parametrize("answer", [
    {"uv": False}, {"up": False}, {"origin": "https://evil.example"}, {"rp_id": "evil.example"},
    {"kind": "webauthn.get"}, {"fmt": "packed"},
], ids=lambda answer: next(iter(answer)))
def test_finish_registration_refuses_bad_attestation_and_stores_nothing(owner, answer):
    store, secret = owner
    _, response = _setup_answer(store, secret, 3030, **answer)
    assert store.finish_registration(response, origin=ORIGIN, now=3031) is False
    assert store.passkeys() == []
    assert _challenges(store) == []  # the challenge is spent


def test_finish_registration_refuses_expired_and_reused_challenges(owner):
    store, secret = owner
    _, response = _setup_answer(store, secret, 3030)
    assert store.finish_registration(response, origin=ORIGIN, now=3331) is False
    fake, response = _setup_answer(store, secret, 3360)
    assert store.finish_registration(response, origin=ORIGIN, now=3361) is True
    assert store.finish_registration(response, origin=ORIGIN, now=3362) is False
    assert [key_id for key_id, _ in store.passkeys()] == [fake.id]


def test_finish_registration_refuses_a_login_challenge(owner):
    store, secret = owner
    _register(store, secret, 3030)
    options = store.begin_login(origin=ORIGIN, now=4000)
    response = FakeAuthenticator().register(options, rp_id="localhost")
    assert store.finish_registration(response, origin=ORIGIN, now=4001) is False
    assert len(store.passkeys()) == 1
    assert _challenges(store) == []


def test_finish_registration_failure_does_not_count_toward_the_throttle(owner):
    store, secret = owner
    _, response = _setup_answer(store, secret, 3030, uv=False)
    assert store.finish_registration(response, origin=ORIGIN, now=3031) is False
    assert store.finish_registration("garbage", origin=ORIGIN, now=3032) is False
    assert _failures(store) == 0


@pytest.mark.parametrize("answer", [None, 7, "", "not json", "{}", "x" * 200_000])
def test_finish_registration_malformed_answer_is_false(owner, answer):
    store, secret = owner
    store.begin_registration(PASSWORD, _code(secret, 3030), origin=ORIGIN, now=3030)
    assert store.finish_registration(answer, origin=ORIGIN, now=3031) is False
    assert store.passkeys() == []


@pytest.mark.parametrize("length,accepted", [(11, False), (12, True), (768, True), (769, False)])
def test_registration_refuses_credential_ids_available_would_reject(owner, length, accepted):
    store, secret = owner
    _, response = _setup_answer(store, secret, 3030, FakeAuthenticator(credential_id=b"\x01" * length))
    assert store.finish_registration(response, origin=ORIGIN, now=3031) is accepted
    assert len(store.passkeys()) == int(accepted)
    store.available()


def test_registering_the_same_credential_twice_is_refused(owner):
    store, secret = owner
    fake = _register(store, secret, 3030)
    _, response = _setup_answer(store, secret, 3060, fake)
    assert store.finish_registration(response, origin=ORIGIN, now=3061) is False
    assert len(store.passkeys()) == 1


def test_registration_refused_while_blocked(owner):
    store, secret = owner
    _, response = _setup_answer(store, secret, 3030)
    for _ in range(5):
        assert store.login("wrong password", "000000", now=3031) is None
    assert store.finish_registration(response, origin=ORIGIN, now=3032) is False
    assert store.passkeys() == []


def test_two_passkeys_each_sign_in_and_removal_is_per_key(owner):
    store, secret = owner
    first = _register(store, secret, 3030)
    second = _register(store, secret, 3060)
    assert {key_id for key_id, _ in store.passkeys()} == {first.id, second.id}
    assert _sign_in(store, first, 4000) and _sign_in(store, second, 4010)
    assert store.remove_passkey(first.id) is True
    assert store.remove_passkey(first.id) is False
    assert _sign_in(store, first, 4020) is None
    assert _sign_in(store, second, 4030)


# --- untrusted challenge rows and older databases --------------------------------

@pytest.mark.parametrize("purpose,expires", [
    ("login", "soon"), ("login", b"\x00"), ("login", float("inf")), ("login", -1.0),
    ("login", 4000 + 10_000), (7, 4300), ("bogus", 4300), ("register", 4300),
])
def test_malformed_challenge_row_is_refused_not_raised(owner, purpose, expires):
    store, secret = owner
    fake = _register(store, secret, 3030)
    challenge = bytes(range(32))
    _execute(store, "INSERT INTO passkey_challenges VALUES(?,?,?)", (hashlib.sha256(challenge).hexdigest(), purpose, expires))
    store.available()
    answer = fake.assertion('{"rpId": "localhost", "challenge": "' + b64url(challenge) + '"}')
    assert store.finish_login(answer, origin=ORIGIN, keep=False, now=4001) is None
    assert _failures(store) == 1
    assert _challenges(store) == []
    store.available()
    # A good flow still works afterwards.
    assert _sign_in(store, fake, 4010)


def test_malformed_unrelated_challenge_rows_do_not_break_sign_in(owner):
    store, secret = owner
    fake = _register(store, secret, 3030)
    _execute(store, "INSERT INTO passkey_challenges VALUES(?,?,?)", ("junk", "login", "never"))
    _execute(store, "INSERT INTO passkey_challenges VALUES(?,?,?)", (b"\x00", 5, 1e300))
    assert _sign_in(store, fake, 4000)
    assert _challenges(store) == []


def test_unauthenticated_login_options_are_capped(owner):
    store, secret = owner
    fake = _register(store, secret, 3030)
    held = store.begin_registration(PASSWORD, _code(secret, 3060), origin=ORIGIN, now=3060)
    for _ in range(100):
        store.begin_login(origin=ORIGIN, now=3070)
    assert len(_challenges(store)) <= 40
    # Flooding sign-in options never evicts a pending setup challenge.
    assert store.finish_registration(FakeAuthenticator().register(held), origin=ORIGIN, now=3071) is True
    assert _sign_in(store, fake, 3080)


def _old_schema_store(tmp_path):
    from app.auth import AuthStore, HASHER
    directory = tmp_path / "old-auth"
    directory.mkdir(mode=0o700)
    secret = pyotp.random_base32()
    path = directory / "owner.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        with db:
            db.execute("CREATE TABLE owner(id INTEGER PRIMARY KEY CHECK(id=1), password TEXT NOT NULL, secret TEXT NOT NULL, counter INTEGER NOT NULL)")
            db.execute("CREATE TABLE sessions(token TEXT PRIMARY KEY, csrf TEXT NOT NULL, created REAL NOT NULL, seen REAL NOT NULL)")
            db.execute("CREATE TABLE throttle(id INTEGER PRIMARY KEY CHECK(id=1), failures INTEGER NOT NULL, blocked REAL NOT NULL)")
            db.execute("INSERT INTO throttle VALUES(1,0,0)")
            db.execute("INSERT INTO owner VALUES(1,?,?,?)", (HASHER.hash(PASSWORD), secret, 100))
    path.chmod(0o600)
    return AuthStore(directory), secret


def test_old_schema_reads_tolerate_missing_tables_without_writing(tmp_path):
    store, secret = _old_schema_store(tmp_path)
    before = store.path.read_bytes()
    assert store.passkeys() == []
    assert store.begin_login(origin=ORIGIN, now=4000) is None
    assert store.remove_passkey("A" * 22) is False
    assert store.path.read_bytes() == before
    # Setup is a write: it brings the schema up to date first.
    fake = _register(store, secret, 3030)
    assert _sign_in(store, fake, 4000)
    store.available()


def test_kept_table_without_passkey_tables(tmp_path):
    store, secret = _old_schema_store(tmp_path)
    _execute(store, "CREATE TABLE kept_sessions(token TEXT PRIMARY KEY)")
    before = store.path.read_bytes()
    assert store.passkeys() == [] and store.begin_login(origin=ORIGIN, now=4000) is None
    assert store.path.read_bytes() == before
    assert store.finish_login("{}", origin=ORIGIN, keep=False, now=4000) is None
    assert store.finish_registration("{}", origin=ORIGIN, now=4000) is False
    assert _failures(store) == 1
    store.available()


def test_passkey_table_without_challenge_table(tmp_path):
    store, secret = _old_schema_store(tmp_path)
    fake = FakeAuthenticator()
    _execute(store, "CREATE TABLE passkeys(id TEXT PRIMARY KEY, public_key BLOB NOT NULL, sign_count INTEGER NOT NULL, created REAL NOT NULL)")
    from app import passkeys as passkey_module
    options = passkey_module.registration_options("localhost", bytes(32), [])
    _, public_key, _ = passkey_module.verify_registration(fake.register(options), bytes(32), "localhost", ORIGIN)
    _execute(store, "INSERT INTO passkeys VALUES(?,?,?,?)", (fake.id, public_key, 0, 1.0))
    assert _sign_in(store, fake, 4000)


# --- app/passkeys.py --------------------------------------------------------------

def test_rp_from_origin():
    from app.passkeys import PasskeyRejected, rp_from_origin
    assert rp_from_origin("https://localhost:8443") == ("localhost", "https://localhost:8443")
    for bad in ("http://localhost:8443", "https://localhost:8443/", "https://user@localhost", "https://", "localhost", None):
        with pytest.raises(PasskeyRejected):
            rp_from_origin(bad)


def test_verify_functions_raise_passkey_rejected_only():
    from app import passkeys as passkey_module
    from app.passkeys import PasskeyRejected
    assert issubclass(PasskeyRejected, ValueError)
    fake = FakeAuthenticator()
    challenge = bytes(range(32))
    options = passkey_module.registration_options("localhost", challenge, [])
    key_id, public_key, count = passkey_module.verify_registration(fake.register(options), challenge, "localhost", ORIGIN)
    assert key_id == fake.credential_id and count == 0
    for answer in ("{}", fake.register(options, uv=False), None):
        with pytest.raises(PasskeyRejected):
            passkey_module.verify_registration(answer, challenge, "localhost", ORIGIN)
    login = passkey_module.authentication_options("localhost", challenge)
    assert passkey_module.verify_authentication(fake.assertion(login, sign_count=3), challenge, "localhost", ORIGIN, public_key, 2) == 3
    for answer, stored in ((fake.assertion(login, sign_count=2), 2), (fake.assertion(login, uv=False), 0), ("[]", 0)):
        with pytest.raises(PasskeyRejected):
            passkey_module.verify_authentication(answer, challenge, "localhost", ORIGIN, public_key, stored)
    with pytest.raises(PasskeyRejected):
        passkey_module.verify_authentication(fake.assertion(login), challenge, "localhost", ORIGIN, b"not a key", 0)
    assert options_challenge(login) == challenge


@pytest.mark.parametrize("cross_origin", [True, "true", 1])
def test_cross_origin_client_data_is_refused(cross_origin):
    from app import passkeys as passkey_module
    from app.passkeys import PasskeyRejected
    fake = FakeAuthenticator()
    challenge = bytes(range(32))
    options = passkey_module.registration_options("localhost", challenge, [])
    with pytest.raises(PasskeyRejected):
        passkey_module.verify_registration(fake.register(options, cross_origin=cross_origin), challenge, "localhost", ORIGIN)
    _, public_key, _ = passkey_module.verify_registration(fake.register(options), challenge, "localhost", ORIGIN)
    login = passkey_module.authentication_options("localhost", challenge)
    with pytest.raises(PasskeyRejected):
        passkey_module.verify_authentication(fake.assertion(login, cross_origin=cross_origin), challenge, "localhost", ORIGIN, public_key, 0)
    assert passkey_module.verify_authentication(fake.assertion(login), challenge, "localhost", ORIGIN, public_key, 0) == 0


def test_cross_origin_answers_are_refused_by_the_store(owner):
    store, secret = owner
    _, response = _setup_answer(store, secret, 3030, cross_origin=True)
    assert store.finish_registration(response, origin=ORIGIN, now=3031) is False
    assert store.passkeys() == []
    fake = _register(store, secret, 3060)
    assert _sign_in(store, fake, 4000, cross_origin=True) is None
    assert _failures(store) == 1


def test_options_timeout_matches_the_challenge_lifetime(owner):
    from app.auth import CHALLENGE_SECONDS
    store, secret = owner
    _register(store, secret, 3030)
    registration = json.loads(store.begin_registration(PASSWORD, _code(secret, 3060), origin=ORIGIN, now=3060))
    login = json.loads(store.begin_login(origin=ORIGIN, now=4000))
    assert registration["timeout"] == login["timeout"] == CHALLENGE_SECONDS * 1000 == 300000


def test_icloud_backed_up_passkey_registers_and_signs_in(owner):
    store, secret = owner
    fake = _register(store, secret, 3030, backup=True)
    assert _sign_in(store, fake, 4000, backup=True)
    assert _failures(store) == 0


# --- the endpoints in OwnerAuthMiddleware -------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from app.auth_web import LIMIT  # noqa: E402

OWN = {"Origin": ORIGIN}
CHECK_MESSAGE = "Password or code not accepted. If you just used this code to sign in, wait for the next one."


@pytest.fixture
def web(owner, make_vault, monkeypatch):
    from tests.conftest import entities_yaml
    store, secret = owner
    monkeypatch.setenv("ONEOS_VAULT", str(make_vault(entities_yaml("sample"))))
    monkeypatch.setenv("ONEOS_AUTH_STATE_DIR", str(store.directory))
    monkeypatch.setenv("ONEOS_AUTH_MODE", "required")
    monkeypatch.setenv("ONEOS_ORIGIN", ORIGIN)
    from app.main import app
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        yield client, store, secret


def _nonce(client):
    """GET the sign-in page (which sets the login cookie) and return its form token."""
    import re
    page = client.get("/login")
    assert page.status_code == 200
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


def _cookie_headers(response, name):
    return [value for value in response.headers.get_list("set-cookie") if value.startswith(name + "=")]


def _store_session(client, store, secret, at=3030):
    """A kept session made at a store time long past (kept sessions have no clock limit)."""
    token = store.login(PASSWORD, _code(secret, at), keep=True, now=at)
    client.cookies.set("__Host-oneos", token, domain="localhost.local")
    return store.session(token)


def _options(client, nonce):
    return client.post("/login/passkey/options", content=b"{}",
                       headers=dict(OWN, **{"X-CSRF-Token": nonce, "Content-Type": "application/json"}))


def _passkey_post(client, nonce, body):
    return client.post("/login/passkey", content=json.dumps(body).encode(),
                       headers=dict(OWN, **{"X-CSRF-Token": nonce, "Content-Type": "application/json"}))


def _web_passkey_sign_in(client, fake, *, keep, **answer):
    nonce = _nonce(client)
    options = _options(client, nonce)
    assert options.status_code == 200, options.text
    assert options.headers["content-type"].startswith("application/json")
    return _passkey_post(client, nonce, {"credential": json.loads(fake.assertion(options.text, **answer)), "keep": keep})


def _begin(client, csrf, password, code):
    return client.post("/settings/passkey/begin", data={"password": password, "code": code},
                       headers=dict(OWN, **{"X-CSRF-Token": csrf}))


def _finish(client, csrf, answer):
    return client.post("/settings/passkey/finish", content=answer.encode() if isinstance(answer, str) else answer,
                       headers=dict(OWN, **{"X-CSRF-Token": csrf, "Content-Type": "application/json"}))


def _tree_digest(root, *, skip):
    """SHA-256 over every path under `root` with its kind and, for files, its bytes.

    `skip` is left out: in these fixtures the auth state directory sits under
    the same temporary root as the vault, and sign-in is meant to write it.
    """
    import os
    from pathlib import Path
    root, skip = Path(root), Path(skip)
    total = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path == skip or skip in path.parents:
            continue
        relative = path.relative_to(root).as_posix().encode()
        if path.is_symlink():
            total.update(b"L" + relative + b"\0" + os.readlink(path).encode() + b"\0")
        elif path.is_dir():
            total.update(b"D" + relative + b"\0")
        else:
            total.update(b"F" + relative + b"\0" + path.read_bytes() + b"\0")
    return total.hexdigest()


def test_setup_then_sign_out_then_touch_id_sign_in_loads_home(web, monkeypatch):
    import os
    import time
    from app import auth
    client, store, secret = web
    vault = os.environ["ONEOS_VAULT"]
    vault_before = _tree_digest(vault, skip=store.directory)
    nonce = _nonce(client)
    real = time.time()
    signed_in = client.post("/login", data={"password": PASSWORD, "code": pyotp.TOTP(secret).at(real), "keep": "1", "csrf_token": nonce}, headers=OWN)
    assert signed_in.status_code == 303
    csrf = store.session(client.cookies.get("__Host-oneos"))
    # Setup needs a fresh code: move to the next 30-second window.
    monkeypatch.setattr(auth.time, "time", lambda: real + 30)
    begun = _begin(client, csrf, PASSWORD, pyotp.TOTP(secret).at(real + 30))
    assert begun.status_code == 200 and begun.headers["content-type"].startswith("application/json")
    assert json.loads(begun.text)["timeout"] == 300000
    fake = FakeAuthenticator()
    finished = _finish(client, csrf, fake.register(begun.text))
    assert finished.status_code == 200 and finished.json() == {"ok": True}
    assert [key_id for key_id, _ in store.passkeys()] == [fake.id]
    assert client.post("/logout", headers=dict(OWN, **{"X-CSRF-Token": csrf})).status_code == 303
    assert client.get("/").status_code == 303
    page = client.get("/login").text
    assert "Sign in with Touch ID" in page
    response = _web_passkey_sign_in(client, fake, keep=True)
    assert response.status_code == 200 and response.json() == {"redirect": "/"}
    assert _cookie_headers(response, "__Host-oneos-login") and "Max-Age=0" in _cookie_headers(response, "__Host-oneos-login")[0]
    home = client.get("/")
    assert home.status_code == 200
    assert _failures(store) == 0
    # Sign-in, setup and sign-out never touch the vault.
    assert _tree_digest(vault, skip=store.directory) == vault_before


@pytest.mark.parametrize("keep,age", [(True, 34560000), (False, 43200)])
def test_passkey_sign_in_cookie_follows_keep(web, keep, age):
    client, store, secret = web
    fake = _register(store, secret, 3030)
    response = _web_passkey_sign_in(client, fake, keep=keep)
    assert response.status_code == 200
    (cookie,) = _cookie_headers(response, "__Host-oneos")
    for flag in (f"Max-Age={age}", "Secure", "HttpOnly", "SameSite=strict", "Path=/"):
        assert flag in cookie
    issued = _cookie_headers(response, "__Host-oneos-issued")
    if keep:
        assert len(issued) == 1 and f"Max-Age={age}" in issued[0]
    else:
        assert all("Max-Age=0" in value for value in issued)
    token = client.cookies.get("__Host-oneos")
    assert store.session_info(token).kept is keep
    assert client.get("/docs").status_code == 200


def test_password_backup_still_signs_in_with_a_passkey_stored(web):
    client, store, secret = web
    _register(store, secret, 3030)
    nonce = _nonce(client)
    response = client.post("/login", data={"password": PASSWORD, "code": pyotp.TOTP(secret).now(), "csrf_token": nonce}, headers=OWN)
    assert response.status_code == 303
    assert "Max-Age=43200" in _cookie_headers(response, "__Host-oneos")[0]


def test_options_without_a_passkey_is_409_and_writes_nothing(web):
    client, store, _ = web
    nonce = _nonce(client)
    before = store.path.read_bytes()
    response = _options(client, nonce)
    assert response.status_code == 409 and response.json() == {"error": "no-passkey"}
    assert store.path.read_bytes() == before


def test_options_while_blocked_is_429_never_no_passkey(web):
    client, store, secret = web
    fake = _register(store, secret, 3030)
    for _ in range(5):
        assert store.login("wrong password", "000000") is None
    nonce = _nonce(client)
    response = _options(client, nonce)
    assert response.status_code == 429 and response.json() == {"error": "blocked"}
    assert _challenges(store) == []
    assert len(store.passkeys()) == 1 and fake


def _replay(client, store, fake):
    nonce = _nonce(client)
    options = _options(client, nonce).text
    answer = json.loads(fake.assertion(options))
    assert _passkey_post(client, nonce, {"credential": answer, "keep": False}).status_code == 200
    client.cookies.delete("__Host-oneos")
    return _passkey_post(client, _nonce(client), {"credential": answer, "keep": False})


@pytest.mark.parametrize("case", [
    {"uv": False}, {"up": False}, {"origin": "https://evil.example"}, {"rp_id": "evil.example"},
    {"cross_origin": True}, {"flip_signature": True}, "replay", "unknown", "removed", "empty",
], ids=str)
def test_passkey_sign_in_refusals_are_401_and_counted(web, case):
    client, store, secret = web
    fake = _register(store, secret, 3030)
    if case == "replay":
        response = _replay(client, store, fake)
        before = 0
    else:
        before = _failures(store)
        if case == "unknown":
            response = _web_passkey_sign_in(client, FakeAuthenticator(), keep=True)
        elif case == "removed":
            nonce = _nonce(client)
            options = _options(client, nonce).text
            assert store.remove_passkey(fake.id)
            response = _passkey_post(client, nonce, {"credential": json.loads(fake.assertion(options)), "keep": True})
        elif case == "empty":
            response = _passkey_post(client, _nonce(client), {"credential": {}, "keep": True})
        else:
            response = _web_passkey_sign_in(client, fake, keep=True, **case)
    assert response.status_code == 401 and response.json() == {"error": "failed"}
    assert _cookie_headers(response, "__Host-oneos") == []
    assert _failures(store) == before + 1
    assert client.get("/docs").status_code == 303


def test_sign_in_page_offers_touch_id_only_with_a_passkey(web):
    import re
    client, store, secret = web

    def check_password_form(page):
        assert '<form id="sign-in" method="post" action="/login">' in page
        assert 'name="password"' in page and 'name="code"' in page and 'name="csrf_token"' in page
        assert re.search(r'<input[^>]*type="checkbox"[^>]*name="keep"[^>]*value="1"[^>]*checked', page)
        assert "Keep me signed in on this Mac" in page
        # Nothing before sign-in needs a static file (they all need a session).
        assert "src=" not in page and "/static" not in page and "<link" not in page

    response = client.get("/login")
    page = response.text
    check_password_form(page)
    assert "Sign in with Touch ID" not in page and "Use password and code instead" not in page
    assert "<script" not in page
    _register(store, secret, 3030)
    response = client.get("/login")
    page = response.text
    check_password_form(page)
    for copy in ("Sign in with Touch ID", "Keep me signed in on this Mac", "Use password and code instead"):
        assert copy in page
    assert page.count("<script>") == 1 and page.count("<script") == 1
    # Without JavaScript the Touch ID controls stay hidden and the form is the page.
    assert re.search(r'<div id="touch-id" hidden>', page)
    assert re.search(r'<p id="use-password" hidden>', page)
    script = page.split("<script>")[1].split("</script>")[0]
    assert "innerHTML" not in script and "parseRequestOptionsFromJSON" not in script
    assert "/login/passkey/options" in script and "/login/passkey" in script


def test_sign_in_page_content_security_policy_allows_only_its_own_script(web):
    import base64
    client, store, secret = web
    policy = client.get("/login").headers["content-security-policy"]
    assert "default-src 'none'" in policy and "frame-ancestors 'none'" in policy and "script-src" not in policy
    _register(store, secret, 3030)
    response = client.get("/login")
    script = response.text.split("<script>")[1].split("</script>")[0]
    expected = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    policy = response.headers["content-security-policy"]
    assert f"script-src 'sha256-{expected}'" in policy
    assert "connect-src 'self'" in policy and "form-action 'self'" in policy and "base-uri 'none'" in policy


@pytest.mark.parametrize("path", ["/login/passkey/options", "/login/passkey"])
def test_passkey_paths_answer_post_only_and_never_render_the_page(web, path):
    client, store, secret = web
    _register(store, secret, 3030)
    for method in ("GET", "HEAD", "PUT", "DELETE"):
        response = client.request(method, path)
        assert response.status_code == 405, method
        assert _cookie_headers(response, "__Host-oneos-login") == []


def _pre_sign_in_cases(path):
    good = b"{}" if path.endswith("options") else json.dumps({"credential": {}, "keep": True}).encode()
    json_type = {"Content-Type": "application/json"}
    bad_bodies = [b"[]", b'"x"', b"7", b"null", b"not json", b"NaN", b'{"a": NaN}', b"\xff\xfe", b"", b"[" * 5000]
    if path == "/login/passkey":
        bad_bodies += [json.dumps(body).encode() for body in (
            {}, {"credential": "x"}, {"credential": []}, {"credential": {}, "keep": "yes"},
            {"credential": {}, "keep": 1}, {"credential": {}, "keep": True, "extra": 1})]
    return good, json_type, bad_bodies


@pytest.mark.parametrize("path", ["/login/passkey/options", "/login/passkey"])
def test_pre_sign_in_endpoints_refuse_bad_token_origin_size_and_body(web, path):
    client, store, secret = web
    _register(store, secret, 3030)
    session_csrf = store.session(store.login(PASSWORD, _code(secret, 3060), keep=True, now=3060))
    nonce = _nonce(client)
    good, json_type, bad_bodies = _pre_sign_in_cases(path)
    with_token = dict(OWN, **json_type, **{"X-CSRF-Token": nonce})
    refusals = [
        (dict(OWN, **json_type), good, 403),  # no token
        (dict(with_token, **{"X-CSRF-Token": "x" * 43}), good, 403),  # wrong token
        (dict(with_token, **{"X-CSRF-Token": session_csrf}), good, 403),  # a session token is not the sign-in token
        (dict(with_token, Origin="https://evil.invalid"), good, 403),
        ({k: v for k, v in with_token.items() if k != "Origin"}, good, 403),
        (with_token, b"{" + b" " * LIMIT + b"}", 413),
        (dict(with_token, **{"Content-Type": "application/x-www-form-urlencoded"}), good, 400),
        (dict(with_token, **{"Content-Type": "text/plain"}), good, 400),
        ({k: v for k, v in with_token.items() if k != "Content-Type"}, good, 400),
    ] + [(with_token, body, 400) for body in bad_bodies]
    for headers, body, status in refusals:
        response = client.post(path, content=body, headers=headers)
        assert response.status_code == status, (headers, body[:40], response.status_code, response.text)
        assert _cookie_headers(response, "__Host-oneos") == []
    # No refusal reached the store: nothing counted, no challenge handed out.
    assert _failures(store) == 0
    assert _challenges(store) == []
    # The token in a form field is accepted for the password form only, never for JSON.
    assert client.post(path, content=good, headers=dict(OWN, **json_type), params={"csrf_token": nonce}).status_code == 403


def _settings_cases(path, fake_id):
    if path == "/settings/passkey/begin":
        return ("application/x-www-form-urlencoded", f"password={PASSWORD}&code=123456".encode(),
                [b"password=a&password=b&code=123456", b"code=123456", b"password=a&code=123456&extra=1", b"\xff=\xfe"])
    if path == "/settings/passkey/remove":
        return ("application/x-www-form-urlencoded", f"id={fake_id}".encode(),
                [f"id={fake_id}&id={fake_id}".encode(), b"", b"other=1", f"id={fake_id}&extra=1".encode()])
    return ("application/json", b"{}", [b"[]", b'"x"', b"null", b"not json", b"NaN", b"\xff", b"", b"[" * 5000])


@pytest.mark.parametrize("path", ["/settings/passkey/begin", "/settings/passkey/finish", "/settings/passkey/remove"])
def test_settings_endpoints_refuse_bad_token_origin_size_and_body(web, path):
    client, store, secret = web
    fake = _register(store, secret, 3030)
    csrf = _store_session(client, store, secret, 3060)
    nonce = _nonce(client)
    content_type, good, bad_bodies = _settings_cases(path, fake.id)
    other_type = "application/json" if content_type != "application/json" else "application/x-www-form-urlencoded"
    with_token = dict(OWN, **{"Content-Type": content_type, "X-CSRF-Token": csrf})
    refusals = [
        ({k: v for k, v in with_token.items() if k != "X-CSRF-Token"}, good, 403),
        (dict(with_token, **{"X-CSRF-Token": "x" * 43}), good, 403),
        (dict(with_token, **{"X-CSRF-Token": nonce}), good, 403),  # the sign-in token is not a session token
        (dict(with_token, Origin="https://evil.invalid"), good, 403),
        ({k: v for k, v in with_token.items() if k != "Origin"}, good, 403),
        (with_token, b"x" * (LIMIT + 1), 413),
        (dict(with_token, **{"Content-Type": other_type}), good, 400),
        ({k: v for k, v in with_token.items() if k != "Content-Type"}, good, 400),
    ] + [(with_token, body, 400) for body in bad_bodies]
    for headers, body, status in refusals:
        response = client.post(path, content=body, headers=headers)
        assert response.status_code == status, (headers, body[:40], response.status_code, response.text)
    assert [key_id for key_id, _ in store.passkeys()] == [fake.id]
    assert _challenges(store) == []
    assert _failures(store) == 0


@pytest.mark.parametrize("path", ["/settings/passkey/begin", "/settings/passkey/finish", "/settings/passkey/remove"])
def test_settings_endpoints_require_a_session(web, path):
    client, store, secret = web
    fake = _register(store, secret, 3030)
    nonce = _nonce(client)
    content_type, good, _ = _settings_cases(path, fake.id)
    response = client.post(path, content=good, headers=dict(OWN, **{"Content-Type": content_type, "X-CSRF-Token": nonce}))
    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == "/login"
    assert len(store.passkeys()) == 1 and _challenges(store) == [] and _failures(store) == 0


NEW_PATHS = ["/login/passkey/options", "/login/passkey", "/settings/passkey/begin",
             "/settings/passkey/finish", "/settings/passkey/remove", "/logout/everywhere"]


@pytest.mark.parametrize("path", NEW_PATHS)
def test_new_endpoints_refuse_a_wrong_host_before_anything_else(web, path):
    """A wrong Host is a 400 with nothing stored, spent or counted, even with a good request."""
    client, store, secret = web
    fake = _register(store, secret, 3030)
    nonce = _nonce(client)
    csrf = _store_session(client, store, secret, 3060)
    token = nonce if path.startswith("/login/") else csrf
    if path == "/login/passkey/options":
        content_type, body = "application/json", b"{}"
    elif path == "/login/passkey":
        options = _options(client, nonce)
        assert options.status_code == 200
        content_type = "application/json"
        body = json.dumps({"credential": json.loads(fake.assertion(options.text)), "keep": True}).encode()
    elif path == "/settings/passkey/begin":
        content_type, body = "application/x-www-form-urlencoded", f"password={PASSWORD}&code={pyotp.TOTP(secret).now()}".encode()
    elif path == "/settings/passkey/finish":
        begun = _begin(client, csrf, PASSWORD, pyotp.TOTP(secret).now())
        assert begun.status_code == 200
        content_type, body = "application/json", FakeAuthenticator().register(begun.text).encode()
    elif path == "/settings/passkey/remove":
        content_type, body = "application/x-www-form-urlencoded", f"id={fake.id}".encode()
    else:
        content_type, body = "application/x-www-form-urlencoded", b""
    headers = dict(OWN, **{"Content-Type": content_type, "X-CSRF-Token": token})
    before = store.path.read_bytes()
    passkeys, challenges, sessions = store.passkeys(), _challenges(store), _rows(store, "SELECT token FROM sessions")
    for host in ("evil.invalid", "localhost:8444", "localhost"):
        response = client.post(path, content=body, headers=dict(headers, Host=host))
        assert response.status_code == 400 and response.text == "Invalid host", host
        assert "set-cookie" not in response.headers
    assert store.path.read_bytes() == before
    assert store.passkeys() == passkeys and _challenges(store) == challenges
    assert _rows(store, "SELECT token FROM sessions") == sessions
    assert _failures(store) == 0
    # The same request on the right host is accepted (so the 400 above was the Host check).
    assert client.post(path, content=body, headers=headers).status_code in (200, 204, 303)


def test_setup_check_while_blocked_says_wait_and_counts_nothing(web):
    client, store, secret = web
    csrf = _store_session(client, store, secret)
    for _ in range(5):
        assert store.login("wrong password", "000000") is None
    assert _failures(store) == 5
    response = _begin(client, csrf, PASSWORD, pyotp.TOTP(secret).now())
    assert response.status_code == 429
    assert response.json() == {"error": "blocked", "message": "Too many failed sign-ins. Wait a few minutes and try again."}
    assert _failures(store) == 5
    assert _challenges(store) == [] and store.passkeys() == []


def test_setup_check_is_refused_with_a_clear_message_and_counted(web):
    client, store, secret = web
    nonce = _nonce(client)
    code = pyotp.TOTP(secret).now()
    assert client.post("/login", data={"password": PASSWORD, "code": code, "csrf_token": nonce}, headers=OWN).status_code == 303
    csrf = store.session(client.cookies.get("__Host-oneos"))
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    for password, attempt in ((PASSWORD, code), (PASSWORD, wrong), ("wrong password", code)):
        response = _begin(client, csrf, password, attempt)
        assert response.status_code == 401
        assert response.json() == {"error": "check", "message": CHECK_MESSAGE}
    assert _failures(store) == 3
    assert _challenges(store) == [] and store.passkeys() == []


def test_finish_with_a_bad_answer_is_400_and_stores_nothing(web):
    client, store, secret = web
    csrf = _store_session(client, store, secret)
    begun = _begin(client, csrf, PASSWORD, pyotp.TOTP(secret).now())
    assert begun.status_code == 200
    for answer in (FakeAuthenticator().register(begun.text, uv=False), "{}", json.dumps({"id": 5})):
        response = _finish(client, csrf, answer)
        assert response.status_code == 400 and response.json() == {"error": "failed"}
    assert store.passkeys() == []


def test_remove_ends_passkey_sign_in_and_returns_to_the_referring_page(web):
    client, store, secret = web
    fake = _register(store, secret, 3030)
    other = _register(store, secret, 3060)
    nonce = _nonce(client)
    held = _options(client, nonce).text
    csrf = _store_session(client, store, secret, 3090)
    headers = dict(OWN, **{"X-CSRF-Token": csrf})
    response = client.post("/settings/passkey/remove", data={"id": fake.id}, headers=dict(headers, Referer=ORIGIN + "/board?tab=1"))
    assert response.status_code == 303 and response.headers["location"] == "/board?tab=1"
    assert [key_id for key_id, _ in store.passkeys()] == [other.id]
    # Removing an unknown id changes nothing; a foreign or odd referrer goes home.
    for referer in ("https://evil.invalid/board", ORIGIN + "//evil.invalid", "javascript:alert(1)", ORIGIN + "/\\evil.invalid", ""):
        response = client.post("/settings/passkey/remove", data={"id": "A" * 22}, headers=dict(headers, Referer=referer))
        assert response.status_code == 303 and response.headers["location"] == "/", referer
    response = client.post("/settings/passkey/remove", data={"id": other.id}, headers=dict(headers, **{"HX-Request": "true", "Referer": ORIGIN + "/outbox"}))
    assert response.status_code == 204 and response.headers["HX-Redirect"] == "/outbox"
    assert store.passkeys() == []
    # The removed passkey no longer signs in, even with options handed out before.
    client.cookies.delete("__Host-oneos")
    response = _passkey_post(client, nonce, {"credential": json.loads(fake.assertion(held)), "keep": True})
    assert response.status_code == 401
    assert _options(client, _nonce(client)).status_code == 409


def test_request_state_passkeys_for_signed_in_requests(owner, monkeypatch):
    from fastapi import FastAPI, Request
    from app.auth_web import OwnerAuthMiddleware
    store, secret = owner
    monkeypatch.setenv("ONEOS_AUTH_STATE_DIR", str(store.directory))
    monkeypatch.setenv("ONEOS_AUTH_MODE", "required")
    monkeypatch.setenv("ONEOS_ORIGIN", ORIGIN)
    app = FastAPI()
    app.add_middleware(OwnerAuthMiddleware)

    @app.get("/state")
    def state(request: Request):
        return {"passkeys": request.state.passkeys}

    @app.get("/static/state")
    def static_state(request: Request):
        return {"passkeys": request.state.passkeys}

    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        _store_session(client, store, secret)
        assert client.get("/state").json() == {"passkeys": []}
        fake = _register(store, secret, 3060)
        assert client.get("/state").json() == {"passkeys": [[fake.id, 3061]]}
        # Static files do not need the list: it stays [] without a database read.
        calls = []
        real = type(store).passkeys
        monkeypatch.setattr(type(store), "passkeys", lambda self: calls.append(1) or real(self))
        assert client.get("/static/state").json() == {"passkeys": []}
        assert calls == []
        assert client.get("/state").json() == {"passkeys": [[fake.id, 3061]]}
        assert calls == [1]


# --- Settings -> Sign-in ----------------------------------------------------------

OCT_28_2026 = 1793188800  # 12:00 UTC: the same calendar day in nearly every time zone
OCT_31_2026 = 1793448000


@pytest.fixture
def utc():
    """Render dates in UTC whatever the machine's time zone; restored afterwards."""
    import os
    import time
    before = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if before is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = before
    time.tzset()


class _Forms(HTMLParser):
    """Each <form>: its attributes, its named inputs and its button labels."""

    def __init__(self):
        super().__init__()
        self.forms = []
        self._form = None
        self._button = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self._form = {"action": attrs.get("action"), "method": (attrs.get("method") or "").lower(), "inputs": {}, "buttons": []}
            self.forms.append(self._form)
        elif tag == "input" and self._form is not None and attrs.get("name"):
            self._form["inputs"][attrs["name"]] = attrs
        elif tag == "button" and self._form is not None:
            self._button = ""

    def handle_data(self, data):
        if self._button is not None:
            self._button += data

    def handle_endtag(self, tag):
        if tag == "button" and self._button is not None:
            self._form["buttons"].append(self._button.strip())
            self._button = None
        elif tag == "form":
            self._form = None


def _settings_page(client):
    response = client.get("/")
    assert response.status_code == 200, response.text[:300]
    parser = _Forms()
    parser.feed(response.text)
    return response.text, parser.forms


def _text_of(page):
    import re
    from html import unescape
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", page)))


def test_settings_sign_in_section_without_a_passkey(web):
    client, store, secret = web
    csrf = _store_session(client, store, secret)
    page, forms = _settings_page(client)
    text = _text_of(page)
    assert ">Sign-in<" in page and "Password &amp; two-factor" not in page
    assert "Touch ID on this Mac: not set up" in text
    assert "Touch ID set up" not in text and "Remove Touch ID" not in text
    assert "Set up Touch ID" in text
    assert "Asks for your password and code once, then your fingerprint." in text
    assert "If you just used a code to sign in, wait for the next one." in text
    assert "which also removes Touch ID" in text
    # The setup form calls the vendored script; the inputs carry the right autofill hints.
    setup = next(form for form in forms if "password" in form["inputs"])
    assert setup["inputs"]["password"]["type"] == "password" and setup["inputs"]["password"]["autocomplete"] == "current-password"
    code = setup["inputs"]["code"]
    assert code["autocomplete"] == "one-time-code" and code["inputmode"] == "numeric"
    assert "OneOSPasskey.register" in page and '<script defer src="/static/passkey.js"' in page
    # Without JavaScript the form can only post to the page itself, never put the password in a URL.
    assert setup["action"] is None and setup["method"] == "post"
    # Sign out everywhere posts the session's token to its endpoint.
    everywhere = next(form for form in forms if form["action"] == "/logout/everywhere")
    assert everywhere["method"] == "post" and everywhere["inputs"]["csrf_token"]["value"] == csrf
    assert everywhere["buttons"] == ["Sign out everywhere"]


def test_settings_lists_each_passkey_with_its_date_and_a_remove_form(web, utc):
    client, store, secret = web
    # Small explicit store times keep the owner's TOTP counter in the past; only `created` is dated.
    first = _register(store, secret, 3030)
    second = _register(store, secret, 3060)
    _execute(store, "UPDATE passkeys SET created=? WHERE id=?", (OCT_28_2026, first.id))
    _execute(store, "UPDATE passkeys SET created=? WHERE id=?", (OCT_31_2026, second.id))
    csrf = _store_session(client, store, secret, 3090)
    page, forms = _settings_page(client)
    text = _text_of(page)
    assert "not set up" not in text
    assert "Touch ID set up · 28 Oct 2026" in text and "Touch ID set up · 31 Oct 2026" in text
    removes = [form for form in forms if form["action"] == "/settings/passkey/remove"]
    assert sorted(form["inputs"]["id"]["value"] for form in removes) == sorted([first.id, second.id])
    for form in removes:
        assert form["method"] == "post" and form["buttons"] == ["Remove Touch ID"]
        assert form["inputs"]["csrf_token"]["value"] == csrf
    assert 'aria-label="Remove Touch ID, set up 28 Oct 2026"' in page and 'aria-label="Remove Touch ID, set up 31 Oct 2026"' in page
    # More than one passkey may be stored (a new Mac), so setup stays available.
    assert any("password" in form["inputs"] for form in forms)
    assert "Add Touch ID on this Mac" in text and "Set up Touch ID" not in text


def test_settings_remove_form_works_end_to_end(web):
    client, store, secret = web
    fake = _register(store, secret, 3030)
    _store_session(client, store, secret, 3090)
    _, forms = _settings_page(client)
    form = next(form for form in forms if form["action"] == "/settings/passkey/remove")
    response = client.post(form["action"], data={name: item["value"] for name, item in form["inputs"].items()}, headers=OWN)
    assert response.status_code == 303
    assert store.passkeys() == []
    assert "Touch ID on this Mac: not set up" in _text_of(client.get("/").text)


def test_settings_sign_in_section_is_for_signed_in_owners_only(web):
    client, store, secret = web
    assert client.get("/").status_code == 303
    assert client.get("/login").status_code == 200


def test_settings_backup_note_says_backups_run_only_on_request(web):
    client, store, secret = web
    _store_session(client, store, secret)
    text = _text_of(client.get("/").text)
    assert "once a day" not in text and "while the Mac is awake" not in text
    assert "only when you run" in text


# --- static/passkey.js (Settings), run under node when it is installed --------------

def _node():
    import shutil
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    return node


def test_passkey_js_has_no_dependencies_and_never_writes_html():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "static" / "passkey.js").read_text()
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "import ", "require(", "http:", "https:", "eval(", "localStorage", "sessionStorage"):
        assert forbidden not in source, forbidden


def test_passkey_js_fetches_fresh_options_for_every_attempt(tmp_path):
    import subprocess
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / "static" / "passkey.js"
    harness = tmp_path / "harness.js"
    harness.write_text(r"""
const assert = require('assert');
const calls = [];
let creates = 0;
let failCreate = true;
const b64 = (bytes) => Buffer.from(bytes).toString('base64url');
global.window = global;
global.document = { querySelector: (s) => s === 'meta[name="csrf-token"]' ? { content: 'T'.repeat(43) } : null };
global.PublicKeyCredential = function () {};
global.location = { reload() { calls.push(['reload']); }, assign(url) { calls.push(['assign', url]); } };
// Node 22 defines a read-only navigator of its own.
Object.defineProperty(globalThis, 'navigator', { configurable: true, writable: true, value: { credentials: { create: async ({ publicKey }) => {
  creates += 1;
  assert.ok(publicKey.challenge instanceof ArrayBuffer && publicKey.user.id instanceof ArrayBuffer);
  assert.ok(publicKey.excludeCredentials[0].id instanceof ArrayBuffer);
  assert.strictEqual(Buffer.from(publicKey.user.id).toString(), 'oneos-owner');
  if (failCreate) { const e = new Error('cancelled'); e.name = 'NotAllowedError'; throw e; }
  const raw = new Uint8Array([1, 2, 3, 250, 251, 252]).buffer;
  return { id: b64(new Uint8Array(raw)), rawId: raw, type: 'public-key', authenticatorAttachment: 'platform',
    response: { clientDataJSON: new Uint8Array([123, 125]).buffer, attestationObject: new Uint8Array([9]).buffer, getTransports: () => ['internal'] },
    getClientExtensionResults: () => ({}) };
} } } });
let n = 0;
global.fetch = async (url, init) => {
  calls.push([url, init]);
  if (url === '/settings/passkey/begin') {
    n += 1;
    const options = { rp: { id: 'localhost', name: 'OneOS' }, user: { id: b64(Buffer.from('oneos-owner')), name: 'owner', displayName: 'OneOS owner' },
      challenge: b64(new Uint8Array(32).fill(n)), pubKeyCredParams: [{ type: 'public-key', alg: -7 }], timeout: 300000,
      excludeCredentials: [{ id: b64(new Uint8Array([7, 7])), type: 'public-key' }], authenticatorSelection: { residentKey: 'required', userVerification: 'required' }, attestation: 'none' };
    return { ok: true, status: 200, json: async () => options };
  }
  if (url === '/settings/passkey/finish') return { ok: true, status: 200, json: async () => ({ ok: true }) };
  if (url === '/settings/passkey/remove') return { ok: false, status: 0, type: 'opaqueredirect', json: async () => ({}) };
  throw new Error('unexpected ' + url);
};
require(process.argv[2]);
const api = window.OneOSPasskey;
(async () => {
  assert.strictEqual(api.toBase64url(api.fromBase64url('AQID-vv8')), 'AQID-vv8');
  assert.strictEqual(api.toBase64url(new Uint8Array([255, 254, 253]).buffer), '__79');
  assert.throws(() => api.fromBase64url('A+/='));
  const first = await api.register('pw one', '111111');
  assert.strictEqual(first.ok, false);
  assert.strictEqual(first.error, 'cancelled');
  failCreate = false;
  const second = await api.register('pw two', '222222');
  assert.deepStrictEqual(second, { ok: true });
  const begins = calls.filter((c) => c[0] === '/settings/passkey/begin');
  assert.strictEqual(begins.length, 2);
  assert.strictEqual(begins[0][1].body, 'password=pw+one&code=111111');
  assert.strictEqual(begins[1][1].body, 'password=pw+two&code=222222');
  for (const [, init] of begins) {
    assert.strictEqual(init.method, 'POST');
    assert.strictEqual(init.headers['X-CSRF-Token'], 'T'.repeat(43));
    assert.strictEqual(init.headers['Content-Type'], 'application/x-www-form-urlencoded');
  }
  assert.strictEqual(creates, 2);
  const finish = calls.find((c) => c[0] === '/settings/passkey/finish');
  const sent = JSON.parse(finish[1].body);
  assert.strictEqual(finish[1].headers['Content-Type'], 'application/json');
  assert.deepStrictEqual(sent.response, { clientDataJSON: 'e30', attestationObject: 'CQ', transports: ['internal'] });
  assert.strictEqual(sent.rawId, 'AQID-vv8');
  assert.strictEqual(sent.type, 'public-key');
  const removed = await api.remove('AQID-vv8');
  assert.deepStrictEqual(removed, { ok: true });
  const remove = calls.find((c) => c[0] === '/settings/passkey/remove');
  assert.strictEqual(remove[1].body, 'id=AQID-vv8');
  assert.strictEqual(remove[1].redirect, 'manual');
  assert.ok(calls.some((c) => c[0] === 'reload'));
  console.log('ok');
})().catch((e) => { console.error(e); process.exit(1); });
""")
    result = subprocess.run([_node(), str(harness), str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ok"


def test_passkey_js_sends_an_expired_session_to_sign_in_not_a_password_message(tmp_path):
    """Only the server's JSON {"error": "check"} is a wrong password or code; any other 401 or a 403 means signed out."""
    import subprocess
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / "static" / "passkey.js"
    harness = tmp_path / "harness.js"
    harness.write_text(r"""
const assert = require('assert');
const calls = [];
const b64 = (bytes) => Buffer.from(bytes).toString('base64url');
global.window = global;
global.document = { querySelector: () => ({ content: 'T'.repeat(43) }) };
global.PublicKeyCredential = function () {};
global.location = { reload() { calls.push(['reload']); }, assign(url) { calls.push(['assign', url]); } };
const credential = () => ({ id: 'AQ', rawId: new Uint8Array([1]).buffer, type: 'public-key',
  response: { clientDataJSON: new Uint8Array([123, 125]).buffer, attestationObject: new Uint8Array([9]).buffer },
  getClientExtensionResults: () => ({}) });
Object.defineProperty(globalThis, 'navigator', { configurable: true, writable: true, value: { credentials: { create: async () => credential() } } });
const options = { rp: { id: 'localhost', name: 'OneOS' }, user: { id: b64(Buffer.from('oneos-owner')), name: 'owner', displayName: 'o' },
  challenge: b64(new Uint8Array(32)), pubKeyCredParams: [{ type: 'public-key', alg: -7 }], timeout: 300000,
  excludeCredentials: [], authenticatorSelection: {}, attestation: 'none' };
let begin, finish;
const reply = (status, body) => ({ ok: status < 300, status, json: async () => { if (typeof body === 'string') throw new SyntaxError('not json'); return body; } });
global.fetch = async (url) => (url === '/settings/passkey/begin' ? begin : finish);
require(process.argv[2]);
const api = window.OneOSPasskey;
(async () => {
  begin = reply(401, { error: 'check', message: 'Server says check.' });
  let result = await api.register('p', '1');
  assert.deepStrictEqual(result, { ok: false, error: 'check', message: 'Server says check.' });
  assert.deepStrictEqual(calls, []);
  begin = reply(401, { error: 'check' });
  result = await api.register('p', '1');
  assert.strictEqual(result.error, 'check');
  assert.ok(result.message.startsWith('Password or code not accepted'));
  for (const refusal of [reply(401, 'Sign in required'), reply(401, { error: 'other' }), reply(401, null), reply(403, 'Invalid form token; reload and retry')]) {
    calls.length = 0;
    begin = refusal;
    result = await api.register('p', '1');
    assert.strictEqual(result.ok, false);
    assert.strictEqual(result.error, 'signed-out');
    assert.ok(!/Password or code not accepted/.test(result.message));
    assert.deepStrictEqual(calls, [['assign', '/login']]);
  }
  begin = reply(200, options);
  for (const status of [401, 403]) {
    calls.length = 0;
    finish = reply(status, 'Sign in required');
    result = await api.register('p', '1');
    assert.strictEqual(result.error, 'signed-out');
    assert.deepStrictEqual(calls, [['assign', '/login']]);
  }
  calls.length = 0;
  finish = reply(400, { error: 'failed' });
  result = await api.register('p', '1');
  assert.strictEqual(result.error, 'failed');
  assert.deepStrictEqual(calls, []);
  // A blocked throttle is a wait, never a sign-out or a password message.
  begin = reply(429, { error: 'blocked', message: 'Server says wait.' });
  result = await api.register('p', '1');
  assert.deepStrictEqual(result, { ok: false, error: 'blocked', message: 'Server says wait.' });
  for (const body of [{ error: 'blocked' }, 'Too many', null]) {
    begin = reply(429, body);
    result = await api.register('p', '1');
    assert.strictEqual(result.error, 'blocked');
    assert.strictEqual(result.message, 'Too many failed sign-ins. Wait a few minutes and try again.');
  }
  assert.deepStrictEqual(calls, []);
  console.log('ok');
})().catch((e) => { console.error(e); process.exit(1); });
""")
    result = subprocess.run([_node(), str(harness), str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ok"


def test_sign_in_page_script_produces_an_answer_the_server_accepts(web, tmp_path):
    """Run the page's inline script under node with stubbed DOM and authenticator, then post its body."""
    import subprocess
    node = _node()
    client, store, secret = web
    fake = _register(store, secret, 3030)
    page = client.get("/login").text
    nonce = __import__("re").search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    script = page.split("<script>")[1].split("</script>")[0]
    options = _options(client, nonce).text
    answer = json.loads(fake.assertion(options))
    (tmp_path / "page.js").write_text(script)
    (tmp_path / "fixture.json").write_text(json.dumps({"options": json.loads(options), "answer": answer, "nonce": nonce}))
    harness = tmp_path / "harness.js"
    harness.write_text(r"""
const assert = require('assert');
const fs = require('fs');
const fixture = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const buf = (text) => { const b = Buffer.from(text, 'base64url'); return b.buffer.slice(b.byteOffset, b.byteOffset + b.length); };
const element = (extra) => Object.assign({ hidden: true, disabled: false, textContent: '', listeners: {},
  addEventListener(name, fn) { this.listeners[name] = fn; } }, extra || {});
const link = element();
const els = {
  'touch-id': element(), 'password-fields': element({ hidden: false }), 'password-submit': element({ hidden: false }),
  'use-password': element({ querySelector: () => link }), 'touch-id-button': element(), 'touch-id-status': element(),
  'sign-in': { elements: { csrf_token: { value: fixture.nonce }, keep: { checked: true }, password: { focus() { els.focused = true; } } } },
};
global.window = global;
global.document = { getElementById: (id) => els[id] || null };
global.PublicKeyCredential = function () {};
global.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
global.atob = (s) => Buffer.from(s, 'base64').toString('binary');
const visits = [];
global.location = { assign(url) { visits.push(url); }, reload() { visits.push('reload'); } };
let gets = 0;
Object.defineProperty(globalThis, 'navigator', { configurable: true, writable: true, value: { credentials: { get: async ({ publicKey }) => {
  gets += 1;
  assert.ok(publicKey.challenge instanceof ArrayBuffer);
  assert.strictEqual(Buffer.from(publicKey.challenge).toString('base64url'), fixture.options.challenge);
  assert.strictEqual(publicKey.rpId, 'localhost');
  assert.strictEqual(publicKey.userVerification, 'required');
  assert.strictEqual(publicKey.timeout, 300000);
  const r = fixture.answer.response;
  return { id: fixture.answer.id, rawId: buf(fixture.answer.rawId), type: 'public-key', authenticatorAttachment: 'platform',
    response: { clientDataJSON: buf(r.clientDataJSON), authenticatorData: buf(r.authenticatorData), signature: buf(r.signature), userHandle: buf(r.userHandle) },
    getClientExtensionResults: () => ({}) };
} } } });
const posts = [];
global.fetch = async (url, init) => {
  posts.push([url, init]);
  assert.strictEqual(init.method, 'POST');
  assert.strictEqual(init.headers['X-CSRF-Token'], fixture.nonce);
  assert.strictEqual(init.headers['Content-Type'], 'application/json');
  if (url === '/login/passkey/options') return { ok: true, status: 200, json: async () => fixture.options };
  if (url === '/login/passkey') return { ok: true, status: 200, json: async () => ({ redirect: '/' }) };
  throw new Error('unexpected ' + url);
};
require(process.argv[2]);
(async () => {
  // Script ran: Touch ID shown, password fields hidden.
  assert.strictEqual(els['touch-id'].hidden, false);
  assert.strictEqual(els['password-fields'].hidden, true);
  await els['touch-id-button'].listeners.click();
  await els['touch-id-button'].listeners.click();
  // Every press asks for fresh options.
  assert.deepStrictEqual(posts.map((p) => p[0]), ['/login/passkey/options', '/login/passkey', '/login/passkey/options', '/login/passkey']);
  assert.strictEqual(gets, 2);
  assert.deepStrictEqual(visits, ['/', '/']);
  // "Use password and code instead" brings the form back.
  link.listeners.click({ preventDefault() {} });
  assert.strictEqual(els['password-fields'].hidden, false);
  assert.strictEqual(els['touch-id'].hidden, true);
  assert.strictEqual(els.focused, true);
  process.stdout.write(posts[1][1].body);
})().catch((e) => { console.error(e); process.exit(1); });
""")
    result = subprocess.run([node, str(harness), str(tmp_path / "page.js"), str(tmp_path / "fixture.json")], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    body = json.loads(result.stdout)
    assert body["keep"] is True and body["credential"]["response"]["userHandle"] == answer["response"]["userHandle"]
    response = client.post("/login/passkey", content=result.stdout.encode(),
                           headers=dict(OWN, **{"X-CSRF-Token": nonce, "Content-Type": "application/json"}))
    assert response.status_code == 200 and response.json() == {"redirect": "/"}
    assert store.session_info(client.cookies.get("__Host-oneos")).kept is True


def test_deeply_nested_json_is_refused_without_an_error(web):
    client, store, secret = web
    _register(store, secret, 3030)
    depth = 7900  # balanced, and just under the 16 KiB cap
    nested = "[" * depth + "]" * depth
    body = ('{"credential": {"x": ' + nested + '}, "keep": true}').encode()
    assert len(body) <= LIMIT
    response = client.post("/login/passkey", content=body, headers=dict(OWN, **{"X-CSRF-Token": _nonce(client), "Content-Type": "application/json"}))
    assert response.status_code in (400, 401) and "error" in response.json()
    csrf = _store_session(client, store, secret, 3060)
    response = _finish(client, csrf, '{"x": ' + nested + '}')
    assert response.status_code == 400 and "error" in response.json()
    assert len(store.passkeys()) == 1
