import { esc, list, img, button, paras } from '../util.mjs';

export default function hero(s) {
  return `<section class="hero"${s.id ? ` id="${esc(s.id)}"` : ''}>
  <div class="container hero-grid">
    <div class="hero-copy">
      ${s.eyebrow ? `<p class="eyebrow">${esc(s.eyebrow)}</p>` : ''}
      <h1>${esc(s.title)}</h1>
      <div class="lead">${paras(s.subtitle)}</div>
      <div class="btn-row">${list(s.buttons, button)}</div>
      ${s.badges?.length ? `<ul class="badges">${list(s.badges, (b) => `<li>${b.image ? img(b.image, b.label) : esc(b.label)}</li>`)}</ul>` : ''}
    </div>
    <div class="hero-media">${img(s.image, s.imageAlt || s.title, 'hero-img')}</div>
  </div>
</section>`;
}
