import { esc, list, section } from '../util.mjs';

// 通用卡片（如 OEM / ODM / OBM 服务模式）。
export default function cards(s) {
  const inner = `<div class="cards">${list(s.items, (c) => `<article class="card">
    ${c.eyebrow ? `<p class="eyebrow">${esc(c.eyebrow)}</p>` : ''}
    <h3>${esc(c.title)}</h3>
    ${c.text ? `<p>${esc(c.text)}</p>` : ''}
    ${c.href ? `<a class="more" href="${esc(c.href)}">${esc(c.linkLabel || '了解更多')} →</a>` : ''}
  </article>`)}</div>`;
  return section(s, inner);
}
