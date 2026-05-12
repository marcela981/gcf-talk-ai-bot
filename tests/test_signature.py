"""Unit tests for the HMAC verifier in app.utils.signature.

Production traffic goes through `nc_py_api`'s `atalk_bot_msg` dependency, so
these tests exercise the standalone helpers we use for replay/debugging.
"""
from app.utils.signature import compute_signature, verify_signature


SECRET = "shared-secret-for-tests"
RANDOM = "abc123randomvalue"
BODY = b'{"type":"Create","actor":{"type":"Person","id":"users/alice"}}'


def test_compute_signature_is_deterministic_and_hex():
    a = compute_signature(SECRET, RANDOM, BODY)
    b = compute_signature(SECRET, RANDOM, BODY)
    assert a == b
    assert len(a) == 64
    int(a, 16)  # raises if not valid hex


def test_verify_accepts_valid_signature():
    sig = compute_signature(SECRET, RANDOM, BODY)
    assert verify_signature(
        secret=SECRET,
        random_header=RANDOM,
        body=BODY,
        provided_signature=sig,
    )


def test_verify_is_case_insensitive_on_provided_hex():
    sig = compute_signature(SECRET, RANDOM, BODY).upper()
    assert verify_signature(
        secret=SECRET,
        random_header=RANDOM,
        body=BODY,
        provided_signature=sig,
    )


def test_verify_rejects_wrong_signature():
    assert not verify_signature(
        secret=SECRET,
        random_header=RANDOM,
        body=BODY,
        provided_signature="0" * 64,
    )


def test_verify_rejects_tampered_body():
    sig = compute_signature(SECRET, RANDOM, BODY)
    assert not verify_signature(
        secret=SECRET,
        random_header=RANDOM,
        body=BODY + b" tampered",
        provided_signature=sig,
    )


def test_verify_rejects_wrong_random_header():
    sig = compute_signature(SECRET, RANDOM, BODY)
    assert not verify_signature(
        secret=SECRET,
        random_header="different-random",
        body=BODY,
        provided_signature=sig,
    )


def test_verify_rejects_wrong_secret():
    sig = compute_signature(SECRET, RANDOM, BODY)
    assert not verify_signature(
        secret="other-secret",
        random_header=RANDOM,
        body=BODY,
        provided_signature=sig,
    )
