#!/usr/bin/env node
// 新建站点：node scripts/new-site.mjs <新站目录名> [--from yuanze]
// 默认从 sites/_starter（空白占位模版）复制；--from 可以基于已有站点（如同行业客户）复制后改写。
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const name = args.find((a) => !a.startsWith('--'));
const fromIdx = args.indexOf('--from');
const from = fromIdx >= 0 ? args[fromIdx + 1] : '_starter';

if (!name || !/^[a-z0-9][a-z0-9-]*$/.test(name)) {
  console.error('用法：npm run new -- <新站目录名，小写字母/数字/连字符> [--from 已有站点]');
  process.exit(1);
}
const src = path.join(root, 'sites', from);
const dst = path.join(root, 'sites', name);
if (!fs.existsSync(src)) { console.error(`来源站点不存在：sites/${from}`); process.exit(1); }
if (fs.existsSync(dst)) { console.error(`目标已存在：sites/${name}`); process.exit(1); }

fs.cpSync(src, dst, { recursive: true });
const cfgPath = path.join(dst, 'site.config.json');
const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
cfg.site.slug = name;
fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 2) + '\n');

console.log(`✓ 已创建 sites/${name}/（来源：sites/${from}）`);
console.log('下一步：');
console.log(`  1. 编辑 sites/${name}/site.config.json，替换所有【请填写…】内容`);
console.log(`  2. 把 Logo、图片放进 sites/${name}/assets/`);
console.log(`  3. npm run dev -- ${name}    本地预览`);
console.log(`  4. npm run build -- ${name} --check    检查无遗漏后构建，dist/${name}/ 即可部署`);
