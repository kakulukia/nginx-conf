import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from nginx_conf.auth import (
    ENROLLMENT_COOKIE_NAME,
    ENROLLMENT_TTL_SECONDS,
    SESSION_COOKIE_NAME,
    EnrollmentRequired,
    confirm_enrollment,
    enrollment_secret,
    load_installation_password,
    login,
    logout,
    password_change_required,
    session_valid,
    set_initial_password,
)
from nginx_conf.certbot_renewal import (
    certbot_renewal_status_to_dict,
    inspect_certbot_renewal,
)
from nginx_conf.configs import (
    NginxConfig,
    certificate_server_group,
    discover_inventory,
    find_project_configs,
    inventory_to_dict,
)
from nginx_conf.django_allowed_hosts import (
    AllowedHostsFinding,
    AllowedHostsInventory,
    allowed_hosts_inventory_to_dict,
    check_django_allowed_hosts,
    findings_for_config,
)
from nginx_conf.django_deploy import (
    action_for_operation,
    django_deploy_statuses_to_dict,
    inspect_django_deploy,
)
from nginx_conf.dns_preflight import check_dns_preflight, dns_preflight_to_dict
from nginx_conf.git_workflow import (
    GitWorkflowError,
    GitWorkflowService,
    git_workflow_result_to_dict,
    inspect_project_git,
    live_diff_for_selected_config,
    project_git_status_to_dict,
)
from nginx_conf.github_clone import GitHubCloneValidationError, plan_github_clone
from nginx_conf.logs import (
    LogSection,
    log_section_to_dict,
    normalize_log_lines,
    read_pm2_log,
    read_system_log,
    read_system_logs,
)
from nginx_conf.pm2_status import PM2StatusInventory, pm2_inventory_to_dict, read_pm2_status
from nginx_conf.selection import build_project_choice_states, project_choice_to_dict, save_project_choice
from nginx_conf.ssl_status import discover_ssl_inventory, ssl_inventory_to_dict
from nginx_conf.static_assets import (
    StaticAssetError,
    discover_static_asset_candidates,
    insert_static_location,
    select_static_asset_candidate,
    static_asset_candidate_to_dict,
)
from nginx_conf.store import ActionLockError, AppDatabase, LoginThrottledError, default_database_path
from nginx_conf.system_actions import (
    CommandRunner,
    SystemActionService,
    certbot_issue_command,
    certbot_renew_dry_run_command,
    django_manage_command,
    github_clone_helper_command,
    helper_command,
    nginx_reload_command,
    nginx_test_command,
    pm2_lifecycle_command,
    pm2_start_command,
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def format_command_output(output: str) -> str:
    try:
        value = json.loads(output)
        return json.dumps(value, indent=2, ensure_ascii=False) if isinstance(value, (dict, list)) else output
    except ValueError, RecursionError:
        return output


templates.env.filters["format_command_output"] = format_command_output


@asynccontextmanager
async def lifespan(_app: FastAPI):
    load_installation_password(get_database())
    yield


app = FastAPI(title="Nginx-Verwaltungstool", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
PROJECT_ROOT = Path("/opt/www")
SITES_ENABLED_ROOT = Path("/etc/nginx/sites-enabled")
DATABASE_PATH = default_database_path()
LETSENCRYPT_ROOT = Path("/etc/letsencrypt")
PUBLIC_PATHS = {
    "/health",
    "/login",
    "/api/login",
    "/login/password",
    "/api/login/password",
    "/login/authenticator",
    "/api/login/authenticator",
    "/static/app.css",
    "/static/styleguide.css",
    "/static/otp.js",
    "/static/favicon.svg",
    "/static/fonts/source-code-pro-regular.woff2",
    "/static/fonts/source-code-pro-bold.woff2",
}


@app.middleware("http")
async def require_session(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method == "POST" and origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
    if request.url.path in PUBLIC_PATHS:
        response = await call_next(request)
        if request.url.path.startswith(("/login", "/api/login")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "same-origin"
        return response
    if not session_valid(get_database(), request.cookies.get(SESSION_COOKIE_NAME)):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


def load_configs(root: Path | None = None) -> list[NginxConfig]:
    if root is None:
        root = PROJECT_ROOT
    return find_project_configs(root)


def load_inventory(project_root: Path | None = None, enabled_root: Path | None = None):
    if project_root is None:
        project_root = PROJECT_ROOT
    if enabled_root is None:
        enabled_root = SITES_ENABLED_ROOT
    return discover_inventory(project_root, enabled_root)


def get_database() -> AppDatabase:
    database = AppDatabase(DATABASE_PATH)
    database.initialize()
    return database


def get_system_action_service(db: AppDatabase) -> SystemActionService:
    return SystemActionService(db)


def get_command_runner(db: AppDatabase) -> CommandRunner:
    return get_system_action_service(db).runner


def find_config_or_404(project_name: str, config_name: str, root: Path | None = None) -> NginxConfig:
    if root is None:
        root = PROJECT_ROOT
    for config in load_configs(root):
        if config.project_name == project_name and config.config_path.name == config_name:
            return config

    raise HTTPException(status_code=404, detail="Config not found")


def find_project_or_404(project_name: str):
    for project in load_inventory().projects:
        if project.project_name == project_name:
            return project
    raise HTTPException(status_code=404, detail="Project not found")


def find_choice_or_404(project_name: str, db: AppDatabase):
    project = find_project_or_404(project_name)
    choices = build_project_choice_states((project,), db)
    if not choices:
        raise HTTPException(status_code=404, detail="Project choice not found")
    return project, choices[0]


def _login_form(request: Request, *, error: str | None = None, status_code: int = 200) -> Response:
    credentials = get_database().get_auth_credentials()
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "title": "Anmelden",
            "configured": credentials is not None,
            "totp_configured": bool(credentials and credentials.totp_secret),
            "password_change_required": bool(credentials and credentials.password_change_required),
            "error": error,
        },
        status_code=status_code,
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    if session_valid(get_database(), request.cookies.get(SESSION_COOKIE_NAME)):
        return RedirectResponse("/", status_code=303)
    return _login_form(request)


@app.post("/api/login")
async def login_api(request: Request) -> Response:
    try:
        payload = await _request_payload(request)
        token = login(
            get_database(), password=str(payload.get("password") or ""), code=str(payload.get("totp_code") or "")
        )
    except ValueError as error:
        status = 429 if isinstance(error, LoginThrottledError) else 401
        headers = {"Retry-After": str(error.retry_after)} if isinstance(error, LoginThrottledError) else {}
        if "application/json" not in request.headers.get("content-type", ""):
            response = _login_form(request, error=str(error), status_code=status)
            response.headers.update(headers)
            return response
        return JSONResponse({"ok": False, "detail": str(error)}, status_code=status, headers=headers)
    if isinstance(token, EnrollmentRequired):
        next_page = "/login/password" if token.password_change_required else "/login/authenticator"
        response = (
            JSONResponse(
                {
                    "ok": True,
                    "enrollment_required": True,
                    "password_change_required": token.password_change_required,
                    "next": next_page,
                }
            )
            if "application/json" in request.headers.get("content-type", "")
            else RedirectResponse(next_page, status_code=303)
        )
        _set_enrollment_cookie(response, token.token, secure=request.url.scheme == "https")
        return response
    response = (
        JSONResponse({"ok": True})
        if "application/json" in request.headers.get("content-type", "")
        else RedirectResponse("/", status_code=303)
    )
    _set_session_cookie(response, token, secure=request.url.scheme == "https")
    response.delete_cookie(ENROLLMENT_COOKIE_NAME)
    return response


def _set_enrollment_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        ENROLLMENT_COOKIE_NAME,
        token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=ENROLLMENT_TTL_SECONDS,
        path="/",
    )


def _password_form(request: Request, *, error: str | None = None, status_code: int = 200) -> Response:
    db = get_database()
    token = request.cookies.get(ENROLLMENT_COOKIE_NAME)
    if not password_change_required(db, token):
        if enrollment_secret(db, token) is not None:
            return RedirectResponse("/login/authenticator", status_code=303)
        response = _login_form(
            request, error="Die Einrichtung ist abgelaufen. Bitte erneut mit deinem Passwort anmelden.", status_code=401
        )
        response.delete_cookie(ENROLLMENT_COOKIE_NAME)
        return response
    return templates.TemplateResponse(
        request, "initial_password.html", {"title": "Setze dein Passwort", "error": error}, status_code=status_code
    )


@app.get("/login/password", response_class=HTMLResponse)
def initial_password_page(request: Request) -> Response:
    return _password_form(request)


@app.post("/api/login/password")
async def initial_password_api(request: Request) -> Response:
    try:
        payload = await _request_payload(request)
        token = set_initial_password(
            get_database(),
            token=request.cookies.get(ENROLLMENT_COOKIE_NAME),
            password=str(payload.get("password") or ""),
            confirmation=str(payload.get("password_confirmation") or ""),
        )
    except ValueError as error:
        return _password_form(request, error=str(error), status_code=400)
    response = RedirectResponse("/login/authenticator", status_code=303)
    _set_enrollment_cookie(response, token, secure=request.url.scheme == "https")
    return response


def _enrollment_form(request: Request, *, error: str | None = None, status_code: int = 200) -> Response:
    import pyotp
    import qrcode
    from qrcode.image.svg import SvgPathImage

    db = get_database()
    token = request.cookies.get(ENROLLMENT_COOKIE_NAME)
    if password_change_required(db, token):
        return RedirectResponse("/login/password", status_code=303)
    secret = enrollment_secret(db, token)
    if secret is None:
        response = _login_form(
            request, error="Die Einrichtung ist abgelaufen. Bitte erneut mit deinem Passwort anmelden.", status_code=401
        )
        response.delete_cookie(ENROLLMENT_COOKIE_NAME)
        return response
    uri = pyotp.TOTP(secret).provisioning_uri(name=request.url.hostname or "nginx-conf", issuer_name="nginx-conf")
    qr = qrcode.make(uri, image_factory=SvgPathImage).to_string().decode("utf-8")
    return templates.TemplateResponse(
        request,
        "enrollment.html",
        {"title": "Authenticator einrichten", "secret": secret, "qr": qr, "error": error},
        status_code=status_code,
    )


@app.get("/login/authenticator", response_class=HTMLResponse)
def enrollment_page(request: Request) -> Response:
    return _enrollment_form(request)


@app.post("/api/login/authenticator")
async def enrollment_api(request: Request) -> Response:
    try:
        payload = await _request_payload(request)
        token = confirm_enrollment(
            get_database(), token=request.cookies.get(ENROLLMENT_COOKIE_NAME), code=str(payload.get("totp_code") or "")
        )
    except ValueError as error:
        status = 429 if isinstance(error, LoginThrottledError) else 401
        response = _enrollment_form(request, error=str(error), status_code=status)
        if isinstance(error, LoginThrottledError):
            response.headers["Retry-After"] = str(error.retry_after)
        return response
    response = RedirectResponse("/", status_code=303)
    _set_session_cookie(response, token, secure=request.url.scheme == "https")
    response.delete_cookie(ENROLLMENT_COOKIE_NAME)
    return response


@app.post("/api/logout")
def logout_api(request: Request) -> Response:
    logout(get_database(), request.cookies.get(SESSION_COOKIE_NAME))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> Response:
    return _projects_page(request, title="Nginx-Verwaltungstool")


@app.get("/configs", response_class=HTMLResponse)
def configs_index(request: Request) -> Response:
    return _projects_page(request, title="Projekte")


@app.get("/projects/{project_name}", response_class=HTMLResponse)
def project_detail(request: Request, project_name: str) -> Response:
    return _projects_page(request, title=project_name, project_name=project_name)


def _projects_page(request: Request, *, title: str, project_name: str | None = None) -> Response:
    inventory = load_inventory()
    selected_project = None
    if project_name is not None:
        selected_project = next(
            (project for project in inventory.projects if project.project_name == project_name), None
        )
        if selected_project is None:
            raise HTTPException(status_code=404, detail="Project not found")
    database = get_database()
    project_choices = build_project_choice_states(inventory.projects, database)
    choices_by_project = {choice.project_name: choice for choice in project_choices}
    ssl_inventory = discover_ssl_inventory(inventory.projects, letsencrypt_root=LETSENCRYPT_ROOT)
    renewal_status = inspect_certbot_renewal()
    allowed_hosts_inventory = check_django_allowed_hosts(inventory.projects)
    django_deploy_statuses = {
        status.project_name: status for status in inspect_django_deploy(inventory.projects) if status.is_django
    }
    pm2_status = read_pm2_status(
        project_root=PROJECT_ROOT,
        projects=inventory.projects,
        db=database,
        runner=get_command_runner(database),
    )
    git_statuses = {
        project.project_name: inspect_project_git(project, choices_by_project[project.project_name])
        for project in inventory.projects
    }
    ssl_statuses = {project.project_name: project for project in ssl_inventory.projects}
    allowed_hosts_warnings = _allowed_hosts_warnings_by_config_block(allowed_hosts_inventory)
    processes_by_project = _pm2_processes_by_project(pm2_status)
    project_rows = []
    for project in inventory.projects:
        choice = choices_by_project[project.project_name]
        active_configs = [config for config in project.configs if config.enabled]
        display_configs = active_configs or [
            config for config in project.configs if config.config_path == choice.selected_nginx_config
        ]
        domains = sorted(
            {name for config in display_configs for block in config.server_blocks for name in block.server_names}
        )
        git_status = git_statuses[project.project_name]
        ssl_status = ssl_statuses.get(project.project_name)
        django_deploy = django_deploy_statuses.get(project.project_name)
        processes = processes_by_project.get(project.project_name, ())
        has_warning = bool(
            project.issues
            or choice.issues
            or not any(config.config_path == choice.selected_nginx_config for config in active_configs)
            or git_status.error
            or git_status.repo_dirty
            or any(allowed_hosts_warnings.get(config.config_path) for config in project.configs)
            or (ssl_status and any(domain.state != "valid" for domain in ssl_status.domains))
            or pm2_status.error
            or any(process.warning or process.status != "online" for process in processes)
            or (django_deploy and any(not action.available for action in django_deploy.actions))
        )
        project_rows.append(
            {
                "name": project.project_name,
                "domains": domains,
                "active": bool(active_configs),
                "has_warning": has_warning,
            }
        )
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "title": title,
            "inventory": inventory,
            "selected_project": selected_project,
            "project_rows": project_rows,
            "project_choices": choices_by_project,
            "git_statuses": git_statuses,
            "ssl_statuses": ssl_statuses,
            "ssl_renewal_warning_projects": _ssl_renewal_warning_projects(ssl_inventory),
            "unassigned_ssl_lineages": ssl_inventory.unassigned_lineages,
            "certbot_renewal_status": renewal_status,
            "last_certbot_renew_dry_run": database.get_last_action_result_by_type("certbot-renew-dry-run"),
            "certbot_actions": _certbot_actions_by_config_block(inventory, ssl_inventory),
            "allowed_hosts_warnings": allowed_hosts_warnings,
            "django_deploy_statuses": django_deploy_statuses,
            "pm2_status": pm2_status,
            "pm2_processes_by_project": processes_by_project,
            "active_action": database.get_active_action(),
            "last_action_result": database.get_last_unacknowledged_result(),
        },
    )


