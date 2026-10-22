import { esc, list, button, paras } from '../util.mjs';

export default function cta(s) {
  return `<section class="cta"${s.id ? ` id="${esc(s.id)}"` : ''}><div class="container cta-inner">
    <div>
      <h2>${esc(s.title)}</h2>
      ${paras(s.text)}
    </div>
    <div class="btn-row">${list(s.buttons, button)}</div>
  </div></section>`;
}
