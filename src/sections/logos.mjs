import { esc, list, section } from '../util.mjs';

// 品牌/供应商 Logo 滚动墙。没有图片时显示名称文字。
export default function logos(s) {
  const item = (l) => `<li>${l.image ? `<img src="${esc(l.image)}" alt="${esc(l.name)}" loading="lazy">` : `<span>${esc(l.name)}</span>`}</li>`;
  const row = list(s.items, item);
  // 复制一份用于无缝滚动，副本对读屏隐藏。
  const inner = `<div class="marquee" data-marquee>
    <ul class="marquee-track">${row}</ul>
    <ul class="marquee-track" aria-hidden="true">${row}</ul>
  </div>`;
  return section(s, inner);
}