@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request) -> Response:
    db = get_database()
    lines = _log_lines_from_request(request)
    inventory = load_inventory()
    pm2_status = read_pm2_status(
        project_root=PROJECT_ROOT,
        projects=inventory.projects,
        db=db,
        runner=get_command_runner(db),
    )
    log_sections = list(read_system_logs(lines=lines))
    if pm2_status.error:
        log_sections.append(_log_error_section("pm2", "PM2", lines, pm2_status.error))
    else:
        for process in pm2_status.processes:
            if process.name:
                log_sections.append(read_pm2_log(process.name, runner=get_command_runner(db), lines=lines))
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "title": "Logs",
            "log_sections": log_sections,
            "log_lines": lines,
            "active_action": db.get_active_action(),
            "last_action_result": db.get_last_unacknowledged_result(),
        },
    )


@app.get("/api/projects")
def projects_api() -> dict[str, object]:
    inventory = load_inventory()
    payload = inventory_to_dict(inventory)
    payload["project_choices"] = [
        project_choice_to_dict(choice) for choice in build_project_choice_states(inventory.projects, get_database())
    ]
    payload["ssl"] = ssl_inventory_to_dict(discover_ssl_inventory(inventory.projects, letsencrypt_root=LETSENCRYPT_ROOT))
    payload["django_allowed_hosts"] = allowed_hosts_inventory_to_dict(check_django_allowed_hosts(inventory.projects))
    payload["django_deploy"] = django_deploy_statuses_to_dict(inspect_django_deploy(inventory.projects))
    return payload


