import { esc, list, section } from '../util.mjs';

// 数字/要点卡片。variant:
//   "cards"（默认，浅色卖点卡片）
//   "stats"（大数字统计条）
//   "panel"（深色品牌面板，紧贴首屏下方上浮，带序号与“了解更多”链接）
// 每项可选 href / linkLabel；配置了 href 时整张卡片可点击。panel 下每项还可配 image 作为背景照片。
export default function facts(s) {
  const variant = s.variant || 'cards';
  const numbered = variant === 'panel';
  const moreLabel = s.linkLabel || '了解更多';
  const num = (i) => String(i + 1).padStart(2, '0');
  const body = (f, i) => `
    ${numbered && f.image ? `<span class="fact-bg" style="background-image:url('${esc(f.image)}')" aria-hidden="true"></span>` : ''}
    ${numbered ? `<span class="fact-mark" aria-hidden="true">${num(i)}</span><span class="fact-index" aria-hidden="true">${num(i)}</span>` : ''}
    <strong class="fact-value">${esc(f.value)}</strong>
    <span class="fact-label">${esc(f.label)}</span>
    ${f.text ? `<p>${esc(f.text)}</p>` : ''}
    ${f.href && numbered ? `<span class="fact-more">${esc(f.linkLabel || moreLabel)}<span aria-hidden="true"> →</span></span>` : ''}`;
  const inner = `<ul class="facts facts-${esc(variant)}">${list(s.items, (f, i) => `<li>${
    f.href ? `<a class="fact-link" href="${esc(f.href)}">${body(f, i)}</a>` : body(f, i)
  }</li>`)}</ul>`;
  return section(s, inner, `facts-wrap-${esc(variant)}`);
}
