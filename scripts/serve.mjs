#!/usr/bin/env node
// 本地预览：node scripts/serve.mjs <站点目录名> [端口]，先构建再在 http://localhost:4321 提供 dist/<名>/。
// 修改 sites/<名>/ 或 src/ 后刷新浏览器即可看到最新效果（每次请求 HTML 时重新构建）。
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const name = process.argv[2];
const port = Number(process.argv[3] || 4321);
if (!name) {
  console.error('用法：npm run dev -- <站点目录名> [端口]');
  process.exit(1);
}
const dir = path.join(root, 'dist', name);
const types = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.json': 'application/json', '.xml': 'application/xml', '.txt': 'text/plain', '.ico': 'image/x-icon', '.pdf': 'application/pdf' };

async function rebuild() {
  // 动态 import 带时间戳，确保读取到最新的模版代码。
  const { buildSite } = await import(`./build.mjs?t=${Date.now()}`);
  buildSite(name);
}

await rebuild();
http.createServer(async (req, res) => {
  let p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  if (p.endsWith('/')) p += 'index.html';
  if (p.endsWith('.html')) {
    try { await rebuild(); } catch (e) { res.writeHead(500); return res.end(String(e.message)); }
  }
  const file = path.join(dir, path.normalize(p));
  if (!file.startsWith(dir) || !fs.existsSync(file)) { res.writeHead(404); return res.end('Not found'); }
  res.writeHead(200, { 'content-type': types[path.extname(file)] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
}).listen(port, () => console.log(`预览：http://localhost:${port}/`));
