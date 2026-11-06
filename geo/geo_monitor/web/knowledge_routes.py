"""Authenticated document management. No third-party AI or automatic publishing."""
from urllib.parse import urlencode
from fastapi import Depends,File,Form,HTTPException,Request,UploadFile
from fastapi.responses import FileResponse,RedirectResponse
from .knowledge import KnowledgeError,KnowledgeStore,STATES,extract_file
from .knowledge_extract import MAX_FILE

def register_knowledge_routes(app,require_login,render,check_csrf):
    store=KnowledgeStore();app.state.knowledge=store
    def redirect(path,msg):return RedirectResponse(path+'?'+urlencode({'msg':msg}),303)
    def document(id):
        item=store.get(id)
        if not item:raise HTTPException(404,'资料不存在。')
        return item
    def edit_page(request,sid,item=None,values=None,error=''):
        return render(request,'knowledge_edit.html',sid,item=item,values=values if values is not None else item or {},categories=store.categories(),error=error)
    def detail(request,sid,id,error='',msg=''):
        item=document(id)
        return render(request,'knowledge_detail.html',sid,item=item,states=STATES,history=store.history(id),revisions=store.revisions(id),error=error,msg=msg[:300])
    @app.get('/knowledge')
    def listing(request:Request,q:str='',category:str='',status:str='approved',page:int=1,msg:str='',sid=Depends(require_login)):
        try:result=store.search(q,category,status,page)
        except KnowledgeError as exc:raise HTTPException(400,str(exc)) from None
        return render(request,'knowledge.html',sid,**result,summary=store.summary(),categories=store.categories(),states=STATES,q=q,category=category,status=status,msg=msg[:300],query=lambda p:'/knowledge?'+urlencode(dict(q=q,category=category,status=status,page=p)))
    @app.get('/knowledge/new')
    def new(request:Request,sid=Depends(require_login)):return edit_page(request,sid)
    @app.post('/knowledge/new')
    async def upload(request:Request,file:UploadFile=File(...),sid=Depends(require_login),csrf:str=Form('')):
        check_csrf(sid,csrf);form=await request.form()
        try:
            data=await file.read(MAX_FILE+1);attachment=extract_file(data,file.filename or '')
            id=store.save(form,attachment,data)
            return redirect('/knowledge/'+str(id),'资料已保存为草稿，请核对正文并提交审核。')
        except KnowledgeError as exc:return edit_page(request,sid,values=form,error=str(exc))
        finally:await file.close()
    @app.get('/knowledge/categories')
    def categories(request:Request,msg:str='',sid=Depends(require_login)):
        return render(request,'knowledge_categories.html',sid,categories=store.categories(),msg=msg[:300],error='')
    @app.post('/knowledge/categories')
    async def save_category(request:Request,sid=Depends(require_login)):
        form=await request.form();check_csrf(sid,form.get('csrf'))
        try:
            id=int(form['category_id']) if form.get('category_id') else None
            store.category(form.get('name',''),id,int(form.get('version','0')) if id else None)
            return redirect('/knowledge/categories','分类已保存，资料与历史版本保留。')
        except (KnowledgeError,ValueError) as exc:
            return render(request,'knowledge_categories.html',sid,categories=store.categories(),msg='',error=str(exc) if isinstance(exc,KnowledgeError) else '分类版本无效，请刷新。')
    @app.get('/knowledge/{id}')
    def view(request:Request,id:int,msg:str='',sid=Depends(require_login)):return detail(request,sid,id,msg=msg)
    @app.get('/knowledge/{id}/edit')
    def edit(request:Request,id:int,sid=Depends(require_login)):
        item=document(id)
        if item['status'] in {'pending','archived'}:return redirect('/knowledge/'+str(id),'请先撤回审核或恢复归档，再编辑资料。')
        return edit_page(request,sid,item)
    @app.post('/knowledge/{id}/edit')
    async def update(request:Request,id:int,file:UploadFile|None=File(None),sid=Depends(require_login),csrf:str=Form('')):
        check_csrf(sid,csrf);form=await request.form();item=document(id)
        try:
            data=await file.read(MAX_FILE+1) if file and file.filename else None
            attachment=extract_file(data,file.filename) if data is not None else None
            # A replacement file starts with its own extracted text, then can be edited.
            values=dict(form)
            if attachment:values['body']=attachment['body']
            store.save(values,attachment,data,id,int(form.get('version','0')))
            return redirect('/knowledge/'+str(id),'新版本已保存为草稿；旧版本和审核历史保留，请重新提交审核。')
        except (KnowledgeError,ValueError) as exc:return edit_page(request,sid,item,form,str(exc) if isinstance(exc,KnowledgeError) else '版本无效，请刷新。')
        finally:
            if file:await file.close()
    @app.post('/knowledge/{id}/workflow')
    async def workflow(request:Request,id:int,sid=Depends(require_login)):
        document(id);form=await request.form();check_csrf(sid,form.get('csrf'))
        try:
            store.transition(id,int(form.get('version','0')),str(form.get('action','')),form.get('reviewer',''),form.get('note',''))
            return redirect('/knowledge/'+str(id),'状态已更新，操作已记入历史。')
        except (KnowledgeError,ValueError) as exc:return detail(request,sid,id,error=str(exc) if isinstance(exc,KnowledgeError) else '版本无效，请刷新。')
    @app.get('/knowledge/{id}/download')
    def download(id:int,revision:int|None=None,sid=Depends(require_login)):
        document(id)
        try:path,name=store.download(id,revision)
        except KnowledgeError as exc:raise HTTPException(404,str(exc)) from None
        return FileResponse(path,media_type='application/octet-stream',filename=name,content_disposition_type='attachment')
    @app.get('/knowledge/{id}/revisions/{revision}')
    def old_revision(request:Request,id:int,revision:int,sid=Depends(require_login)):
        item=document(id);old=store.revision(id,revision)
        if not old:raise HTTPException(404,'资料版本不存在。')
        return render(request,'knowledge_revision.html',sid,item=item,revision=old)
    return store
