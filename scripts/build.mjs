#!/usr/bin/env node
// 用法：
//   node scripts/build.mjs <站点目录名>     构建 sites/<名>/ → dist/<名>/
//   node scripts/build.mjs --all            构建 sites/ 下所有站点（跳过 _ 开头的模版目录）
//   加 --check                              同时检查未填写的占位符，有则以非零退出
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { layout } from '../src/layout.mjs';
import { sections } from '../src/sections/index.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PLACEHOLDER = /【请填写[^】]*】/g;

const REQUIRED = ['site.slug', 'site.title', 'site.description', 'brand.name', 'contact.email', 'pages'];

function get(obj, dotted) {
  return dotted.split('.').reduce((o, k) => (o == null ? o : o[k]), obj);
}

function copyDir(from, to) {
  if (!fs.existsSync(from)) return;
  fs.mkdirSync(to, { recursive: true });
  for (const entry of fs.readdirSync(from, { withFileTypes: true })) {
    const src = path.join(from, entry.name);
    const dst = path.join(to, entry.name);
    if (entry.isDirectory()) copyDir(src, dst);
    else fs.copyFileSync(src, dst);
  }
}

export function buildSite(name, { check = false, outRoot = path.join(root, 'dist') } = {}) {
  const siteDir = path.join(root, 'sites', name);
  const configPath = path.join(siteDir, 'site.config.json');
  if (!fs.existsSync(configPath)) throw new Error(`找不到 ${path.relative(root, configPath)}`);
  const site = JSON.parse(fs.readFileSync(configPath, 'utf8'));

  const missing = REQUIRED.filter((k) => get(site, k) == null || get(site, k) === '');
  if (missing.length) throw new Error(`[${name}] 配置缺少必填项：${missing.join(', ')}`);

  const out = path.join(outRoot, name);
  fs.rmSync(out, { recursive: true, force: true });
  copyDir(path.join(root, 'src', 'assets'), path.join(out, 'assets'));
  copyDir(path.join(siteDir, 'assets'), path.join(out, 'assets'));

  const placeholders = new Set();
  for (const page of site.pages) {
    const body = page.sections.map((s, i) => {
      const render = sections[s.type];
      if (!render) throw new Error(`[${name}] 页面 ${page.path} 第 ${i + 1} 个区块类型未知：${s.type}`);
      return render(s, site);
    }).join('\n');
    const html = layout(site, page, body);
    for (const m of html.match(PLACEHOLDER) || []) placeholders.add(`${page.path}: ${m}`);
    const file = path.join(out, page.path);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, html);
  }

  // SEO 与部署辅助文件
  const base = site.site.baseUrl?.replace(/\/$/, '');
  if (base) {
    const urls = site.pages.map((p) => `  <url><loc>${base}/${p.path === 'index.html' ? '' : p.path}</loc></url>`).join('\n');
    fs.writeFileSync(path.join(out, 'sitemap.xml'), `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`);
    fs.writeFileSync(path.join(out, 'robots.txt'), `User-agent: *\nAllow: /\nSitemap: ${base}/sitemap.xml\n`);
  }
  if (site.site.domain) fs.writeFileSync(path.join(out, 'CNAME'), `${site.site.domain}\n`);

  console.log(`✓ ${name} → ${path.relative(root, out)}/（${site.pages.length} 个页面）`);
  if (placeholders.size) {
    console.warn(`  ⚠ 还有 ${placeholders.size} 处未填写的占位内容：`);
    for (const p of placeholders) console.warn(`    ${p}`);
  }
  return { ok: !(check && placeholders.size), placeholders: [...placeholders] };
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const args = process.argv.slice(2);
  const check = args.includes('--check');
  let names = args.filter((a) => !a.startsWith('--'));
  if (args.includes('--all')) {
    names = fs.readdirSync(path.join(root, 'sites')).filter((n) => !n.startsWith('_') && !n.startsWith('.'));
  }
  if (!names.length) {
    console.error('用法：npm run build -- <站点目录名>   或   npm run build -- --all');
    process.exit(1);
  }
  let ok = true;
  for (const n of names) {
    try {
      ok = buildSite(n, { check }).ok && ok;
    } catch (e) {
      console.error(`✗ ${e.message}`);
      ok = false;
    }
  }
  process.exit(ok ? 0 : 1);
}
