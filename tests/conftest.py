"""Test environment. Set before the app reads configuration."""

import os

os.environ["SIMLAB_SECRET_KEY"] = "test-secret-key"
os.environ["SIMLAB_EMAIL"] = "alex@example.com"
os.environ["SIMLAB_PASSWORD"] = "test-password"
os.environ["SIMLAB_DATABASE_URL"] = "sqlite:////tmp/simlab-pytest.db"
os.environ["SIMLAB_DEV_EXPOSE_MAGIC_LINK"] = "true"
os.environ["SIMLAB_BASE_URL"] = "http://testserver"
os.environ["SIMLAB_COOKIE_SECURE"] = "false"

import pytest


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from simlab.db import Base, engine, init_db
    from simlab.main import app, ensure_user

    Base.metadata.drop_all(engine)
    init_db()
    ensure_user()
    with TestClient(app) as test_client:
        yield test_client
