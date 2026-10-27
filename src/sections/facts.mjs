import { esc, list, section } from '../util.mjs';

// 数字/要点卡片。variant:
//   "cards"（默认，浅色卖点卡片）
//   "stats"（大数字统计条）
//   "panel"（错落的便当格：首张为大主卡，紧贴首屏下方上浮，深绿 / 香槟 / 金色三种卡面交替）
// 每项可选 href / linkLabel；配置了 href 时整张卡片可点击。
// panel 下每项还可配：icon（见下方 ICONS 的键名）、tone（"dark" | "light" | "gold"，默认按位置轮换）、image（背景照片）。

const ICONS = {
  box: '<path d="M12 2.8 20.5 7.3v9.4L12 21.2 3.5 16.7V7.3Z"/><path d="M3.5 7.3 12 11.8l8.5-4.5M12 11.8v9.4M7.8 5 16.2 9.6"/>',
  flask: '<path d="M9.5 3h5M10.3 3v5.6L5 18.2A2 2 0 0 0 6.8 21h10.4a2 2 0 0 0 1.8-2.8l-5.3-9.6V3"/><path d="M7.4 14h9.2"/>',
  clock: '<circle cx="12" cy="12" r="8.8"/><path d="M12 7v5.2l3.4 2"/>',
  shield: '<path d="M12 2.8 19.5 5.6v5.6c0 4.6-3.1 8.4-7.5 10-4.4-1.6-7.5-5.4-7.5-10V5.6Z"/><path d="m8.6 12 2.4 2.4 4.4-4.6"/>',
  leaf: '<path d="M5 19c0-8 5-13 15-14-1 10-6 15-14 15"/><path d="M5 19 13 11"/>',
  globe: '<circle cx="12" cy="12" r="8.8"/><path d="M3.2 12h17.6M12 3.2c2.4 2.4 3.6 5.3 3.6 8.8s-1.2 6.4-3.6 8.8c-2.4-2.4-3.6-5.3-3.6-8.8S9.6 5.6 12 3.2Z"/>',
  award: '<circle cx="12" cy="9" r="5.8"/><path d="m8.6 13.7-1.4 7.3 4.8-2.6 4.8 2.6-1.4-7.3"/>',
  truck: '<path d="M2.8 6h11v10h-11zM13.8 9.5h4l2.4 3V16h-6.4"/><circle cx="7" cy="17.5" r="1.7"/><circle cx="17" cy="17.5" r="1.7"/>',
};
const TONES = ['dark', 'light', 'dark', 'gold'];

// 主卡右下角的金色同心纹样
const ART = `<svg class="fact-art" viewBox="0 0 200 200" aria-hidden="true">${
  [96, 82, 68, 54, 40, 26].map((r) => `<circle cx="100" cy="100" r="${r}"/>`).join('')
}${Array.from({ length: 24 }, (_, i) => {
  const a = (i * Math.PI) / 12;
  return `<path d="M${(100 + 26 * Math.cos(a)).toFixed(1)} ${(100 + 26 * Math.sin(a)).toFixed(1)}L${(100 + 96 * Math.cos(a)).toFixed(1)} ${(100 + 96 * Math.sin(a)).toFixed(1)}"/>`;
}).join('')}</svg>`;

export default function facts(s) {
  const variant = s.variant || 'cards';
  const panel = variant === 'panel';
  const moreLabel = s.linkLabel || '了解更多';
  const num = (i) => String(i + 1).padStart(2, '0');
  const icon = (name) => (ICONS[name] ? `<span class="fact-icon" aria-hidden="true"><svg viewBox="0 0 24 24">${ICONS[name]}</svg></span>` : '');
  const body = (f, i) => `
    ${panel && f.image ? `<span class="fact-bg" style="background-image:url('${esc(f.image)}')" aria-hidden="true"></span>` : ''}
    ${panel && i === 0 ? ART : ''}
    ${panel ? `<span class="fact-top">${icon(f.icon)}<span class="fact-index" aria-hidden="true">${num(i)}</span></span>` : ''}
    <strong class="fact-value">${esc(f.value)}</strong>
    <span class="fact-label">${esc(f.label)}</span>
    ${f.text ? `<p>${esc(f.text)}</p>` : ''}
    ${f.href && panel ? `<span class="fact-more">${esc(f.linkLabel || moreLabel)}<span aria-hidden="true"> →</span></span>` : ''}`;
  const tone = (f, i) => (panel ? ` class="tone-${esc(f.tone || TONES[i % TONES.length])}"` : '');
  const inner = `<ul class="facts facts-${esc(variant)}">${list(s.items, (f, i) => `<li${tone(f, i)}>${
    f.href ? `<a class="fact-link" href="${esc(f.href)}">${body(f, i)}</a>` : body(f, i)
  }</li>`)}</ul>`;
  return section(s, inner, `facts-wrap-${esc(variant)}`);
}
