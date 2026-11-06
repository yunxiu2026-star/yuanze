"""Authenticated growth tools, with explicit per-job consent."""
import csv
import io
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from .ai_monitor import AIMonitor, AIError, STATES
from . import queries as Q


def register_growth_routes(app,cfg,require_login,get_db,render,check_csrf):
    service = AIMonitor(cfg)
    app.state.ai_monitor = service
    app.add_event_handler('startup',service.start)
    app.add_event_handler('shutdown',service.close)

    def redirect(message):
        return RedirectResponse('/ai?'+urlencode({'msg':message}),303)

    def page(request,sid,db,*,job=None,preview=None,error='',msg='',query=''):
        info = service.summary()
        try:
            questions = service.questions()
        except AIError as exc:
            questions, error = [], str(exc)
        query=query.strip()[:200]
        if query:
            questions=[q for q in questions if query.casefold() in ' '.join(str(v) for v in q.values()).casefold()]
        selected = next((j for j in service.history(None) if j['id']==job),None) if job else None
        if job and not selected:
            raise HTTPException(404,'找不到这次检测')
        rows = Q.run_rows(db,selected['run_id']) if selected and selected['run_id'] else []
        legacy_count = db.query('SELECT COUNT(*) AS n FROM runs')[0]['n'] - sum(bool(j['run_id']) for j in info['jobs'])
        return render(request,'ai_monitor.html',sid,info=info,questions=questions,states=STATES,
                      selected=selected,rows=rows,plan=preview,error=error,msg=msg,
                      legacy_count=max(0,legacy_count),query=query)

    @app.get('/ai')
    def ai(request: Request,msg: str='',q: str='',sid: str=Depends(require_login),db=Depends(get_db)):
        return page(request,sid,db,msg=msg[:240],query=q)

    @app.get('/ai/jobs/{job_id}')
    def job(request: Request,job_id: int,sid: str=Depends(require_login),db=Depends(get_db)):
        return page(request,sid,db,job=job_id)

    @app.post('/ai/settings')
    async def settings(request: Request,sid: str=Depends(require_login)):
        form = await request.form();check_csrf(sid,form.get('csrf'))
        try:
            service.save_key(str(form.get('api_key','')))
        except AIError as exc:
            return redirect(str(exc))
        return redirect('密钥已加密保存，未发起任何付费调用。')

    @app.post('/ai/disconnect')
    async def disconnect(request: Request,sid: str=Depends(require_login)):
        form = await request.form();check_csrf(sid,form.get('csrf'))
        service.disconnect()
        return redirect('已清空本应用保存的 API 密钥，检测历史保留；服务商端密钥未撤销。')

    @app.post('/ai/prepare')
    async def prepare(request: Request,sid: str=Depends(require_login),db=Depends(get_db)):
        form = await request.form();check_csrf(sid,form.get('csrf'))
        try:
            token, qs = service.prepare(sid,form.getlist('questions'))
            return page(request,sid,db,preview=dict(token=token,questions=qs,allowance=round(len(qs)*.1,2)))
        except AIError as exc:
            return redirect(str(exc))

    @app.post('/ai/start')
    async def start(request: Request,sid: str=Depends(require_login)):
        form = await request.form();check_csrf(sid,form.get('csrf'))
        try:
            job = service.approve(sid,str(form.get('token','')),form.get('consent'))
            return RedirectResponse(f'/ai/jobs/{job}',303)
        except AIError as exc:
            return redirect(str(exc))

    @app.get('/ai/export.csv')
    def export(sid: str=Depends(require_login),db=Depends(get_db)):
        out=io.StringIO(newline='');writer=csv.writer(out)
        writer.writerow(['任务','时间','状态','问题','模型','提及本站','引用本站','实际检索词','回答','错误'])
        def safe(value):
            text=str(value or '')
            return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
        for job in service.history(None):
            rows=Q.run_rows(db,job['run_id']) if job['run_id'] else []
            if not rows:
                writer.writerow([safe(x) for x in [job['id'],job['created_at'],STATES[job['status']],'','','','','','',job['error']]])
            for r in rows:
                writer.writerow([safe(x) for x in [job['id'],r['checked_at'],STATES[job['status']],r['prompt'],r['model'],r['mentioned'],r['cited'],' / '.join(r['search_queries']),r['answer_text'],job['error']]])
        return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',
            headers={'Content-Disposition':'attachment; filename="b2b-ai-monitor.csv"'})
    return service
