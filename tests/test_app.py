from fastapi.testclient import TestClient

from nginx_conf.main import app

client = TestClient(app)


def test_home_page() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Nginx-Verwaltungstool" in response.text
    assert "Hello World" in response.text


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
