import { esc, list, img, button, paras, section } from '../util.mjs';

// 图文左右分栏，reverse: true 时图片在左。
export default function split(s) {
  const inner = `<div class="split${s.reverse ? ' split-reverse' : ''}">
    <div class="split-copy">
      ${paras(s.text)}
      ${s.bullets?.length ? `<ul class="ticks">${list(s.bullets, (b) => `<li>${esc(b)}</li>`)}</ul>` : ''}
      ${s.buttons?.length ? `<div class="btn-row">${list(s.buttons, button)}</div>` : ''}
    </div>
    <div class="split-media">${img(s.image, s.imageAlt || s.title)}</div>
  </div>`;
  return section(s, inner);
}
