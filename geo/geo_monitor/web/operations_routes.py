"""Authenticated local inquiry statistics and page inspection workflows."""
import csv
from datetime import datetime, timedelta
import hashlib
import io
import json
from urllib.parse import urlencode

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from .google_data import GoogleError
from .indexing import INDEX_STATES, SOURCES, check_public_page, google_result, indexing_summary
from .inquiries import EVENTS, KINDS, OUTCOMES, dates, inquiry_summary, parse_records
from .operations_store import OperationsError, OperationsStore


def register_operations_routes(app,cfg,google,imports,require_login,render,check_csrf,tz):
    from .release import phase1_enabled
    phase1=phase1_enabled()
    store=OperationsStore(cfg.target_domain,cfg.database_url)

    def redirect(path,message):
        return RedirectResponse(path+('&' if '?' in path else '?')+urlencode({'msg':message}),303)

    def current_dates(start='',end='',days=28):
        finish=datetime.now(tz).date()-timedelta(days=1 if phase1 else 0)
        return dates(start or (finish-timedelta(days=(days if days in {7,28,90} else 28)-1)).isoformat(),
                     end or finish.isoformat(),cfg.timezone)

    def inquiry_context(request,sid,*,start='',end='',days=28,record_type='customer',status='',batch=None,page=1,msg='',error='',realtime=False):
        start,end=current_dates(start,end,days)
        resource=google.settings().get('property','')
        summary=inquiry_summary(store,resource,start,end,cfg.timezone,record_type,batch,status)
        pages=max(1,(summary['record_count']+24)//25);page=min(max(1,page),pages)
        summary['records']=summary['records'][(page-1)*25:page*25]
        live=None;live_error=''
        if realtime:
            try:live=google.realtime_inquiry_events()
            except GoogleError as exc:live_error=str(exc)
        def query(**kw):
            params=dict(start=start,end=end,record_type=record_type,status=status,batch=batch or '');params.update(kw)
            return '/inquiries?'+urlencode(params)
        return render(request,'phase1_events.html' if phase1 else 'inquiries.html',sid,info=summary,record_type=record_type,status=status,
            events=EVENTS,kinds=KINDS,outcomes=OUTCOMES,resource=resource,connected=google.connected(),
            historical=store.baseline.get('test') if cfg.target_domain==store.baseline['domain'] else None,
            page=page,pages=pages,query=query,msg=msg,error=error,live=live,live_error=live_error)

    @app.get('/inquiries')
    def inquiry_page(request:Request,start:str='',end:str='',days:int=28,record_type:str='customer',
                     status:str='',batch:int|None=None,page:int=1,msg:str='',realtime:bool=False,sid=Depends(require_login)):
        if record_type not in {'customer','test','unknown','all'} or status not in {'',*OUTCOMES}:
            raise HTTPException(400,'请选择有效的记录类型和接收结果。')
        try: return inquiry_context(request,sid,start=start,end=end,days=days,record_type=record_type,status=status,batch=batch,page=page,msg=msg[:300],realtime=realtime)
        except OperationsError as exc: raise HTTPException(400,str(exc)) from None

    @app.post('/inquiries/import')
    async def inquiry_import(request:Request,kind:str=Form(''),start:str=Form(''),end:str=Form(''),
                             file:UploadFile=File(...),csrf:str=Form(''),sid=Depends(require_login)):
        check_csrf(sid,csrf)
        if phase1 and kind != 'ga4': raise HTTPException(404,'首版只开放 GA4 事件报表')
        if kind not in {'receipts','ga4'}: raise HTTPException(400,'请选择接收记录或 GA4 事件报表。')
        resource='https://'+cfg.target_domain+'/' if kind=='receipts' else google.settings().get('property','')
        try:
            if not resource: raise OperationsError('请先填写 GA4 资源 ID，再导入该资源的事件报表。')
            content=await file.read(5*1024*1024+1)
            payload=parse_records(kind,file.filename or '',content,start,end,cfg.target_domain,cfg.timezone)
            batch,created=store.save_batch(kind,resource,file.filename or '',start,end,payload['digest'],payload)
            message=f'#{batch} '+('导入成功' if created else '已导入过，未重复添加')+'。只保存统计字段，历史批次保留。'
            return redirect('/inquiries?'+urlencode(dict(start=start,end=end)),message)
        except OperationsError as exc:
            response=inquiry_context(request,sid,error=str(exc));response.status_code=422;return response

    @app.post('/inquiries/sync')
    def inquiry_sync(request:Request,start:str=Form(''),end:str=Form(''),csrf:str=Form(''),sid=Depends(require_login)):
        check_csrf(sid,csrf)
        try:
            if not google.connected(): raise OperationsError('Google API 尚未授权。可以先免费导入 GA4 事件报表。')
            start,end=dates(start,end,cfg.timezone,90)
            yesterday=(datetime.now(tz).date()-timedelta(days=1)).isoformat()
            end=min(end,yesterday)
            if start>end: raise OperationsError('GA4 快照只读取截至昨日的报告，请调整日期。')
            payload=google.inquiry_events(start,end)
            digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
            store.save_batch('ga4',google.settings().get('property',''),'Google API 事件快照',start,end,digest,payload)
            return redirect('/inquiries?'+urlencode(dict(start=start,end=end)),'GA4 事件快照已读取。事件总数可能包含测试流量，不等于客户接收数量。')
        except (OperationsError,GoogleError) as exc:
            response=inquiry_context(request,sid,error=str(exc));response.status_code=422;return response

    def csv_response(filename,rows):
        out=io.StringIO(newline='');writer=csv.writer(out)
        for row in rows:
            writer.writerow(["'"+str(v) if str(v).lstrip().startswith(('=','+','-','@')) else v for v in row])
        return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{filename}"'})

    @app.get('/inquiries/template/{kind}.csv')
    def inquiry_template(kind:str,sid=Depends(require_login)):
        if kind=='receipts': fields=['request_id','occurred_at','outcome','http_status','is_test','form_id','page_path','error_code']
        elif kind=='ga4': fields=['date','event_name','event_count','is_test','page_path']
        else: raise HTTPException(404,'模板不存在')
        return csv_response('inquiry-'+kind+'-template.csv',[fields])

    @app.get('/inquiries/export.csv')
    def inquiry_export(start:str='',end:str='',record_type:str='customer',status:str='',batch:int|None=None,sid=Depends(require_login)):
        try:
            start,end=current_dates(start,end)
            if record_type not in {'customer','test','unknown','all'} or status not in {'',*OUTCOMES}: raise OperationsError('筛选无效。')
            data=inquiry_summary(store,google.settings().get('property',''),start,end,cfg.timezone,record_type,batch_id=batch,status=status)
            if phase1:
                if not data['ga_selected']:raise OperationsError('当前没有可导出的 GA4 事件快照。')
                if not data['compatible']:raise OperationsError('所选日期不能完整覆盖汇总快照，请调整日期后导出。')
                b=data['ga_selected']
                fields=['date','event_name','event_count','record_type','page_path','batch_id','resource','snapshot_start','snapshot_end']
                return csv_response('b2b-ga4-inquiry-events.csv',[fields]+[[r['date'],r['event'],r['count'],r['kind'],r['page'],b['id'],b['resource'],b['start'],b['end']] for r in data['ga_rows']])
        except OperationsError as exc: raise HTTPException(400,str(exc)) from None
        fields=['request_id','occurred_at','kind','outcome','http_status','form','page','error_code','batch']
        return csv_response('inquiry-receipts.csv',[fields]+[[r.get(k,'') for k in fields] for r in data['records']])

    def index_context(request,sid,*,state='',q='',msg='',error='',page_id=None,manual_form=None):
        site=google.settings().get('site','')
        search=imports.get('search',resource=site) if site else None
        data=indexing_summary(store,search)
        if page_id:
            selected=next((p for p in data['pages'] if p['id']==page_id),None)
            if not selected: raise HTTPException(404,'找不到本站的这个页面。')
            return render(request,'indexing_detail.html',sid,p=selected,history=store.checks(page_id),
                          labels=INDEX_STATES,sources=SOURCES,site_resource=site,connected=google.connected(),
                          today=datetime.now(tz).date().isoformat(),manual_form=manual_form or {},msg=msg,error=error)
        visible=[p for p in data['pages'] if (not state or p['state']==state if state!='intentional' else p['policy']=='noindex')
                 and (not q or q.casefold() in (p['title']+' '+p['path']).casefold())]
        return render(request,'indexing.html',sid,info=data,rows=visible,state=state,q=q,labels=INDEX_STATES,
                      sources=SOURCES,site_resource=site,connected=google.connected(),msg=msg,error=error)

    @app.get('/indexing')
    def index_page(request:Request,state:str='',q:str='',msg:str='',sid=Depends(require_login)):
        if state not in {'',*INDEX_STATES,'intentional'}: raise HTTPException(400,'筛选状态无效。')
        return index_context(request,sid,state=state,q=q[:150],msg=msg[:300])

    @app.post('/indexing/pages')
    def add_index_page(request:Request,path:str=Form(''),title:str=Form(''),policy:str=Form('index'),csrf:str=Form(''),sid=Depends(require_login)):
        check_csrf(sid,csrf)
        try:
            store.add_page(path,title,policy)
            return redirect('/indexing','页面已加入本机核查清单，不会修改网站。')
        except OperationsError as exc:
            response=index_context(request,sid,error=str(exc));response.status_code=422;return response

    @app.post('/indexing/sitemap-sync')
    def sitemap_sync(request:Request,csrf:str=Form(''),sid=Depends(require_login)):
        check_csrf(sid,csrf)
        from .sitemap_sync import fetch_sitemap_paths
        try:
            result=store.add_sitemap_paths(fetch_sitemap_paths(cfg.target_domain))
            return redirect('/indexing',f"Sitemap 同步完成：发现 {result['discovered']} 页，新增 {result['added']} 页，已有 {result['existing']} 页未改动。Google 收录状态仍以实际核查为准。")
        except OperationsError as exc:
            response=index_context(request,sid,error=str(exc));response.status_code=422;return response

    @app.get('/indexing/export.csv')
    def index_export(sid=Depends(require_login)):
        rows=[['页面','网址','排名策略','Google状态','核查日期','来源','Google原始结果','技术检查日期','HTTP状态']]
        for p in indexing_summary(store)['pages']:
            g,t=p['google'],p['technical']
            rows.append([p['title'],p['url'],p['policy'],p['state_label'],g['checked_at'] if g else '',
                         SOURCES.get(g['source'],'') if g else '',g['payload'].get('coverage','') if g else '',
                         t['checked_at'] if t else '',t['payload'].get('http_status','') if t else ''])
        return csv_response('core-page-index-status.csv',rows)

    @app.get('/indexing/{page_id}')
    def index_detail(request:Request,page_id:int,msg:str='',sid=Depends(require_login)):
        return index_context(request,sid,page_id=page_id,msg=msg[:300])

    @app.post('/indexing/{page_id}/technical')
    def technical_check(page_id:int,csrf:str=Form(''),sid=Depends(require_login)):
        check_csrf(sid,csrf)
        page=store.page(page_id)
        if not page: raise HTTPException(404,'找不到本站的这个页面。')
        payload=check_public_page(cfg.target_domain,page['path'])
        store.check(page_id,'technical','public_http',payload)
        return redirect(f'/indexing/{page_id}','公开页面技术检查已保存。它不代表 Google 已收录；Google 状态保持独立。')

    @app.post('/indexing/{page_id}/google')
    def google_check(request:Request,page_id:int,csrf:str=Form(''),sid=Depends(require_login)):
        check_csrf(sid,csrf)
        page=store.page(page_id)
        if not page: raise HTTPException(404,'找不到本站的这个页面。')
        try:
            if not google.connected(): raise GoogleError('Google API 尚未授权。可先在 Search Console 手工核查并录入结果。')
            site=google.settings().get('site','')
            result=google_result(google.inspect_url('https://'+cfg.target_domain+page['path']),site)
            store.check(page_id,'google','google_api',result)
            return redirect(f'/indexing/{page_id}','Google 收录快照已保存；未提交收录申请，也未修改网站。')
        except GoogleError as exc:
            # Preserve the last known Google result; errors never turn it into zero/unknown.
            store.check(page_id,'google_error','google_api',{'error':str(exc)})
            response=index_context(request,sid,page_id=page_id,error=str(exc));response.status_code=422;return response

    @app.post('/indexing/{page_id}/manual')
    def manual_check(request:Request,page_id:int,state:str=Form(''),checked_date:str=Form(''),coverage:str=Form(''),note:str=Form(''),csrf:str=Form(''),sid=Depends(require_login)):
        check_csrf(sid,csrf)
        if not store.page(page_id): raise HTTPException(404,'找不到本站的这个页面。')
        try:
            dates(checked_date,checked_date,cfg.timezone)
            if state not in INDEX_STATES or not coverage.strip() or len(coverage)>500 or len(note)>1000:
                raise OperationsError('请选择状态，并粘贴 Search Console 原始结果（500 字以内）。')
            payload=dict(state=state,coverage=coverage.strip(),note=note.strip(),resource=google.settings().get('site',''))
            store.check(page_id,'google','manual_ui',payload,datetime.fromisoformat(checked_date).replace(tzinfo=tz).isoformat())
            return redirect(f'/indexing/{page_id}','人工核查已追加保存；之前的检查记录保留。')
        except OperationsError as exc:
            response=index_context(request,sid,page_id=page_id,error=str(exc),manual_form=dict(state=state,
                checked_date=checked_date,coverage=coverage,note=note));response.status_code=422;return response

    return store
