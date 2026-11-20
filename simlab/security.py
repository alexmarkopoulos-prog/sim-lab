"""Password hashing and session tokens. The raw password never touches the database."""

from __future__ import annotations

import hashlib
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from simlab.config import get_settings

PBKDF2_ROUNDS = 120_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    salt, digest = stored.split("$", 1)
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS)
    return secrets.compare_digest(check.hex(), digest)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    secret = get_settings().secret_key
    return hashlib.sha256(f"{secret}:{token}".encode()).hexdigest()


def expiry(minutes: int = 0, days: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes, days=days)


def send_magic_email(recipient: str, link: str) -> bool:
    """Send the sign-in link when SMTP is configured. Returns False if it is not."""
    settings = get_settings()
    if not settings.smtp_host:
        return False
    message = EmailMessage()
    message["Subject"] = "Your Sim Lab sign-in link"
    message["From"] = settings.smtp_from or settings.smtp_user or "simlab@localhost"
    message["To"] = recipient
    message.set_content(
        "Use this link to sign in to Sim Lab. It expires in 20 minutes and works once.\n\n"
        f"{link}\n"
    )
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_tls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
    return True
