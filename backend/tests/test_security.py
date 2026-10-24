import re

import pytest

from app.core import security

PASSWORD = "correct horse battery staple"


def test_hash_round_trip():
    hashed = security.hash_password(PASSWORD)
    assert security.verify_password(PASSWORD, hashed) is True


def test_hash_is_argon2id_and_not_plaintext():
    hashed = security.hash_password(PASSWORD)
    assert hashed.startswith("$argon2id$")
    assert PASSWORD not in hashed


def test_hashes_are_salted():
    assert security.hash_password(PASSWORD) != security.hash_password(PASSWORD)


def test_wrong_password_rejected():
    hashed = security.hash_password(PASSWORD)
    assert security.verify_password("wrong password", hashed) is False
    assert security.verify_password("", hashed) is False


@pytest.mark.parametrize("bad_hash", ["", "hash", "$argon2id$garbage", PASSWORD])
def test_malformed_hash_is_not_a_match(bad_hash):
    assert security.verify_password(PASSWORD, bad_hash) is False


def test_empty_password_cannot_be_hashed():
    with pytest.raises(ValueError):
        security.hash_password("")


def test_fresh_hash_does_not_need_rehash():
    assert security.needs_rehash(security.hash_password(PASSWORD)) is False


def test_tokens_are_unique_and_long_enough():
    tokens = {security.new_session_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(token) >= 43 for token in tokens)  # 32 bytes, base64url


def test_token_hash_is_deterministic_hex_digest():
    token = security.new_session_token()
    digest = security.hash_token(token)
    assert digest == security.hash_token(token)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest != token
    assert security.hash_token(token + "x") != digest
