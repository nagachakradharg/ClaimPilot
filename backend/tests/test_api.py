from fastapi.testclient import TestClient

from claimpilot.api import main


def test_health_reports_mode_and_degrades_without_db(monkeypatch):
    async def no_db():
        return {"ok": False, "error": "OperationalError"}

    monkeypatch.setattr(main, "check_database", no_db)
    client = TestClient(main.create_app())
    body = client.get("/health").json()
    assert body["llm_mode"] == "fake"
    assert body["status"] == "degraded"


def test_openapi_docs_available():
    client = TestClient(main.create_app())
    assert client.get("/openapi.json").status_code == 200
