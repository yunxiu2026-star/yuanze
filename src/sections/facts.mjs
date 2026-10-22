import { esc, list, section } from '../util.mjs';

// 数字/要点卡片。variant: "cards"（默认，关键卖点）或 "stats"（大数字统计条）。
export default function facts(s) {
  const variant = s.variant || 'cards';
  const inner = `<ul class="facts facts-${esc(variant)}">${list(s.items, (f) => `<li>
    <strong class="fact-value">${esc(f.value)}</strong>
    <span class="fact-label">${esc(f.label)}</span>
    ${f.text ? `<p>${esc(f.text)}</p>` : ''}
  </li>`)}</ul>`;
  return section(s, inner);
}
