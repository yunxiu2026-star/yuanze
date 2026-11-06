"""Web dashboard (FastAPI, server-rendered)."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import Config
from ..db import DB
from ..engines import LABELS
from ..questions import load_questions
from . import queries as Q
from .auth import COOKIE, MAX_AGE, Auth
from .password_store import CredentialStoreError
from .charts import trend_svg

HERE = Path(__file__).parent
MAX_UPLOAD = 5 * 1024 * 1024
REPORT_RE = re.compile(r"^[\w\-一-鿿]+\.xlsx$")


class LoginRequired(Exception):
    pass


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()
    from .release import allowed, phase1_enabled, validate_release, growth_enabled
    phase1 = phase1_enabled()
    growth = growth_enabled()
    validate_release(cfg)
    database_parent=Path(cfg.database_url[10:]).parent if cfg.database_url.startswith('sqlite:///') else Path('data')
    auth = Auth(cfg.web_password, cfg.web_secret,credential_dir=os.environ.get('AUTH_DATA_DIR',str(database_parent/'auth')))
    auth.password_state()
    tz = ZoneInfo(cfg.timezone)
    boot = DB(cfg.database_url)
    boot.init()
    boot.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    if phase1 and os.environ.get('WEB_DEPLOY_MODE') == 'production':
        from starlette.middleware.trustedhost import TrustedHostMiddleware
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=[__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(os.environ.get('WEB_PUBLIC_ORIGIN','')).hostname,'127.0.0.1','localhost'])
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")

    # ---- template helpers ---------------------------------------------------
    def pct(v, digits: int = 0) -> str:
        return "—" if v is None else f"{v * 100:.{digits}f}%"

    def delta(v) -> str:
        if v is None:
            return ""
        pts = v * 100
        if abs(pts) < 0.5:
            return "持平"
        return f"{'+' if pts > 0 else '−'}{abs(pts):.0f} 个百分点"

    def local(v, fmt: str = "%Y-%m-%d %H:%M") -> str:
        if not v:
            return ""
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(tz).strftime(fmt)

    tpl.env.filters.update(pct=pct, delta=delta, local=local, engine=lambda e: LABELS.get(e, e))
    tpl.env.globals.update(site=cfg.target_domain, site_name=cfg.site_name, dashboard_origin=os.environ.get("WEB_PUBLIC_ORIGIN", "http://127.0.0.1:9322"))

    # ---- dependencies -------------------------------------------------------
    def get_db():
        db = DB(cfg.database_url)
        try:
            yield db
        finally:
            db.close()

    def require_login(request: Request) -> str:
        sid = auth.session_id(request.cookies.get(COOKIE))
        if not sid:
            raise LoginRequired()
        return sid

    def client_ip(request: Request) -> str:
        return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")

    def render(request: Request, name: str, sid: str | None, **ctx) -> HTMLResponse:
        nav = {'phase1_overview':'overview','phase1_events':'inquiries'}.get(name.split('.')[0],name.split('.')[0])
        workspace_nav = {'overview':'overview','phase1_overview':'overview','phase1_events':'inquiries',
            'inquiries':'inquiries','leads':'leads','lead_detail':'leads','lead_new':'leads','lead_import':'leads','indexing':'indexing','indexing_detail':'indexing',
            'social':'social','meta_dashboard':'meta','content_optimization':'optimize',
            'google_imports':'imports','google_settings':'settings','ai_monitor':'ai','result_detail':'ai',
            'knowledge':'knowledge','knowledge_edit':'knowledge','knowledge_detail':'knowledge','knowledge_categories':'knowledge','knowledge_revision':'knowledge',
            'settings':'account_settings','password_settings':'account_settings'}.get(name.split('.')[0],'')
        if name == 'google_dashboard.html':
            workspace_nav = 'search' if ctx.get('kind') == 'search' else 'analytics'
        ctx.update(request=request, csrf=auth.csrf_token(sid) if sid else "", nav=nav,
                   workspace_nav=workspace_nav,
                   preview=os.environ.get('WEB_PREVIEW') == '1', phase1=phase1, growth=growth,
                   rehearsal=phase1 and os.environ.get('WEB_DEPLOY_MODE', 'rehearsal') == 'rehearsal')
        return tpl.TemplateResponse(request, name, ctx)

    def check_csrf(sid: str, token: str | None) -> None:
        if not auth.check_csrf(sid, token):
            raise HTTPException(403, "页面已过期，请刷新后重试")

    @app.exception_handler(LoginRequired)
    async def _login_redirect(request: Request, exc: LoginRequired):
        nxt = str(request.url.path) + ('?' + request.url.query if request.url.query else '') if request.method == 'GET' else '/'
        return RedirectResponse('/login?next=' + _q(nxt), status_code=303)

    @app.exception_handler(CredentialStoreError)
    async def _credential_error(request:Request,exc:CredentialStoreError):
        return PlainTextResponse(str(exc),status_code=503)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if phase1 and not allowed(request.method, request.url.path):
            resp = PlainTextResponse('首版暂未开放此功能', status_code=404)
        else:
            resp = await call_next(request)
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith('/social/meta'):
            resp.headers['Referrer-Policy'] = 'no-referrer'
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "form-action 'self' https://accounts.google.com https://www.facebook.com; frame-ancestors 'none'; base-uri 'none'")
        if request.url.path != "/static" and not request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---- auth ---------------------------------------------------------------
    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz():
        return "ok"

    @app.get('/readyz', response_class=PlainTextResponse)
    def readyz():
        try:
            db = DB(cfg.database_url)
            try:
                db.query('SELECT COUNT(*) AS n FROM runs')
            finally:
                db.close()
            return 'ready'
        except Exception:
            return PlainTextResponse('not ready', status_code=503)

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        return "User-agent: *\nDisallow: /\n"

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/",password_changed:bool=False):
        return render(request, "login.html", None, next=next if next.startswith("/") else "/", error="",password_changed=password_changed)

    @app.post("/login")
    def login(request: Request, password: str = Form(""), next: str = Form("/")):
        ip = client_ip(request)
        wait = auth.locked(ip)
        if wait:
            return render(request, "login.html", None, next=next,
                          error=f"尝试次数过多，请 {wait // 60 + 1} 分钟后再试")
        login_cookie=auth.login_session(ip,password)
        if not login_cookie:
            return render(request, "login.html", None, next=next, error="密码不正确")
        target = next if next.startswith("/") and not next.startswith("//") else "/"
        resp = RedirectResponse(target, status_code=303)
        resp.set_cookie(COOKIE, login_cookie, max_age=MAX_AGE, httponly=True,
                        secure=cfg.web_cookie_secure, samesite="lax")
        return resp

    @app.post("/logout")
    def logout(sid: str = Depends(require_login), csrf: str = Form("")):
        check_csrf(sid, csrf)
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    # ---- pages --------------------------------------------------------------
    from .password_routes import register_password_routes
    register_password_routes(app,cfg,auth,require_login,render,check_csrf,client_ip)

    def run_context(db: DB, run_id: int | None):
        run = Q.get_run(db, run_id)
        if run_id and not run:
            raise HTTPException(404, "找不到这次检测")
        rows = Q.run_rows(db, run["id"]) if run else []
        prev, prev_rows = Q.previous_rows(db, run, rows) if run else (None, [])
        return run, rows, prev, prev_rows

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request, run: int | None = None, sid: str = Depends(require_login),
                 db: DB = Depends(get_db)):
        # Keep old run links working while giving the home page a business-wide scope.
        if run is not None and not phase1:
            return ai_overview(request, run, sid, db)
        from .workspace import home_summary
        from .content_optimization import DraftStore
        settings = google.settings()
        analytics = imports.get('analytics', resource=settings['property']) if settings.get('property') else None
        search = imports.get('search', resource=settings['site']) if settings.get('site') else None
        try:
            meta_status = meta_data.status()
        except MetaError:
            meta_status = {}
        from .inquiries import inquiry_summary
        from .indexing import indexing_summary
        today = datetime.now(tz).date() - timedelta(days=1 if phase1 else 0)
        local_inquiries = inquiry_summary(operations_store, settings.get('property',''),
                                          (today-timedelta(days=27)).isoformat(),today.isoformat(),cfg.timezone)
        local_indexing = indexing_summary(operations_store)
        if phase1:
            if local_inquiries['ga_selected']:
                batch = local_inquiries['ga_selected']
                local_inquiries = inquiry_summary(operations_store, settings.get('property',''),
                                                  batch['start'], batch['end'], cfg.timezone)
            if growth:
                summary = home_summary(analytics,search,DraftStore().list(),meta_status,google.connected(),
                    None,{},False,
                    dict(receipts_available=False,indexed=local_indexing['indexed'],
                         core_count=local_indexing['core_count'],
                         index_follow_up=local_indexing['follow_up']+local_indexing['unknown']),
                    growth=dict(ai=ai_service.summary(),inquiry=local_inquiries))
                return render(request,'overview.html',sid,home=summary,leads=lead_store.summary(datetime.now(tz).date().isoformat()),website_sync=app.state.website_sync.status(),knowledge=app.state.knowledge.summary())
            return render(request, 'phase1_overview.html', sid, inquiry=local_inquiries,
                          indexing=local_indexing, connected=google.connected(), settings=settings,
                          analytics=analytics, nav='overview',
                          ai=ai_service.summary() if growth else None,
                          meta=meta_status if growth else None,
                          draft_count=len(DraftStore().list()) if growth else 0)
        ops = dict(receipts_available=local_inquiries['available'],accepted=local_inquiries['accepted'],
                   start=local_inquiries['start'],end=local_inquiries['end'],
                   indexed=local_indexing['indexed'],core_count=local_indexing['core_count'],
                   index_follow_up=local_indexing['follow_up']+local_indexing['unknown'])
        summary = home_summary(analytics, search, DraftStore().list(), meta_status, google.connected(),
                               Q.get_run(db, None), Q.traffic(db, datetime.now(tz).date(), 7),
                               os.environ.get('WEB_PREVIEW') == '1',ops)
        return render(request, 'overview.html', sid, home=summary)

    @app.get('/ai-overview', response_class=HTMLResponse)
    def ai_overview(request: Request, run: int | None = None, sid: str = Depends(require_login),
                    db: DB = Depends(get_db)):
        r, rows, prev, prev_rows = run_context(db, run)
        tr = Q.trend(db)
        gained, lost = Q.changes(rows, prev_rows)
        return render(
            request, "ai_overview.html", sid, run=r, prev=prev, runs=Q.done_runs(db, 30),
            total=Q.total_summary(rows, prev_rows),
            engines=Q.group_summary(rows, prev_rows, "engine"),
            languages=Q.group_summary(rows, prev_rows, "language"),
            trend=tr, trend_svg=trend_svg(tr), gained=gained[:8], lost=lost[:8],
            gap_count=len(Q.gaps(rows)), top_comp=Q.competitors(rows, 5),
            traffic=Q.traffic(db, datetime.now(tz).date(), 7),
            pending=db.query("SELECT COUNT(*) AS n FROM run_requests WHERE status IN ('pending','running')")[0]["n"],
        )

    @app.get("/results", response_class=HTMLResponse)
    def results(request: Request, run: int | None = None, engine: str = "", lang: str = "",
                priority: str = "", status: str = "", q: str = "", page: int = 1,
                sid: str = Depends(require_login), db: DB = Depends(get_db)):
        r, rows, _, _ = run_context(db, run)
        shown = Q.filter_rows(rows, engine, lang, priority, status, q)
        pages = max(1, (len(shown) + 24) // 25)
        page = max(1, min(page, pages))
        from urllib.parse import urlencode
        filters = dict(run=r['id'] if r else '', engine=engine, lang=lang, priority=priority, status=status, q=q)
        base = urlencode({k:v for k,v in filters.items() if v != ''})
        return render(request, "results.html", sid, run=r, runs=Q.done_runs(db, 30), rows=shown[(page-1)*25:page*25],
                      matched=len(shown), page=page, pages=pages, page_base=base,
                      total=len(rows), engines=Q.sort_engines(x["engine"] for x in rows),
                      languages=list(dict.fromkeys(x.get("language") for x in rows if x.get("language"))),
                      f={"engine": engine, "lang": lang, "priority": priority, "status": status, "q": q})

    @app.get('/results/export.csv')
    def export_results(run: int | None = None, engine: str = '', lang: str = '', priority: str = '',
                       status: str = '', q: str = '', sid: str = Depends(require_login), db: DB = Depends(get_db)):
        import csv
        import io
        _, rows, _, _ = run_context(db, run)
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['问题ID', '引擎', '语言', '优先级', '问题', '是否提及', '引用排名', '错误'])
        def cell(v):
            s = str(v if v is not None else '')
            return "'" + s if s.lstrip().startswith(('=', '+', '-', '@')) or s.startswith(('\t','\r','\n')) else s
        for row in Q.filter_rows(rows, engine, lang, priority, status, q):
            writer.writerow([cell(row.get(k)) for k in ['question_id','engine','language','priority','prompt','mentioned','rank','error']])
        return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="geo-results.csv"'})

    @app.get("/results/{rid}", response_class=HTMLResponse)
    def result_detail(request: Request, rid: int, sid: str = Depends(require_login), db: DB = Depends(get_db)):
        from ..db import decode_result
        rows = db.query("SELECT * FROM results WHERE id = %s", (rid,))
        if not rows:
            raise HTTPException(404, "找不到这条记录")
        row = decode_result(rows[0])
        if growth and not any(j['run_id']==row['run_id'] and j['status'] in {'done','partial'} for j in ai_service.history(None)):
            raise HTTPException(404,'旧检测来源尚未核实，暂不作为真实 API 依据；原始记录仍保留。')
        return render(request, "result_detail.html", sid, r=row)

    @app.get("/gaps", response_class=HTMLResponse)
    def gaps(request: Request, run: int | None = None, sid: str = Depends(require_login),
             db: DB = Depends(get_db)):
        r, rows, _, _ = run_context(db, run)
        from .workspace import site_page
        items = Q.gaps(rows)
        for item in items:
            item['page_link'] = site_page(item.get('expected_url'), cfg.target_domain)
            item['optimization_result'] = next((x['id'] for x in rows if x['question_id'] == item['question_id'] and not x.get('error')), None)
        return render(request, "gaps.html", sid, run=r, runs=Q.done_runs(db, 30), items=items)

    @app.get("/competitors", response_class=HTMLResponse)
    def competitors(request: Request, run: int | None = None, sid: str = Depends(require_login),
                    db: DB = Depends(get_db)):
        r, rows, _, _ = run_context(db, run)
        return render(request, "competitors.html", sid, run=r, runs=Q.done_runs(db, 30),
                      items=Q.competitors(rows, 60), ignore=cfg.ignore_domains)

    @app.get("/traffic", response_class=HTMLResponse)
    def traffic(request: Request, days: int = 7, sid: str = Depends(require_login), db: DB = Depends(get_db)):
        days = days if days in (7, 30, 90) else 7
        return render(request, "traffic.html", sid, t=Q.traffic(db, datetime.now(tz).date(), days))

    @app.get("/questions", response_class=HTMLResponse)
    def questions(request: Request, msg: str = "", sid: str = Depends(require_login), db: DB = Depends(get_db)):
        try:
            qs = load_questions(cfg.questions_file, None, only_enabled=False)
            err = ""
        except (OSError, ValueError) as exc:
            qs, err = [], str(exc)
        latest = Q.latest_by_question(db)
        engines = Q.sort_engines(e for v in latest.values() for e in v) or Q.sort_engines(cfg.engines)
        counts = {p: sum(1 for x in qs if x.enabled and x.priority == p) for p in ("P1", "P2", "P3")}
        return render(request, "questions.html", sid, qs=qs, latest=latest, engines=engines, err=err,
                      msg=msg, counts=counts, active=cfg.priorities)

    @app.get("/questions/download")
    def questions_download(sid: str = Depends(require_login)):
        if not os.path.isfile(cfg.questions_file):
            raise HTTPException(404, "问题清单不存在")
        return FileResponse(cfg.questions_file, filename="questions.xlsx")

    @app.post("/questions/upload")
    async def questions_upload(request: Request, file: UploadFile = File(...), csrf: str = Form(""),
                               sid: str = Depends(require_login)):
        check_csrf(sid, csrf)
        if not (file.filename or "").lower().endswith(".xlsx"):
            return RedirectResponse("/questions?msg=" + _q("请上传 .xlsx 文件"), status_code=303)
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            return RedirectResponse("/questions?msg=" + _q("文件超过 5MB"), status_code=303)
        target = Path(cfg.questions_file)
        fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            n = len(load_questions(tmp, None, only_enabled=False))
            if n == 0:
                raise ValueError("文件里没有问题")
            if target.exists():
                shutil.copy2(target, target.with_name(target.stem + ".previous.xlsx"))
            os.replace(tmp, target)
        except Exception as exc:  # noqa: BLE001 - show any validation failure to the user
            if os.path.exists(tmp):
                os.unlink(tmp)
            detail = '文件正被其他程序占用，请关闭 Excel，或换一个文件名后重新上传。' if isinstance(exc, PermissionError) else '文件无法读取，请确认是有效的 Excel 文件且表头未被修改。'
            if isinstance(exc, ValueError):
                detail = str(exc)
            return RedirectResponse("/questions?msg=" + _q(f"上传失败：{detail}"), status_code=303)
        return RedirectResponse("/questions?msg=" + _q(f"已更新问题清单，共 {n} 个问题；下次检测生效"),
                                status_code=303)

    @app.get("/questions/{qid}", response_class=HTMLResponse)
    def question_detail(request: Request, qid: str, sid: str = Depends(require_login), db: DB = Depends(get_db)):
        hist = Q.question_history(db, qid)
        try:
            meta = next((x for x in load_questions(cfg.questions_file, None, only_enabled=False)
                         if x.id == qid), None)
        except (OSError, ValueError):
            meta = None
        if not hist and not meta:
            raise HTTPException(404, "找不到这个问题")
        return render(request, "question_detail.html", sid, qid=qid, meta=meta, hist=hist,
                      prompt=meta.prompt if meta else hist[0]["prompt"])

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request, msg: str = "", sid: str = Depends(require_login), db: DB = Depends(get_db)):
        reqs = db.query("SELECT * FROM run_requests ORDER BY id DESC LIMIT 20")
        keyed = {e: bool(cfg.api_key(e)) for e in ("openai", "gemini", "perplexity")}
        return render(request, "runs.html", sid, runs=Q.all_runs(db, 50), reqs=reqs, msg=msg, keyed=keyed,
                      default_pri=cfg.priorities)

    @app.post("/runs")
    def request_run(engines: list[str] = Form([]), priorities: list[str] = Form([]), limit: str = Form(""),
                    csrf: str = Form(""), sid: str = Depends(require_login), db: DB = Depends(get_db)):
        check_csrf(sid, csrf)
        engines = [e for e in engines if e in ("openai", "gemini", "perplexity", "mock")]
        if os.environ.get('WEB_PREVIEW') == '1':
            engines = [e for e in engines if e == 'mock']
        else:
            engines = [e for e in engines if cfg.api_key(e)]
        priorities = [p for p in priorities if p in ("P1", "P2", "P3")]
        if not engines or not priorities:
            return RedirectResponse("/runs?msg=" + _q("请至少选择一个引擎和一个优先级"), status_code=303)
        busy = db.query("SELECT COUNT(*) AS n FROM run_requests WHERE status IN ('pending','running')")[0]["n"]
        if busy:
            return RedirectResponse("/runs?msg=" + _q("已有检测在排队或进行中，请等它完成"), status_code=303)
        if limit.strip() and (not limit.strip().isdigit() or not 1 <= int(limit) <= 500):
            return RedirectResponse('/runs?msg=' + _q('检测数量必须为 1–500 的整数，留空表示全部。'), 303)
        lim = int(limit) if limit.strip() else None
        db.insert("run_requests", {"requested_at": datetime.now(timezone.utc).isoformat(),
                                   "engines": ",".join(engines), "priorities": ",".join(priorities),
                                   "limit_n": lim, "status": "pending"})
        return RedirectResponse("/runs?msg=" + _q("已提交，30 秒内开始；完成后刷新本页查看"), status_code=303)

    @app.get("/reports", response_class=HTMLResponse)
    def reports(request: Request, sid: str = Depends(require_login)):
        d = Path(cfg.reports_dir)
        files = sorted((p for p in d.glob("*.xlsx")), key=lambda p: p.stat().st_mtime, reverse=True) \
            if d.is_dir() else []
        items = [{"name": p.name, "size": p.stat().st_size,
                  "mtime": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)} for p in files[:100]]
        return render(request, "reports.html", sid, items=items)

    @app.get("/reports/{name}")
    def report_file(name: str, sid: str = Depends(require_login)):
        if not REPORT_RE.match(name):
            raise HTTPException(404)
        path = Path(cfg.reports_dir) / name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, filename=name)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        generic = {404: "页面不存在", 405: "不支持这个操作"}
        detail = exc.detail if isinstance(exc.detail, str) and exc.detail not in (
            "Not Found", "Method Not Allowed", "Not Allowed") else generic.get(exc.status_code, "出错了")
        return tpl.TemplateResponse(request, "error.html", {"request": request, "code": exc.status_code,
                                                            "detail": detail, "nav": "", "csrf": ""},
                                    status_code=exc.status_code)

    # Google read-only integrations share this dashboard's authenticated session.
    from datetime import timedelta
    from .google_data import GoogleData, GoogleError
    from .google_imports import ImportStore, ImportErrorDetail
    google = GoogleData()
    imports = ImportStore()
    ai_service = None
    if growth:
        from .growth_routes import register_growth_routes
        ai_service = register_growth_routes(app,cfg,require_login,get_db,render,check_csrf)
    from .operations_routes import register_operations_routes
    operations_store = register_operations_routes(app,cfg,google,imports,require_login,render,check_csrf,tz)
    if growth:
        from .lead_routes import register_lead_routes
        lead_store = register_lead_routes(app,cfg,require_login,render,check_csrf,tz)
        from .knowledge_routes import register_knowledge_routes
        register_knowledge_routes(app,require_login,render,check_csrf)
    from .content_routes import register_content_routes
    register_content_routes(app, cfg, google, imports, require_login, get_db, render, check_csrf)
    redirect_uri = os.environ.get('GOOGLE_REDIRECT_URI', 'http://localhost:8000/google/callback')

    from .social import PLATFORMS, SocialError, SocialStore, campaign_link, social_traffic
    from .meta_data import MetaData, MetaError, OAuthLogFilter
    import logging
    if not any(isinstance(f, OAuthLogFilter) for f in logging.getLogger('uvicorn.access').filters):
        logging.getLogger('uvicorn.access').addFilter(OAuthLogFilter())
    meta_data = MetaData()
    social_store = SocialStore(cfg.target_domain)

    def social_context(request, sid, *, form=None, editor=False, error='', msg='', link_form=None, generated=''):
        accounts = social_store.accounts()
        archived = social_store.accounts(archived=True)
        resource = google.settings().get('property', '')
        data = imports.get('analytics', resource=resource) if resource else None
        try:
            meta_status = meta_data.status()
        except MetaError:
            meta_status = {'authorized':False}
        return render(request, 'social.html', sid, accounts=accounts, archived=archived,
                      platforms=PLATFORMS, form=form or {}, editor=editor, error=error, msg=msg,
                      unused=[k for k in PLATFORMS if k not in {a['platform'] for a in accounts}],
                      social_count=sum(not a['platform_info'].get('contact') for a in accounts),
                      contact_count=sum(bool(a['platform_info'].get('contact')) for a in accounts),
                      traffic=social_traffic(data), generated=generated, meta=meta_status,
                      link_form=link_form or {'track_platform': 'instagram', 'destination': 'https://'+cfg.target_domain+'/',
                                             'campaign': '', 'medium': 'organic_social'})

    @app.get('/social', response_class=HTMLResponse)
    def social_page(request: Request, edit: str = '', new: int = 0, platform: str = '', msg: str = '',
                    track_platform: str = 'instagram', destination: str = '', campaign: str = '',
                    medium: str = 'organic_social', build: int = 0, sid: str = Depends(require_login)):
        form = social_store.get(edit) if edit else {'platform': platform if platform in PLATFORMS else 'linkedin'}
        if edit and not form:
            raise HTTPException(404, '找不到这个社媒账号')
        link_form = {'track_platform': track_platform, 'destination': destination or 'https://'+cfg.target_domain+'/',
                     'campaign': campaign, 'medium': medium}
        generated, error = '', ''
        if build:
            try:
                generated = campaign_link(cfg.target_domain, track_platform, link_form['destination'], campaign, medium)
            except SocialError as exc:
                error = str(exc)
        return social_context(request, sid, form=form, editor=bool(edit or new), error=error,
                              msg=msg, link_form=link_form, generated=generated)

    @app.post('/social/accounts')
    def social_save(request: Request, account_id: str = Form(''), platform: str = Form(''),
                    name: str = Form(''), url: str = Form(''), notes: str = Form(''),
                    csrf: str = Form(''), sid: str = Depends(require_login)):
        check_csrf(sid, csrf)
        try:
            social_store.save(account_id, platform, name, url, notes)
        except SocialError as exc:
            response = social_context(request, sid, form={'id':account_id,'platform':platform,'name':name,'url':url,'notes':notes},
                                      editor=True, error=str(exc))
            response.status_code = 422
            return response
        return RedirectResponse('/social?msg='+_q('账号已保存。'), 303)

    @app.post('/social/accounts/{account_id}/status')
    def social_status(account_id: str, archived: str = Form('1'), csrf: str = Form(''),
                      sid: str = Depends(require_login)):
        check_csrf(sid, csrf)
        if archived not in {'0', '1'}:
            raise HTTPException(400, '请选择正确的账号状态')
        try:
            social_store.archive(account_id, archived == '1')
        except SocialError as exc:
            raise HTTPException(404, str(exc)) from None
        return RedirectResponse('/social?msg='+_q('账号已归档，可在页面底部恢复。' if archived == '1' else '账号已恢复。'), 303)

    @app.get('/social/export.csv')
    def social_export(sid: str = Depends(require_login)):
        import csv
        import io
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['平台','账号名称','主页链接','运营备注','状态','确认来源'])
        def safe(value):
            value = str(value or '')
            return "'"+value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t','\r','\n')) else value
        for account in social_store.accounts()+social_store.accounts(archived=True):
            writer.writerow([safe(value) for value in [account['platform_info']['name'],account['name'],account['url'],
                                                      account['notes'],'已归档' if account['archived'] else '使用中',account['source_url']]])
        return Response('\ufeff'+output.getvalue(), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition':'attachment; filename="b2b-social-accounts.csv"'})

    def meta_context(request, sid, msg='', error=''):
        try:
            status, reports = meta_data.status(), meta_data.latest()
        except MetaError as exc:
            status, reports, error = {'configured':False,'authorized':False,'redirect_uri':'http://localhost:8000/social/meta/callback',
                'graph_version':'v26.0','app_id':'','config_id':'','pages':[],'selected_page':''}, {}, str(exc)
        return render(request, 'meta_dashboard.html', sid, meta=status, reports=reports, msg=msg, error=error)

    @app.get('/social/meta', response_class=HTMLResponse)
    def meta_dashboard(request: Request, msg: str = '', sid: str = Depends(require_login)):
        return meta_context(request, sid, msg)

    @app.post('/social/meta/client')
    def meta_client_save(request: Request, app_id: str = Form(''), app_secret: str = Form(''),
                         redirect_uri: str = Form(''), graph_version: str = Form('v26.0'), config_id: str = Form(''),
                         csrf: str = Form(''), sid: str = Depends(require_login)):
        check_csrf(sid,csrf)
        try:
            meta_data.save_client(app_id,app_secret,redirect_uri,graph_version,config_id)
        except MetaError as exc:
            response = meta_context(request,sid,error=str(exc))
            response.status_code = 422
            return response
        return RedirectResponse('/social/meta?msg='+_q('应用配置已加密保存。下一步连接 Meta 并选择当前品牌主页。'),303)

    @app.post('/social/meta/connect')
    def meta_connect(request: Request, csrf: str = Form(''), sid: str = Depends(require_login)):
        check_csrf(sid,csrf)
        try:
            return RedirectResponse(meta_data.begin(sid,str(request.base_url).rstrip('/')),303)
        except MetaError as exc:
            return RedirectResponse('/social/meta?msg='+_q(str(exc)),303)

    @app.get('/social/meta/callback')
    def meta_callback(code: str = '', state: str = '', error: str = '', sid: str = Depends(require_login)):
        try:
            if error:
                raise MetaError('Meta 授权未完成；现有连接与历史数据会保留，可以重新连接。')
            meta_data.finish(sid,state,code)
            msg = 'Meta 授权已收到。请核对并选择当前品牌的 Facebook 主页，再同步真实数据。'
        except MetaError as exc:
            msg = str(exc)
        return RedirectResponse('/social/meta?msg='+_q(msg),303)

    @app.post('/social/meta/page')
    def meta_page_select(page_id: str = Form(''), csrf: str = Form(''), sid: str = Depends(require_login)):
        check_csrf(sid,csrf)
        try:
            meta_data.select_page(page_id)
            msg = '主页已选择。点击「同步真实数据」读取粉丝和互动统计。'
        except MetaError as exc:
            msg = str(exc)
        return RedirectResponse('/social/meta?msg='+_q(msg),303)

    @app.post('/social/meta/sync')
    def meta_sync(csrf: str = Form(''), sid: str = Depends(require_login)):
        check_csrf(sid,csrf)
        try:
            msg = meta_data.sync()
        except MetaError as exc:
            msg = str(exc)
        return RedirectResponse('/social/meta?msg='+_q(msg),303)

    @app.post('/social/meta/disconnect')
    def meta_disconnect(csrf: str = Form(''), sid: str = Depends(require_login)):
        check_csrf(sid,csrf)
        meta_data.disconnect()
        return RedirectResponse('/social/meta?msg='+_q('本地连接已移除，历史快照保留。如需撤销 Meta 侧权限，请到 Facebook 的商业集成设置移除本应用。'),303)

    @app.get('/google', response_class=HTMLResponse)
    def google_settings(request: Request, msg: str = '', sid: str = Depends(require_login)):
        return render(request, 'google_settings.html', sid, settings=google.settings(),
                      connected=google.connected(), ready=bool(google.client()),
                      redirect_uri=redirect_uri, msg=msg, import_history=imports.history())

    @app.get('/google/imports', response_class=HTMLResponse)
    def google_imports(request: Request, msg: str = '', sid: str = Depends(require_login)):
        end = datetime.now(tz).date() - timedelta(days=1)
        return render(request, 'google_imports.html', sid, settings=google.settings(),
                      history=imports.history(), msg=msg, default_start=end-timedelta(days=27), default_end=end)

    @app.post('/google/imports')
    async def google_import_upload(kind: str = Form(''), start: str = Form(''), end: str = Form(''),
                                   files: list[UploadFile] = File(...), csrf: str = Form(''),
                                   sid: str = Depends(require_login)):
        check_csrf(sid, csrf)
        if phase1 and not growth and kind != 'analytics':
            raise HTTPException(404, '首版只开放 GA4 报表导入')
        if kind not in ('analytics','search') or len(files) > 8:
            return RedirectResponse('/google/imports?msg='+_q('请选择正确的服务，每次最多上传 8 个文件。'),303)
        resource=google.settings().get('property' if kind=='analytics' else 'site','')
        messages=[]
        for file in files:
            try:
                content=await file.read(10*1024*1024+1)
                batch,created=imports.save(kind,file.filename or '',content,resource,start,end)
                messages.append(f'#{batch} ' + ('导入成功' if created else '文件已导入，未重复添加'))
            except ImportErrorDetail as exc:
                messages.append(f'{Path(file.filename or "文件").name}：{exc}')
        return RedirectResponse('/google/imports?msg='+_q('；'.join(messages)),303)

    @app.get('/google/imports/{batch}/download')
    def google_import_download(batch: int, sid: str = Depends(require_login)):
        if phase1 and not growth and not any(h['id'] == batch and h['kind'] == 'analytics' for h in imports.history()):
            raise HTTPException(404, '首版暂未开放此报表')
        original=imports.original(batch)
        if not original or not original[0].is_file():
            raise HTTPException(404,'原始导出文件不存在')
        return FileResponse(original[0], filename=original[1])

    @app.post('/google/settings')
    def google_save(property_id: str = Form(''), site_url: str = Form(''),
                    csrf: str = Form(''), sid: str = Depends(require_login)):
        check_csrf(sid, csrf)
        property_id, site_url = property_id.strip(), site_url.strip()
        if (property_id and not property_id.isdigit()) or (site_url and not site_url.startswith(('https://', 'http://', 'sc-domain:'))):
            return RedirectResponse('/google?msg=' + _q('请填写数字 GA4 资源 ID 和有效的网站资源。'), 303)
        with google.lock:
            google.write('settings.json', {'property': property_id, 'site': site_url})
        return RedirectResponse('/google?msg=' + _q('资源设置已保存。'), 303)

    @app.post('/google/connect')
    def google_connect(csrf: str = Form(''), sid: str = Depends(require_login)):
        check_csrf(sid, csrf)
        try:
            return RedirectResponse(google.begin(sid, redirect_uri), 303)
        except GoogleError as exc:
            return RedirectResponse('/google?msg=' + _q(str(exc)), 303)

    @app.get('/google/callback')
    def google_callback(code: str = '', state: str = '', error: str = '', sid: str = Depends(require_login)):
        try:
            if error or not code:
                raise GoogleError('授权未完成，请重新连接并允许读取报告。')
            google.finish(sid, state, code)
            msg = 'Google 授权成功，请填写资源 ID 后查看数据。'
        except GoogleError as exc:
            msg = str(exc)
        return RedirectResponse('/google?msg=' + _q(msg), 303)

    @app.post('/google/disconnect')
    def google_disconnect(csrf: str = Form(''), sid: str = Depends(require_login)):
        check_csrf(sid, csrf)
        with google.lock:
            (google.folder / 'tokens.json').unlink(missing_ok=True)
            google.pending.clear()
        return RedirectResponse('/google?msg=' + _q('本地授权已移除。若要撤销 Google 侧权限，请到 Google 账号的第三方连接中移除本应用。'), 303)

    def google_dashboard(request, sid, kind, days, source='', batch=None):
        days = days if days in (7, 28, 90) else 28
        # GA4 dates follow the property timezone; Search Console dates follow Pacific time.
        report_tz = ZoneInfo('America/Los_Angeles') if kind == 'search' else tz
        end = datetime.now(report_tz).date() - timedelta(days=3 if kind == 'search' else 1)
        start = end - timedelta(days=days-1)
        resource=google.settings().get('property' if kind=='analytics' else 'site','')
        snapshot=imports.get(kind,batch,resource) if resource else None
        mode=source if source in ('import','api') else ('import' if snapshot else 'api')
        data, error = None, ''
        if mode=='import':
            data=snapshot
            if not data:
                error='未找到对应导入记录，请先上传报表或选择已有批次。'
            else:
                start,end=data['import_meta']['start'],data['import_meta']['end']
        elif google.connected():
            try:
                data = (google.search if kind == 'search' else google.analytics)(str(start), str(end))
            except GoogleError as exc:
                error = str(exc)
        points = ''
        if data and data['daily']:
            rows = data['daily']; maximum = max((r['values'][0] for r in rows), default=0) or 1
            points = ' '.join(f'{20+i*760/max(len(rows)-1,1):.1f},{150-(r["values"][0] or 0)*125/maximum:.1f}' for i,r in enumerate(rows))
        if data:
            from .workspace import search_action, site_page
            for name, rows in data.get('tables', []):
                for row in rows:
                    if kind == 'search' and mode == 'import' and '关键词' in name:
                        row['optimize_href'] = search_action(row['label'], data['import_meta']['id'])
                    if '页面' in name or '落地页' in name:
                        row['page_link'] = site_page(row['label'], cfg.target_domain)
        return render(request, 'google_dashboard.html', sid, kind=kind, days=days,
                      start=start, end=end, data=data, error=error, points=points,
                      connected=google.connected(), settings=google.settings(), mode=mode,
                      batches=[h for h in imports.history(kind) if h['resource']==resource], batch=batch)

    @app.get('/analytics', response_class=HTMLResponse)
    def analytics_dashboard(request: Request, days: int = 28, source: str = '', batch: int | None = None,
                            sid: str = Depends(require_login)):
        return google_dashboard(request, sid, 'analytics', days, source, batch)

    @app.get('/search-console', response_class=HTMLResponse)
    def search_dashboard(request: Request, days: int = 28, source: str = '', batch: int | None = None,
                         sid: str = Depends(require_login)):
        return google_dashboard(request, sid, 'search', days, source, batch)

    return app


def _q(s: str) -> str:
    from urllib.parse import quote
    return quote(s)
