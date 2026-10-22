import { esc, list, img, section } from '../util.mjs';

// 目标市场与对应资质文件。
export default function markets(s) {
  const inner = `${s.image ? `<div class="markets-map">${img(s.image, s.title)}</div>` : ''}
  <ul class="markets">${list(s.items, (m) => `<li>
    <h3>${esc(m.name)}</h3>
    <ul class="chips">${list(m.tags, (t) => `<li>${esc(t)}</li>`)}</ul>
  </li>`)}</ul>`;
  return section(s, inner);
}
