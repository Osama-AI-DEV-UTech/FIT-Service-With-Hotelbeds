import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="fit_tests_")
os.environ.update({
    "ENV": "test",
    "HOTELBEDS_API_KEY": "test-key",
    "HOTELBEDS_SECRET": "test-secret",
    "HOTELBEDS_BASE_URL": "https://api.test.hotelbeds.com",
    "HOTELBEDS_MAX_RETRIES": "1",
    "HOTELBEDS_RETRY_BASE_DELAY_SECONDS": "0.01",
    "DATABASE_PATH": os.path.join(_TMP, "fit_test.db"),
    "LOG_JSON": "false",
    "LOG_LEVEL": "WARNING",
    "OPENAI_API_KEY": "",
})

import copy  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tests.fake_hotelbeds import FakeHotelbeds  # noqa: E402


@pytest.fixture()
def hb():
    with FakeHotelbeds() as fake:
        yield fake


@pytest.fixture()
def client(hb, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "fit.db"))
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def fit_request():
    from app.apis.examples import FIT_MULTI_CITY
    return copy.deepcopy(FIT_MULTI_CITY)
