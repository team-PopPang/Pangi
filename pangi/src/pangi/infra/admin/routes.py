from __future__ import annotations

import secrets
import time
from hashlib import sha256
import hmac
from datetime import datetime
from html import escape
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from pangi.config import get_settings
from pangi.domain.models import AgentJob, CodexRun, SlackThread
from pangi.infra.notion.oauth import NotionOAuthClient, NotionOAuthError
from pangi.infra.notion.token_store import JsonNotionTokenStore
from pangi.repository import get_job_repository


ADMIN_PATH_PREFIX = "/pangi-admin"
ADMIN_LOGIN_PATH = f"{ADMIN_PATH_PREFIX}/login"
ADMIN_DB_PATH = f"{ADMIN_PATH_PREFIX}/db"
ADMIN_LOGOUT_PATH = f"{ADMIN_PATH_PREFIX}/logout"
ADMIN_NOTION_PATH = f"{ADMIN_PATH_PREFIX}/notion"
ADMIN_NOTION_CONNECT_PATH = f"{ADMIN_PATH_PREFIX}/notion/connect"
ADMIN_NOTION_CALLBACK_PATH = f"{ADMIN_PATH_PREFIX}/notion/callback"
ADMIN_NOTION_DISCONNECT_PATH = f"{ADMIN_PATH_PREFIX}/notion/disconnect"

router = APIRouter(prefix=ADMIN_PATH_PREFIX, tags=["admin"])
ADMIN_USERNAME = "pangi"
ADMIN_COOKIE_NAME = "pangi_admin_session"
ADMIN_SESSION_TTL_SECONDS = 60 * 60 * 12


@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request) -> Response:
    _require_admin_enabled()
    if _has_valid_session(request):
        return RedirectResponse(ADMIN_DB_PATH, status_code=status.HTTP_303_SEE_OTHER)
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

    response = RedirectResponse(ADMIN_DB_PATH, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        _new_session_cookie(settings.admin_password or ""),
        max_age=ADMIN_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return response


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
    <a class="link secondary" href="{ADMIN_DB_PATH}">DB 보기</a>
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