@app.post("/api/projects/clone")
async def clone_project_api(request: Request) -> dict[str, object]:
    payload = await _request_payload(request)
    clone_url = payload.get("clone_url")
    if not isinstance(clone_url, str):
        raise HTTPException(status_code=400, detail="GitHub clone URL required")
    try:
        plan = plan_github_clone(clone_url, project_root=PROJECT_ROOT)
    except GitHubCloneValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    db = get_database()
    action_id = str(uuid.uuid4())
    command = github_clone_helper_command(action_id=action_id, plan=plan)
    try:
        result = get_system_action_service(db).run(command, action_id=action_id)
    except ActionLockError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return {
        "clone": _system_action_result_to_dict(result),
        "clone_result": _helper_stdout_json(result.result.stdout),
        "project_visible": plan.target_path.is_dir(),
    }


@app.get("/api/ssl")
def ssl_api() -> dict[str, object]:
    inventory = load_inventory()
    return ssl_inventory_to_dict(discover_ssl_inventory(inventory.projects, letsencrypt_root=LETSENCRYPT_ROOT))


@app.get("/api/certbot/renewal")
def certbot_renewal_api() -> dict[str, object]:
    db = get_database()
    return {
        "renewal": certbot_renewal_status_to_dict(inspect_certbot_renewal()),
        "last_dry_run": _action_to_dict(db.get_last_action_result_by_type("certbot-renew-dry-run")),
    }


