import { esc, list, section } from '../util.mjs';

// 产品分类：若干系列，每个系列下若干品类。
export default function categories(s) {
  const inner = `<div class="cat-grid">${list(s.groups, (g) => `<article class="cat">
    ${g.image ? `<img src="${esc(g.image)}" alt="${esc(g.title)}" loading="lazy">` : ''}
    <h3>${esc(g.title)}</h3>
    ${g.subtitle ? `<p class="muted">${esc(g.subtitle)}</p>` : ''}
    <ul class="chips">${list(g.items, (i) => `<li>${esc(i)}</li>`)}</ul>
  </article>`)}</div>`;
  return section(s, inner);
}
