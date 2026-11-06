"""Read-only home summary. Opening the home page never calls a provider API."""
from urllib.parse import urlencode

from .content_optimization import OptimizationError, _signal, page_url


def site_page(value, domain):
    value = str(value or '').strip()
    if value.startswith('/') and not value.startswith('//'):
        value = 'https://' + domain + value
    try:
        return page_url(value, domain)
    except OptimizationError:
        return ''


def search_action(text, batch):
    return '/optimize?' + urlencode({'source': 'real', 'signal': _signal('gsc', text)['id'],
                                     'search_batch': batch}) + '#generator'


def home_summary(analytics, search, drafts, meta, google_connected, run, traffic, preview, operations=None, growth=None):
    operations = operations or {}
    def metric(report, index):
        value = report['total'][index] if report else None
        return '—' if value is None else f'{value:,.0f}'

    def period(report):
        if not report:
            return '尚无已导入报表'
        m = report['import_meta']
        return f"{m['start']} 至 {m['end']}"

    metrics = [
        dict(label='网站会话', value=metric(analytics, 1), note='GA4 导入行汇总',
             period=period(analytics), href='/analytics', action='查看网站流量'),
        dict(label='搜索曝光', value=metric(search, 1), note='Search Console 文件导入',
             period=period(search), href='/search-console', action='查看搜索表现'),
        dict(label='搜索点击', value=metric(search, 0), note='Search Console 文件导入',
             period=period(search), href='/search-console', action='查看关键词'),
        dict(label='成功询盘', value=str(operations.get('accepted',0)) if operations.get('receipts_available') else '待核实',
             note='导入接收记录内的客户成功数；不含测试与未标识' if operations.get('receipts_available') else '客户接收数据未接入，保持待核实',
             period=f"{operations.get('start','')} 至 {operations.get('end','')}" if operations.get('receipts_available') else '接收结果与 GA4 上报分别统计',
             href='/inquiries', action='查看询盘统计'),
    ]
    pending = [d for d in drafts if d['status'] == 'draft']
    tasks = []
    if pending:
        tasks.append(dict(title=f'{len(pending)} 份内容草稿待审核',
                          description='核对目标页面、页面语言与业务信息，再确认文案。',
                          href=f"/optimize?draft_id={pending[0]['id']}#draft-editor", action='继续审核草稿', tag='内容优化'))
    if operations and not operations.get('receipts_available'):
        tasks.append(dict(title='补充客户询盘接收记录',description='先导入接收端统计记录，再与 GA4 成功事件分别核对。历史测试不会计入客户询盘。',
                          href='/inquiries#inquiry-import',action='查看接收数据接入',tag='询盘核查'))
    if operations.get('index_follow_up'):
        tasks.append(dict(title=f"{operations['index_follow_up']} 个业务页面需要跟进核查",
                          description='按已保存的 Google 结果整理，包含历史核查；先核对日期，再查看当前技术条件。',
                          href='/indexing',action='查看核心页面',tag='页面收录'))
    if search and search.get('query_rows'):
        small_sample = all((r['values'][1] or 0) < 20 for r in search['query_rows'])
        tasks.append(dict(title='核对真实搜索词与业务的关联',
                          description='当前曝光样本较少，先核对词语和页面，避免仅凭零点击判断标题有问题。' if small_sample else '核对曝光、点击与排名，再选择需要优化的页面。',
                          href='/optimize?source=real#signal-heading', action='查看真实搜索词', tag='搜索表现'))
    if not analytics or not analytics.get('daily'):
        tasks.append(dict(title='补充网站每日会话数据' if analytics else '导入第一份网站流量报表',
                          description='导入带日期和会话数的 GA4 报表，才能查看每日访问趋势。',
                          href='/google/imports', action='导入 GA4 报表', tag='数据补充'))
    if not meta.get('authorized') or meta.get('expired'):
        tasks.append(dict(title='连接 Facebook / Instagram 真实数据',
                          description='完成应用配置、账号授权和主页选择，再同步粉丝与互动。',
                          href='/social/meta', action='查看连接步骤', tag='社交媒体'))
    if not search:
        tasks.append(dict(title='导入 Google 搜索表现', description='使用官方 CSV / Excel，无需先完成 API 授权。',
                          href='/google/imports', action='导入搜索报表', tag='数据补充'))
    def report_status(label, report, href):
        return dict(label=label, state='文件导入可用' if report else '待导入', ready=bool(report),
                    detail=period(report), href=href, action='查看报表' if report else '导入报表')
    states = [report_status('网站流量 · GA4', analytics, '/analytics' if analytics else '/google/imports'),
              report_status('Google 搜索', search, '/search-console' if search else '/google/imports'),
              dict(label='Google API', state='已保存授权' if google_connected else '待授权',
                   ready=google_connected, detail='授权有效性以读取报告结果为准；文件导入可独立使用。',
                   href='/google', action='管理连接'),
              dict(label='Facebook / Instagram', state='授权已过期' if meta.get('expired') else '已授权' if meta.get('authorized') else '待授权',
                   ready=bool(meta.get('authorized') and not meta.get('expired')),
                   detail='粉丝与互动需要在 Meta 页面同步。', href='/social/meta', action='查看连接'),
              dict(label='AI 推荐检测', state='模拟演练' if preview or run and run.get('engines') == 'mock' else '有检测记录' if run else '待检测',
                   ready=False, detail='模拟结果用于演练，不作为真实买家需求。' if preview else '检测结果与访客实际提问分别统计。',
                   href='/ai-overview', action='查看 AI 报告'),
              dict(label='AI 来源日志', state='已有日志' if traffic.get('latest_day') else '未接入',
                   ready=bool(traffic.get('latest_day')), detail='与 GA4 的 AI 渠道统计分别展示。',
                   href='/traffic', action='查看日志状态')]
    if operations:
        states.extend([
            dict(label='询盘接收统计',state='已有导入记录' if operations.get('receipts_available') else '未接入客户数据',
                 ready=bool(operations.get('receipts_available')),detail='客户、测试与未标识记录分别统计。',href='/inquiries',action='查看统计'),
            dict(label='核心页面收录',state=f"{operations.get('indexed',0)} / {operations.get('core_count',0)} 有收录依据",
                 ready=False,detail='含历史核查，状态与核查日期逐页展示。',href='/indexing',action='查看核查记录')])
    queries = []
    if search:
        for row in search.get('query_rows', [])[:5]:
            queries.append(dict(text=row['label'], clicks=row['values'][0], impressions=row['values'][1],
                                href=search_action(row['label'], search['import_meta']['id'])))
    modules=[]
    if growth:
        ai,inquiry=growth['ai'],growth['inquiry']
        ga=inquiry['ga_totals'].get('generate_lead')
        metrics[3]=dict(label='GA4 成功事件',value='—' if ga is None else f'{ga:,}',
            note='可能含测试和重复上报；不等于已接收客户数',
            period=f"{inquiry['start']} 至 {inquiry['end']}",href='/inquiries',action='查看询盘事件')
        tasks=[t for t in tasks if t.get('title')!='补充客户询盘接收记录']
        if not ai['enabled'] or not ai['configured']:
            tasks.append(dict(title='AI 监测配置留待确认',description='已保留监测问题与执行流程；按当前选择，付费调用关闭。',
                              href='/ai',action='查看监测问题与配置',tag='AI 推荐'))
        states=[s for s in states if s['label'] not in {'AI 来源日志','询盘接收统计'}]
        for s in states:
            if s['label']=='AI 推荐检测':
                s.update(state='可手动检测' if ai['configured'] and ai['enabled'] else '待确认费用' if ai['configured'] else '待配置密钥',
                    ready=bool(ai['configured'] and ai['enabled']),href='/ai',action='查看监测配置',
                    detail=f"已完成 {ai['completed']} 批真实 API 检测；定时调用关闭。")
        states.append(dict(label='GA4 询盘事件',state='已有事件快照' if inquiry['ga_selected'] else '待同步',
            ready=bool(inquiry['ga_selected']),detail=f"{inquiry['start']} 至 {inquiry['end']}；事件与客户接收分别核对。",
            href='/inquiries',action='查看事件快照'))
        modules=[dict(label='AI 推荐监测',href='/ai',icon='report',state='可手动检测' if ai['configured'] and ai['enabled'] else '待配置密钥' if not ai['configured'] else '待确认费用',
                      detail='监测问题、回答证据与执行记录'),
                 dict(label='社交媒体中心',href='/social',icon='social',state='已授权' if meta.get('authorized') and not meta.get('expired') else '待授权',
                      detail='品牌账号、粉丝与内容互动'),
                 dict(label='标题与词语优化',href='/optimize',icon='spark',state='可以使用',detail='真实检索词、内容草稿与审核')]
        for m in metrics[:3]:
            m['href'] += '?source=import'
        for m in metrics[1:3]:
            m['note']='Search Console 已保存报表；以每日表统计总量'
        for s in states:
            if s['label']=='Google 搜索' and search and search.get('import_meta',{}).get('filename')=='gsc-api-queries.csv':
                s.update(state='API 快照已保存',detail=period(search)+'；查询与既有报表按相同日期组合。')
    return dict(metrics=metrics, tasks=tasks[:4], states=states, queries=queries,
                analytics=analytics, search=search, pending_drafts=len(pending), run=run,modules=modules)
