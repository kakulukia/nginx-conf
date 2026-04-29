from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class NginxConfig:
    project_name: str
    config_path: Path


def find_project_configs(root: Path) -> list[NginxConfig]:
    if not root.is_dir():
        return []

    base_root = root.resolve()
    results: list[NginxConfig] = []

    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir() or project_dir.is_symlink():
            continue

        for current_root, dirnames, filenames in os.walk(project_dir, followlinks=False):
            current_path = Path(current_root)
            dirnames[:] = sorted(dirname for dirname in dirnames if not (current_path / dirname).is_symlink())

            for filename in sorted(filenames):
                if not filename.endswith(".nginx"):
                    continue

                config_path = current_path / filename
                if config_path.is_symlink():
                    continue

                if not config_path.resolve().is_relative_to(base_root):
                    continue

                results.append(
                    NginxConfig(
                        project_name=project_dir.name,
                        config_path=config_path,
                    )
                )

    return results
