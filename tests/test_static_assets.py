from pathlib import Path

import pytest

from nginx_conf.static_assets import (
    StaticAssetError,
    discover_static_asset_candidates,
    insert_static_location,
    select_static_asset_candidate,
)


def test_discover_static_asset_candidates_maps_known_directories(tmp_path: Path) -> None:
    project = tmp_path / "demo-app"
    (project / "static").mkdir(parents=True)
    (project / "frontend/assets").mkdir(parents=True)
    (project / "node_modules/static").mkdir(parents=True)

    candidates = discover_static_asset_candidates(project)

    assert [(candidate.relative_path, candidate.location_path) for candidate in candidates] == [
        ("frontend/assets", "/assets/"),
        ("static", "/static/"),
    ]


def test_select_static_asset_candidate_rejects_free_paths(tmp_path: Path) -> None:
    project = tmp_path / "demo-app"
    (project / "static").mkdir(parents=True)

    assert select_static_asset_candidate(project, "static").asset_path == (project / "static").resolve()
    with pytest.raises(StaticAssetError):
        select_static_asset_candidate(project, "../static")
    with pytest.raises(StaticAssetError):
        select_static_asset_candidate(project, "media")


def test_insert_static_location_adds_block_to_first_server_and_is_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "demo-app"
    static_dir = project / "static"
    static_dir.mkdir(parents=True)
    candidate = select_static_asset_candidate(project, "static")
    content = "server { listen 80; server_name example.com; }\n"

    updated, changed = insert_static_location(content, candidate)
    repeated, repeated_changed = insert_static_location(updated, candidate)

    assert changed is True
    assert repeated_changed is False
    assert repeated == updated
    assert "location /static/ {" in updated
    assert f"alias {static_dir.resolve()}/;" in updated
    assert updated.count("location /static/") == 1


def test_insert_static_location_rejects_conflicting_existing_location(tmp_path: Path) -> None:
    project = tmp_path / "demo-app"
    (project / "static").mkdir(parents=True)
    candidate = select_static_asset_candidate(project, "static")
    content = "server { location /static/ { alias /srv/other/; } }\n"

    with pytest.raises(StaticAssetError, match="different alias"):
        insert_static_location(content, candidate)
