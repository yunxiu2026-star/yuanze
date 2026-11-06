"""Evidence-based editorial drafts. Reads monitoring data; never publishes pages."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from .queries import label


LANGUAGES = {'en': '英语', 'ar': '阿拉伯语（手动编辑与审核）', 'zh': '中文', 'fr': '法语', 'es': '西班牙语', 'original': '保留原文，手动润色'}
STATUSES = {'draft': '待审核', 'reviewed': '已审核', 'archived': '已归档'}
SOURCES = {'gsc': '真实 Google 搜索词', 'ai': 'AI 实际检索词', 'prompt': '监测问题',
           'demo': '模拟 AI 检索词', 'demo_prompt': '模拟监测问题'}
BUSINESS = re.compile(
    r'cosmetic|skin\s?care|beauty|serum|cream|mask|shampoo|conditioner|body\s?(?:oil|scrub|wash)|'
    r'private\s?label|\boem\b|\bodm\b|packag|flacon|bouchon|fermeture|emballage|'
    r'cosm[eé]ti|envase|化妆|护肤|精华|面膜|面霜|身体|包材|包装|贴牌|代工', re.I)

if os.getenv('BUSINESS_KEYWORDS'):
    BUSINESS=re.compile('|'.join(re.escape(x.strip()) for x in os.environ['BUSINESS_KEYWORDS'].split(',') if x.strip()),re.I)

class OptimizationError(ValueError):
    pass


def clean(value, maximum=300):
    value = unicodedata.normalize('NFKC', str(value or ''))
    return re.sub(r'\s+', ' ', ''.join(c for c in value if c >= ' ')).strip()[:maximum]


def language_of(text):
    if re.search(r'[\u4e00-\u9fff]', text):
        return 'zh'
    if re.search(r'flacon|bouchon|fermeture|emballage|fabricant|cosmétique', text, re.I):
        return 'fr'
    if re.search(r'fabricante|envase|cosméticos|marca privada', text, re.I):
        return 'es'
    if re.search(r'[\u0400-\u04ff\u0600-\u06ff\u0e00-\u0e7f\uac00-\ud7af]', text):
        return 'original'
    return 'en'


def page_url(value, domain):
    value = str(value or '').strip()
    if not value:
        return ''
    if len(value) > 2000 or any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value:
        raise OptimizationError('目标页面地址格式不正确。')
    try:
        p = urlsplit(value)
        valid = (p.scheme == 'https' and p.hostname in {domain, 'www.' + domain}
                 and not p.username and not p.password and not p.port)
    except ValueError:
        valid = False
    if not valid:
        raise OptimizationError('目标页面请填写本站的完整 HTTPS 地址；留空可先保存内容方向。')
    return urlunsplit((p.scheme, p.netloc, p.path or '/', p.query, ''))


def _signal(kind, text):
    text = clean(text, 300)
    return {'id': hashlib.sha256((kind + ':' + text.casefold()).encode()).hexdigest()[:20],
            'kind': kind, 'source': SOURCES[kind], 'text': text, 'language': language_of(text),
            'demo': kind.startswith('demo'), 'evidence': [], 'occurrences': 0,
            'url': '', 'clicks': None, 'impressions': None, 'ctr': None, 'position': None}


def collect_signals(rows, search_report, domain, preview=False):
    """Keep real queries, simulated queries, and input prompts in separate groups."""
    groups = {}
    for row in rows:
        if row.get('error'):
            continue
        demo = preview or row.get('engine') == 'mock'
        queries = [q for q in (row.get('search_queries') or []) if isinstance(q, str) and q.strip()]
        kind = ('demo' if demo else 'ai') if queries else ('demo_prompt' if demo else 'prompt')
        for text in dict.fromkeys(queries or [row.get('prompt', '')]):
            if not clean(text):
                continue
            item = _signal(kind, text)
            item = groups.setdefault(item['id'], item)
            item['occurrences'] += 1
            if len(item['evidence']) < 8:
                item['evidence'].append({'result_id': row['id'], 'engine': label(row['engine']),
                    'date': str(row.get('checked_at', ''))[:19], 'prompt': clean(row.get('prompt'), 600),
                    'answer': clean(row.get('answer_text'), 500), 'mentioned': bool(row.get('mentioned')),
                    'competitors': [clean(x, 120) for x in (row.get('competitors') or [])[:4]]})
            if not item['url']:
                try:
                    item['url'] = page_url(row.get('expected_url'), domain)
                except OptimizationError:
                    pass
    if search_report:
        # Raw query rows, not the dashboard's top ten. Page aggregates are never joined to queries.
        for row in search_report.get('query_rows', []):
            if not clean(row.get('label')):
                continue
            item = _signal('gsc', row['label'])
            values = list(row.get('values') or []) + [None] * 4
            item.update(clicks=values[0], impressions=values[1], ctr=values[2], position=values[3],
                        import_meta=search_report.get('section_meta', {}).get('query') or search_report['import_meta'])
            groups[item['id']] = item
    for item in groups.values():
        branded = any(t.casefold() in item['text'].casefold() for t in os.getenv('BRAND_TERMS','Demo Works').split(',') if t)
        item['relevant'] = bool(BUSINESS.search(item['text']) or branded)
        item['intent'] = ('品牌导航' if branded else '包材选择' if re.search(r'packag|flacon|bouchon|fermeture|emballage|envase|包材|包装', item['text'], re.I)
                          else '采购与产品' if item['relevant'] else '相关性待核实')
        if not item['relevant']:
            item.update(priority='先核对相关性', order=4, reason='与化妆品采购业务的关联较弱，先核对对应页面，暂不据此生成主业务标题。')
        elif item['kind'] == 'gsc':
            n = item['impressions']
            if n is None or n < 20:
                item.update(priority='积累样本', order=2, reason='曝光样本较少，零点击不足以证明标题有问题；先核对相关性与页面语言。')
            elif (item['position'] is not None and 4 <= item['position'] <= 20
                  and item['ctr'] is not None and item['ctr'] < .02):
                item.update(priority='优先复查', order=0, reason='已出现一定曝光、平均排名在 4–20 且点击率低于 2%，可复查标题与搜索意图是否匹配。')
            else:
                item.update(priority='常规复查', order=1, reason='根据搜索意图核对页面标题、主标题和内容；排序不代表预期提升幅度。')
        else:
            missing = any(not e['mentioned'] for e in item['evidence'])
            item.update(priority='模拟演练' if item['demo'] else '补充内容' if missing else '检查表达', order=3 if item['demo'] else 1,
                        reason=('这条来自模拟检测，可演练编辑流程，不作为真实需求证据。' if item['demo'] else
                                '至少一次检测未推荐本站，可检查预期页面是否直接回答这个采购问题。' if missing else
                                '已被提及，可核对页面表达是否清楚，并保留已有相关信息。'))
    return sorted(groups.values(), key=lambda s: (s['order'], -(s['impressions'] or 0), s['text']))


def _topic(keyword, language):
    # Conservative vocabulary: no unverified claims about certifications, prices, MOQ, or delivery.
    topics = [
        (r'flacon|bouchon|fermeture|\bbottles?\b|\bcaps?\b|瓶|瓶盖', ['Cosmetic Bottles & Closures', '化妆品瓶与瓶盖选型', 'Flacons et fermetures cosmétiques', 'Frascos y cierres cosméticos']),
        (r'packag|flacon|bouchon|fermeture|emballage|envase|包材|包装', ['Cosmetic Packaging', '化妆品包装', 'Emballages cosmétiques', 'Envases cosméticos']),
        (r'serum|精华', ['Private Label Serum', '精华贴牌定制', 'Sérums en marque blanche', 'Sérums de marca privada']),
        (r'mask|面膜', ['Private Label Face Masks', '面膜贴牌定制', 'Masques en marque blanche', 'Mascarillas de marca privada']),
        (r'body.*scrub|身体磨砂|磨砂膏', ['Private Label Body Scrub', '身体磨砂膏贴牌定制', 'Gommages corporels en marque blanche', 'Exfoliantes corporales de marca privada']),
        (r'body.*cream|body.*lotion|身体霜|身体乳', ['Private Label Body Care', '身体护理贴牌定制', 'Soins du corps en marque blanche', 'Cuidado corporal de marca privada']),
        (r'shampoo|hair|洗发|护发', ['Private Label Hair Care', '头发护理贴牌定制', 'Soins capillaires en marque blanche', 'Cuidado capilar de marca privada']),
        (r'skin.?care|护肤', ['Private Label Skincare', '护肤品贴牌定制', 'Soins de la peau en marque blanche', 'Cuidado de la piel de marca privada']),
        (r'cosmetic|\boem\b|\bodm\b|private label|b2b|化妆|代工|贴牌|示例品牌|示例品牌', ['Cosmetics OEM & Private Label', '化妆品 OEM 与贴牌定制', 'Cosmétiques OEM et marque blanche', 'Cosméticos OEM y marca privada']),
    ]
    for pattern, names in topics:
        if re.search(pattern, keyword, re.I):
            return names[['en', 'zh', 'fr', 'es'].index(language)]
    return keyword.rstrip('?.？。')


def title_checks(title, keyword, url, all_titles=()):
    checks = []
    if len(title) > 80:
        checks.append('标题较长，可检查是否包含不必要的词；80 字符仅为编辑提示，并非 Google 限制。')
    words = re.findall(r'[^\W\d_]{3,}', title.casefold())
    if any(words.count(w) >= 3 for w in set(words)):
        checks.append('有词语重复三次或以上，请检查是否堆砌关键词。')
    if re.search(r'\bbest\b|\bguarantee\w*\b|\bfda[- ]approved\b|#1|全球第一|保证|免费|free samples|certified', title, re.I):
        checks.append('含有比较、承诺或资质用语，发布前需有可核实的依据。')
    if any(title.casefold().strip() == t.casefold().strip() for t in all_titles):
        checks.append('已有草稿使用相同标题，请核对是否属于同一页面。')
    if not url:
        checks.append('尚未指定目标页面，请先确认页面与检索词的关联。')
    checks.append('确认标题与页面正文、语言及 H1 一致；候选标题不保证收录、排名或询盘增长。')
    return checks


def make_draft(keyword, language, url, domain, brand='Demo Works', signal=None):
    keyword = clean(keyword, 1000)
    if not keyword:
        raise OptimizationError('请先选择一个检索词，或填写核心关键词。')
    if len(keyword) > 140:
        raise OptimizationError('核心词超过 140 个字符，请提炼后再生成，原始依据会保留。')
    if language not in LANGUAGES:
        raise OptimizationError('请选择支持的页面语言。')
    if signal and not signal['relevant'] and keyword.casefold() == signal['text'].casefold():
        raise OptimizationError('该词与主营业务的相关性较弱。请先核对来源页面，或改写为与你的产品有关的核心词。')
    url = page_url(url, domain)
    brand = clean(brand, 40) or 'Demo Works'
    topic = _topic(keyword, language) if language not in {'original','ar'} else keyword
    if language == 'en':
        variants = [f'{topic} | {brand}', f'{topic}: Options & Order Planning | {brand}', f'{topic}: A Buyer’s Guide | {brand}']
        desc = f'Explore {topic.lower()} with {brand}. Discuss your product brief, packaging choices and sample requirements, and request a quote for your project.'
        outline = ['Product scope and intended use', 'Formula and packaging options to confirm', 'Sampling process, MOQ and production timing: confirmed terms only', 'Documentation availability and inquiry checklist']
        extras = ['cosmetics OEM', 'private label manufacturing', 'packaging options']
    elif language == 'zh':
        variants = [f'{topic} | {brand}', f'{topic}：选型与采购流程 | {brand}', f'{topic}采购指南 | {brand}']
        desc = f'了解{brand}的{topic}，沟通产品需求、配方方向、包装与打样要求，按具体项目确认报价和采购条件。'
        outline = ['产品范围与适用需求', '配方与包装的可选方向', '打样、起订量与交期：只填写已确认的条件', '文件可提供范围与询价清单']
        extras = ['化妆品 OEM', '贴牌定制', '包装选型']
    elif language == 'fr':
        variants = [f'{topic} | {brand}', f'{topic} : options et projet | {brand}', f'{topic} : guide d’achat | {brand}']
        desc = f'{topic} : échangez avec {brand} sur vos besoins, les options d’emballage et les échantillons souhaités. Demandez un devis pour votre projet.'
        outline = ['Produits et besoins du projet', 'Formules et options d’emballage à confirmer', 'Échantillons, minimum de commande et délais : conditions confirmées uniquement', 'Documents disponibles et informations pour le devis']
        extras = ['cosmétiques OEM', 'marque blanche', 'options d’emballage']
    elif language == 'es':
        variants = [f'{topic} | {brand}', f'{topic}: opciones y proyecto | {brand}', f'{topic}: guía de compra | {brand}']
        desc = f'Consulta con {brand} las opciones de {topic.lower()}. Define tus necesidades, los envases y las muestras, y solicita una cotización para tu proyecto.'
        outline = ['Productos y necesidades del proyecto', 'Fórmulas y opciones de envase por confirmar', 'Muestras, pedido mínimo y plazos: solo condiciones confirmadas', 'Documentación disponible e información para cotizar']
        extras = ['cosméticos OEM', 'marca privada', 'opciones de envase']
    else:
        variants, desc, outline, extras = [f'{keyword} | {brand}'], '', [], []
    if language not in {'original','ar'}:
        index = ['en', 'zh', 'fr', 'es'].index(language)
        if re.search(r'packag|flacon|bouchon|fermeture|emballage|envase|\bbottles?\b|\bcaps?\b|包材|包装|瓶', keyword, re.I):
            extras = [
                ['cosmetic bottles', 'caps and closures', 'formula packaging compatibility'],
                ['化妆品瓶', '瓶盖与封口', '配方与包材相容性'],
                ['flacons cosmétiques', 'bouchons et systèmes de fermeture', 'compatibilité formule-emballage'],
                ['frascos cosméticos', 'tapones y cierres', 'compatibilidad fórmula-envase'],
            ][index]
            outline = [
                ['Bottle capacity, material and intended formula', 'Caps, pumps and closure options to confirm', 'Formula compatibility and testing arrangements', 'Artwork, packaging MOQ and quotation checklist'],
                ['瓶型、容量、材质与适配料体', '瓶盖、泵头及封口方式的可选范围', '料体相容性与测试安排', '印刷工艺、包材起订量与询价清单'],
                ['Capacité, matériau du flacon et formule prévue', 'Bouchons, pompes et fermetures à confirmer', 'Compatibilité avec la formule et essais à prévoir', 'Décoration, minimum de commande et informations pour le devis'],
                ['Capacidad, material del frasco y fórmula prevista', 'Tapones, bombas y cierres por confirmar', 'Compatibilidad con la fórmula y pruebas previstas', 'Decoración, pedido mínimo e información para cotizar'],
            ][index]
        elif re.search(r'serum|精华', keyword, re.I):
            extras = [
                ['private label serum', 'serum formulation', 'serum packaging'],
                ['精华贴牌', '精华配方开发', '精华包装选型'],
                ['sérum en marque blanche', 'formulation de sérum', 'emballage pour sérum'],
                ['sérum de marca privada', 'formulación de sérum', 'envases para sérum'],
            ][index]
        if re.search(r'\bmoq\b|minimum order|起订|pedido mínimo|minimum de commande', keyword, re.I):
            variants[1] = [f'{topic}: MOQ & Sampling | {brand}', f'{topic}：起订与打样 | {brand}',
                           f'{topic} : minimum de commande | {brand}', f'{topic}: pedido mínimo | {brand}'][index]
    basis = dict(signal) if signal else {'kind': 'manual', 'source': '手动输入', 'text': keyword, 'evidence': []}
    basis['rewritten'] = bool(signal and keyword.casefold() != signal['text'].casefold())
    return {'keyword': keyword, 'language': language, 'url': url, 'title': variants[0], 'variants': variants,
            'h1': topic, 'description': desc, 'secondary': '\n'.join(extras), 'outline': '\n'.join(outline),
            'basis': basis, 'checks': title_checks(variants[0], keyword, url), 'notes': ''}


class DraftStore:
    def __init__(self):
        self.folder = Path(os.environ.get('CONTENT_DATA_DIR', 'data/content-optimization'))

    @contextmanager
    def database(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.folder / 'drafts.db', timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS drafts (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)')
                yield db
        finally:
            db.close()

    def list(self):
        with self.database() as db:
            rows = db.execute('SELECT * FROM drafts ORDER BY id DESC').fetchall()
        return [dict(json.loads(row['payload']), id=row['id'], created_at=row['created_at'], status=row['status']) for row in rows]

    def save(self, draft):
        with self.database() as db:
            return db.execute('INSERT INTO drafts(created_at,status,payload) VALUES(?,?,?)',
                (datetime.now(timezone.utc).isoformat(), 'draft', json.dumps(draft, ensure_ascii=False))).lastrowid

    def status(self, draft_id, status):
        if status not in STATUSES:
            raise OptimizationError('草稿状态不正确。')
        with self.database() as db:
            return db.execute('UPDATE drafts SET status=? WHERE id=?', (status, draft_id)).rowcount > 0
