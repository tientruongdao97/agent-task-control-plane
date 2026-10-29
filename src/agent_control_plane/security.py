import hashlib
import hmac
import secrets


def constant_time_equal(candidate: str, expected: str) -> bool:
    return hmac.compare_digest(candidate.encode(), expected.encode())


def new_lease_token() -> str:
    return secrets.token_urlsafe(32)


def hash_lease_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
