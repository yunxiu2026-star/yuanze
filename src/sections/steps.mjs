import { esc, list, img, section } from '../util.mjs';

// 有序流程（如七步开发流程、四段质检）。
export default function steps(s) {
  const inner = `<ol class="steps">${list(s.items, (st, i) => `<li>
    ${st.image ? `<div class="step-media">${img(st.image, st.title)}</div>` : ''}
    <span class="step-num">${String(i + 1).padStart(2, '0')}</span>
    <h3>${esc(st.title)}</h3>
    ${st.text ? `<p>${esc(st.text)}</p>` : ''}
  </li>`)}</ol>`;
  return section(s, inner);
}
