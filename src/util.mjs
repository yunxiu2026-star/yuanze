// 渲染工具函数：所有来自配置的文本都必须经过 esc() 再输出。

export function esc(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// 把列表渲染成 HTML 字符串，空列表返回空串。
export function list(items, fn) {
  return (items || []).map(fn).join('');
}

// 图片：配置了路径就输出 <img>，没有则输出占位块，方便新站先搭结构后补图。
export function img(src, alt, cls = '') {
  if (!src) {
    return `<div class="ph ${esc(cls)}" role="img" aria-label="${esc(alt)}"><span>${esc(alt || '图片占位')}</span></div>`;
  }
  return `<img class="${esc(cls)}" src="${esc(src)}" alt="${esc(alt)}" loading="lazy" decoding="async">`;
}

export function button(b, i = 0) {
  const style = b.style || (i === 0 ? 'primary' : 'ghost');
  return `<a class="btn btn-${esc(style)}" href="${esc(b.href)}">${esc(b.label)}</a>`;
}

// 段落：字符串或字符串数组均可。
export function paras(text) {
  return list([].concat(text || []), (p) => `<p>${esc(p)}</p>`);
}

// 区块统一外壳：标题、眉标题、导语、背景变体。
export function section(s, inner, extraClass = '') {
  const cls = ['section', `section-${s.type}`, s.background === 'alt' ? 'section-alt' : '', s.background === 'dark' ? 'section-dark' : '', extraClass]
    .filter(Boolean)
    .join(' ');
  const head = s.title || s.eyebrow || s.intro
    ? `<header class="section-head">
        ${s.eyebrow ? `<p class="eyebrow">${esc(s.eyebrow)}</p>` : ''}
        ${s.title ? `<h2>${esc(s.title)}</h2>` : ''}
        ${s.intro ? `<div class="lead">${paras(s.intro)}</div>` : ''}
      </header>`
    : '';
  return `<section class="${cls}"${s.id ? ` id="${esc(s.id)}"` : ''}><div class="container">${head}${inner}</div></section>`;
}