@app.post("/api/certbot/renew/dry-run")
def certbot_renew_dry_run_api() -> dict[str, object]:
    db = get_database()
    try:
        result = get_system_action_service(db).run(certbot_renew_dry_run_command())
    except ActionLockError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _system_action_result_to_dict(result)


@app.get("/api/pm2")
def pm2_api() -> dict[str, object]:
    inventory = load_inventory()
    db = get_database()
    return pm2_inventory_to_dict(
        read_pm2_status(
            project_root=PROJECT_ROOT,
            projects=inventory.projects,
            db=db,
            runner=get_command_runner(db),
        )
    )


@app.post("/api/projects/{project_name}/django/{operation}")
def django_deploy_action_api(project_name: str, operation: str) -> dict[str, object]:
    project = find_project_or_404(project_name)
    status = inspect_django_deploy((project,))[0]
    action = action_for_operation(status, operation)
    if action is None:
        raise HTTPException(status_code=400, detail=f"Unsupported Django deploy operation: {operation}")
    if not status.is_django:
        raise HTTPException(status_code=404, detail="Project is not a Django project")
    if not action.available:
        raise HTTPException(status_code=400, detail=f"Django deploy action unavailable: {', '.join(action.blockers)}")
    db = get_database()
    try:
        result = get_system_action_service(db).run(
            django_manage_command(
                operation,
                project_root=PROJECT_ROOT,
                project_path=project.project_path,
                python_path=status.python_path,
            )
        )
    except ActionLockError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return _system_action_result_to_dict(result)


@app.post("/api/projects/{project_name}/pm2/create")
async def pm2_create_api(project_name: str, request: Request) -> dict[str, object]:
    payload = await _request_payload(request)
    pm2_name = _blank_to_none(payload.get("pm2_name")) or project_name
    db = get_database()
    project, choice = find_choice_or_404(project_name, db)
    if choice.selected_pm2_start_script is None:
        raise HTTPException(status_code=400, detail="PM2 start script must be selected before create")
    if db.list_managed_pm2_processes(project_name):
        raise HTTPException(status_code=400, detail="Project already has a managed PM2 process")
    try:
        command = pm2_start_command(
            name=pm2_name,
            project_root=PROJECT_ROOT,
            cwd=project.project_path,
            start_script=choice.selected_pm2_start_script,
        )
        result = get_system_action_service(db).run(command)
    except ActionLockError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if result.result.exit_code == 0 or result.result.state_changed:
        db.register_pm2_process(
            project_name=project.project_name,
            pm2_name=pm2_name,
            cwd=project.project_path,
            start_script=choice.selected_pm2_start_script,
        )
    return _system_action_result_to_dict(result)


@app.post("/api/projects/{project_name}/pm2/{pm2_name}/{operation}")
def pm2_lifecycle_api(project_name: str, pm2_name: str, operation: str) -> dict[str, object]:
    if operation not in {"delete", "reload", "restart", "start", "stop"}:
        raise HTTPException(status_code=400, detail=f"Unsupported PM2 operation: {operation}")
    db = get_database()
    managed = _managed_pm2_process_or_404(db, project_name, pm2_name)
    try:
        result = get_system_action_service(db).run(pm2_lifecycle_command(operation, managed.pm2_name))
    except ActionLockError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if operation == "delete" and (result.result.exit_code == 0 or result.result.state_changed):
        db.unregister_pm2_process(project_name=project_name, pm2_name=pm2_name)
    return _system_action_result_to_dict(result)


@app.get("/api/logs/system/{log_name}")
def system_log_api(log_name: str, lines: int | None = None) -> dict[str, object]:
    try:
        normalized_lines = normalize_log_lines(lines)
        section = read_system_log(log_name, lines=normalized_lines)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return log_section_to_dict(section)


@app.get("/api/logs/pm2/{pm2_name}")
def pm2_log_api(pm2_name: str, lines: int | None = None) -> dict[str, object]:
    try:
        normalized_lines = normalize_log_lines(lines)
        _validate_pm2_log_target(pm2_name)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return log_section_to_dict(read_pm2_log(pm2_name, runner=get_command_runner(get_database()), lines=normalized_lines))


@app.post("/api/dns/preflight")
async def dns_preflight_api(request: Request) -> dict[str, object]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object expected")
    domains = payload.get("domains")
    if not isinstance(domains, list) or not all(isinstance(domain, str) and domain for domain in domains):
        raise HTTPException(status_code=400, detail="domains must be a non-empty string list")
    return dns_preflight_to_dict(check_dns_preflight(domains, get_database()))


@app.post("/api/nginx/test")
def nginx_test_api() -> dict[str, object]:
    db = get_database()
    try:
        result = get_system_action_service(db).run(nginx_test_command())
    except ActionLockError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    ok = result.result.exit_code == 0
    db.set_ui_state(
        "nginx.last_test",
        {
            "ok": ok,
            "action_id": result.action_id,
            "exit_code": result.result.exit_code,
        },
    )
    return _system_action_result_to_dict(result)


@app.post("/api/nginx/reload")
def nginx_reload_api() -> dict[str, object]:
    db = get_database()
    last_test = db.get_ui_state("nginx.last_test")
    if not last_test or last_test.get("ok") is not True:
        raise HTTPException(status_code=400, detail="Reload requires a successful nginx -t first")
    reload_blockers = _django_allowed_hosts_reload_blockers(load_inventory())
    if reload_blockers:
        raise HTTPException(status_code=400, detail=_allowed_hosts_error(reload_blockers))

    try:
        result = get_system_action_service(db).run(nginx_reload_command())
    except ActionLockError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _system_action_result_to_dict(result)


@app.post("/api/projects/{project_name}/configs/{config_name}/activate")
def activate_config_api(project_name: str, config_name: str) -> dict[str, object]:
    return _set_config_activation(project_name, config_name, subcommand="activate-config")


