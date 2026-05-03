from fastapi.testclient import TestClient

from nginx_conf import main

client = TestClient(main.app)


def test_home_page() -> None:
    main.PROJECT_ROOT = main.Path(__file__).parent / "opt/www"
    response = client.get("/")

    assert response.status_code == 200
    assert "Nginx-Verwaltungstool" in response.text
    assert "demo-app" in response.text


def test_configs_index_shows_demo_config(monkeypatch) -> None:
    monkeypatch.setattr(main, "PROJECT_ROOT", main.Path(__file__).parent / "opt/www")

    response = client.get("/configs")

    assert response.status_code == 200
    assert "demo-app / +.nginx" in response.text


def test_config_detail_shows_config_content(monkeypatch) -> None:
    monkeypatch.setattr(main, "PROJECT_ROOT", main.Path(__file__).parent / "opt/www")

    response = client.get("/configs/demo-app/+.nginx")

    assert response.status_code == 200
    assert "server_name" in response.text
    assert "demo-app" in response.text


def test_config_detail_rejects_free_path(monkeypatch) -> None:
    monkeypatch.setattr(main, "PROJECT_ROOT", main.Path(__file__).parent / "opt/www")

    response = client.get("/configs/demo-app/../../etc/passwd")

    assert response.status_code == 404


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
