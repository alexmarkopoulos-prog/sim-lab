"""Environment configuration. Secrets stay in the environment, never in git."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def load_dotenv(path: str | os.PathLike[str] | None = None) -> None:
    """Load KEY=VALUE lines into the environment without overriding real env vars."""
    env_path = Path(path or os.environ.get("SIMLAB_ENV_FILE", ".env"))
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    secret_key: str
    email: str
    password: str
    database_url: str
    base_url: str
    dev_expose_magic_link: bool
    cookie_secure: bool
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_tls: bool

    @property
    def password_login_enabled(self) -> bool:
        return bool(self.password)


def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


@lru_cache
def get_settings() -> Settings:
    load_dotenv()
    secret = os.environ.get("SIMLAB_SECRET_KEY", "").strip()
    email = os.environ.get("SIMLAB_EMAIL", "").strip().lower()
    if not secret:
        raise RuntimeError("SIMLAB_SECRET_KEY is required. See .env.example.")
    if not email:
        raise RuntimeError("SIMLAB_EMAIL is required. See .env.example.")
    return Settings(
        secret_key=secret,
        email=email,
        password=os.environ.get("SIMLAB_PASSWORD", ""),
        database_url=os.environ.get("SIMLAB_DATABASE_URL", "sqlite:///./simlab.db"),
        base_url=os.environ.get("SIMLAB_BASE_URL", "http://localhost:8000").rstrip("/"),
        dev_expose_magic_link=_flag("SIMLAB_DEV_EXPOSE_MAGIC_LINK", "false"),
        cookie_secure=_flag("SIMLAB_COOKIE_SECURE", "false"),
        smtp_host=os.environ.get("SMTP_HOST", "").strip(),
        smtp_port=int(os.environ.get("SMTP_PORT", "587") or "587"),
        smtp_user=os.environ.get("SMTP_USER", "").strip(),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        smtp_from=os.environ.get("SMTP_FROM", "").strip(),
        smtp_tls=_flag("SMTP_TLS", "true"),
    )
