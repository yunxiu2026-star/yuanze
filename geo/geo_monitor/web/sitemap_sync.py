"""Bounded, same-site sitemap discovery. No website or existing-row writes."""
import re
import os
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import httpx
from .operations_store import OperationsError
from .indexing import public_get


def valid_page_path(path):
    return bool(isinstance(path,str) and len(path)<=300 and
                re.fullmatch(r'/[a-zA-Z0-9/_~.%-]*',path) and
                not path.startswith('//') and not any(p in {'.','..'} for p in path.split('/')) and
                not re.search(r'%(?![0-9a-fA-F]{2})|%2[ef]|%5c',path,re.I))


def site_url(value,domain):
    try:
        p=urlsplit(value)
        if p.scheme!='https' or p.hostname not in {domain,'www.'+domain} or p.port or p.username or p.password or p.query or p.fragment or '\\' in value:
            return None
        return p
    except ValueError:return None


def fetch_sitemap_paths(domain,client=None):
    if domain!=os.environ.get('TARGET_DOMAIN','example.test'):raise OperationsError('只允许同步已配置的主站 Sitemap。')
    if client is None:
        with httpx.Client(timeout=8,headers={'User-Agent':'Demo WorksSitemapMonitor/1.0'},trust_env=False) as session:
            return fetch_sitemap_paths(domain,session)
    queue=[('https://'+domain+'/sitemap.xml',0)]
    seen=set();paths=set();total=0;started=time.monotonic()
    try:
        while queue:
            url,depth=queue.pop(0)
            if url in seen:continue
            if len(seen)>=32 or time.monotonic()-started>25:
                raise OperationsError('站点地图超过本次读取上限（32 份 / 25 秒），未添加部分结果。')
            seen.add(url)
            code,_,body,_=public_get(client,url,domain,limit=4*1024*1024)
            if code!=200:raise OperationsError('站点地图读取失败（HTTP '+str(code)+'），已有监控清单保留。')
            total+=len(body.encode('utf-8'))
            if total>12*1024*1024 or re.search(r'<!\s*(DOCTYPE|ENTITY)',body,re.I):
                raise OperationsError('站点地图过大或包含不支持的 XML 声明。')
            root=ET.fromstring(body)
            kind=root.tag.rsplit('}',1)[-1]
            if kind not in {'urlset','sitemapindex'}:raise OperationsError('站点地图不是有效的 urlset / sitemapindex。')
            expected='url' if kind=='urlset' else 'sitemap'
            for entry in root:
                if entry.tag.rsplit('}',1)[-1]!=expected:continue
                loc=next((n.text.strip() for n in entry if n.tag.rsplit('}',1)[-1]=='loc' and n.text),'')
                parsed=site_url(loc,domain)
                if not parsed:continue
                if kind=='sitemapindex':
                    if depth>=3:raise OperationsError('子站点地图层级超过 3 层，未添加部分结果。')
                    if loc not in seen:queue.append((loc,depth+1))
                    if len(queue)>64:raise OperationsError('子站点地图数量过多。')
                elif valid_page_path(parsed.path or '/'):
                    paths.add(parsed.path or '/')
                    if len(paths)>5000:raise OperationsError('发现超过 5,000 个页面，请缩小站点地图范围。')
        if not paths:raise OperationsError('站点地图未返回可纳管的本站页面，已有清单保留。')
        return sorted(paths)
    except (httpx.HTTPError,ET.ParseError):
        raise OperationsError('站点地图连接失败或 XML 无法解析；本次未添加页面，已有清单保留。') from None
