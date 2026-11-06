"""Local social account directory and attribution links; no platform credentials."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

PLATFORMS = {
    'linkedin': {'name': 'LinkedIn', 'mark': 'in', 'source': 'linkedin', 'hosts': ('linkedin.com',)},
    'instagram': {'name': 'Instagram', 'mark': '◎', 'source': 'instagram', 'hosts': ('instagram.com',)},
    'facebook': {'name': 'Facebook', 'mark': 'f', 'source': 'facebook', 'hosts': ('facebook.com',)},
    'youtube': {'name': 'YouTube', 'mark': '▶', 'source': 'youtube', 'hosts': ('youtube.com',)},
    'tiktok': {'name': 'TikTok', 'mark': '♪', 'source': 'tiktok', 'hosts': ('tiktok.com',)},
    'pinterest': {'name': 'Pinterest', 'mark': 'p', 'source': 'pinterest', 'hosts': ('pinterest.com',)},
    'x': {'name': 'X', 'mark': '𝕏', 'source': 'x', 'hosts': ('x.com', 'twitter.com')},
    'whatsapp': {'name': 'WhatsApp', 'mark': 'W', 'source': 'whatsapp', 'hosts': ('wa.me', 'whatsapp.com'), 'contact': True},
}
OFFICIAL = []


class SocialError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def profile_url(platform, value):
    if platform not in PLATFORMS:
        raise SocialError('请选择支持的社交平台。')
    try:
        parts = urlsplit(value.strip())
        host = (parts.hostname or '').lower()
        valid = any(host == h or host.endswith('.'+h) for h in PLATFORMS[platform]['hosts'])
        if (parts.scheme != 'https' or not valid or parts.username or parts.password or parts.port
                or not parts.path.strip('/') or len(value) > 1000 or re.search(r'[\s\\<>]', value)):
            raise ValueError()
        query = [(k, v) for k, v in parse_qsl(parts.query) if not k.startswith('utm_') and k not in {'fbclid', 'igsh', 'igshid'}]
        if query and not (platform == 'facebook' and parts.path == '/profile.php'
                          and len(query) == 1 and query[0][0] == 'id' and query[0][1].isdigit()):
            raise ValueError()
    except (ValueError, AttributeError):
        raise SocialError('请填写所选平台的 HTTPS 主页链接，移除多余的分享或跟踪参数。') from None
    return urlunsplit(('https', host, parts.path, urlencode(query), ''))


def handle(url):
    parts = urlsplit(url)
    return ('+'+parts.path.strip('/') if parts.hostname == 'wa.me'
            else parts.path.strip('/').removeprefix('company/'))


class SocialStore:
    def __init__(self, domain='example.test'):
        self.folder = Path(os.environ.get('SOCIAL_DATA_DIR', 'data/social-media'))
        self.domain = domain.lower().removeprefix('www.')

    @contextmanager
    def database(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.folder/'social.db', timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute('''CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, platform TEXT NOT NULL, name TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE, notes TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '', confirmed_at TEXT,
                    archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
                if self.domain == 'example.test':
                    for platform, name, url in OFFICIAL:
                        db.execute('''INSERT OR IGNORE INTO accounts
                            (id,platform,name,url,source_url,confirmed_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)''',
                            ('official-'+platform, platform, name, url, 'https://example.test/',
                             '2026-10-23T00:00:00+00:00', now(), now()))
                yield db
        finally:
            db.close()

    def accounts(self, archived=False):
        with self.database() as db:
            rows = db.execute('SELECT * FROM accounts WHERE archived=? ORDER BY created_at,id', (int(archived),)).fetchall()
        return [{**dict(row), 'platform_info': PLATFORMS[row['platform']], 'handle': handle(row['url'])} for row in rows]

    def get(self, account_id):
        with self.database() as db:
            row = db.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
        return dict(row) if row else None

    def save(self, account_id, platform, name, url, notes=''):
        name, notes = name.strip(), notes.strip()
        if not name or len(name) > 100 or len(notes) > 600:
            raise SocialError('请填写账号名称（最多 100 字），运营备注最多 600 字。')
        url = profile_url(platform, url)
        with self.database() as db:
            old = db.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone() if account_id else None
            if account_id and old is None:
                raise SocialError('账号不存在，请刷新页面后重试。')
            try:
                if old:
                    same = old['url'] == url and old['platform'] == platform
                    db.execute('''UPDATE accounts SET platform=?,name=?,url=?,notes=?,source_url=?,confirmed_at=?,updated_at=? WHERE id=?''',
                               (platform, name, url, notes, old['source_url'] if same else '',
                                old['confirmed_at'] if same else None, now(), account_id))
                else:
                    account_id = uuid.uuid4().hex
                    db.execute('INSERT INTO accounts(id,platform,name,url,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                               (account_id, platform, name, url, notes, now(), now()))
            except sqlite3.IntegrityError:
                raise SocialError('这个主页已在账号集合中，请编辑已有账号；已归档账号可以直接恢复。') from None
        return account_id

    def archive(self, account_id, archived):
        with self.database() as db:
            cur = db.execute('UPDATE accounts SET archived=?,updated_at=? WHERE id=?', (int(archived), now(), account_id))
            if cur.rowcount != 1:
                raise SocialError('账号不存在，请刷新后重试。')


def campaign_link(domain, platform, destination, campaign, medium='organic_social'):
    if platform not in PLATFORMS or medium not in {'organic_social', 'paid_social', 'referral'}:
        raise SocialError('请选择正确的平台和推广类型。')
    campaign = campaign.strip()
    if not campaign or len(campaign) > 100 or re.search(r'[\x00-\x1f]', campaign):
        raise SocialError('请填写活动名称，最多 100 字。')
    try:
        parts = urlsplit(destination.strip())
        host = (parts.hostname or '').lower().removeprefix('www.')
        expected = domain.lower().removeprefix('www.')
        if (parts.scheme != 'https' or host != expected or parts.username or parts.password
                or parts.port or len(destination) > 1800 or re.search(r'[\s\\<>]', destination)):
            raise ValueError()
    except ValueError:
        raise SocialError('落地页请填写本站的 HTTPS 链接。') from None
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.casefold().startswith('utm_')]
    pairs += [('utm_source', PLATFORMS[platform]['source']), ('utm_medium', medium), ('utm_campaign', campaign)]
    return urlunsplit(('https', parts.netloc, parts.path or '/', urlencode(pairs), parts.fragment))


SOURCE_ALIASES = {
    'linkedin': ('linkedin', 'linkedin.com', 'lnkd.in'),
    'instagram': ('instagram', 'instagram.com'),
    'facebook': ('facebook', 'facebook.com', 'fb', 'fb.com'),
    'youtube': ('youtube', 'youtube.com', 'youtu.be'),
    'tiktok': ('tiktok', 'tiktok.com'),
    'pinterest': ('pinterest', 'pinterest.com', 'pin.it'),
    'x': ('x', 'x.com', 'twitter', 'twitter.com', 't.co'),
    'whatsapp': ('whatsapp', 'whatsapp.com', 'wa.me'),
}


def social_traffic(data):
    result = {'meta': data.get('import_meta') if data else None, 'rows': [], 'sessions': None, 'unattributed': None}
    if not data:
        return result
    totals = {}
    for row in data.get('source_rows', []):
        source = row['label'].split(' / ', 1)[0].strip().lower()
        if '://' in source:
            source = urlsplit(source).hostname or ''
        platform = next((key for key, aliases in SOURCE_ALIASES.items()
                         if any(source == alias or source.endswith('.'+alias) for alias in aliases)), None)
        if source in {'organic social', 'paid social', 'social', '自然社交', '付费社交'}:
            platform = 'unattributed'
        sessions = row['values'][1]
        if platform and sessions is not None:
            totals[platform] = totals.get(platform, 0)+sessions
    result['unattributed'] = totals.pop('unattributed', None)
    result['rows'] = [{'platform': key, 'name': PLATFORMS[key]['name'], 'sessions': count} for key, count in totals.items()]
    if totals or result['unattributed'] is not None:
        result['sessions'] = sum(totals.values())+(result['unattributed'] or 0)
    return result
