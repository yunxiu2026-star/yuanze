import { esc, list, paras, section } from '../util.mjs';

export default function faq(s) {
  const inner = `<div class="faq">${list(s.items, (f) => `<details>
    <summary>${esc(f.q)}</summary>
    <div>${paras(f.a)}</div>
  </details>`)}</div>`;
  return section(s, inner);
}
