"""Read-only Google reporting and server-side OAuth credentials."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlencode, quote

import httpx

SCOPES = 'https://www.googleapis.com/auth/analytics.readonly https://www.googleapis.com/auth/webmasters.readonly'


class GoogleError(Exception):
    pass


class GoogleData:
    def __init__(self):
        self.folder = Path(os.environ.get('GOOGLE_PRIVATE_DIR', 'data/.google-private'))
        self.lock = threading.RLock()
        self.pending = {}

    def read(self, name):
        p = self.folder / name
        return json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else {}

    def write(self, name, value):
        self.folder.mkdir(parents=True, exist_ok=True)
        p = self.folder / name
        tmp = p.with_suffix('.tmp')
        tmp.write_text(json.dumps(value), encoding='utf-8')
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(p)

    def client(self):
        data = self.read('client.json')
        return data.get('web', {})

    def settings(self):
        return self.read('settings.json')

    def connected(self):
        return bool(self.read('tokens.json').get('refresh_token') or self.read('tokens.json').get('access_token'))

    def begin(self, sid, redirect):
        client = self.client()
        if not client.get('client_id') or not client.get('client_secret'):
            raise GoogleError('请先按页面说明放置 Google OAuth 客户端文件。')
        if redirect not in client.get('redirect_uris', []):
            raise GoogleError('OAuth 客户端未登记当前回调地址，请按页面说明添加。')
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if time.time() - v[3] < 600}
            self.pending[state] = (sid, verifier, redirect, time.time())
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(dict(
            client_id=client['client_id'], redirect_uri=redirect, response_type='code', scope=SCOPES,
            access_type='offline', prompt='consent', state=state,
            code_challenge=challenge, code_challenge_method='S256'))

    def token_request(self, body):
        client = self.client()
        try:
            r = httpx.post('https://oauth2.googleapis.com/token', data={**body,
                'client_id': client['client_id'], 'client_secret': client['client_secret']}, timeout=25)
            if r.status_code != 200:
                raise GoogleError('Google 授权已失效或客户端配置不正确，请重新连接。')
            t = r.json()
            t['expires_at'] = time.time() + int(t.get('expires_in', 3600))
            return t
        except (httpx.HTTPError, KeyError, ValueError):
            raise GoogleError('无法连接 Google 授权服务，请检查网络及客户端配置。') from None

    def finish(self, sid, state, code):
        with self.lock:
            entry = self.pending.pop(state, None)
            if not entry or entry[0] != sid or time.time() - entry[3] > 600:
                raise GoogleError('授权请求已过期或与当前登录不匹配，请重新连接。')
            t = self.token_request(dict(grant_type='authorization_code', code=code,
                                      code_verifier=entry[1], redirect_uri=entry[2]))
            if not t.get('refresh_token'):
                raise GoogleError('未获得离线授权，请撤销旧授权后重新连接。')
            self.write('tokens.json', t)

    def token(self):
        with self.lock:
            t = self.read('tokens.json')
            if not t:
                raise GoogleError('尚未连接 Google 账号。')
            if t.get('expires_at', 0) < time.time() + 60:
                new = self.token_request(dict(grant_type='refresh_token', refresh_token=t.get('refresh_token', '')))
                t.update(new)
                self.write('tokens.json', t)
            return t['access_token']

    def report(self, url, body):
        try:
            r = httpx.post(url, json=body, headers={'Authorization': 'Bearer ' + self.token()}, timeout=25)
            if r.status_code != 200:
                hints = {401: '授权失效，请重新连接。', 403: '请确认已启用 API，且授权账号有该资源的查看权限。',
                         404: '资源不存在，请核对资源 ID。', 429: '调用额度暂时受限，请稍后再试。'}
                raise GoogleError(hints.get(r.status_code, 'Google 未能返回报告，请检查资源配置后重试。'))
            return r.json()
        except (httpx.HTTPError, ValueError):
            raise GoogleError('Google 数据读取超时或网络不可用，请稍后重试。') from None

    def analytics(self, start, end):
        prop = self.settings().get('property', '')
        if not prop.isdigit():
            raise GoogleError('请先填写 GA4 数字资源 ID。')
        url = f'https://analyticsdata.googleapis.com/v1beta/properties/{prop}:runReport'
        metrics = ['activeUsers', 'sessions', 'screenPageViews', 'keyEvents']
        def run(dims, names=metrics, limit=10):
            body = {'dateRanges': [{'startDate': start, 'endDate': end}],
                    'metrics': [{'name': m} for m in names], 'dimensions': [{'name': d} for d in dims],
                    'limit': limit}
            if dims == ['date']:
                body['orderBys'] = [{'dimension': {'dimensionName': 'date'}}]
            elif dims:
                body['orderBys'] = [{'metric': {'metricName': names[0]}, 'desc': True}]
            data = self.report(url, body)
            return [{'label': ' / '.join(x['value'] for x in r.get('dimensionValues', [])),
                     'values': [float(x['value']) for x in r['metricValues']]} for r in data.get('rows', [])]
        total = run([])
        return {'total': total[0]['values'] if total else [0]*4, 'daily': run(['date'], ['sessions'], 90),
                'tables': [('流量来源 / 媒介', run(['sessionSourceMedium'], ['sessions'])),
                           ('热门页面', run(['pagePath'], ['screenPageViews'])),
                           ('访客国家', run(['country'], ['activeUsers']))]}

    def search(self, start, end):
        site = self.settings().get('site', '')
        if not site:
            raise GoogleError('请先填写 Search Console 网站资源。')
        url = 'https://www.googleapis.com/webmasters/v3/sites/' + quote(site, safe='') + '/searchAnalytics/query'
        def run(dims, limit=10):
            data = self.report(url, {'startDate': start, 'endDate': end, 'dimensions': dims,
                                     'type': 'web', 'dataState': 'final', 'rowLimit': limit})
            return [{'label': ' / '.join(r.get('keys', [])),
                     'values': [r['clicks'], r['impressions'], r['ctr'], r['position']]} for r in data.get('rows', [])]
        total = run([])
        return {'total': total[0]['values'] if total else [0]*4,
                'daily': sorted(run(['date'], 90), key=lambda r: r['label']),
                'tables': [('搜索关键词', run(['query'])), ('搜索落地页', run(['page'])),
                           ('搜索用户国家', run(['country']))]}

    def search_terms(self, start, end):
        site = self.settings().get('site','')
        if not site:
            raise GoogleError('请先填写 Search Console 网站资源。')
        url='https://www.googleapis.com/webmasters/v3/sites/'+quote(site,safe='')+'/searchAnalytics/query'
        data=self.report(url,{'startDate':start,'endDate':end,'dimensions':['query'],
                             'type':'web','dataState':'final','rowLimit':1000})
        return [{'label':r['keys'][0],'values':[r['clicks'],r['impressions'],r['ctr'],r['position']]}
                for r in data.get('rows',[]) if r.get('keys')]

    def inquiry_events(self, start, end):
        from .inquiries import EVENTS
        prop=self.settings().get('property','')
        if not prop.isdigit(): raise GoogleError('请先填写 GA4 数字资源 ID。')
        data=self.report(f'https://analyticsdata.googleapis.com/v1beta/properties/{prop}:runReport',
            {'dateRanges':[{'startDate':start,'endDate':end}],
             'dimensions':[{'name':'date'},{'name':'eventName'}], 'metrics':[{'name':'eventCount'}],
             'dimensionFilter':{'filter':{'fieldName':'eventName','inListFilter':{'values':list(EVENTS)}}},
             'orderBys':[{'dimension':{'dimensionName':'date'}}], 'limit':10000})
        rows=[]
        for row in data.get('rows',[]):
            d,event=[v['value'] for v in row['dimensionValues']]
            rows.append(dict(date=d[:4]+'-'+d[4:6]+'-'+d[6:8],event=event,
                             count=int(row['metricValues'][0]['value']),kind='unknown',page=''))
        return {'rows':rows,'source':'api','ignored':0}

    def realtime_inquiry_events(self):
        from .inquiries import EVENTS
        from datetime import datetime,timezone
        prop=self.settings().get('property','')
        if not prop.isdigit():raise GoogleError('请先填写 GA4 数字资源 ID。')
        data=self.report(f'https://analyticsdata.googleapis.com/v1beta/properties/{prop}:runRealtimeReport',
            {'dimensions':[{'name':'eventName'},{'name':'minutesAgo'}], 'metrics':[{'name':'eventCount'}],
             'dimensionFilter':{'filter':{'fieldName':'eventName','inListFilter':{'values':list(EVENTS)}}},
             'minuteRanges':[{'startMinutesAgo':29,'endMinutesAgo':0}], 'limit':1000})
        totals=dict.fromkeys(EVENTS,0);rows=[]
        for row in data.get('rows',[]):
            event,minute=[x['value'] for x in row['dimensionValues']]
            count=int(row['metricValues'][0]['value'])
            if event not in totals:continue
            totals[event]+=count;rows.append(dict(event=event,minutes_ago=int(minute),count=count))
        return dict(totals=totals,rows=rows,checked_at=datetime.now(timezone.utc).isoformat(),property=prop)

    def inspect_url(self, url):
        from .indexing import covers
        site=self.settings().get('site','')
        if not site or not covers(site,url): raise GoogleError('当前 Search Console 资源不覆盖这个网址，请核对资源设置。')
        return self.report('https://searchconsole.googleapis.com/v1/urlInspection/index:inspect',
                           {'inspectionUrl':url,'siteUrl':site,'languageCode':'zh-CN'})
