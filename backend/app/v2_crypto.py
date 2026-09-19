from __future__ import annotations

import hashlib
import hmac
import secrets


PBKDF2_ITERATIONS = 390_000


def default_initial_password(employee_no: str) -> str:
    """Return the documented first-login password for a seven-digit employee ID.

    This is intentionally limited to account creation and the separately audited
    one-time test recovery tool. Administrator password resets follow the
    related documented rule implemented by :func:`account_reset_password`.
    """
    value = str(employee_no or "").strip()
    if len(value) != 7 or not value.isdigit():
        raise ValueError("employee number must be a seven-digit number")
    return value[-4:]


def account_reset_password(login_account: str) -> str:
    """Return the documented administrator reset password: the login account suffix.

    Resets are recorded in docs/security.md as "登录账号后四位". Login accounts
    shorter than four characters cannot produce a password that satisfies the
    system password policy (minimum four characters), which would leave the
    owner unable to change it back, so they are rejected here instead.
    """
    value = str(login_account or "").strip()
    if len(value) < 4:
        raise ValueError("login account must have at least four characters to reset by suffix")
    return value[-4:]


def new_temporary_password(length: int = 16) -> str:
    """Return a one-time password with guaranteed character classes."""
    if length < 12:
        raise ValueError("temporary passwords must be at least 12 characters")
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    symbols = "!@#$%^&*_-"
    characters = [
        secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ"),
        secrets.choice("abcdefghijkmnopqrstuvwxyz"),
        secrets.choice("23456789"),
        secrets.choice(symbols),
    ]
    characters.extend(secrets.choice(alphabet + symbols) for _ in range(length - len(characters)))
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(40)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
