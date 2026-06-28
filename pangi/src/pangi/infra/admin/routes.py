from __future__ import annotations

import secrets
import time
from hashlib import sha256
import hmac
from datetime import date, datetime
from html import escape
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from pangi.config import get_settings
from pangi.domain.models import AgentJob, CodexRun, ScheduleType, ScheduledTask, ScheduledTaskRun, SlackThread, utc_now
from pangi.infra.notion.oauth import NotionOAuthClient, NotionOAuthError
from pangi.infra.notion.token_store import JsonNotionTokenStore
from pangi.repository import get_job_repository
from pangi.usecase.scheduler import (
    ScheduleValidationError,
    compute_initial_next_run,
    local_datetime_to_utc,
    normalize_timezone,
    parse_time_of_day,
)


ADMIN_PATH_PREFIX = "/pangi-admin"
ADMIN_HOME_PATH = f"{ADMIN_PATH_PREFIX}/"
ADMIN_LOGIN_PATH = f"{ADMIN_PATH_PREFIX}/login"
ADMIN_DB_PATH = f"{ADMIN_PATH_PREFIX}/db"
ADMIN_LOGOUT_PATH = f"{ADMIN_PATH_PREFIX}/logout"
ADMIN_MCP_PATH = f"{ADMIN_PATH_PREFIX}/mcp"
ADMIN_NOTION_PATH = f"{ADMIN_PATH_PREFIX}/notion"
ADMIN_NOTION_CONNECT_PATH = f"{ADMIN_PATH_PREFIX}/notion/connect"
ADMIN_NOTION_CALLBACK_PATH = f"{ADMIN_PATH_PREFIX}/notion/callback"
ADMIN_NOTION_DISCONNECT_PATH = f"{ADMIN_PATH_PREFIX}/notion/disconnect"
ADMIN_SCHEDULES_PATH = f"{ADMIN_PATH_PREFIX}/schedules"

router = APIRouter(prefix=ADMIN_PATH_PREFIX, tags=["admin"])
ADMIN_USERNAME = "pangi"
ADMIN_COOKIE_NAME = "pangi_admin_session"
ADMIN_SESSION_TTL_SECONDS = 60 * 60 * 12


@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request) -> Response:
    _require_admin_enabled()
    if _has_valid_session(request):
        return RedirectResponse(ADMIN_HOME_PATH, status_code=status.HTTP_303_SEE_OTHER)
    return HTMLResponse(_render_login_page())


