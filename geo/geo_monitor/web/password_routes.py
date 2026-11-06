"""Authenticated self-service password change. No business database writes."""
import hmac
from fastapi import Depends,Request,HTTPException
from fastapi.responses import RedirectResponse
from .auth import COOKIE
from .password_store import OldPasswordError,PasswordPolicyError

def register_password_routes(app,cfg,auth,require_login,render,check_csrf,client_ip):
    def page(request,sid,*,error='',status=200):
        response=render(request,'password_settings.html',sid,error=error,**auth.password_state())
        response.status_code=status;return response

    @app.get('/settings')
    def settings(request:Request,sid=Depends(require_login)):
        return render(request,'settings.html',sid,**auth.password_state())

    @app.get('/settings/password')
    def password_settings(request:Request,sid=Depends(require_login)):
        return page(request,sid)

    @app.post('/settings/password')
    async def password_change(request:Request,sid=Depends(require_login)):
        form=await request.form(max_files=0,max_fields=8)
        check_csrf(sid,form.get('csrf'))
        ip=client_ip(request)
        if auth.locked('change:'+ip):return page(request,sid,error='旧密码验证失败次数过多，请 15 分钟后重试。',status=429)
        fields=[form.get(k,'') for k in ['old_password','new_password','confirm_password']]
        if any(not isinstance(value,str) for value in fields):raise HTTPException(422,'密码字段格式无效。')
        old,new,confirm=fields
        if not old:return page(request,sid,error='请填写旧密码。',status=422)
        if len(old)>1024 or len(new)>128 or len(confirm)>128:return page(request,sid,error='密码长度超出允许范围。',status=422)
        if not hmac.compare_digest(new.encode(),confirm.encode()):return page(request,sid,error='两次输入的新密码不一致，请重新填写。',status=422)
        try:
            if not auth.change_password(ip,old,new):return page(request,sid,error='旧密码验证失败次数过多，请 15 分钟后重试。',status=429)
        except (OldPasswordError,PasswordPolicyError) as exc:return page(request,sid,error=str(exc),status=422)
        response=RedirectResponse('/login?password_changed=1',303)
        response.delete_cookie(COOKIE,path='/',secure=cfg.web_cookie_secure,httponly=True,samesite='lax')
        return response
