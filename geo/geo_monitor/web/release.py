"""Explicit, fail-closed route surface for the first public release."""
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

PROFILE = 'ga4-indexing-v1'
GROWTH_PROFILE = 'growth-v2'


def growth_enabled():
    return os.environ.get('WEB_RELEASE_PROFILE') == GROWTH_PROFILE


def phase1_enabled():
    profile = os.environ.get('WEB_RELEASE_PROFILE', 'full')
    if profile not in {'full', PROFILE, GROWTH_PROFILE}:
        raise ValueError('Unknown WEB_RELEASE_PROFILE')
    return profile in {PROFILE, GROWTH_PROFILE}


def validate_release(cfg):
    if not phase1_enabled():
        return
    if not cfg.database_url.startswith('sqlite:///'):
        raise ValueError('Phase 1 requires the preserved SQLite database')
    if not Path(cfg.database_url[10:]).is_file():
        raise ValueError('Restore the existing SQLite database before starting')
    mode = os.environ.get('WEB_DEPLOY_MODE', 'rehearsal')
    if mode not in {'rehearsal', 'production'}:
        raise ValueError('Unknown WEB_DEPLOY_MODE')
    if mode == 'production':
        origin = os.environ.get('WEB_PUBLIC_ORIGIN', '')
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Production requires the exact HTTPS public origin')
        if not cfg.web_cookie_secure:
            raise ValueError('Production requires secure session cookies')
        if os.environ.get('GOOGLE_REDIRECT_URI') != origin + '/google/callback':
            raise ValueError('Production Google callback does not match public origin')


def allowed(method, path):
    if method in {'GET','HEAD'} and path in {'/settings','/settings/password'}:return True
    if method=='POST' and path=='/settings/password':return True
    if growth_enabled():
        if method in {'GET','HEAD'} and (path in {'/knowledge','/knowledge/new','/knowledge/categories'}
                or re.fullmatch(r'/knowledge/[0-9]+(?:/(?:edit|download)|/revisions/[0-9]+)?',path)):
            return True
        if method=='POST' and (path in {'/knowledge/new','/knowledge/categories'}
                or re.fullmatch(r'/knowledge/[0-9]+/(?:edit|workflow)',path)):
            return True
        if method in {'GET', 'HEAD'} and (path in {
                '/ai', '/ai/export.csv', '/social', '/social/export.csv', '/social/meta',
                '/social/meta/callback', '/optimize', '/optimize/export.csv', '/search-console',
                '/leads','/leads/new','/leads/template.csv','/leads/export.csv','/inquiries/export.csv'}
                or re.fullmatch(r'/optimize/drafts/[0-9]+/aeo\.(?:html|json)',path)
                or re.fullmatch(r'/leads/[0-9]+',path)
                or re.fullmatch(r'/ai/jobs/[0-9]+', path)
                or re.fullmatch(r'/results/[0-9]+', path)):
            return True
        if method == 'POST' and (path in {
                '/ai/settings', '/ai/prepare', '/ai/start', '/ai/disconnect',
                '/optimize/preview', '/optimize/drafts', '/optimize/sync', '/indexing/sitemap-sync', '/social/accounts',
                '/social/meta/client', '/social/meta/connect', '/social/meta/page',
                '/social/meta/sync', '/social/meta/disconnect', '/leads/new','/leads/import/preview',
                '/leads/import/confirm','/leads/filter','/leads/export.csv'}
                or re.fullmatch(r'/leads/[0-9]+/(follow-up|correct)',path)
                or re.fullmatch(r'/social/accounts/[a-zA-Z0-9_-]+/status', path)
                or re.fullmatch(r'/optimize/drafts/[0-9]+/status', path)):
            return True
    if method in {'GET', 'HEAD'}:
        return (path in {'/', '/login', '/healthz', '/readyz', '/robots.txt', '/analytics',
                         '/inquiries', '/indexing', '/indexing/export.csv', '/google',
                         '/google/imports', '/google/callback', '/inquiries/template/ga4.csv'}
                or path.startswith('/static/')
                or bool(re.fullmatch(r'/indexing/[0-9]+', path))
                or bool(re.fullmatch(r'/google/imports/[0-9]+/download', path)))
    if method == 'POST':
        return (path in {'/login', '/logout', '/inquiries/sync', '/inquiries/import',
                         '/indexing/pages', '/google/settings', '/google/connect',
                         '/google/disconnect', '/google/imports'}
                or bool(re.fullmatch(r'/indexing/[0-9]+/(google|technical|manual)', path)))
    return False