@app.post("/api/projects/{project_name}/configs/{config_name}/deactivate")
def deactivate_config_api(project_name: str, config_name: str) -> dict[str, object]:
    return _set_config_activation(project_name, config_name, subcommand="deactivate-config")


@app.post("/api/projects/{project_name}/configs/{config_name}/save")
async def save_config_api(project_name: str, config_name: str, request: Request) -> dict[str, object]:
    payload = await _request_payload(request)
    new_content = payload.get("content", payload.get("new_content"))
    if not isinstance(new_content, str):
        raise HTTPException(status_code=400, detail="Config content required")

    db = get_database()
    project, choice = find_choice_or_404(project_name, db)
    config = find_config_or_404(project_name, config_name)
    _require_selected_config(choice, config)

    action_id = str(uuid.uuid4())
    command = helper_command(
        subcommand="write-config",
        action_id=action_id,
        project=project.project_name,
        config_path=config.config_path,
        expected_file=config.config_path,
        payload={"new_content": new_content},
    )
    try:
        write_result = get_system_action_service(db).run(command, action_id=action_id)
    except ActionLockError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    response: dict[str, object] = {
        "write": _system_action_result_to_dict(write_result),
        "write_result": _helper_stdout_json(write_result.result.stdout),
        "nginx_test": None,
    }
    if write_result.result.exit_code == 0:
        nginx_result = _run_nginx_test_and_record(db, config_key=(project.project_name, config.config_path))
        response["nginx_test"] = _system_action_result_to_dict(nginx_result)

    refreshed_project, refreshed_choice = find_choice_or_404(project_name, db)
    git_status = inspect_project_git(refreshed_project, refreshed_choice)
    response["git"] = project_git_status_to_dict(git_status)
    if git_status.git_available and git_status.selected_config_dirty:
        response["diff"] = live_diff_for_selected_config(refreshed_project, refreshed_choice)
    return response


@app.post("/api/projects/{project_name}/configs/{config_name}/static-location")
async def static_location_api(project_name: str, config_name: str, request: Request) -> dict[str, object]:
    payload = await _request_payload(request)
    static_path = payload.get("static_path")
    if not isinstance(static_path, str):
        raise HTTPException(status_code=400, detail="Static path required")

    db = get_database()
    project, choice = find_choice_or_404(project_name, db)
    config = find_config_or_404(project_name, config_name)
    _require_selected_config(choice, config)
    try:
        candidate = select_static_asset_candidate(project.project_path, static_path)
        content = config.config_path.read_text(encoding="utf-8")
        new_content, changed = insert_static_location(content, candidate)
    except (OSError, StaticAssetError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    response: dict[str, object] = {
        "static_asset": static_asset_candidate_to_dict(candidate),
        "changed": changed,
        "write": None,
        "write_result": None,
        "nginx_test": None,
    }
    if changed:
        action_id = str(uuid.uuid4())
        command = helper_command(
            subcommand="write-config",
            action_id=action_id,
            project=project.project_name,
            config_path=config.config_path,
            expected_file=config.config_path,
            payload={"new_content": new_content},
        )
        try:
            write_result = get_system_action_service(db).run(command, action_id=action_id)
        except ActionLockError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        response["write"] = _system_action_result_to_dict(write_result)
        response["write_result"] = _helper_stdout_json(write_result.result.stdout)
        if write_result.result.exit_code == 0:
            nginx_result = _run_nginx_test_and_record(db, config_key=(project.project_name, config.config_path))
            response["nginx_test"] = _system_action_result_to_dict(nginx_result)

    refreshed_project, refreshed_choice = find_choice_or_404(project_name, db)
    git_status = inspect_project_git(refreshed_project, refreshed_choice)
    response["git"] = project_git_status_to_dict(git_status)
    if git_status.git_available and git_status.selected_config_dirty:
        response["diff"] = live_diff_for_selected_config(refreshed_project, refreshed_choice)
    return response


@app.post("/api/projects/{project_name}/configs/{config_name}/server-blocks/{block_ordinal}/certbot/issue")
def certbot_issue_api(project_name: str, config_name: str, block_ordinal: int) -> dict[str, object]:
    return _issue_certbot_for_block(project_name, config_name, block_ordinal)


@app.get("/api/actions/active")
def active_action_api() -> dict[str, object]:
    return {"active_action": _action_to_dict(get_database().get_active_action())}


@app.get("/api/actions/last-result")
def last_action_result_api() -> dict[str, object]:
    return {"last_action_result": _action_to_dict(get_database().get_last_unacknowledged_result())}


@app.post("/api/actions/{action_id}/ack")
def acknowledge_action_result_api(action_id: str) -> Response:
    db = get_database()
    try:
        db.acknowledge_action_result(action_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Action result not found") from error
    return RedirectResponse("/", status_code=303)


@app.get("/api/projects/{project_name}/git")
def project_git_api(project_name: str) -> dict[str, object]:
    db = get_database()
    project, choice = find_choice_or_404(project_name, db)
    return project_git_status_to_dict(inspect_project_git(project, choice))


@app.get("/api/projects/{project_name}/git/diff")
def project_git_diff_api(project_name: str) -> dict[str, object]:
    db = get_database()
    project, choice = find_choice_or_404(project_name, db)
    try:
        diff = live_diff_for_selected_config(project, choice)
    except GitWorkflowError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"project_name": project_name, "diff": diff}


@app.post("/api/projects/{project_name}/git/commit")
async def project_git_commit_api(project_name: str, request: Request) -> dict[str, object]:
    payload = await _request_payload(request)
    message = _blank_to_none(payload.get("message"))
    if message is None:
        raise HTTPException(status_code=400, detail="Commit message required")

    db = get_database()
    project, choice = find_choice_or_404(project_name, db)
    _ensure_saved_edit_passed_nginx_test(db, project.project_name, choice.selected_nginx_config)
    allowed_hosts_blockers = _django_allowed_hosts_findings_for_choice(project, choice)
    if allowed_hosts_blockers:
        raise HTTPException(status_code=400, detail=_allowed_hosts_error(allowed_hosts_blockers))
    try:
        result = GitWorkflowService(db, project_root=PROJECT_ROOT).commit_selected_config(
            project,
            choice,
            message=message,
        )
    except ActionLockError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except GitWorkflowError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if result.ok and choice.selected_nginx_config is not None:
        db.delete_ui_state(_config_edit_test_state_key(project.project_name, choice.selected_nginx_config))
    return git_workflow_result_to_dict(result)


@app.post("/api/projects/{project_name}/git/reset")
def project_git_reset_api(project_name: str) -> dict[str, object]:
    db = get_database()
    project, choice = find_choice_or_404(project_name, db)
    try:
        result = GitWorkflowService(db, project_root=PROJECT_ROOT).reset_selected_config(project, choice)
    except ActionLockError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except GitWorkflowError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    response = git_workflow_result_to_dict(result)
    if result.ok and choice.selected_nginx_config is not None:
        nginx_result = _run_nginx_test_and_record(db, config_key=(project.project_name, choice.selected_nginx_config))
        response["nginx_test"] = _system_action_result_to_dict(nginx_result)
        db.delete_ui_state(_config_edit_test_state_key(project.project_name, choice.selected_nginx_config))
    return response


@app.post("/api/projects/{project_name}/selection")
async def save_project_selection_api(project_name: str, request: Request) -> dict[str, object]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object expected")

    project = find_project_or_404(project_name)
    try:
        choice = save_project_choice(
            project,
            get_database(),
            nginx_config_path=_blank_to_none(payload.get("nginx_config_path")),
            pm2_start_script_path=_blank_to_none(payload.get("pm2_start_script_path")),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return project_choice_to_dict(choice)


@app.get("/configs/{project_name}/{config_name}", response_class=HTMLResponse)
def config_detail(request: Request, project_name: str, config_name: str) -> Response:
    db = get_database()
    project, choice = find_choice_or_404(project_name, db)
    config = find_config_or_404(project_name, config_name)
    _require_selected_config(choice, config)
    content = config.config_path.read_text(encoding="utf-8")
    git_status = inspect_project_git(project, choice)
    ssl_inventory = discover_ssl_inventory((project,), letsencrypt_root=LETSENCRYPT_ROOT)
    config_diff = ""
    if git_status.git_available and git_status.selected_config_dirty:
        config_diff = live_diff_for_selected_config(project, choice)
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "title": f"{config.project_name} / {config.config_path.name}",
            "config": config,
            "config_content": content,
            "git_status": git_status,
            "config_diff": config_diff,
            "static_asset_candidates": discover_static_asset_candidates(project.project_path),
            "certbot_actions": _certbot_actions_by_config_block((project,), ssl_inventory),
            "config_test_result": db.get_ui_state(_config_edit_test_state_key(project.project_name, config.config_path)),
            "active_action": db.get_active_action(),
            "last_action_result": db.get_last_unacknowledged_result(),
        },
    )


