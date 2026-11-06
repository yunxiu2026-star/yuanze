"""Authenticated editorial workbench, independent of live website publishing."""
from __future__ import annotations

import csv
import io
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import queries as Q
from .content_optimization import (DraftStore, LANGUAGES, STATUSES, OptimizationError,
                                   clean, collect_signals, make_draft, page_url, title_checks)


def register_content_routes(app, cfg, google, imports, require_login, get_db, render, check_csrf):
    store = DraftStore()
    from .release import growth_enabled
    from datetime import datetime, timedelta, timezone, date

    def verified_runs(db):
        if not growth_enabled():
            return Q.done_runs(db,100)
        ids = {j['run_id'] for j in app.state.ai_monitor.history(None) if j['status'] in {'done','partial'} and j['run_id']}
        return [r for r in Q.done_runs(db,100000) if r['id'] in ids]

    def signals(db, run_id=None, search_batch=None, result_id=None):
        runs = verified_runs(db)
        run = next((r for r in runs if r['id']==run_id),None) if run_id else (runs[0] if runs else None)
        if run_id and (not run or run['status'] != 'done'):
            raise HTTPException(404, '找不到已完成的检测')
        rows = Q.run_rows(db, run['id']) if run else []
        site = google.settings().get('site', '')
        report = imports.get('search', search_batch, resource=site) if site else None
        if search_batch and not report:
            raise HTTPException(404, '找不到当前网站的这份搜索报表，请重新选择来源')
        import os
        preview = os.environ.get('WEB_PREVIEW') == '1'
        items = collect_signals(rows, report, cfg.target_domain, preview)
        if result_id:
            row = next((r for r in rows if r['id'] == result_id and not r.get('error')), None)
            if not row:
                raise HTTPException(404, '找不到本批次的有效检测依据')
            related = collect_signals([row], None, cfg.target_domain, preview)
            # A contextual entry retains this result's page and evidence, even when
            # another question happened to return the same search phrase.
            contextual = {s['id']: s for s in related}
            items = [contextual.get(s['id'], s) for s in items]
            for s in items:
                s['contextual'] = s['id'] in contextual
        return run, items, report

    def context(request, sid, db, *, run_id=None, selected='', source='', q='', page=1,
                search_batch=None, result_id=None,
                form=None, draft=None, error='', msg=''):
        def retained_id(value):
            try:
                return int(value) if value else None
            except (TypeError, ValueError):
                return None
        if form:
            run_id = run_id or retained_id(form.get('run'))
            search_batch = search_batch or retained_id(form.get('search_batch'))
            result_id = result_id or retained_id(form.get('result_id'))
        run, items, report = signals(db, run_id, search_batch, result_id)
        if result_id and not selected:
            selected = next((s['id'] for s in items if s.get('contextual')), '')
        chosen = next((s for s in items if s['id'] == selected), None)
        if selected and not chosen:
            raise HTTPException(404, '该信号不在本次检测或当前报表内，请重新选择')
        drafts = store.list()
        if not form:
            form = {'keyword': chosen['text'] if chosen else '', 'url': chosen['url'] if chosen else '',
                    'language': chosen['language'] if chosen else 'en', 'current_title': '',
                    'signal_id': selected, 'run': run['id'] if run else ''}
        form.update(search_batch=search_batch or '', result_id=result_id or '')
        source = source or ('demo' if chosen and chosen['demo'] else 'actual')
        visible = [s for s in items if (not q or q.casefold() in s['text'].casefold()) and (
            source == 'all' or source == 'actual' and not s['demo'] or source == 'real' and s['kind'] == 'gsc' or
            source == 'ai' and s['kind'] in {'ai', 'prompt'} or source == 'demo' and s['demo'])]
        pages = max(1, (len(visible) + 19) // 20)
        page = min(max(1, page), pages)
        def query(**kwargs):
            return '/optimize?' + urlencode({k:v for k,v in dict(run=run['id'] if run else '', source=source, q=q,
                search_batch=search_batch or '', result_id=result_id or '', **kwargs).items() if v != ''})
        finish = datetime.now(timezone.utc).date()-timedelta(days=3)
        return render(request, 'content_optimization.html', sid, run=run, runs=verified_runs(db),
                      signals=visible[(page-1)*20:page*20], signal_count=len(visible), total_count=len(items),
                      page=page, pages=pages, query=query, selected=chosen, form=form, draft=draft,
                      languages=LANGUAGES, statuses=STATUSES, drafts=drafts[:30], draft_count=len(drafts),
                      real_count=sum(s['kind'] == 'gsc' for s in items), report=report,
                      source=source, q=q, error=error, msg=msg,google_connected=google.connected(),
                      sync_start=(finish-timedelta(days=27)).isoformat(),sync_end=finish.isoformat())

    @app.post('/optimize/sync')
    async def sync(request: Request, sid: str=Depends(require_login)):
        data = await request.form();check_csrf(sid,data.get('csrf'))
        from .google_data import GoogleError
        from .google_imports import ImportErrorDetail
        try:
            start,end=date.fromisoformat(str(data.get('start',''))),date.fromisoformat(str(data.get('end','')))
            if start>end or (end-start).days>89 or end>datetime.now(timezone.utc).date()-timedelta(days=2):
                raise ValueError()
            rows=google.search_terms(start.isoformat(),end.isoformat())
            if not rows:
                return RedirectResponse('/optimize?'+urlencode({'msg':'Google 在这个时间范围未返回可用查询。旧报告和草稿保留，请调整日期后重试。'}),303)
            out=io.StringIO(newline='');writer=csv.writer(out)
            writer.writerow(['Top queries','Clicks','Impressions','CTR','Position'])
            for r in rows:writer.writerow([r['label'],*r['values']])
            batch,_=imports.save('search','gsc-api-queries.csv',out.getvalue().encode('utf-8'),
                                 google.settings().get('site',''),start.isoformat(),end.isoformat())
            return RedirectResponse('/optimize?'+urlencode({'search_batch':batch,'source':'real',
                'msg':f'已保存 {len(rows)} 条 Search Console 查询快照；API 最多读取 1000 条高曝光查询，不包含匿名查询。'}),303)
        except (ValueError,GoogleError,ImportErrorDetail) as exc:
            msg = str(exc) if isinstance(exc,(GoogleError,ImportErrorDetail)) else '日期无效：最多 90 天，结束日期至少留出两天数据延迟。'
            return RedirectResponse('/optimize?'+urlencode({'msg':msg}),303)

    @app.get('/optimize', response_class=HTMLResponse)
    def optimize(request: Request, run: int | None = None, signal: str = '', source: str = '',
                 q: str = '', page: int = 1, draft_id: int | None = None, msg: str = '',
                 search_batch: int | None = None, result_id: int | None = None,
                 sid: str = Depends(require_login), db=Depends(get_db)):
        draft, form = None, None
        if draft_id:
            draft = next((d for d in store.list() if d['id'] == draft_id), None)
            if not draft:
                raise HTTPException(404, '找不到草稿')
            form = {**draft, 'origin_draft': draft_id, 'run': run or '', 'signal_id': ''}
        return context(request, sid, db, run_id=run, selected=signal, source=source if source in {'actual','all','real','ai','demo'} else '',
                       search_batch=search_batch, result_id=result_id,
                       q=clean(q, 200), page=page, form=form, draft=draft, msg=clean(msg, 160))

    async def submitted(request, sid):
        data = await request.form()
        check_csrf(sid, data.get('csrf', ''))
        allowed = {'keyword','language','url','current_title','signal_id','run','origin_draft',
                   'title','h1','description','secondary','outline','notes','faq_text','search_batch','result_id'}
        form = {k:v for k,v in data.items() if k in allowed and isinstance(v,str)}
        if sum(len(v) for v in form.values()) > 24000 or any(len(v)>10000 for v in form.values()):
            raise HTTPException(422, '填写内容过长，请精简后重试')
        return form

    def assemble(form, db):
        try:
            run_id = int(form['run']) if form.get('run') else None
            search_batch = int(form['search_batch']) if form.get('search_batch') else None
            result_id = int(form['result_id']) if form.get('result_id') else None
        except ValueError:
            raise OptimizationError('检测批次不正确，请刷新页面。')
        run, items, _ = signals(db, run_id, search_batch, result_id)
        chosen = next((s for s in items if s['id'] == form.get('signal_id')), None)
        if form.get('signal_id') and not chosen:
            raise OptimizationError('原始信号已变化，请刷新页面重新选择。')
        draft = make_draft(form.get('keyword'), form.get('language', 'en'), form.get('url'),
                           cfg.target_domain, cfg.site_name, chosen)
        if form.get('origin_draft'):
            old = next((d for d in store.list() if str(d['id']) == form['origin_draft']), None)
            if not old:
                raise OptimizationError('原始草稿不存在，请重新选择。')
            draft['basis'] = {**old['basis'], 'rewritten': old['basis'].get('rewritten', False)
                              or draft['keyword'].casefold() != old['keyword'].casefold()}
            draft['previous_draft'] = old['id']
        draft['current_title'] = clean(form.get('current_title'), 300)
        form.update(run=run['id'] if run else '', **{k:draft[k] for k in ['keyword','language','url','current_title']})
        return run_id, draft

    @app.post('/optimize/preview', response_class=HTMLResponse)
    async def preview(request: Request, sid: str = Depends(require_login), db=Depends(get_db)):
        form = await submitted(request, sid)
        try:
            run_id, draft = assemble(form, db)
            return context(request, sid, db, run_id=run_id, selected=form.get('signal_id',''),
                           search_batch=int(form['search_batch']) if form.get('search_batch') else None,
                           result_id=int(form['result_id']) if form.get('result_id') else None, form=form, draft=draft)
        except OptimizationError as exc:
            response = context(request, sid, db, form=form, error=str(exc))
            response.status_code = 422
            return response

    @app.post('/optimize/drafts')
    async def save(request: Request, sid: str = Depends(require_login), db=Depends(get_db)):
        form = await submitted(request, sid)
        try:
            run_id, draft = assemble(form, db)
            for name, maximum in [('title',300),('h1',300),('description',1000),('secondary',3000),('outline',6000),('notes',2000)]:
                value = str(form.get(name, '')).strip()
                if len(value) > maximum:
                    raise OptimizationError('内容过长，请按页面提示精简后保存。')
                draft[name] = value
            if not draft['title'] or not draft['h1']:
                raise OptimizationError('标题和 H1 不能为空。')
            draft['url'] = page_url(draft['url'], cfg.target_domain)
            draft['checks'] = title_checks(draft['title'], draft['keyword'], draft['url'],
                                          [d['title'] for d in store.list() if d['status'] != 'archived'])
            from .aeo_export import parse_faq
            draft['faq_text']=str(form.get('faq_text','')).strip()
            draft['faq']=parse_faq(draft['faq_text'])
            draft['method'] = '规则辅助草稿，经用户编辑保存；未调用付费生成接口'
            draft_id = store.save(draft)
            return RedirectResponse('/optimize?' + urlencode({'draft_id':draft_id, 'msg':'草稿已保存，等待人工审核。'}) + '#draft-editor', 303)
        except OptimizationError as exc:
            # Keep edited fields when validation fails.
            response = context(request, sid, db, form=form, draft={**form,'variants':[], 'basis':{'source':'尚未保存'},'checks':[]}, error=str(exc))
            response.status_code = 422
            return response

    @app.post('/optimize/drafts/{draft_id}/status')
    async def status(request: Request, draft_id: int, sid: str = Depends(require_login)):
        data = await request.form()
        check_csrf(sid, data.get('csrf',''))
        try:
            if not store.status(draft_id, data.get('status','')):
                raise HTTPException(404, '找不到草稿')
        except OptimizationError as exc:
            raise HTTPException(422, str(exc))
        return RedirectResponse('/optimize?msg=' + urlencode({'m':'草稿状态已更新。'})[2:] + '#saved-drafts', 303)

    @app.get('/optimize/drafts/{draft_id}/aeo.{format}')
    def aeo_export(draft_id:int,format:str,sid:str=Depends(require_login)):
        from .aeo_export import export_draft
        draft=next((d for d in store.list() if d['id']==draft_id),None)
        if not draft:raise HTTPException(404,'找不到草稿')
        if format not in {'html','json'}:raise HTTPException(404,'导出格式不存在')
        try:body=export_draft(draft,cfg.target_domain,format)
        except OptimizationError as exc:raise HTTPException(422,str(exc)) from None
        return Response(body,media_type='text/html; charset=utf-8' if format=='html' else 'application/ld+json',
                        headers={'Content-Disposition':f'attachment; filename="b2b-aeo-draft-{draft_id}.{format}"'})

    @app.get('/optimize/export.csv')
    def export(sid: str = Depends(require_login)):
        out = io.StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(['草稿ID','保存时间','状态','目标页面','核心词','语言','原有标题','建议标题','H1','描述','扩展词（建议）','内容提纲','来源','原始检索词','来源详情','复查提示','备注'])
        def safe(value):
            value = str(value or '')
            return "'" + value if value.lstrip().startswith(('=','+','-','@')) else value
        for d in store.list():
            writer.writerow([safe(x) for x in [d['id'],d['created_at'],STATUSES[d['status']],d['url'],d['keyword'],
                LANGUAGES[d['language']],d.get('current_title',''),d['title'],d['h1'],d['description'],d['secondary'],d['outline'],
                d['basis']['source'],d['basis']['text'],json_dumps(d['basis']),'\n'.join(d['checks']),d.get('notes','')]])
        return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition':'attachment; filename="b2b-title-drafts.csv"'})


def json_dumps(value):
    import json
    return json.dumps(value, ensure_ascii=False)
