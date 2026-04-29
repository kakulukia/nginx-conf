from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="Nginx-Verwaltungstool")


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> Response:
    return templates.TemplateResponse(
        request,
        "home.html",
        {"title": "Nginx-Verwaltungstool"},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