@app.get("/styleguide", response_class=HTMLResponse)
def styleguide(request: Request) -> Response:
    return templates.TemplateResponse(request, "styleguide.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _blank_to_none(value) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="Selection values must be strings or null")
    return value


async def _request_payload(request: Request) -> dict[str, object]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON object expected")
        return payload
    form = await request.form()
    return dict(form)


def _log_lines_from_request(request: Request) -> int:
    try:
        return normalize_log_lines(request.query_params.get("lines"))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def _validate_pm2_log_target(pm2_name: str) -> None:
    db = get_database()
    inventory = load_inventory()
    status = read_pm2_status(
        project_root=PROJECT_ROOT,
        projects=inventory.projects,
        db=db,
        runner=get_command_runner(db),
    )
    if status.error:
        raise ValueError(status.error)
    if not any(process.name == pm2_name for process in status.processes):
        raise ValueError(f"Unknown PM2 process: {pm2_name}")


def _managed_pm2_process_or_404(db: AppDatabase, project_name: str, pm2_name: str):
    for process in db.list_managed_pm2_processes(project_name):
        if process.pm2_name == pm2_name:
            return process
    raise HTTPException(status_code=404, detail="Managed PM2 process not found")


def _log_error_section(key: str, label: str, lines: int, error: str) -> LogSection:
    return LogSection(key=key, label=label, lines=lines, content="", error=error)


def _require_selected_config(choice, config: NginxConfig) -> None:
    if choice.selected_nginx_config is None or choice.selected_nginx_config.resolve() != config.config_path.resolve():
        raise HTTPException(status_code=400, detail="Config must be the saved project Nginx selection")


def _run_nginx_test_and_record(
    db: AppDatabase,
    *,
    config_key: tuple[str, Path] | None = None,
):
    try:
        result = get_system_action_service(db).run(nginx_test_command())
    except ActionLockError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    ok = result.result.exit_code == 0
    state = {
        "ok": ok,
        "action_id": result.action_id,
        "exit_code": result.result.exit_code,
    }
    db.set_ui_state("nginx.last_test", state)
    if config_key is not None:
        project_name, config_path = config_key
        db.set_ui_state(
            _config_edit_test_state_key(project_name, config_path),
            {
                **state,
                "project": project_name,
                "config_path": str(config_path),
            },
        )
    return result


def _ensure_saved_edit_passed_nginx_test(db: AppDatabase, project_name: str, selected_config: Path | None) -> None:
    if selected_config is None:
        return
    state = db.get_ui_state(_config_edit_test_state_key(project_name, selected_config))
    if state is None:
        return
    if state.get("ok") is not True:
        raise HTTPException(status_code=400, detail="Commit requires a successful nginx -t for the saved config edit")


def _config_edit_test_state_key(project_name: str, config_path: Path) -> str:
    return f"config-edit-test:{project_name}:{config_path}"


def _helper_stdout_json(stdout: str) -> dict[str, object] | None:
    if not stdout.strip():
        return None
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _issue_certbot_for_block(project_name: str, config_name: str, block_ordinal: int) -> dict[str, object]:
    db = get_database()
    inventory = load_inventory()
    project, choice = _find_choice_in_inventory_or_404(project_name, inventory, db)
    config = _find_config_in_project_or_404(project, config_name)
    _require_selected_config(choice, config)
    block = _find_server_block_or_404(config, block_ordinal)
    _validate_certbot_server_block(inventory, project, config, block)
    _validate_certbot_needs_certificate(inventory, project, block)
    _validate_clean_git_for_certbot(project, choice)

    response: dict[str, object] = {
        "ok": False,
        "project_name": project.project_name,
        "config_path": str(config.config_path),
        "server_block": block.ordinal,
        "domains": list(block.server_names),
        "pre_nginx_test": None,
        "dns_preflight": None,
        "certbot": None,
        "post_nginx_test": None,
        "reload": None,
        "git": None,
        "commit_required": False,
        "error": None,
    }

    pre_nginx = _run_nginx_test_and_record(db)
    response["pre_nginx_test"] = _system_action_result_to_dict(pre_nginx)
    if pre_nginx.result.exit_code != 0:
        response["error"] = "pre_nginx_test_failed"
        _record_certbot_workflow_audit(db, project.project_name, block, response)
        return response

    dns_result = check_dns_preflight(block.server_names, db)
    response["dns_preflight"] = dns_preflight_to_dict(dns_result)
    if not dns_result.ok:
        response["error"] = "dns_preflight_failed"
        _record_certbot_workflow_audit(db, project.project_name, block, response)
        return response

    try:
        certbot_result = get_system_action_service(db).run(
            certbot_issue_command(block.server_names, project=project.project_name, config_path=config.config_path)
        )
    except ActionLockError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    response["certbot"] = _system_action_result_to_dict(certbot_result)
    if certbot_result.result.exit_code != 0:
        response["error"] = "certbot_failed"
        _record_certbot_workflow_audit(db, project.project_name, block, response)
        return response

    post_nginx = _run_nginx_test_and_record(db, config_key=(project.project_name, config.config_path))
    response["post_nginx_test"] = _system_action_result_to_dict(post_nginx)
    if post_nginx.result.exit_code != 0:
        response["error"] = "post_nginx_test_failed"
        _record_certbot_workflow_audit(db, project.project_name, block, response)
        return response

    refreshed_project, refreshed_choice = find_choice_or_404(project_name, db)
    git_status = inspect_project_git(refreshed_project, refreshed_choice)
    response["git"] = project_git_status_to_dict(git_status)
    response["commit_required"] = git_status.selected_config_dirty
    if not _certbot_git_diff_allows_reload(git_status):
        response["error"] = "uncontrolled_git_diff"
        _record_certbot_workflow_audit(db, project.project_name, block, response)
        return response

    try:
        reload_result = get_system_action_service(db).run(nginx_reload_command())
    except ActionLockError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    response["reload"] = _system_action_result_to_dict(reload_result)
    response["ok"] = reload_result.result.exit_code == 0
    if reload_result.result.exit_code != 0:
        response["error"] = "reload_failed"

    _record_certbot_workflow_audit(db, project.project_name, block, response)
    return response


def _find_choice_in_inventory_or_404(project_name: str, inventory, db: AppDatabase):
    for project in inventory.projects:
        if project.project_name == project_name:
            choices = build_project_choice_states((project,), db)
            if not choices:
                raise HTTPException(status_code=404, detail="Project choice not found")
            return project, choices[0]
    raise HTTPException(status_code=404, detail="Project not found")


def _find_config_in_project_or_404(project, config_name: str) -> NginxConfig:
    for config in project.configs:
        if config.config_path.name == config_name:
            return config
    raise HTTPException(status_code=404, detail="Config not found")


def _find_server_block_or_404(config: NginxConfig, block_ordinal: int):
    for block in config.server_blocks:
        if block.ordinal == block_ordinal:
            return block
    raise HTTPException(status_code=404, detail="Server block not found")


def _validate_certbot_server_block(inventory, project, config: NginxConfig, block) -> None:
    if not block.certbot_eligible:
        raise HTTPException(status_code=400, detail=f"Server block is not certbot-capable: {block.certbot_blockers}")
    if certificate_server_group(config.server_blocks, block.server_names) is None:
        raise HTTPException(
            status_code=400, detail="server_name group must match one unambiguous HTTP/HTTPS configuration"
        )

    conflicts: list[str] = []
    selected_path = config.config_path.resolve()
    selected_domains = set(block.server_names)
    for candidate_project in inventory.projects:
        for candidate_config in candidate_project.configs:
            if candidate_config.config_path.resolve() == selected_path:
                continue
            for candidate_block in candidate_config.domain_groups:
                overlapping = selected_domains.intersection(candidate_block.server_names)
                if overlapping:
                    conflicts.append(f"{candidate_config.config_path}: {', '.join(sorted(overlapping))}")
    if conflicts:
        raise HTTPException(status_code=400, detail=f"server_name group conflicts with another config: {conflicts}")


def _validate_certbot_needs_certificate(inventory, project, block) -> None:
    ssl_inventory = discover_ssl_inventory(inventory.projects, letsencrypt_root=LETSENCRYPT_ROOT)
    action = _certbot_action_for_block(block, _ssl_status_for_project(ssl_inventory, project.project_name))
    if not action["available"]:
        raise HTTPException(status_code=400, detail=f"Certbot action not available: {action['blockers']}")


def _validate_clean_git_for_certbot(project, choice) -> None:
    git_status = inspect_project_git(project, choice)
    if git_status.error:
        raise HTTPException(status_code=400, detail=git_status.error)
    if git_status.repo_dirty:
        raise HTTPException(status_code=400, detail="Project Git status must be clean before Certbot")


def _certbot_git_diff_allows_reload(git_status) -> bool:
    if git_status.error:
        return False
    if git_status.foreign_changes:
        return False
    if git_status.repo_dirty and not git_status.selected_config_dirty:
        return False
    return True


def _record_certbot_workflow_audit(db: AppDatabase, project_name: str, block, response: dict[str, object]) -> None:
    db.record_audit(
        action="certbot-issue-workflow",
        project=project_name,
        subject_type="server-block",
        subject=f"{block.ordinal}:{', '.join(block.server_names)}",
        result="succeeded" if response.get("ok") is True else "failed",
        message_excerpt=str(response.get("error") or "ok"),
    )


def _certbot_actions_by_config_block(projects_or_inventory, ssl_inventory) -> dict[Path, dict[int, dict[str, object]]]:
    projects = projects_or_inventory.projects if hasattr(projects_or_inventory, "projects") else projects_or_inventory
    ssl_by_project = {project.project_name: project for project in ssl_inventory.projects}
    actions: dict[Path, dict[int, dict[str, object]]] = {}
    for project in projects:
        project_ssl = ssl_by_project.get(project.project_name)
        for config in project.configs:
            actions[config.config_path] = {
                block.ordinal: _certbot_action_for_block(block, project_ssl)
                for block in config.domain_groups
                if certificate_server_group(config.server_blocks, block.server_names) is not None
            }
    return actions


def _ssl_renewal_warning_projects(ssl_inventory) -> set[str]:
    warning_states = {"expired", "critical", "red", "yellow"}
    return {
        project.project_name
        for project in ssl_inventory.projects
        if any(domain.state in warning_states for domain in project.domains)
    }


def _certbot_action_for_block(block, project_ssl) -> dict[str, object]:
    blockers = list(block.certbot_blockers)
    if not _block_needs_certificate(block, project_ssl):
        blockers.append("certificate_present")
    return {
        "available": block.certbot_eligible and not blockers,
        "domains": list(block.server_names),
        "blockers": blockers,
    }


def _block_needs_certificate(block, project_ssl) -> bool:
    if project_ssl is None:
        return True
    statuses = {status.domain: status for status in project_ssl.domains}
    for domain in block.server_names:
        status = statuses.get(domain)
        if status is None or status.state in {"missing", "error"}:
            return True
    return False


def _ssl_status_for_project(ssl_inventory, project_name: str):
    for project_ssl in ssl_inventory.projects:
        if project_ssl.project_name == project_name:
            return project_ssl
    return None


def _set_session_cookie(response: Response, session_token: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )


def _set_config_activation(project_name: str, config_name: str, *, subcommand: str) -> dict[str, object]:
    db = get_database()
    project, choice = find_choice_or_404(project_name, db)
    config = find_config_or_404(project_name, config_name)
    if choice.selected_nginx_config is None or choice.selected_nginx_config.resolve() != config.config_path.resolve():
        raise HTTPException(status_code=400, detail="Config must be the saved project Nginx selection")

    action_id = str(uuid.uuid4())
    link_path = SITES_ENABLED_ROOT / f"{project.project_name}.conf"
    command = helper_command(
        subcommand=subcommand,
        action_id=action_id,
        project=project.project_name,
        config_path=config.config_path,
        expected_file=config.config_path,
        payload={"link_path": str(link_path)},
    )
    try:
        helper_result = get_system_action_service(db).run(command, action_id=action_id)
    except ActionLockError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    response: dict[str, object] = {"activation": _system_action_result_to_dict(helper_result), "nginx_test": None}
    if helper_result.result.exit_code == 0:
        try:
            nginx_result = get_system_action_service(db).run(nginx_test_command())
        except ActionLockError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        ok = nginx_result.result.exit_code == 0
        db.set_ui_state(
            "nginx.last_test",
            {
                "ok": ok,
                "action_id": nginx_result.action_id,
                "exit_code": nginx_result.result.exit_code,
            },
        )
        response["nginx_test"] = _system_action_result_to_dict(nginx_result)
    return response


def _action_to_dict(action) -> dict[str, object] | None:
    if action is None:
        return None
    return {
        "action_id": action.action_id,
        "status": action.status,
        "action_type": action.action_type,
        "target": action.target,
        "project": action.project,
        "config_path": action.config_path,
        "started_at": action.started_at,
        "updated_at": action.updated_at,
        "finished_at": action.finished_at,
        "exit_code": action.exit_code,
        "stdout_excerpt": action.stdout_excerpt,
        "stderr_excerpt": action.stderr_excerpt,
        "error_excerpt": action.error_excerpt,
        "banner_acknowledged": action.banner_acknowledged,
    }


def _allowed_hosts_warnings_by_config_block(inventory: AllowedHostsInventory):
    warnings_by_config: dict[Path, dict[int, AllowedHostsFinding]] = {}
    for finding in inventory.findings:
        warnings_by_config.setdefault(finding.config_path, {})[finding.server_block_ordinal] = finding
    return warnings_by_config


def _django_allowed_hosts_findings_for_choice(project, choice) -> tuple[AllowedHostsFinding, ...]:
    if choice.selected_nginx_config is None:
        return ()
    return findings_for_config(project, choice.selected_nginx_config)


def _django_allowed_hosts_reload_blockers(inventory) -> tuple[AllowedHostsFinding, ...]:
    enabled_configs = {
        config.config_path for project in inventory.projects for config in project.configs if config.enabled
    }
    allowed_hosts_inventory = check_django_allowed_hosts(inventory.projects)
    return tuple(finding for finding in allowed_hosts_inventory.findings if finding.config_path in enabled_configs)


def _allowed_hosts_error(findings: tuple[AllowedHostsFinding, ...]) -> str:
    domains = sorted({domain for finding in findings for domain in finding.missing_domains})
    return f"Django ALLOWED_HOSTS fehlt fuer: {', '.join(domains)}"


def _pm2_processes_by_project(inventory: PM2StatusInventory):
    processes_by_project: dict[str, list[object]] = {}
    for process in inventory.processes:
        if process.project_name is not None:
            processes_by_project.setdefault(process.project_name, []).append(process)
    return processes_by_project


def _system_action_result_to_dict(result) -> dict[str, object]:
    return {
        "action_id": result.action_id,
        "action_type": result.command.action_type,
        "target": result.command.target,
        "exit_code": result.result.exit_code,
        "stdout": result.result.stdout,
        "stderr": result.result.stderr,
        "ok": result.result.exit_code == 0,
    }
