import { esc, list } from './util.mjs';

// 主题色、字体、圆角 → CSS 变量，写入每页 <head>，站点样式全部引用这些变量。
function themeVars(theme = {}) {
  const c = theme.colors || {};
  const map = {
    '--c-primary': c.primary,
    '--c-primary-contrast': c.primaryContrast,
    '--c-accent': c.accent,
    '--c-ink': c.ink,
    '--c-muted': c.muted,
    '--c-bg': c.bg,
    '--c-surface': c.surface,
    '--c-border': c.border,
    '--f-heading': theme.fonts?.heading,
    '--f-body': theme.fonts?.body,
    '--radius': theme.radius,
  };
  return Object.entries(map)
    .filter(([, v]) => v)
    .map(([k, v]) => `${k}:${v}`)
    .join(';');
}

function navItem(n, current) {
  const active = n.href === current ? ' aria-current="page"' : '';
  if (!n.children?.length) return `<li><a href="${esc(n.href)}"${active}>${esc(n.label)}</a></li>`;
  return `<li class="has-sub">
    <a href="${esc(n.href)}"${active}>${esc(n.label)}</a>
    <ul class="sub">${list(n.children, (c) => `<li><a href="${esc(c.href)}">${esc(c.label)}</a></li>`)}</ul>
  </li>`;
}

function header(site, page) {
  const b = site.brand;
  const logo = b.logo
    ? `<img src="${esc(b.logo)}" alt="${esc(b.name)}" height="36">`
    : `<span class="logo-text">${esc(b.name)}</span>`;
  const langs = site.site.languages?.length > 1
    ? `<details class="lang"><summary>${esc(site.site.languages.find((l) => l.code === site.site.lang)?.label || site.site.lang)}</summary>
        <ul>${list(site.site.languages, (l) => `<li><a href="${esc(l.href)}" hreflang="${esc(l.code)}" lang="${esc(l.code)}">${esc(l.label)}</a></li>`)}</ul>
      </details>`
    : '';
  return `<header class="site-header">
  <div class="container header-inner">
    <a class="logo" href="index.html">${logo}</a>
    <button class="nav-toggle" aria-expanded="false" aria-controls="site-nav">☰<span class="sr">Menu</span></button>
    <nav id="site-nav" class="site-nav" aria-label="Main">
      <ul>${list(site.nav, (n) => navItem(n, page.path))}</ul>
      ${site.navCta ? `<a class="btn btn-primary btn-sm" href="${esc(site.navCta.href)}">${esc(site.navCta.label)}</a>` : ''}
      ${langs}
    </nav>
  </div>
</header>`;
}

function footer(site) {
  const f = site.footer || {};
  const b = site.brand;
  const year = new Date().getFullYear();
  return `<footer class="site-footer">
  <div class="container footer-grid">
    <div>
      <p class="logo-text">${esc(b.name)}</p>
      ${b.tagline ? `<p class="muted">${esc(b.tagline)}</p>` : ''}
      ${b.legalName ? `<p class="muted">${esc(b.legalName)}</p>` : ''}
      ${site.social?.length ? `<ul class="social">${list(site.social, (s) => `<li><a href="${esc(s.url)}" rel="noopener" target="_blank">${esc(s.label)}</a></li>`)}</ul>` : ''}
    </div>
    ${list(f.groups, (g) => `<div><p class="footer-title">${esc(g.title)}</p><ul>${list(g.links, (l) => `<li><a href="${esc(l.href)}">${esc(l.label)}</a></li>`)}</ul></div>`)}
  </div>
  <div class="container footer-bottom">
    <p>© ${year} ${esc(b.copyright || b.name)}</p>
    ${f.legal?.length ? `<ul>${list(f.legal, (l) => `<li><a href="${esc(l.href)}">${esc(l.label)}</a></li>`)}</ul>` : ''}
    ${f.note ? `<p class="muted">${esc(f.note)}</p>` : ''}
  </div>
</footer>`;
}

export function layout(site, page, body) {
  const s = site.site;
  const title = page.title || s.title;
  const description = page.description || s.description;
  const url = s.baseUrl ? `${s.baseUrl.replace(/\/$/, '')}/${page.path === 'index.html' ? '' : page.path}` : '';
  const ga = site.analytics?.gaId;
  return `<!doctype html>
<html lang="${esc(s.lang || 'en')}"${s.dir ? ` dir="${esc(s.dir)}"` : ''}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)}</title>
<meta name="description" content="${esc(description)}">
${url ? `<link rel="canonical" href="${esc(url)}">` : ''}
<meta property="og:type" content="website">
<meta property="og:title" content="${esc(title)}">
<meta property="og:description" content="${esc(description)}">
${s.ogImage ? `<meta property="og:image" content="${esc(s.ogImage)}">` : ''}
${s.favicon ? `<link rel="icon" href="${esc(s.favicon)}">` : ''}
${site.theme?.fonts?.googleFontsUrl ? `<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link rel="stylesheet" href="${esc(site.theme.fonts.googleFontsUrl)}">` : ''}
<link rel="stylesheet" href="assets/site.css">
<style>:root{${themeVars(site.theme)}}</style>
${ga ? `<script async src="https://www.googletagmanager.com/gtag/js?id=${esc(ga)}"></script><script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments)}gtag('js',new Date());gtag('config','${esc(ga)}');</script>` : ''}
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
${header(site, page)}
<main id="main">
${body}
</main>
${footer(site)}
<button class="motion-toggle" type="button" data-motion-toggle aria-pressed="false">${esc(site.labels?.pauseMotion || 'Pause motion')}</button>
<script src="assets/site.js" defer></script>
</body>
</html>
`;
}
