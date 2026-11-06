"""Offline, reviewed-only editorial snippets. Never publishes or invents answers."""
from html import escape
import os
import json
import re
from urllib.parse import urlencode
from .content_optimization import OptimizationError, page_url


def parse_faq(text):
    if len(text)>6000:raise OptimizationError('FAQ 最多 6,000 字符。')
    if not text.strip():return []
    pairs=[];question=None;answers=[]
    def finish():
        answer='\n'.join(answers).strip()
        if not question or not answer:raise OptimizationError('FAQ 须成对填写 Q: 问题与 A: 答案。')
        if len(question)>300 or len(answer)>2000:raise OptimizationError('每个 FAQ 问题最多 300 字符，答案最多 2,000 字符。')
        pairs.append({'question':question,'answer':answer})
    for line in text.splitlines():
        if re.match(r'^Q\s*:',line,re.I):
            if question is not None:finish()
            question=line.split(':',1)[1].strip();answers=[]
        elif re.match(r'^A\s*:',line,re.I):
            if question is None or answers:raise OptimizationError('请先填写 Q:，再填写对应的一条 A:。')
            answers.append(line.split(':',1)[1].strip())
        elif line.strip():
            if not answers:raise OptimizationError('FAQ 使用 Q: 与 A: 开头，答案可分行填写。')
            answers.append(line.strip())
    finish()
    if len(pairs)>12:raise OptimizationError('最多导出 12 组 FAQ。')
    if len({p['question'].casefold() for p in pairs})!=len(pairs):raise OptimizationError('FAQ 问题重复，请合并后保存。')
    return pairs


def safe_json(value):
    return json.dumps(value,ensure_ascii=False,indent=2).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e').replace('\u2028','\\u2028').replace('\u2029','\\u2029')


def export_draft(draft,domain,format):
    if draft.get('status')!='reviewed':raise OptimizationError('仅已审核草稿可导出 AEO 片段。请先保存并完成审核。')
    if not draft.get('title','').strip() or not draft.get('h1','').strip():raise OptimizationError('标题与 H1 不能为空。')
    faq=parse_faq(draft.get('faq_text',''))
    if not faq:raise OptimizationError('请先在编辑器补充完整 FAQ 并另存、审核。系统不会把提纲问句编造成答案。')
    url=page_url(draft.get('url',''),domain)
    schema={'@context':'https://schema.org','@type':'FAQPage','inLanguage':draft.get('language','en'),
            'mainEntity':[{'@type':'Question','name':p['question'],'acceptedAnswer':{'@type':'Answer','text':p['answer']}} for p in faq]}
    if url:schema['url']=url
    if format=='json':return safe_json(schema)+'\n'
    if format!='html':raise OptimizationError('导出格式不存在。')
    e=lambda text:escape(str(text or ''),quote=True)
    rtl=draft.get('language')=='ar';lang=draft.get('language','en')
    outline=[line.strip() for line in draft.get('outline','').splitlines() if line.strip()]
    sections=''.join('<section><h2>'+e(line)+'</h2></section>\n' for line in outline)
    questions=''.join('<details open><summary>'+e(p['question'])+'</summary><p>'+e(p['answer']).replace('\n','<br>')+'</p></details>\n' for p in faq)
    topic=draft['title']
    href=('/ar/' if rtl else '/')+'get-a-quote/?'+urlencode({'brief':topic})
    cta='اطلب عينات مطابقة ←' if rtl else 'Request Matched Samples →'
    return f'''<!-- B2B main-site Editorial fragment, reviewed draft #{int(draft['id'])}.
Place title/description/canonical from the template below into the document head.
Complete the outline sections with verified procurement content before publication.
FAQ text below matches FAQPage exactly. Downloading does not publish this fragment. -->
<template data-b2b-head>
<title>{e(draft['title'])}</title>
<meta name="description" content="{e(draft.get('description',''))}">
{('<link rel="canonical" href="'+e(url)+'">') if url else ''}
</template>
<style>
.yz-aeo{{max-width:1000px;margin:0 auto;padding:clamp(24px,5vw,72px);color:#153f33;background:#f4f1e8;font-family:Arial,sans-serif;line-height:1.8}}
.yz-aeo .eyebrow{{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#92743c}}
.yz-aeo h1{{font-size:clamp(30px,4vw,56px);line-height:1.15;letter-spacing:-.025em;font-weight:600}}
.yz-aeo h2{{font-size:24px;line-height:1.4;margin-block:36px 14px;font-weight:500}}
.yz-aeo .lede{{font-size:20px;max-width:760px}}.yz-aeo details{{padding:18px 0;border-top:1px solid #c4cbbb}}
.yz-aeo summary{{font-weight:600;cursor:pointer}}.yz-aeo .cta{{display:inline-block;margin-top:28px;padding:12px 22px;background:#153f33;color:white;border-radius:4px;text-decoration:none}}
</style>
<article class="yz-aeo" lang="{e(lang)}" dir="{'rtl' if rtl else 'ltr'}">
<header><p class="eyebrow">{e(os.getenv('SITE_NAME','Demo Works'))} · BUYER DECISION GUIDE</p><h1>{e(draft['h1'])}</h1><p class="lede">{e(draft.get('description',''))}</p></header>
{sections}<section aria-label="FAQ"><h2>{'أسئلة المشترين' if rtl else 'Buyer questions'}</h2>{questions}</section>
<a class="cta" data-sample data-guide="{e(topic)}" href="{e(href)}">{cta}</a>
</article>
<script type="application/ld+json">{safe_json(schema)}</script>
'''
