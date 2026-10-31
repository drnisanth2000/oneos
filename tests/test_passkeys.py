"""Passkey registration and sign-in, with a synthetic authenticator only (tests/passkey_fake.py)."""
import hashlib
import json
import sqlite3
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


def test_kept_column_without_passkey_tables(tmp_path):
    store, secret = _old_schema_store(tmp_path)
    _execute(store, "ALTER TABLE sessions ADD COLUMN kept INTEGER NOT NULL DEFAULT 0")
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
