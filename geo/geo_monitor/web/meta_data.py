"""Meta read-only OAuth and reporting. Credentials never enter templates or snapshots."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlencode, urlsplit

from cryptography.fernet import Fernet, InvalidToken
import httpx

GRAPH_VERSION = 'v26.0'
SCOPES = ('pages_show_list', 'pages_read_engagement',
          'instagram_basic', 'instagram_manage_insights')
CALLBACK_PATH = '/social/meta/callback'


class MetaError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class OAuthLogFilter(logging.Filter):
    def filter(self, record):
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2].split('?', 1)[0]
            if path in (CALLBACK_PATH, '/google/callback'):
                record.args = (*args[:2], path, *args[3:])
        return True


def nonnegative(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def timestamp(value):
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return date if date.tzinfo else date.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def public_link(value, hosts):
    try:
        p = urlsplit(value or '')
        if (p.scheme == 'https' and not p.username and not p.password and not p.port
                and any(p.hostname == h or (p.hostname or '').endswith('.'+h) for h in hosts)):
            return value
    except ValueError:
        pass
    return ''


class MetaData:
    def __init__(self, transport=None):
        self.folder = Path(os.environ.get('META_PRIVATE_DIR', 'data/.meta-private'))
        self.report_folder = Path(os.environ.get('SOCIAL_DATA_DIR', 'data/social-media'))
        self.lock = threading.RLock()
        self.sync_lock = threading.Lock()
        self.pending = {}
        self.transport = transport

    def _vault(self):
        p = self.folder/'credentials.enc'
        if not p.exists():
            return {}
        try:
            return json.loads(Fernet((self.folder/'encryption.key').read_bytes()).decrypt(p.read_bytes()))
        except (OSError, ValueError, InvalidToken):
            raise MetaError('无法读取 Meta 本地凭据，请保留原文件及加密密钥，检查备份或重新配置。') from None

    def _write(self, value):
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            self.folder.chmod(0o700)
        except OSError:
            pass
        key_path = self.folder/'encryption.key'
        if not key_path.exists():
            key_path.write_bytes(Fernet.generate_key())
            key_path.chmod(0o600)
        raw = Fernet(key_path.read_bytes()).encrypt(json.dumps(value).encode())
        tmp = self.folder/'credentials.tmp'
        tmp.write_bytes(raw)
        tmp.chmod(0o600)
        tmp.replace(self.folder/'credentials.enc')

    def client(self):
        with self.lock:
            return self._vault().get('client', {})

    def status(self):
        with self.lock:
            vault = self._vault()
        client, token = vault.get('client', {}), vault.get('connection', {})
        authorized = bool(token.get('user_token') and token.get('app_id') == client.get('app_id'))
        pages = token.get('pages', []) if authorized else []
        selected = next((p for p in pages if p['id'] == token.get('selected_page')), None)
        return {'configured':bool(client.get('app_id') and client.get('app_secret')),
                'app_id':client.get('app_id',''), 'graph_version':client.get('graph_version',GRAPH_VERSION),
                'redirect_uri':client.get('redirect_uri',os.environ.get('META_REDIRECT_URI',
                    os.environ.get('WEB_PUBLIC_ORIGIN','http://localhost:8000').rstrip('/')+CALLBACK_PATH)),
                'config_id':client.get('config_id',''), 'authorized':authorized,
                'expires_at':datetime.fromtimestamp(token['expires_at'],timezone.utc).isoformat() if authorized and token.get('expires_at') else None,
                'expired':bool(authorized and token.get('expires_at') and token['expires_at']<=time.time()),
                'permissions':token.get('permissions',[]) if authorized else [],
                'selected_page':selected['id'] if selected else '',
                'selected_name':selected['name'] if selected else '',
                'instagram_id':selected.get('instagram_id','') if selected else '',
                'pages':[{k:p.get(k,'') for k in ['id','name','instagram_id','instagram_username']} for p in pages]}

    def save_client(self, app_id, app_secret, redirect_uri, graph_version=GRAPH_VERSION, config_id=''):
        app_id, app_secret, graph_version = app_id.strip(), app_secret.strip(), graph_version.strip()
        config_id, redirect_uri = config_id.strip(), redirect_uri.strip()
        if os.environ.get('WEB_DEPLOY_MODE') == 'production' and redirect_uri != os.environ.get('WEB_PUBLIC_ORIGIN','')+CALLBACK_PATH:
            raise MetaError('正式环境只能登记当前后台的 HTTPS 回调地址。')
        try:
            p = urlsplit(redirect_uri)
            if (not re.fullmatch(r'\d{5,30}',app_id) or not re.fullmatch(r'v\d{2}\.\d',graph_version)
                    or (config_id and not config_id.isdigit()) or p.path != CALLBACK_PATH or p.query or p.fragment
                    or p.username or p.password or not p.hostname
                    or (p.scheme != 'https' and not (p.scheme == 'http' and p.hostname in {'localhost','127.0.0.1'}))):
                raise ValueError()
            p.port
        except ValueError:
            raise MetaError('请填写数字应用 ID、有效 API 版本，以及本站的 HTTPS 回调地址；本机可使用 localhost 或 127.0.0.1。') from None
        with self.lock:
            vault = self._vault()
            old = vault.get('client', {})
            if not app_secret and old.get('app_id') == app_id:
                app_secret = old.get('app_secret','')
            if not 16 <= len(app_secret) <= 256 or any(ch.isspace() for ch in app_secret):
                raise MetaError('首次配置需要填写 App Secret；更换应用时也需要填写对应的新密钥。')
            vault['client'] = {'app_id':app_id,'app_secret':app_secret,'redirect_uri':redirect_uri,
                               'graph_version':graph_version,'config_id':config_id}
            self._write(vault)
            self.pending.clear()

    def begin(self, sid, origin):
        client = self.client()
        if not client.get('app_secret'):
            raise MetaError('请先创建 Meta 应用并保存应用配置。')
        p = urlsplit(client['redirect_uri'])
        if origin.rstrip('/') != f'{p.scheme}://{p.netloc}':
            raise MetaError('请使用与回调地址相同的后台域名登录，再点击连接 Meta。')
        state = secrets.token_urlsafe(32)
        with self.lock:
            self.pending = {k:v for k,v in self.pending.items() if time.time()-v[1] < 600}
            if len(self.pending) >= 100:
                raise MetaError('待完成的授权请求过多，请稍后重试。')
            self.pending[state] = (sid,time.time(),hashlib.sha256(json.dumps(client,sort_keys=True).encode()).hexdigest())
        args = {'client_id':client['app_id'],'redirect_uri':client['redirect_uri'],
                'state':state,'response_type':'code'}
        if client.get('config_id'):
            args['config_id'] = client['config_id']
        else:
            args['scope'] = ','.join(SCOPES)
            args['auth_type'] = 'rerequest'
        return f'https://www.facebook.com/{client["graph_version"]}/dialog/oauth?'+urlencode(args)

    def _request(self, path, token='', params=None, body=None):
        client = self.client()
        if not client:
            raise MetaError('请先保存 Meta 应用配置。')
        if not re.fullmatch(r'(?:me|\d+)(?:/(?:accounts|permissions|published_posts|media|insights))?|oauth/access_token',path):
            raise MetaError('不支持这个 Meta 只读请求。')
        query = dict(params or {})
        headers = {}
        if token:
            headers['Authorization'] = 'Bearer '+token
            query['appsecret_proof'] = hmac.new(client['app_secret'].encode(),token.encode(),hashlib.sha256).hexdigest()
        try:
            with httpx.Client(timeout=25,transport=self.transport,follow_redirects=False) as http:
                url = f'https://graph.facebook.com/{client["graph_version"]}/{path}'
                response = http.post(url,data=body) if body is not None else http.get(url,params=query,headers=headers)
            data = response.json()
        except (httpx.HTTPError,ValueError):
            raise MetaError('暂时无法连接 Meta，请检查网络后重试；已有历史数据会保留。') from None
        if not isinstance(data,dict):
            raise MetaError('Meta 返回的数据格式无法识别。')
        if response.status_code != 200 or 'error' in data:
            code = (data.get('error') or {}).get('code') if isinstance(data,dict) else None
            if code in {190,102}:
                raise MetaError('Meta 授权已过期或被撤销，请重新连接。',code)
            if code in {10,200,294}:
                raise MetaError('Meta 未授予这项数据的读取权限，请检查授权范围和主页访问权限。',code)
            if code in {4,17,32,613} or response.status_code == 429:
                raise MetaError('Meta 暂时限制请求频率，请稍后同步。',code)
            raise MetaError('Meta 未返回所需数据，请检查应用设置、权限和专业账号关联。',code)
        return data

    def _edge(self, path, token, fields, max_pages=5):
        after, rows = '', []
        for _ in range(max_pages):
            params = {'fields':fields,'limit':50}
            if after:
                params['after'] = after
            data = self._request(path,token,params)
            batch = data.get('data')
            if not isinstance(batch,list):
                raise MetaError('Meta 未返回可读取的数据列表。')
            rows.extend(batch)
            paging = data.get('paging',{})
            new_after = paging.get('cursors',{}).get('after','')
            if not paging.get('next'):
                return rows, False
            if not new_after or new_after == after:
                return rows, True
            after = new_after
        return rows, True

    def finish(self, sid, state, code):
        client = self.client()
        fingerprint = hashlib.sha256(json.dumps(client,sort_keys=True).encode()).hexdigest()
        with self.lock:
            entry = self.pending.pop(state,None)
        if not entry or entry[0] != sid or time.time()-entry[1] > 600 or entry[2] != fingerprint:
            raise MetaError('授权请求已过期或不属于当前登录，请重新连接。')
        if not code or len(code)>4096:
            raise MetaError('Meta 未返回有效授权码，请重试。')
        short = self._request('oauth/access_token',body={'client_id':client['app_id'],
                 'client_secret':client['app_secret'],'redirect_uri':client['redirect_uri'],'code':code})
        if not short.get('access_token'):
            raise MetaError('Meta 未返回访问凭据，请重新连接。')
        long = self._request('oauth/access_token',body={'client_id':client['app_id'],
                'client_secret':client['app_secret'],'grant_type':'fb_exchange_token',
                'fb_exchange_token':short['access_token']})
        token = long.get('access_token')
        if not token:
            raise MetaError('Meta 未返回长期访问凭据，请重新连接。')
        grants = self._request('me/permissions',token).get('data',[])
        permissions = [p.get('permission') for p in grants if p.get('status') == 'granted']
        if any(p in permissions for p in {'pages_manage_posts','instagram_content_publish',
                'pages_messaging','instagram_manage_messages','ads_management','pages_manage_engagement'}):
            raise MetaError('这次授权包含发布、私信或广告管理权限，请将 Business Login 配置改为本页的读取权限后重新连接。')
        if not {'pages_show_list','pages_read_engagement'}.issubset(permissions):
            raise MetaError('需要允许列出主页和读取主页数据，才能连接 Facebook。')
        pages, limited = self._edge('me/accounts',token,'id,name,access_token,tasks')
        if limited:
            raise MetaError('可管理主页超过本次读取范围，请通过 Meta 授权页仅选择当前品牌主页后重试。')
        selected = []
        for page in pages:
            if not re.fullmatch(r'\d+',str(page.get('id',''))) or not page.get('access_token'):
                continue
            item = {'id':page['id'],'name':page.get('name','Facebook Page'),'token':page['access_token']}
            if {'instagram_basic','instagram_manage_insights'}.issubset(permissions):
                try:
                    ig = self._request(page['id'],page['access_token'],{'fields':'instagram_business_account{id,username}'}).get('instagram_business_account') or {}
                    if re.fullmatch(r'\d+',str(ig.get('id',''))):
                        item.update(instagram_id=ig['id'],instagram_username=ig.get('username',''))
                except MetaError:
                    pass
            selected.append(item)
        if not selected:
            raise MetaError('未找到可管理的 Facebook 主页，请用拥有 Demo Works 主页访问权限的账号重新授权。')
        with self.lock:
            vault = self._vault()
            if vault.get('client') != client:
                raise MetaError('应用设置已变化，请重新授权。')
            prior = vault.get('connection',{}).get('selected_page')
            expiry = nonnegative(long.get('expires_in'))
            vault['connection'] = {'app_id':client['app_id'],'user_token':token,'permissions':permissions,
                'expires_at':time.time()+expiry if expiry else None,'pages':selected,
                'selected_page':prior if any(p['id']==prior for p in selected) else '',
                'connected_at':datetime.now(timezone.utc).isoformat()}
            self._write(vault)

    def select_page(self, page_id):
        with self.lock:
            vault = self._vault()
            connection = vault.get('connection',{})
            if (connection.get('app_id') != vault.get('client',{}).get('app_id')
                    or not any(p['id']==page_id for p in connection.get('pages',[]))):
                raise MetaError('请选择本次授权中可管理的主页。')
            connection['selected_page'] = page_id
            vault['last_selected_page'] = page_id
            self._write(vault)

    def disconnect(self):
        with self.lock:
            vault = self._vault()
            vault.pop('connection',None)
            self._write(vault)
            self.pending.clear()

    @contextmanager
    def _reports(self):
        self.report_folder.mkdir(parents=True,exist_ok=True)
        db = sqlite3.connect(self.report_folder/'meta-insights.db',timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute('''CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY, page_id TEXT NOT NULL, platform TEXT NOT NULL,
                    collected_at TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, payload TEXT NOT NULL)''')
                yield db
        finally:
            db.close()

    def latest(self):
        if not (self.report_folder/'meta-insights.db').exists():
            return {}
        state = self.status()
        with self.lock:
            previous = self._vault().get('last_selected_page','')
        page_id = state['selected_page'] or (previous if not state['authorized'] else '')
        if not page_id:
            return {}
        with self._reports() as db:
            rows = db.execute('SELECT * FROM snapshots WHERE page_id=? ORDER BY id DESC LIMIT 100',(page_id,)).fetchall()
        reports = {}
        for row in rows:
            if row['platform'] not in reports:
                reports[row['platform']] = {**json.loads(row['payload']),'collected_at':row['collected_at'],
                    'start':row['start'],'end':row['end']}
        return reports

    def _save_report(self, page_id, platform, start, end, report):
        with self._reports() as db:
            db.execute('INSERT INTO snapshots(page_id,platform,collected_at,start,end,payload) VALUES(?,?,?,?,?,?)',
                (page_id,platform,datetime.now(timezone.utc).isoformat(),start.date().isoformat(),
                 (end-timedelta(days=1)).date().isoformat(),json.dumps(report,ensure_ascii=False)))

    def sync(self):
        if not self.sync_lock.acquire(blocking=False):
            raise MetaError('同步正在进行，请稍候刷新。')
        try:
            return self._sync()
        finally:
            self.sync_lock.release()

    def _sync(self):
        with self.lock:
            vault = self._vault()
        connection = vault.get('connection',{})
        if connection.get('app_id') != vault.get('client',{}).get('app_id'):
            raise MetaError('请先连接当前 Meta 应用。')
        page = next((p for p in connection.get('pages',[]) if p['id']==connection.get('selected_page')),None)
        if not page:
            raise MetaError('请先授权并选择 Demo Works 的 Facebook 主页。')
        if connection.get('expires_at') and connection['expires_at'] <= time.time():
            raise MetaError('Meta 授权已到期，请重新连接。')
        end = datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
        start = end-timedelta(days=28)
        profile = self._request(page['id'],page['token'],{'fields':'id,name,link,followers_count,fan_count'})
        fb = {'name':profile.get('name',page['name']),'followers':nonnegative(profile.get('followers_count')),
              'page_likes':nonnegative(profile.get('fan_count')),'interactions':None,'posts':[], 'limited':False,'warnings':[]}
        try:
            posts, limited = self._edge(page['id']+'/published_posts',page['token'],
                'id,permalink_url,message,created_time,reactions.limit(0).summary(true),comments.limit(0).summary(true),shares')
            fb['limited'] = limited
            for post in posts:
                date = timestamp(post.get('created_time'))
                if not date or not start <= date < end:
                    continue
                reactions = nonnegative(post.get('reactions',{}).get('summary',{}).get('total_count'))
                comments = nonnegative(post.get('comments',{}).get('summary',{}).get('total_count'))
                fb['posts'].append({'date':date.isoformat(),'text':str(post.get('message',''))[:300],
                    'url':public_link(post.get('permalink_url'),('facebook.com',)), 'likes':reactions,
                    'comments':comments,'shares':nonnegative(post.get('shares',{}).get('count'))})
            if all(p['likes'] is not None and p['comments'] is not None for p in fb['posts']):
                fb['interactions'] = sum(p['likes']+p['comments'] for p in fb['posts'])
        except MetaError as exc:
            if exc.code in {190,102}:
                raise
            fb['warnings'].append(str(exc))
        self._save_report(page['id'],'facebook',start,end,fb)
        messages = ['Facebook 已同步']
        if not page.get('instagram_id'):
            return '；'.join(messages+['未找到关联的 Instagram 专业账号，请检查主页关联和 Instagram 读取权限。'])
        try:
            ig_id = page['instagram_id']
            profile = self._request(ig_id,page['token'],{'fields':'id,username,name,followers_count,media_count'})
            ig = {'name':profile.get('username',page.get('instagram_username','Instagram')),
                  'followers':nonnegative(profile.get('followers_count')),'media_count':nonnegative(profile.get('media_count')),
                  'interactions':None,'posts':[],'limited':False,'warnings':[]}
            try:
                insight = self._request(ig_id+'/insights',page['token'],{'metric':'total_interactions','period':'day',
                    'metric_type':'total_value','since':int(start.timestamp()),'until':int(end.timestamp())})
                metric = next((r for r in insight.get('data',[]) if r.get('name')=='total_interactions'),{})
                ig['interactions'] = nonnegative(metric.get('total_value',{}).get('value'))
                if ig['interactions'] is None:
                    ig['warnings'].append('Meta 未提供该时段的互动统计；未返回的数据不会补零。')
            except MetaError as exc:
                if exc.code in {190,102}:
                    raise
                ig['warnings'].append(str(exc))
            try:
                posts, ig['limited'] = self._edge(ig_id+'/media',page['token'],'id,caption,permalink,timestamp,like_count,comments_count,media_type')
                for post in posts:
                    date = timestamp(post.get('timestamp'))
                    if date and start <= date < end:
                        ig['posts'].append({'date':date.isoformat(),'text':str(post.get('caption',''))[:300],
                            'url':public_link(post.get('permalink'),('instagram.com',)),
                            'likes':nonnegative(post.get('like_count')),'comments':nonnegative(post.get('comments_count'))})
            except MetaError as exc:
                if exc.code in {190,102}:
                    raise
                ig['warnings'].append(str(exc))
            self._save_report(page['id'],'instagram',start,end,ig)
            messages.append('Instagram 已同步')
        except MetaError as exc:
            messages.append('Instagram 暂未更新：'+str(exc))
        return '；'.join(messages)+'。'
