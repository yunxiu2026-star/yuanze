import { esc, list, img, paras, section } from '../util.mjs';

// 图片卡片墙（如包材方向、案例）。
export default function gallery(s) {
  const inner = `<div class="gallery">${list(s.items, (g) => `<figure>
    ${img(g.image, g.title)}
    <figcaption><strong>${esc(g.title)}</strong>${g.text ? `<span>${esc(g.text)}</span>` : ''}</figcaption>
  </figure>`)}</div>
  ${s.note ? `<div class="note">${paras(s.note)}</div>` : ''}`;
  return section(s, inner);
}