@router.post("/login")
async def admin_login(request: Request) -> Response:
    settings = _require_admin_enabled()
    body = await request.body()
    form = {key: values[0] if values else "" for key, values in parse_qs(body.decode("utf-8")).items()}
    username_ok = secrets.compare_digest(form.get("username", ""), ADMIN_USERNAME)
    password_ok = secrets.compare_digest(form.get("password", ""), settings.admin_password or "")
    if not username_ok or not password_ok:
        return HTMLResponse(_render_login_page(error="아이디 또는 비밀번호가 올바르지 않습니다."), status_code=401)

    response = RedirectResponse(ADMIN_HOME_PATH, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        _new_session_cookie(settings.admin_password or ""),
        max_age=ADMIN_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/", response_class=HTMLResponse)
async def admin_home(request: Request) -> Response:
    _require_admin_enabled()
    if not _has_valid_session(request):
        return RedirectResponse(ADMIN_LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    repository = get_job_repository()
    return HTMLResponse(
        _render_home_page(
            jobs=repository.list_jobs(limit=10),
            tasks=repository.list_scheduled_tasks(limit=10),
            runs=repository.list_scheduled_task_runs(limit=10),
        )
    )


@router.post("/logout")
async def admin_logout() -> RedirectResponse:
    _require_admin_enabled()
    response = RedirectResponse(ADMIN_LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return response


@router.get("/db", response_class=HTMLResponse)
async def admin_db(request: Request) -> Response:
    _require_admin_enabled()
    if not _has_valid_session(request):
        return RedirectResponse(ADMIN_LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    repository = get_job_repository()
    html = _render_page(
        threads=repository.list_threads(limit=50),
        jobs=repository.list_jobs(limit=50),
        codex_runs=repository.list_codex_runs(limit=50),
    )
    return HTMLResponse(html)


@router.get("/mcp", response_class=HTMLResponse)
async def admin_mcp(request: Request) -> Response:
    _require_admin_enabled()
    if not _has_valid_session(request):
        return RedirectResponse(ADMIN_LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    token_store = _notion_token_store()
    notion_connection = token_store.load() if token_store else None
    return HTMLResponse(
        _render_mcp_page(notion_connected=bool(notion_connection and notion_connection.tokens))
    )


@router.get("/schedules", response_class=HTMLResponse)
async def admin_schedules(request: Request) -> Response:
    _require_admin_enabled()
    if not _has_valid_session(request):
        return RedirectResponse(ADMIN_LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    repository = get_job_repository()
    return HTMLResponse(
        _render_schedules_page(
            tasks=repository.list_scheduled_tasks(limit=100),
            runs=repository.list_scheduled_task_runs(limit=50),
        )
    )


@router.post("/schedules")
async def admin_create_schedule(request: Request) -> Response:
    settings = _require_admin_enabled()
    if not _has_valid_session(request):
        return RedirectResponse(ADMIN_LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)

    body = await request.body()
    form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    repository = get_job_repository()
    try:
        task_input = _scheduled_task_input_from_form(form)
        settings.validate_slack_access(
            user_id=task_input["requester_user_id"],
            channel_id=task_input["channel_id"],
        )
        repository.create_scheduled_task(**task_input)
    except Exception as error:
        return HTMLResponse(
            _render_schedules_page(
                tasks=repository.list_scheduled_tasks(limit=100),
                runs=repository.list_scheduled_task_runs(limit=50),
                error=str(error),
            ),
            status_code=400,
        )
    return RedirectResponse(ADMIN_SCHEDULES_PATH, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/schedules/{task_id}/enable")
async def admin_enable_schedule(request: Request, task_id: str) -> Response:
    _require_admin_enabled()
    if not _has_valid_session(request):
        return RedirectResponse(ADMIN_LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    get_job_repository().set_scheduled_task_enabled(task_id, enabled=True)
    return RedirectResponse(ADMIN_SCHEDULES_PATH, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/schedules/{task_id}/disable")
async def admin_disable_schedule(request: Request, task_id: str) -> Response:
    _require_admin_enabled()
    if not _has_valid_session(request):
        return RedirectResponse(ADMIN_LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    get_job_repository().set_scheduled_task_enabled(task_id, enabled=False)
    return RedirectResponse(ADMIN_SCHEDULES_PATH, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/notion", response_class=HTMLResponse)
async def admin_notion(request: Request) -> Response:
    _require_admin_enabled()
    if not _has_valid_session(request):
        return RedirectResponse(ADMIN_LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    token_store = _notion_token_store()
    connection = token_store.load() if token_store else None
    return HTMLResponse(_render_notion_page(connection_status="connected" if connection and connection.tokens else "disconnected"))


@router.post("/notion/connect")
async def admin_notion_connect(request: Request) -> Response:
    settings = _require_admin_enabled()
    if not _has_valid_session(request):
        return RedirectResponse(ADMIN_LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    token_store = _notion_token_store()
    if token_store is None:
        return HTMLResponse(_render_notion_page(error="Notion token store path가 설정되지 않았습니다."), status_code=400)
    oauth_client = NotionOAuthClient(mcp_url=settings.notion_mcp_url, token_store=token_store)
    try:
        authorize_url = await oauth_client.begin_authorization(redirect_uri=_notion_callback_url(request))
    except NotionOAuthError as error:
        return HTMLResponse(_render_notion_page(error=f"Notion OAuth 시작에 실패했습니다: {error}"), status_code=502)
    return RedirectResponse(authorize_url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/notion/callback", response_class=HTMLResponse)
async def admin_notion_callback(request: Request) -> Response:
    settings = _require_admin_enabled()
    token_store = _notion_token_store()
    if token_store is None:
        return HTMLResponse(_render_notion_page(error="Notion token store path가 설정되지 않았습니다."), status_code=400)
    error = request.query_params.get("error")
    if error:
        return HTMLResponse(_render_notion_page(error=f"Notion OAuth error: {error}"), status_code=400)
    code = request.query_params.get("code") or ""
    state_param = request.query_params.get("state") or ""
    oauth_client = NotionOAuthClient(mcp_url=settings.notion_mcp_url, token_store=token_store)
    try:
        await oauth_client.complete_authorization(code=code, state=state_param)
    except NotionOAuthError as oauth_error:
        return HTMLResponse(_render_notion_page(error=f"Notion OAuth 완료에 실패했습니다: {oauth_error}"), status_code=400)
    return RedirectResponse(ADMIN_NOTION_PATH, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/notion/disconnect")
async def admin_notion_disconnect(request: Request) -> Response:
    _require_admin_enabled()
    if not _has_valid_session(request):
        return RedirectResponse(ADMIN_LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    token_store = _notion_token_store()
    if token_store:
        token_store.clear()
    return RedirectResponse(ADMIN_NOTION_PATH, status_code=status.HTTP_303_SEE_OTHER)


def _require_admin_enabled():
    settings = get_settings()
    if not settings.enable_admin_pages:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if settings.admin_password is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return settings


def _notion_token_store() -> JsonNotionTokenStore | None:
    settings = get_settings()
    if settings.notion_token_store_path is None:
        return None
    return JsonNotionTokenStore(settings.notion_token_store_path)


def _notion_callback_url(request: Request) -> str:
    settings = get_settings()
    if settings.public_base_url:
        return settings.public_base_url + ADMIN_NOTION_CALLBACK_PATH
    return str(request.url_for("admin_notion_callback"))


def _scheduled_task_input_from_form(form: dict[str, list[str]]) -> dict[str, object]:
    name = _form_value(form, "name")
    team_id = _form_value(form, "team_id")
    channel_id = _form_value(form, "channel_id")
    requester_user_id = _form_value(form, "requester_user_id")
    prompt = _form_value(form, "prompt")
    timezone_name = normalize_timezone(_form_value(form, "timezone") or "Asia/Seoul")
    schedule_type = ScheduleType(_form_value(form, "schedule_type"))
    time_of_day = None
    days_of_week = None
    run_at = None

    if not name or not team_id or not channel_id or not requester_user_id or not prompt:
        raise ScheduleValidationError("name, team_id, channel_id, requester_user_id, prompt are required")

    if schedule_type == ScheduleType.ONCE:
        run_date = _parse_date(_form_value(form, "run_date"))
        run_time = parse_time_of_day(_form_value(form, "time_of_day"))
        run_at = local_datetime_to_utc(local_date=run_date, local_time=run_time, timezone_name=timezone_name)
    else:
        parsed_time = parse_time_of_day(_form_value(form, "time_of_day"))
        time_of_day = f"{parsed_time.hour:02d}:{parsed_time.minute:02d}"
        if schedule_type == ScheduleType.WEEKLY:
            selected_days = tuple(day for day in form.get("days_of_week", []) if day.strip())
            if not selected_days:
                raise ScheduleValidationError("weekly schedule requires at least one day")
            days_of_week = ",".join(sorted(set(selected_days), key=int))

    next_run_at = compute_initial_next_run(
        schedule_type=schedule_type,
        timezone_name=timezone_name,
        time_of_day=time_of_day,
        days_of_week=days_of_week,
        run_at=run_at,
        after=utc_now(),
    )
    if next_run_at is None:
        raise ScheduleValidationError("next run must be in the future")

    return {
        "name": name,
        "team_id": team_id,
        "channel_id": channel_id,
        "requester_user_id": requester_user_id,
        "prompt": prompt,
        "schedule_type": schedule_type,
        "timezone": timezone_name,
        "time_of_day": time_of_day,
        "days_of_week": days_of_week,
        "run_at": run_at,
        "next_run_at": next_run_at,
        "enabled": True,
    }


def _form_value(form: dict[str, list[str]], name: str) -> str:
    values = form.get(name) or [""]
    return values[0].strip()


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ScheduleValidationError("run_date must use YYYY-MM-DD") from None


def _has_valid_session(request: Request) -> bool:
    settings = _require_admin_enabled()
    raw_cookie = request.cookies.get(ADMIN_COOKIE_NAME)
    if not raw_cookie:
        return False
    try:
        issued_at_raw, signature = raw_cookie.split(":", 1)
        issued_at = int(issued_at_raw)
    except ValueError:
        return False
    if issued_at <= 0 or time.time() - issued_at > ADMIN_SESSION_TTL_SECONDS:
        return False
    expected = _session_signature(settings.admin_password or "", issued_at_raw)
    return secrets.compare_digest(signature, expected)


def _new_session_cookie(admin_password: str) -> str:
    issued_at = str(int(time.time()))
    return f"{issued_at}:{_session_signature(admin_password, issued_at)}"


def _session_signature(admin_password: str, issued_at: str) -> str:
    return hmac.new(admin_password.encode("utf-8"), issued_at.encode("utf-8"), sha256).hexdigest()


def _render_login_page(error: str | None = None) -> str:
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pangi Admin Login</title>
  <style>
    :root {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #eef1f4;
      color: #172026;
    }}
    body {{
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
    }}
    form {{
      width: min(360px, calc(100vw - 32px));
      padding: 24px;
      background: #ffffff;
      border: 1px solid #d9dee3;
      border-radius: 8px;
      box-shadow: 0 12px 30px rgba(21, 35, 48, 0.08);
    }}
    h1 {{ margin: 0 0 18px; font-size: 22px; letter-spacing: 0; }}
    label {{ display: block; margin: 12px 0 6px; font-size: 13px; color: #43505a; }}
    input {{
      width: 100%;
      box-sizing: border-box;
      padding: 10px 11px;
      border: 1px solid #c8d0d7;
      border-radius: 6px;
      font: inherit;
    }}
    button {{
      width: 100%;
      margin-top: 18px;
      padding: 10px 12px;
      border: 0;
      border-radius: 6px;
      background: #0f766e;
      color: #ffffff;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
    }}
    .error {{
      margin: 0 0 12px;
      padding: 9px 10px;
      border-radius: 6px;
      background: #fff1f0;
      color: #b42318;
      font-size: 13px;
    }}
  </style>
</head>
<body>
  <form method="post" action="{ADMIN_LOGIN_PATH}">
    <h1>Pangi Admin</h1>
    {error_html}
    <label for="username">아이디</label>
    <input id="username" name="username" autocomplete="username" autofocus>
    <label for="password">비밀번호</label>
    <input id="password" name="password" type="password" autocomplete="current-password">
    <button type="submit">로그인</button>
  </form>
</body>
</html>"""


def _render_home_page(
    *,
    jobs: list[AgentJob],
    tasks: list[ScheduledTask],
    runs: list[ScheduledTaskRun],
) -> str:
    settings = get_settings()
    active_tasks = sum(1 for task in tasks if task.enabled)
    failed_runs = sum(1 for run in runs if run.status.value == "failed")
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pangi Admin</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f6f7f8;
      color: #172026;
    }}
    body {{ margin: 0; }}
    header {{
      padding: 24px 28px 16px;
      background: #ffffff;
      border-bottom: 1px solid #d9dee3;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    main {{ padding: 22px 28px 38px; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 10px; font-size: 18px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 8px; font-size: 16px; letter-spacing: 0; }}
    p {{ margin: 0; color: #5b6670; }}
    .nav, .actions {{
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .nav a, .card, .link {{
      color: inherit;
      text-decoration: none;
    }}
    .nav a {{
      color: #0f766e;
      font-size: 14px;
      font-weight: 650;
    }}
    .logout {{
      border: 1px solid #c8d0d7;
      border-radius: 6px;
      background: #ffffff;
      color: #172026;
      padding: 8px 11px;
      font: inherit;
      cursor: pointer;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
    }}
    .card {{
      min-height: 120px;
      display: block;
      background: #ffffff;
      border: 1px solid #d9dee3;
      border-radius: 8px;
      padding: 16px;
    }}
    .card strong {{
      display: block;
      margin-bottom: 8px;
      font-size: 17px;
    }}
    .card span {{
      display: block;
      color: #5b6670;
      line-height: 1.45;
      font-size: 14px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: 18px;
    }}
    .metric {{
      background: #ffffff;
      border: 1px solid #d9dee3;
      border-radius: 8px;
      padding: 14px;
    }}
    .metric .value {{
      font-size: 24px;
      font-weight: 750;
      color: #172026;
    }}
    .metric .label {{
      margin-top: 4px;
      color: #5b6670;
      font-size: 13px;
    }}
    .panels {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin-top: 18px;
    }}
    .panel {{
      background: #ffffff;
      border: 1px solid #d9dee3;
      border-radius: 8px;
      padding: 16px;
    }}
    ul {{ list-style: none; padding: 0; margin: 0; }}
    li {{
      padding: 8px 0;
      border-bottom: 1px solid #edf0f2;
      color: #2a333a;
      font-size: 14px;
    }}
    li:last-child {{ border-bottom: 0; }}
    .muted {{ color: #6c767f; }}
    @media (max-width: 960px) {{
      .cards, .metrics, .panels {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 640px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      .cards, .metrics, .panels {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Pangi Admin</h1>
      <p>운영 상태를 보고 필요한 관리 화면으로 이동합니다.</p>
    </div>
    <div class="nav">
      <a href="{ADMIN_DB_PATH}">DB</a>
      <a href="{ADMIN_SCHEDULES_PATH}">스케줄</a>
      <a href="{ADMIN_MCP_PATH}">MCP</a>
      <a href="{ADMIN_NOTION_PATH}">Notion</a>
      <form method="post" action="{ADMIN_LOGOUT_PATH}">
        <button class="logout" type="submit">로그아웃</button>
      </form>
    </div>
  </header>
  <main>
    <div class="cards">
      <a class="card" href="{ADMIN_DB_PATH}">
        <strong>DB 기록</strong>
        <span>Slack thread, job, Codex run 기록을 확인합니다.</span>
      </a>
      <a class="card" href="{ADMIN_SCHEDULES_PATH}">
        <strong>스케줄</strong>
        <span>반복 작업을 만들고 예약 실행 이력을 봅니다.</span>
      </a>
      <a class="card" href="{ADMIN_MCP_PATH}">
        <strong>MCP 상태</strong>
        <span>Notion/Git MCP 설정과 read-only context 상태를 봅니다.</span>
      </a>
      <a class="card" href="{ADMIN_NOTION_PATH}">
        <strong>Notion 연결</strong>
        <span>Notion OAuth 연결을 관리합니다.</span>
      </a>
    </div>
    <div class="metrics">
      {_metric(len(jobs), "최근 job")}
      {_metric(active_tasks, "활성 스케줄")}
      {_metric(failed_runs, "최근 실패 예약")}
      {_metric("켜짐" if settings.scheduler_enabled else "꺼짐", "Scheduler")}
    </div>
    <div class="panels">
      <section class="panel">
        <h3>최근 job</h3>
        {_recent_jobs_list(jobs)}
      </section>
      <section class="panel">
        <h3>최근 스케줄</h3>
        {_recent_schedules_list(tasks)}
      </section>
    </div>
  </main>
</body>
</html>"""


def _render_mcp_page(*, notion_connected: bool) -> str:
    settings = get_settings()
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pangi MCP</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f6f7f8;
      color: #172026;
    }}
    body {{ margin: 0; }}
    header {{
      padding: 24px 28px 16px;
      background: #ffffff;
      border-bottom: 1px solid #d9dee3;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    main {{ padding: 20px 28px 36px; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 10px; font-size: 18px; letter-spacing: 0; }}
    p {{ margin: 0; color: #5b6670; }}
    .nav {{
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .nav a {{
      color: #0f766e;
      text-decoration: none;
      font-size: 14px;
      font-weight: 650;
    }}
    .logout {{
      border: 1px solid #c8d0d7;
      border-radius: 6px;
      background: #ffffff;
      color: #172026;
      padding: 8px 11px;
      font: inherit;
      cursor: pointer;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    .panel {{
      background: #ffffff;
      border: 1px solid #d9dee3;
      border-radius: 8px;
      padding: 16px;
    }}
    dl {{
      display: grid;
      grid-template-columns: 180px 1fr;
      gap: 10px 14px;
      margin: 0;
      font-size: 14px;
    }}
    dt {{ color: #5b6670; }}
    dd {{ margin: 0; color: #172026; font-weight: 650; overflow-wrap: anywhere; }}
    .table-wrap {{
      overflow-x: auto;
      background: #ffffff;
      border: 1px solid #d9dee3;
      border-radius: 8px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
      font-size: 13px;
    }}
    th, td {{
      padding: 9px 10px;
      border-bottom: 1px solid #edf0f2;
      text-align: left;
      vertical-align: top;
    }}
    th {{ background: #f0f3f5; color: #2a333a; font-weight: 650; }}
    tr:last-child td {{ border-bottom: 0; }}
    .status {{
      display: inline-block;
      padding: 2px 7px;
      border-radius: 999px;
      background: #e8eef5;
      font-size: 12px;
    }}
    @media (max-width: 760px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      .grid {{ grid-template-columns: 1fr; }}
      dl {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Pangi MCP</h1>
      <p>Context provider 설정을 확인합니다. token 값은 표시하지 않습니다.</p>
    </div>
    <div class="nav">
      <a href="{ADMIN_HOME_PATH}">홈</a>
      <a href="{ADMIN_DB_PATH}">DB</a>
      <a href="{ADMIN_SCHEDULES_PATH}">스케줄</a>
      <a href="{ADMIN_NOTION_PATH}">Notion</a>
      <form method="post" action="{ADMIN_LOGOUT_PATH}">
        <button class="logout" type="submit">로그아웃</button>
      </form>
    </div>
  </header>
  <main>
    <div class="grid">
      <section class="panel">
        <h2>Notion MCP</h2>
        <dl>
          <dt>기능</dt><dd>{_status(settings.notion_enabled)}</dd>
          <dt>OAuth 연결</dt><dd>{_status(notion_connected)}</dd>
          <dt>MCP URL</dt><dd>{_cell(settings.notion_mcp_url)}</dd>
          <dt>허용 page</dt><dd>{len(settings.notion_allowed_page_ids)}개</dd>
          <dt>허용 database</dt><dd>{len(settings.notion_allowed_database_ids)}개</dd>
          <dt>write 요청</dt><dd>{_status(settings.notion_write_enabled)} / MVP에서는 차단</dd>
        </dl>
      </section>
      <section class="panel">
        <h2>Git MCP</h2>
        <dl>
          <dt>기능</dt><dd>{_status(settings.git_mcp_enabled)}</dd>
          <dt>token</dt><dd>{_status(bool(settings.git_mcp_token))}</dd>
          <dt>조직</dt><dd>{_cell(settings.git_mcp_org)}</dd>
          <dt>context 최대 길이</dt><dd>{settings.git_mcp_context_max_chars}</dd>
          <dt>timeout</dt><dd>{settings.git_mcp_timeout_seconds}초</dd>
          <dt>write 요청</dt><dd>{_status(settings.git_mcp_write_enabled)} / MVP에서는 차단</dd>
        </dl>
      </section>
    </div>
    <h2>MCP Endpoint 목록</h2>
    {_mcp_endpoint_table()}
  </main>
</body>
</html>"""


def _render_page(
    *,
    threads: list[SlackThread],
    jobs: list[AgentJob],
    codex_runs: list[CodexRun],
) -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pangi DB</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f6f7f8;
      color: #172026;
    }}
    body {{ margin: 0; }}
    header {{
      padding: 24px 28px 16px;
      background: #ffffff;
      border-bottom: 1px solid #d9dee3;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    main {{ padding: 20px 28px 36px; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 10px; font-size: 18px; letter-spacing: 0; }}
    p {{ margin: 0; color: #5b6670; }}
    .table-wrap {{
      overflow-x: auto;
      background: #ffffff;
      border: 1px solid #d9dee3;
      border-radius: 8px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 960px;
      font-size: 13px;
    }}
    th, td {{
      padding: 9px 10px;
      border-bottom: 1px solid #edf0f2;
      vertical-align: top;
      text-align: left;
      white-space: nowrap;
    }}
    th {{
      background: #f0f3f5;
      color: #2a333a;
      font-weight: 650;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    .text {{ white-space: normal; min-width: 260px; max-width: 520px; }}
    .status {{
      display: inline-block;
      padding: 2px 7px;
      border-radius: 999px;
      background: #e8eef5;
      font-size: 12px;
    }}
    .empty {{ padding: 16px; color: #6c767f; }}
    .logout {{
      border: 1px solid #c8d0d7;
      border-radius: 6px;
      background: #ffffff;
      color: #172026;
      padding: 8px 11px;
      font: inherit;
      cursor: pointer;
    }}
    .nav {{
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: flex-end;
    }}
    .nav a {{
      color: #0f766e;
      text-decoration: none;
      font-size: 14px;
      font-weight: 650;
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Pangi DB</h1>
      <p>최근 SQLite job 기록을 확인합니다. 최대 50개씩 표시합니다.</p>
    </div>
    <div class="nav">
      <a href="{ADMIN_HOME_PATH}">홈</a>
      <a href="{ADMIN_SCHEDULES_PATH}">스케줄</a>
      <a href="{ADMIN_MCP_PATH}">MCP</a>
      <a href="{ADMIN_NOTION_PATH}">Notion 연결</a>
      <form method="post" action="{ADMIN_LOGOUT_PATH}">
        <button class="logout" type="submit">로그아웃</button>
      </form>
    </div>
  </header>
  <main>
    <h2>agent_jobs</h2>
    {_jobs_table(jobs)}
    <h2>slack_threads</h2>
    {_threads_table(threads)}
    <h2>codex_runs</h2>
    {_codex_runs_table(codex_runs)}
  </main>
</body>
</html>"""


def _render_notion_page(*, connection_status: str = "disconnected", error: str | None = None) -> str:
    settings = get_settings()
    enabled_text = "켜짐" if settings.notion_enabled else "꺼짐"
    connected_text = "연결됨" if connection_status == "connected" else "연결 안 됨"
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    page_count = len(settings.notion_allowed_page_ids)
    database_count = len(settings.notion_allowed_database_ids)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pangi Notion</title>
  <style>
    :root {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f6f7f8;
      color: #172026;
    }}
    body {{ margin: 0; }}
    header {{
      padding: 24px 28px 16px;
      background: #ffffff;
      border-bottom: 1px solid #d9dee3;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    main {{ padding: 20px 28px 36px; max-width: 760px; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; letter-spacing: 0; }}
    h2 {{ margin: 24px 0 10px; font-size: 18px; letter-spacing: 0; }}
    p {{ color: #5b6670; }}
    .panel {{
      background: #ffffff;
      border: 1px solid #d9dee3;
      border-radius: 8px;
      padding: 18px;
    }}
    dl {{
      display: grid;
      grid-template-columns: 180px 1fr;
      gap: 10px 14px;
      margin: 0;
      font-size: 14px;
    }}
    dt {{ color: #5b6670; }}
    dd {{ margin: 0; color: #172026; font-weight: 650; }}
    button, .link {{
      display: inline-block;
      border: 0;
      border-radius: 6px;
      background: #0f766e;
      color: #ffffff;
      padding: 9px 12px;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
      text-decoration: none;
    }}
    .secondary {{ background: #ffffff; color: #172026; border: 1px solid #c8d0d7; }}
    .danger {{ background: #b42318; }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }}
    .error {{
      margin: 0 0 12px;
      padding: 9px 10px;
      border-radius: 6px;
      background: #fff1f0;
      color: #b42318;
      font-size: 13px;
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Pangi Notion</h1>
      <p>Notion MCP OAuth 연결 상태를 관리합니다. 토큰 값은 화면에 표시하지 않습니다.</p>
    </div>
    <div class="actions">
      <a class="link secondary" href="{ADMIN_HOME_PATH}">홈</a>
      <a class="link secondary" href="{ADMIN_DB_PATH}">DB 보기</a>
      <a class="link secondary" href="{ADMIN_MCP_PATH}">MCP</a>
      <a class="link secondary" href="{ADMIN_SCHEDULES_PATH}">스케줄</a>
    </div>
  </header>
  <main>
    {error_html}
    <section class="panel">
      <dl>
        <dt>Notion 기능</dt><dd>{enabled_text}</dd>
        <dt>OAuth 연결</dt><dd>{connected_text}</dd>
        <dt>MCP URL</dt><dd>{escape(settings.notion_mcp_url)}</dd>
        <dt>허용 page</dt><dd>{page_count}개</dd>
        <dt>허용 database</dt><dd>{database_count}개</dd>
      </dl>
      <div class="actions">
        <form method="post" action="{ADMIN_NOTION_CONNECT_PATH}">
          <button type="submit">Notion 연결</button>
        </form>
        <form method="post" action="{ADMIN_NOTION_DISCONNECT_PATH}">
          <button class="danger" type="submit">연결 해제</button>
        </form>
      </div>
    </section>
  </main>
</body>
</html>"""


def _render_schedules_page(
    *,
    tasks: list[ScheduledTask],
    runs: list[ScheduledTaskRun],
    error: str | None = None,
) -> str:
    settings = get_settings()
    enabled_text = "켜짐" if settings.scheduler_enabled else "꺼짐"
    default_user = _single_allowed_value(settings.slack_allowed_user_ids)
    default_channel = _single_allowed_value(settings.slack_allowed_channel_ids)
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pangi Schedules</title>
  <style>
    :root {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f6f7f8;
      color: #172026;
    }}
    body {{ margin: 0; }}
    header {{
      padding: 24px 28px 16px;
      background: #ffffff;
      border-bottom: 1px solid #d9dee3;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    main {{ padding: 20px 28px 36px; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 10px; font-size: 18px; letter-spacing: 0; }}
    p {{ margin: 0; color: #5b6670; }}
    form.create {{
      max-width: 920px;
      background: #ffffff;
      border: 1px solid #d9dee3;
      border-radius: 8px;
      padding: 18px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px 14px;
    }}
    label {{ display: block; margin: 0 0 6px; color: #43505a; font-size: 13px; }}
    input, select, textarea {{
      width: 100%;
      box-sizing: border-box;
      padding: 9px 10px;
      border: 1px solid #c8d0d7;
      border-radius: 6px;
      font: inherit;
      background: #ffffff;
    }}
    textarea {{ min-height: 120px; resize: vertical; }}
    .wide {{ grid-column: 1 / -1; }}
    .days {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      padding: 9px 0 0;
    }}
    .days label {{ display: inline-flex; gap: 5px; align-items: center; margin: 0; }}
    .days input {{ width: auto; }}
    button, .link {{
      display: inline-block;
      border: 0;
      border-radius: 6px;
      background: #0f766e;
      color: #ffffff;
      padding: 9px 12px;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
      text-decoration: none;
    }}
    .secondary {{ background: #ffffff; color: #172026; border: 1px solid #c8d0d7; }}
    .danger {{ background: #b42318; }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
    .table-wrap {{
      overflow-x: auto;
      background: #ffffff;
      border: 1px solid #d9dee3;
      border-radius: 8px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 960px;
      font-size: 13px;
    }}
    th, td {{
      padding: 9px 10px;
      border-bottom: 1px solid #edf0f2;
      vertical-align: top;
      text-align: left;
      white-space: nowrap;
    }}
    th {{ background: #f0f3f5; color: #2a333a; font-weight: 650; }}
    tr:last-child td {{ border-bottom: 0; }}
    .text {{ white-space: normal; min-width: 260px; max-width: 520px; }}
    .status {{
      display: inline-block;
      padding: 2px 7px;
      border-radius: 999px;
      background: #e8eef5;
      font-size: 12px;
    }}
    .empty {{ padding: 16px; color: #6c767f; }}
    .error {{
      margin: 0 0 12px;
      padding: 9px 10px;
      border-radius: 6px;
      background: #fff1f0;
      color: #b42318;
      font-size: 13px;
    }}
    @media (max-width: 760px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Pangi Schedules</h1>
      <p>Scheduler 상태: {enabled_text}. 예약 작업은 기존 Slack 요청 처리 흐름을 그대로 사용합니다.</p>
    </div>
    <div class="actions">
      <a class="link secondary" href="{ADMIN_HOME_PATH}">홈</a>
      <a class="link secondary" href="{ADMIN_DB_PATH}">DB 보기</a>
      <a class="link secondary" href="{ADMIN_MCP_PATH}">MCP</a>
      <a class="link secondary" href="{ADMIN_NOTION_PATH}">Notion 연결</a>
      <form method="post" action="{ADMIN_LOGOUT_PATH}">
        <button class="secondary" type="submit">로그아웃</button>
      </form>
    </div>
  </header>
  <main>
    {error_html}
    <h2>새 스케줄</h2>
    <form class="create" method="post" action="{ADMIN_SCHEDULES_PATH}">
      <div class="grid">
        <div>
          <label for="name">이름</label>
          <input id="name" name="name" placeholder="전날 업무 요약">
        </div>
        <div>
          <label for="team_id">Slack team</label>
          <input id="team_id" name="team_id" placeholder="T..." required>
        </div>
        <div>
          <label for="channel_id">Slack channel</label>
          <input id="channel_id" name="channel_id" value="{escape(default_channel)}" placeholder="C..." required>
        </div>
        <div>
          <label for="requester_user_id">요청자 user</label>
          <input id="requester_user_id" name="requester_user_id" value="{escape(default_user)}" placeholder="U..." required>
        </div>
        <div>
          <label for="schedule_type">반복</label>
          <select id="schedule_type" name="schedule_type">
            <option value="daily">매일</option>
            <option value="weekly">매주</option>
            <option value="once">한 번</option>
          </select>
        </div>
        <div>
          <label for="timezone">Timezone</label>
          <input id="timezone" name="timezone" value="Asia/Seoul">
        </div>
        <div>
          <label for="run_date">실행 날짜</label>
          <input id="run_date" name="run_date" type="date" value="{date.today().isoformat()}">
        </div>
        <div>
          <label for="time_of_day">실행 시간</label>
          <input id="time_of_day" name="time_of_day" type="time" value="09:00" required>
        </div>
        <div>
          <label>요일</label>
          <div class="days">
            {_day_checkbox(0, "월")}
            {_day_checkbox(1, "화")}
            {_day_checkbox(2, "수")}
            {_day_checkbox(3, "목")}
            {_day_checkbox(4, "금")}
            {_day_checkbox(5, "토")}
            {_day_checkbox(6, "일")}
          </div>
        </div>
        <div class="wide">
          <label for="prompt">Prompt</label>
          <textarea id="prompt" name="prompt" required></textarea>
        </div>
      </div>
      <div class="actions" style="margin-top: 14px;">
        <button type="submit">스케줄 생성</button>
      </div>
    </form>
    <h2>scheduled_tasks</h2>
    {_scheduled_tasks_table(tasks)}
    <h2>scheduled_task_runs</h2>
    {_scheduled_runs_table(runs)}
  </main>
</body>
</html>"""


def _single_allowed_value(values: frozenset[str]) -> str:
    if "*" in values or len(values) != 1:
        return ""
    return next(iter(values))


def _day_checkbox(value: int, label: str) -> str:
    return f'<label><input type="checkbox" name="days_of_week" value="{value}">{label}</label>'


def _metric(value: object, label: str) -> str:
    return f"""<div class="metric">
<div class="value">{_cell(value)}</div>
<div class="label">{_cell(label)}</div>
</div>"""


def _recent_jobs_list(jobs: list[AgentJob]) -> str:
    if not jobs:
        return '<p class="muted">최근 job이 없습니다.</p>'
    items = "\n".join(
        f"<li>{_cell(job.status.value)} · {_cell(job.repo_key)} · {_cell(job.id)}</li>"
        for job in jobs[:5]
    )
    return f"<ul>{items}</ul>"


def _recent_schedules_list(tasks: list[ScheduledTask]) -> str:
    if not tasks:
        return '<p class="muted">등록된 스케줄이 없습니다.</p>'
    items = "\n".join(
        f"<li>{_cell('enabled' if task.enabled else 'disabled')} · {_cell(task.name)} · next {_cell(_format_dt(task.next_run_at))}</li>"
        for task in tasks[:5]
    )
    return f"<ul>{items}</ul>"


def _status(value: bool) -> str:
    return f'<span class="status">{"켜짐" if value else "꺼짐"}</span>'


def _mcp_endpoint_table() -> str:
    settings = get_settings()
    rows = (
        ("Notion", "mcp", settings.notion_mcp_url),
        ("Git", "default", settings.git_mcp_url),
        ("Git", "context", settings.git_mcp_context_url),
        ("Git", "orgs", settings.git_mcp_orgs_url),
        ("Git", "repos", settings.git_mcp_repos_url),
        ("Git", "issues", settings.git_mcp_issues_url),
        ("Git", "pull_requests", settings.git_mcp_pull_requests_url),
        ("Git", "actions", settings.git_mcp_actions_url),
    )
    body = "\n".join(
        "<tr>"
        f"<td>{_cell(provider)}</td>"
        f"<td>{_cell(purpose)}</td>"
        f"<td>{_cell(url)}</td>"
        "</tr>"
        for provider, purpose, url in rows
    )
    return f"""<div class="table-wrap"><table>
<thead><tr><th>provider</th><th>toolset</th><th>url</th></tr></thead>
<tbody>{body}</tbody>
</table></div>"""


def _scheduled_tasks_table(tasks: list[ScheduledTask]) -> str:
    if not tasks:
        return '<div class="table-wrap"><div class="empty">저장된 스케줄이 없습니다.</div></div>'
    rows = "\n".join(
        "<tr>"
        f"<td>{_cell(task.id)}</td>"
        f"<td><span class=\"status\">{_cell('enabled' if task.enabled else 'disabled')}</span></td>"
        f"<td>{_cell(task.name)}</td>"
        f"<td>{_cell(task.schedule_type.value)}</td>"
        f"<td>{_cell(task.channel_id)}</td>"
        f"<td>{_cell(task.requester_user_id)}</td>"
        f"<td>{_cell(_schedule_summary(task))}</td>"
        f"<td>{_cell(_format_dt(task.next_run_at))}</td>"
        f"<td>{_cell(_format_dt(task.last_run_at))}</td>"
        f"<td class=\"text\">{_cell(task.prompt)}</td>"
        f"<td>{_schedule_action(task)}</td>"
        "</tr>"
        for task in tasks
    )
    return f"""<div class="table-wrap"><table>
<thead><tr>
<th>id</th><th>status</th><th>name</th><th>type</th><th>channel</th><th>user</th>
<th>schedule</th><th>next</th><th>last</th><th>prompt</th><th>action</th>
</tr></thead>
<tbody>{rows}</tbody>
</table></div>"""


def _scheduled_runs_table(runs: list[ScheduledTaskRun]) -> str:
    if not runs:
        return '<div class="table-wrap"><div class="empty">저장된 스케줄 실행 기록이 없습니다.</div></div>'
    rows = "\n".join(
        "<tr>"
        f"<td>{_cell(run.id)}</td>"
        f"<td>{_cell(run.scheduled_task_id)}</td>"
        f"<td><span class=\"status\">{_cell(run.status.value)}</span></td>"
        f"<td>{_cell(_format_dt(run.scheduled_for))}</td>"
        f"<td>{_cell(run.classification)}</td>"
        f"<td>{_cell(run.job_id)}</td>"
        f"<td>{_cell(run.slack_thread_ts)}</td>"
        f"<td class=\"text\">{_cell(run.error_message)}</td>"
        f"<td>{_cell(_format_dt(run.updated_at))}</td>"
        "</tr>"
        for run in runs
    )
    return f"""<div class="table-wrap"><table>
<thead><tr>
<th>id</th><th>task_id</th><th>status</th><th>scheduled_for</th><th>classification</th>
<th>job_id</th><th>thread_ts</th><th>error</th><th>updated</th>
</tr></thead>
<tbody>{rows}</tbody>
</table></div>"""


def _schedule_action(task: ScheduledTask) -> str:
    if task.enabled:
        return (
            f'<form method="post" action="{ADMIN_SCHEDULES_PATH}/{escape(task.id)}/disable">'
            '<button class="danger" type="submit">중지</button></form>'
        )
    if task.next_run_at is None:
        return ""
    return (
        f'<form method="post" action="{ADMIN_SCHEDULES_PATH}/{escape(task.id)}/enable">'
        '<button type="submit">재개</button></form>'
    )


def _schedule_summary(task: ScheduledTask) -> str:
    if task.schedule_type == ScheduleType.ONCE:
        return f"once at {_format_dt(task.run_at)} ({task.timezone})"
    if task.schedule_type == ScheduleType.DAILY:
        return f"daily {task.time_of_day} ({task.timezone})"
    return f"weekly {_days_label(task.days_of_week)} {task.time_of_day} ({task.timezone})"


def _days_label(days_of_week: str | None) -> str:
    labels = ("월", "화", "수", "목", "금", "토", "일")
    if not days_of_week:
        return ""
    values: list[str] = []
    for raw_value in days_of_week.split(","):
        try:
            values.append(labels[int(raw_value)])
        except (ValueError, IndexError):
            values.append(raw_value)
    return ",".join(values)


def _jobs_table(jobs: list[AgentJob]) -> str:
    if not jobs:
        return '<div class="table-wrap"><div class="empty">저장된 job이 없습니다.</div></div>'
    rows = "\n".join(
        "<tr>"
        f"<td>{_cell(job.id)}</td>"
        f"<td><span class=\"status\">{_cell(job.status.value)}</span></td>"
        f"<td>{_cell(job.job_type.value)}</td>"
        f"<td>{_cell(job.repo_key)}</td>"
        f"<td>{_cell(job.requester_user_id)}</td>"
        f"<td>{_cell(job.slack_channel_id)}</td>"
        f"<td>{_cell(job.slack_thread_ts)}</td>"
        f"<td>{_cell(job.slack_message_ts)}</td>"
        f"<td class=\"text\">{_cell(job.prompt)}</td>"
        f"<td class=\"text\">{_cell(job.error_message)}</td>"
        f"<td>{_cell(job.worktree_path)}</td>"
        f"<td>{_cell(_format_dt(job.updated_at))}</td>"
        "</tr>"
        for job in jobs
    )
    return f"""<div class="table-wrap"><table>
<thead><tr>
<th>id</th><th>status</th><th>type</th><th>repo</th><th>user</th><th>channel</th>
<th>thread_ts</th><th>message_ts</th><th>prompt</th><th>error</th><th>worktree</th><th>updated</th>
</tr></thead>
<tbody>{rows}</tbody>
</table></div>"""


def _threads_table(threads: list[SlackThread]) -> str:
    if not threads:
        return '<div class="table-wrap"><div class="empty">저장된 Slack thread가 없습니다.</div></div>'
    rows = "\n".join(
        "<tr>"
        f"<td>{_cell(thread.id)}</td>"
        f"<td>{_cell(thread.team_id)}</td>"
        f"<td>{_cell(thread.channel_id)}</td>"
        f"<td>{_cell(thread.thread_ts)}</td>"
        f"<td>{_cell(thread.last_job_id)}</td>"
        f"<td>{_cell(_format_dt(thread.updated_at))}</td>"
        "</tr>"
        for thread in threads
    )
    return f"""<div class="table-wrap"><table>
<thead><tr><th>id</th><th>team</th><th>channel</th><th>thread_ts</th><th>last_job_id</th><th>updated</th></tr></thead>
<tbody>{rows}</tbody>
</table></div>"""


def _codex_runs_table(codex_runs: list[CodexRun]) -> str:
    if not codex_runs:
        return '<div class="table-wrap"><div class="empty">저장된 Codex run이 없습니다.</div></div>'
    rows = "\n".join(
        "<tr>"
        f"<td>{_cell(run.id)}</td>"
        f"<td>{_cell(run.job_id)}</td>"
        f"<td>{_cell(run.mode)}</td>"
        f"<td class=\"text\">{_cell(run.command)}</td>"
        f"<td>{_cell(run.exit_code)}</td>"
        f"<td>{_cell(run.timed_out)}</td>"
        f"<td class=\"text\">{_cell(run.stderr)}</td>"
        f"<td>{_cell(_format_dt(run.started_at))}</td>"
        f"<td>{_cell(_format_dt(run.finished_at))}</td>"
        "</tr>"
        for run in codex_runs
    )
    return f"""<div class="table-wrap"><table>
<thead><tr><th>id</th><th>job_id</th><th>mode</th><th>command</th><th>exit</th><th>timeout</th><th>stderr</th><th>started</th><th>finished</th></tr></thead>
<tbody>{rows}</tbody>
</table></div>"""


def _cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    if len(text) > 500:
        text = text[:497] + "..."
    return escape(text)


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat(timespec="seconds")
