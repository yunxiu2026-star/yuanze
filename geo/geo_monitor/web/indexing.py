"""Google index evidence and current public-page checks remain independent."""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

import httpx
import os

from .operations_store import OperationsError

INDEX_STATES={'indexed':'已收录','not_indexed':'未收录 / 已排除','unknown':'待核查'}
SOURCES={'historical_ui':'历史人工核查','manual_ui':'人工录入核查','google_api':'Google URL Inspection API','public_http':'公开页面技术检查'}


def covers(site, url):
    p=urlsplit(url)
    if site.startswith('sc-domain:'):
        domain=site[len('sc-domain:'):].lower()
        return bool(domain and (p.hostname==domain or (p.hostname or '').endswith('.'+domain)))
    prop=urlsplit(site)
    return bool(prop.scheme=='https' and prop.hostname==p.hostname and prop.netloc==p.netloc and
                p.path.startswith(prop.path or '/'))


def google_result(data, site):
    result=data.get('inspectionResult',{})
    status=result.get('indexStatusResult',{})
    verdict=status.get('verdict','VERDICT_UNSPECIFIED')
    return dict(state='indexed' if verdict=='PASS' else 'not_indexed' if verdict in {'FAIL','NEUTRAL'} else 'unknown',
        verdict=verdict,coverage=status.get('coverageState','Google 未返回明确状态'),resource=site,
        last_crawl=status.get('lastCrawlTime',''),google_canonical=status.get('googleCanonical',''),
        user_canonical=status.get('userCanonical',''),robots=status.get('robotsTxtState',''),
        indexing=status.get('indexingState',''),fetch=status.get('pageFetchState',''))


class PageMarkup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.robots=[];self.canonicals=[];self.title='';self.in_title=False
    def handle_starttag(self,tag,attrs):
        a={k.lower():v or '' for k,v in attrs}
        if tag=='meta' and a.get('name','').lower() in {'robots','googlebot'}: self.robots.append(a.get('content',''))
        if tag=='link' and 'canonical' in a.get('rel','').lower().split(): self.canonicals.append(a.get('href',''))
        if tag=='title': self.in_title=True
    def handle_endtag(self,tag):
        if tag=='title': self.in_title=False
    def handle_data(self,data):
        if self.in_title: self.title+=data[:400]


def public_get(client,url,domain,limit=1024*1024):
    for _ in range(5):
        p=urlsplit(url)
        if p.scheme!='https' or p.hostname not in {domain,'www.'+domain} or p.username or p.password or p.port:
            raise OperationsError('检查已停止：页面重定向到本站之外或非标准 HTTPS 地址。')
        transport=url
        internal=os.getenv('MAIN_SITE_INTERNAL_ORIGIN','').rstrip('/')
        if internal:
            gateway=urlsplit(internal)
            if gateway.scheme not in {'http','https'} or not gateway.hostname or gateway.username or gateway.password or gateway.path or gateway.query or gateway.fragment:
                raise OperationsError('Invalid configured internal site origin')
            transport=internal+p.path+('?' +p.query if p.query else '')
        with client.stream('GET',transport,follow_redirects=False) as response:
            if response.status_code in {301,302,303,307,308}:
                location=response.headers.get('location')
                if not location: raise OperationsError('重定向没有返回目标地址。')
                url=urljoin(url,location);continue
            chunks=[];size=0
            for chunk in response.iter_bytes():
                size+=len(chunk)
                if size>limit: raise OperationsError('页面超过检查大小上限，本次未完成分析。')
                chunks.append(chunk)
            return response.status_code,dict(response.headers),b''.join(chunks).decode('utf-8','replace'),url
    raise OperationsError('重定向次数过多，本次未完成检查。')


def check_public_page(domain,path,client=None):
    if client is None:
        with httpx.Client(timeout=httpx.Timeout(10),headers={'User-Agent':'Demo WorksLocalAudit/1.0'},trust_env=False) as session:
            return check_public_page(domain,path,session)
    url='https://'+domain+path
    try:
        status,headers,text,final=public_get(client,url,domain)
        parser=PageMarkup();parser.feed(text)
        directives=' '.join(parser.robots+[headers.get('x-robots-tag','')]).lower()
        noindex='noindex' in directives or 'none' in directives.replace(',',' ').split()
        canonical=urljoin(final,parser.canonicals[0]) if parser.canonicals else ''
        robots_allowed=None;sitemap=None;notes=[]
        try:
            code,_,body,_=public_get(client,'https://'+domain+'/robots.txt',domain)
            if code==200:
                robot=RobotFileParser();robot.parse(body.splitlines());robots_allowed=robot.can_fetch('Googlebot',url)
            elif code==404: robots_allowed=True
            else: notes.append('robots.txt 未能确认')
        except (httpx.HTTPError,OperationsError): notes.append('robots.txt 读取失败')
        try:
            from .sitemap_sync import fetch_sitemap_paths
            paths=set(fetch_sitemap_paths(domain,client))
            sitemap=path in paths or urlsplit(final).path in paths
        except (httpx.HTTPError,OperationsError,ET.ParseError): notes.append('站点地图读取失败')
        return dict(http_status=status,final_url=final,title=parser.title.strip()[:300],noindex=noindex,
                    robots_directives=directives[:300],robots_allowed=robots_allowed,canonical=canonical,
                    sitemap=sitemap,notes=notes)
    except (httpx.HTTPError,OperationsError) as exc:
        return dict(error=str(exc) if isinstance(exc,OperationsError) else '网络连接失败或超时，本次未完成技术检查。')


def indexing_summary(store, search=None):
    histories=store.checks();pages=[]
    for page in store.pages():
        checks=[c for c in histories if c['page_id']==page['id']]
        google=next((c for c in checks if c['kind']=='google'),None)
        technical=next((c for c in checks if c['kind']=='technical'),None)
        error=next((c for c in checks if c['kind']=='google_error'),None)
        state=google['payload']['state'] if google else 'unknown'
        signals=[]
        if technical:
            t=technical['payload']
            if t.get('error'): signals.append('本次技术检查未完成')
            else:
                if t['http_status']!=200: signals.append('HTTP '+str(t['http_status']))
                if page['policy']=='index' and t.get('noindex'): signals.append('检测到 noindex')
                if page['policy']=='noindex' and not t.get('noindex'): signals.append('与既定 noindex 策略不同，需核对')
                if t.get('robots_allowed') is False: signals.append('robots.txt 禁止抓取')
                if t.get('robots_allowed') is None: signals.append('robots.txt 状态未能确认')
                if page['policy']=='index' and t.get('sitemap') is False: signals.append('站点地图未发现此页')
                if page['policy']=='index' and t.get('sitemap') is None: signals.append('站点地图状态未能确认')
                target='https://'+store.domain+page['path']
                if t.get('canonical') and t['canonical']!=target: signals.append('声明 canonical 指向其他地址')
        perf=next((r for r in (search or {}).get('page_rows',[]) if r['label']=='https://'+store.domain+page['path']),None)
        pages.append(dict(page,google=google,technical=technical,api_error=error,state=state,
                          state_label=INDEX_STATES.get(state,'待核查'),signals=signals,performance=perf,
                          url='https://'+store.domain+page['path']))
    core=[p for p in pages if p['policy']=='index']
    return dict(pages=pages,core_count=len(core),indexed=sum(p['state']=='indexed' for p in core),
                follow_up=sum(p['state']=='not_indexed' for p in core),unknown=sum(p['state']=='unknown' for p in core),
                intentional=sum(p['policy']=='noindex' for p in pages))
