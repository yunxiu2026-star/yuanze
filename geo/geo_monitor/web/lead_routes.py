"""Private inquiry workflow; no automatic email, website writes or ad uploads."""
import csv
from datetime import datetime
import hashlib
import io
import secrets
import threading
import time
from urllib.parse import urlencode
import uuid

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from .leads import EVIDENCE, FIELDS, KINDS, STAGES, LeadError, LeadStore, csv_safe, parse_file, select_rows
from .website_inquiries import WebsiteSync, TAG_LABELS, filter_buyer_tags

def register_lead_routes(app,cfg,require_login,render,check_csrf,tz):
    store=LeadStore(cfg.target_domain);previews={};lock=threading.Lock()
    app.state.leads=store
    sync=WebsiteSync(store);app.state.website_sync=sync

    def redirect(path,msg):return RedirectResponse(path+'?'+urlencode({'msg':msg}),303)
    def today():return datetime.now(tz).date().isoformat()
    def context():return dict(stages=STAGES,kinds=KINDS,evidence=EVIDENCE,today=today())
    def detail(request,sid,id,*,error='',msg='',submitted=None):
        lead=store.get(id)
        if not lead:raise HTTPException(404,'询盘不存在。')
        lead=sync.decorate([lead])[0]
        return render(request,'lead_detail.html',sid,lead=lead,history=store.history(id),error=error,
                      msg=msg[:300],submitted=submitted,receipt=sync.snapshot(id),**context())

    def list_page(request,sid,q='',stage='',kind='customer',owner='',due='',archive='active',page=1,msg='',category='',route='',volume='',guide='',ad_source=''):
        filters=dict(q=q,stage=stage,kind=kind,owner=owner,due=due,archive=archive)
        try:rows=select_rows(store,**filters,today=today())
        except LeadError as exc:raise HTTPException(400,str(exc)) from None
        rows=sync.decorate(rows)
        tag_options={k:sorted({v for r in rows for v in r['buyer_tags'][k]},key=str.casefold) for k in TAG_LABELS}
        tag_filters=dict(category=category,route=route,volume=volume,guide=guide,ad_source=ad_source)
        if any(len(v)>300 for v in tag_filters.values()):raise HTTPException(400,'标签过长。')
        rows=filter_buyer_tags(rows,tag_filters);filters.update(tag_filters)
        pages=max(1,(len(rows)+24)//25);page=min(max(page,1),pages)
        def query(**kw):return '/leads?'+urlencode({**filters,'page':page,**kw})
        return render(request,'leads.html',sid,rows=rows[(page-1)*25:page*25],count=len(rows),
            tag_options=tag_options,tag_labels=TAG_LABELS,summary=store.summary(today()),website_sync=sync.status(),filters=filters,page=page,pages=pages,query=query,
            owners=sorted({r['owner'] for r in store.all() if r['owner']}),msg=msg[:300],**context())

    @app.get('/leads')
    def leads(request:Request,stage:str='',kind:str='customer',due:str='',archive:str='active',msg:str='',category:str='',route:str='',volume:str='',guide:str='',ad_source:str='',q:str='',owner:str='',page:int=1,sid=Depends(require_login)):
        return list_page(request,sid,stage=stage,kind=kind,due=due,archive=archive,msg=msg,category=category,route=route,volume=volume,guide=guide,ad_source=ad_source,q=q,owner=owner,page=page)

    @app.post('/leads/filter')
    async def filtered(request:Request,sid=Depends(require_login)):
        form=await request.form();check_csrf(sid,form.get('csrf'))
        try:page=int(form.get('page','1'))
        except ValueError:raise HTTPException(400,'页码无效。') from None
        return list_page(request,sid,**{k:str(form.get(k,v)) for k,v in dict(q='',stage='',kind='customer',owner='',due='',archive='active',category='',route='',volume='',guide='',ad_source='').items()},page=page)

    @app.get('/leads/new')
    def new(request:Request,sid=Depends(require_login)):
        return render(request,'lead_new.html',sid,values=dict(external_id='manual-'+uuid.uuid4().hex,
            received_at=today(),record_type='customer',evidence='unknown'),error='',**context())

    @app.post('/leads/new')
    async def create(request:Request,sid=Depends(require_login)):
        form=await request.form();check_csrf(sid,form.get('csrf'))
        try:
            result=store.add_many([form])
            if not result['added']:return redirect('/leads','该外部编号已存在，本次未重复建立或覆盖；可搜索编号核对。')
            return redirect('/leads/'+str(result['ids'][0]),'询盘已建立，当前状态为待核实。')
        except LeadError as exc:
            return render(request,'lead_new.html',sid,values=form,error=str(exc),**context())

    @app.get('/leads/template.csv')
    def template(sid=Depends(require_login)):
        out=io.StringIO(newline='');csv.writer(out).writerow(FIELDS)
        return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="b2b-customer-inquiries-template.csv"'})

    @app.post('/leads/import/preview')
    async def preview(request:Request,file:UploadFile=File(...),csrf:str=Form(''),sid=Depends(require_login)):
        check_csrf(sid,csrf)
        try:
            data=await file.read(5*1024*1024+1)
            records=parse_file(data,file.filename or '',store.domain)
        except LeadError as exc:return redirect('/leads',str(exc))
        finally:await file.close()
        digest=hashlib.sha256(__import__('json').dumps(records,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        token=secrets.token_urlsafe(32)
        with lock:
            for key in list(previews):
                if previews[key]['expires']<time.time() or previews[key]['sid']==sid:previews.pop(key)
            if len(previews)>=50:return redirect('/leads','导入预览繁忙，请稍后重试。')
            previews[token]=dict(sid=sid,expires=time.time()+900,records=records,digest=digest)
        existing={r['external_id'] for r in store.all()}
        added=sum(r['external_id'] not in existing for r in records)
        return render(request,'lead_import.html',sid,token=token,total=len(records),added=added,
            skipped=len(records)-added,counts={k:sum(r['record_type']==k for r in records) for k in KINDS},
            sample=records[:10],**context())

    @app.post('/leads/import/confirm')
    async def confirm(request:Request,sid=Depends(require_login)):
        form=await request.form();check_csrf(sid,form.get('csrf'));token=str(form.get('token',''))
        with lock:
            item=previews.get(token)
            if not item or item['sid']!=sid or item['expires']<time.time():
                return redirect('/leads','导入预览已过期或不属于当前登录，请重新选择文件。')
            if form.get('confirm')!='yes':return redirect('/leads','请先确认记录类型及来源依据。')
            previews.pop(token)
        result=store.add_many(item['records'],'file',item['digest'])
        return redirect('/leads',f"导入完成：新增 {result['added']} 条，跳过重复编号 {result['skipped']} 条；现有记录未覆盖。")

    @app.post('/leads/export.csv')
    async def export(request:Request,sid=Depends(require_login)):
        form=await request.form();check_csrf(sid,form.get('csrf'))
        q,stage,kind,owner,due,archive=[str(form.get(k,v)) for k,v in dict(q='',stage='',kind='customer',owner='',due='',archive='active').items()]
        try:rows=select_rows(store,q=q,stage=stage,kind=kind,owner=owner,due=due,archive=archive,today=today())
        except LeadError as exc:raise HTTPException(400,str(exc)) from None
        rows=filter_buyer_tags(sync.decorate(rows),{k:str(form.get(k,'')) for k in TAG_LABELS})
        out=io.StringIO(newline='');writer=csv.writer(out)
        fields=FIELDS+['stage','owner','next_contact','archived','origin','created_at','updated_at']
        tag_fields=['brief_'+k for k in TAG_LABELS]
        writer.writerow(fields+tag_fields)
        for r in rows:writer.writerow([csv_safe(r[k]) for k in fields]+[csv_safe(' | '.join(r['buyer_tags'][k])) for k in TAG_LABELS])
        return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="b2b-customer-inquiries.csv"'})

    @app.get('/leads/{lead_id}')
    def lead(request:Request,lead_id:int,msg:str='',sid=Depends(require_login)):
        return detail(request,sid,lead_id,msg=msg)

    @app.post('/leads/{lead_id}/follow-up')
    async def follow(request:Request,lead_id:int,sid=Depends(require_login)):
        form=await request.form();check_csrf(sid,form.get('csrf'))
        try:
            store.update(lead_id,int(form.get('version','0')),stage=str(form.get('stage','')),
                owner=str(form.get('owner','')),next_contact=str(form.get('next_contact','')),
                record_type=str(form.get('record_type','')),note=str(form.get('note','')),archived=str(form.get('archived','0')))
            return redirect('/leads/'+str(lead_id),'跟进已保存，变更记录已保留。')
        except (LeadError,ValueError) as exc:
            return detail(request,sid,lead_id,error=str(exc) if isinstance(exc,LeadError) else '记录版本无效，请刷新后重试。',submitted=form)

    @app.post('/leads/{lead_id}/correct')
    async def correct(request:Request,lead_id:int,sid=Depends(require_login)):
        form=await request.form();check_csrf(sid,form.get('csrf'))
        try:
            store.correct(lead_id,int(form.get('version','0')),form,str(form.get('correction_note','')))
            return redirect('/leads/'+str(lead_id),'资料已更正，原始资料及变更依据已保留。')
        except (LeadError,ValueError) as exc:
            return detail(request,sid,lead_id,error=str(exc) if isinstance(exc,LeadError) else '记录版本无效，请刷新后重试。')
    return store
