from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from nginx_conf.configs import NginxConfig, find_project_configs

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="Nginx-Verwaltungstool")
PROJECT_ROOT = Path("/opt/www")


def load_configs(root: Path | None = None) -> list[NginxConfig]:
    if root is None:
        root = PROJECT_ROOT
    return find_project_configs(root)


def find_config_or_404(project_name: str, config_name: str, root: Path | None = None) -> NginxConfig:
    if root is None:
        root = PROJECT_ROOT
    for config in load_configs(root):
        if config.project_name == project_name and config.config_path.name == config_name:
            return config

    raise HTTPException(status_code=404, detail="Config not found")


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> Response:
    configs = load_configs()
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "title": "Nginx-Verwaltungstool",
            "configs": configs,
        },
    )


@app.get("/configs", response_class=HTMLResponse)
def configs_index(request: Request) -> Response:
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "title": "Gefundene Nginx-Configs",
            "configs": load_configs(),
        },
    )


@app.get("/configs/{project_name}/{config_name}", response_class=HTMLResponse)
def config_detail(request: Request, project_name: str, config_name: str) -> Response:
    config = find_config_or_404(project_name, config_name)
    content = config.config_path.read_text(encoding="utf-8")
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "title": f"{config.project_name} / {config.config_path.name}",
            "config": config,
            "config_content": content,
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
