import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id with the library's default parameters

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024  # same cap the login request applies


class PasswordPolicyError(ValueError):
    """A new password is not acceptable; the message is safe to show."""


def validate_new_password(password: str, email: str) -> None:
    """Length and obvious-choice checks for a password being set (not for login).

    No composition rules (digits, symbols): they do not make passwords stronger.
    """
    if not password.strip():
        raise PasswordPolicyError("Password must not be blank.")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
        )
    if len(password) > PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at most {PASSWORD_MAX_LENGTH} characters."
        )
    if password.strip().lower() == email.strip().lower():
        raise PasswordPolicyError("Password must not be the same as the email.")


def hash_password(plain: str) -> str:
    if not plain:
        raise ValueError("password must not be empty")
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """True only for a matching password; a malformed hash is simply False."""
    try:
        return _hasher.verify(hashed, plain)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    return _hasher.check_needs_rehash(hashed)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex digest. Fine for tokens because they are server-generated with
    256 bits of entropy; never use it for passwords."""
    return hashlib.sha256(token.encode()).hexdigest()


# 32 letters and digits with no look-alikes (no 0/O or 1/I), so a code can be read out
# over the phone. 12 characters of 5 bits each is 60 bits of randomness.
RESET_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
RESET_CODE_LENGTH = 12
RESET_CODE_GROUP = 4


def new_reset_code() -> str:
    """A fresh one-time reset code, grouped for reading aloud: `K7F2-9QXM-4PWD`."""
    raw = "".join(secrets.choice(RESET_CODE_ALPHABET) for _ in range(RESET_CODE_LENGTH))
    return "-".join(
        raw[i : i + RESET_CODE_GROUP]
        for i in range(0, RESET_CODE_LENGTH, RESET_CODE_GROUP)
    )


def normalize_reset_code(code: str) -> str:
    """Case, spaces and hyphens do not matter when a code is typed in."""
    return "".join(ch for ch in code.upper() if ch not in " -\t\r\n")


def hash_reset_code(user_id: int, code: str) -> str:
    """The value stored for a code. It is tied to the account, so a code issued for one
    person cannot be redeemed against another, and the code itself is never stored."""
    return hashlib.sha256(
        f"{user_id}:{normalize_reset_code(code)}".encode()
    ).hexdigest()
