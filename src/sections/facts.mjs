import { esc, list, section } from '../util.mjs';

// 数字/要点卡片。variant:
//   "cards"（默认，浅色卖点卡片）
//   "stats"（大数字统计条）
//   "path"（一条金线把各项串成先后顺序，滚动到时依次出现，适合“起订 → 打样 → 生产 → 品控”这类流程型卖点；
//          配 background: "alt" 时与下方同底色区块连成一体）
// 每项可选 href / linkLabel；配置了 href 时整项可点击。path 下每项还可配 unit（数字后的小号斜体单位）。
export default function facts(s) {
  const variant = s.variant || 'cards';
  const path = variant === 'path';
  const body = (f) => `
    <strong class="fact-value">${esc(f.value)}${f.unit ? `<small>${esc(f.unit)}</small>` : ''}</strong>
    ${path ? '<span class="fact-dot" aria-hidden="true"></span>' : ''}
    <span class="fact-label">${esc(f.label)}</span>
    ${f.text ? `<p>${esc(f.text)}</p>` : ''}
    ${f.href && path && f.linkLabel ? `<span class="fact-more">${esc(f.linkLabel)}</span>` : ''}`;
  const tag = path ? 'ol' : 'ul';
  const inner = `<${tag} class="facts facts-${esc(variant)}">${list(s.items, (f, i) => `<li${path ? ` style="--i:${i}"` : ''}>${
    f.href ? `<a class="fact-link" href="${esc(f.href)}">${body(f)}</a>` : body(f)
  }</li>`)}</${tag}>`;
  return section(s, inner, `facts-wrap-${esc(variant)}`);
}
