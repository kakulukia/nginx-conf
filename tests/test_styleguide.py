import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from nginx_conf import main
from nginx_conf.auth import SESSION_COOKIE_NAME
from nginx_conf.store import AppDatabase


def test_styleguide_requires_session(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "styleguide.sqlite3")
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    browser = TestClient(main.app, follow_redirects=False)
    assert browser.get("/styleguide").status_code == 303
    db.create_session(
        session_hash=hashlib.sha256(b"local-test-session").hexdigest(), expires_at="9999-01-01T00:00:00+00:00"
    )
    browser.cookies.set(SESSION_COOKIE_NAME, "local-test-session")
    page = browser.get("/styleguide")
    assert page.status_code == 200
    assert '/static/select.js" defer' in page.text
    assert 'src="https://' not in page.text


def test_styleguide_only_exposes_allowed_assets_without_a_session(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "styleguide.sqlite3")
    browser = TestClient(main.app, follow_redirects=False)
    for path, content_type in {
        "styleguide.css": "text/css",
        "app.css": "text/css",
        "otp.js": "javascript",
        "select.js": "javascript",
        "favicon.svg": "image/svg+xml",
    }.items():
        response = browser.get(f"/static/{path}")
        assert response.status_code == 200, path
        assert content_type in response.headers["content-type"], path
    for weight in ("regular", "bold"):
        assert browser.get(f"/static/fonts/source-code-pro-{weight}.woff2").status_code == 200
    for path in ("unknown.js", "_tokens.sass", "fonts/OFL.txt"):
        assert browser.get(f"/static/{path}").status_code == 303, path
