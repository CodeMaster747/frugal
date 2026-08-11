"""Tests for password hashing and token primitives."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.errors import UnauthenticatedError
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)


class TestPasswordHashing:
    def test_verifies_a_correct_password(self):
        assert verify_password("CorrectHorse9Battery", hash_password("CorrectHorse9Battery"))

    def test_rejects_a_wrong_password(self):
        assert not verify_password("wrong", hash_password("CorrectHorse9Battery"))

    def test_uses_argon2id(self):
        """Argon2id is the memory-hard variant; memory cost is what makes GPU
        cracking expensive."""
        assert hash_password("x" * 12).startswith("$argon2id$")

    def test_is_salted(self):
        """Identical passwords must produce different hashes, or the hash file
        becomes a lookup table."""
        assert hash_password("SamePassword1") != hash_password("SamePassword1")

    def test_rejects_a_malformed_hash_without_raising(self):
        assert not verify_password("anything", "not-a-hash")


class TestAccessTokens:
    def test_round_trips_the_subject(self):
        user_id = uuid.uuid4()
        assert decode_access_token(create_access_token(user_id)) == user_id

    def test_rejects_an_expired_token(self):
        token = create_access_token(uuid.uuid4(), expires_in=timedelta(seconds=-1))
        with pytest.raises(UnauthenticatedError):
            decode_access_token(token)

    def test_rejects_a_token_signed_with_another_key(self):
        import jwt

        forged = jwt.encode(
            {"sub": str(uuid.uuid4()), "type": "access", "exp": 9999999999},
            "attacker-key-that-is-long-enough-here",
            "HS256",
        )
        with pytest.raises(UnauthenticatedError):
            decode_access_token(forged)

    def test_rejects_an_unsigned_token(self):
        """The `alg: none` attack: a token the attacker writes themselves."""
        import jwt

        unsigned = jwt.encode(
            {"sub": str(uuid.uuid4()), "type": "access"}, key="", algorithm="none"
        )
        with pytest.raises(UnauthenticatedError):
            decode_access_token(unsigned)

    def test_rejects_a_token_of_the_wrong_type(self):
        """Stops some other signed token being presented where an access token
        is expected."""
        import jwt

        from app.core.config import get_settings

        wrong_type = jwt.encode(
            {"sub": str(uuid.uuid4()), "type": "refresh", "exp": 9999999999},
            get_settings().jwt_secret.get_secret_value(),
            "HS256",
        )
        with pytest.raises(UnauthenticatedError):
            decode_access_token(wrong_type)

    def test_rejects_garbage(self):
        with pytest.raises(UnauthenticatedError):
            decode_access_token("not.a.token")

    def test_failure_reasons_are_not_leaked(self):
        """Expiry and a bad signature must look the same to the caller --
        telling an attacker which one failed is free information."""
        import jwt

        expired = create_access_token(uuid.uuid4(), expires_in=timedelta(seconds=-1))
        forged = jwt.encode(
            {"sub": str(uuid.uuid4()), "type": "access", "exp": 9999999999},
            "another-key-long-enough-to-be-valid!",
            "HS256",
        )

        messages = set()
        for token in (expired, forged):
            with pytest.raises(UnauthenticatedError) as exc:
                decode_access_token(token)
            messages.add(exc.value.status_code)

        assert messages == {401}


class TestRefreshTokens:
    def test_tokens_are_unique(self):
        assert len({generate_refresh_token() for _ in range(100)}) == 100

    def test_tokens_have_high_entropy(self):
        # 48 random bytes, urlsafe-encoded.
        assert len(generate_refresh_token()) >= 60

    def test_hashing_is_deterministic(self):
        token = generate_refresh_token()
        assert hash_refresh_token(token) == hash_refresh_token(token)

    def test_hash_does_not_reveal_the_token(self):
        """Only the digest is stored, so a database leak yields no usable
        tokens."""
        token = generate_refresh_token()
        digest = hash_refresh_token(token)
        assert token not in digest
        assert len(digest) == 64  # sha256 hex
