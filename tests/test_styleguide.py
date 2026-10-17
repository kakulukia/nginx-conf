import re
from pathlib import Path
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from nginx_conf import main
from nginx_conf.auth import SESSION_COOKIE_NAME, create_session
from nginx_conf.store import AppDatabase


def test_styleguide_requires_session_and_renders_dark_desktop_examples(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "styleguide.sqlite3")
    db = AppDatabase(main.DATABASE_PATH)
    db.initialize()
    user = db.create_user("andy")
    browser = TestClient(main.app, follow_redirects=False)
    assert browser.get("/styleguide").status_code == 303
    browser.cookies.set(SESSION_COOKIE_NAME, create_session(db, user))
    page = browser.get("/styleguide")
    assert page.status_code == 200
    classes = {name for attribute in re.findall(r'class="([^"]*)"', page.text) for name in attribute.split()}
    assert "nc-theme-dark" in classes
    assert "nc-theme-light" not in classes
    assert 'aria-invalid="true"' in page.text
    assert 'src="https://' not in page.text
    assert "Gravity" not in browser.get("/static/styleguide.css").text


def test_styleguide_only_exposes_css_fonts_and_favicon_without_a_session(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "styleguide.sqlite3")
    browser = TestClient(main.app, follow_redirects=False)
    css = browser.get("/static/styleguide.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert ".nc-button" in css.text
    for weight in ("regular", "bold"):
        font = browser.get(f"/static/fonts/source-code-pro-{weight}.woff2")
        assert font.status_code == 200
        assert font.content.startswith(b"wOF2")
    favicon = browser.get("/static/favicon.svg")
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/svg+xml")
    assert ElementTree.fromstring(favicon.content).tag == "{http://www.w3.org/2000/svg}svg"
    assert browser.get("/static/_tokens.sass").status_code == 303
    assert browser.get("/static/fonts/OFL.txt").status_code == 303
