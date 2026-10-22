import { esc, list, section } from '../util.mjs';

// 联系方式 + 询盘表单。form.action 为空时表单以 mailto 方式发送到 contact.email。
export default function contact(s, site) {
  const c = site.contact || {};
  const rows = [
    c.email && ['Email', `<a href="mailto:${esc(c.email)}">${esc(c.email)}</a>`],
    c.phone && [c.phoneLabel || 'Tel', `<a href="tel:${esc(c.phone.replace(/\s/g, ''))}">${esc(c.phone)}</a>`],
    c.whatsapp && ['WhatsApp', `<a href="https://wa.me/${esc(c.whatsapp.replace(/\D/g, ''))}">${esc(c.whatsapp)}</a>`],
    c.wechat && [c.wechatLabel || 'WeChat', esc(c.wechat)],
    c.address && [c.addressLabel || 'Address', esc(c.address)],
    c.hours && [c.hoursLabel || 'Hours', esc(c.hours)],
  ].filter(Boolean);
  const form = s.form || {};
  const action = form.action || `mailto:${c.email || ''}`;
  const enctype = form.action ? '' : ' enctype="text/plain"';
  const fields = form.fields || [
    { name: 'name', label: 'Name', required: true },
    { name: 'email', label: 'Email', type: 'email', required: true },
    { name: 'company', label: 'Company' },
    { name: 'message', label: 'Message', type: 'textarea', required: true },
  ];
  const field = (f) => {
    const req = f.required ? ' required' : '';
    const ctrl = f.type === 'textarea'
      ? `<textarea name="${esc(f.name)}" rows="5"${req}></textarea>`
      : `<input type="${esc(f.type || 'text')}" name="${esc(f.name)}"${req}>`;
    return `<label><span>${esc(f.label)}${f.required ? ' *' : ''}</span>${ctrl}</label>`;
  };
  const inner = `<div class="contact">
    <dl class="contact-list">${list(rows, ([k, v]) => `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`)}</dl>
    <form class="contact-form" action="${esc(action)}" method="post"${enctype}>
      ${list(fields, field)}
      <button class="btn btn-primary" type="submit">${esc(form.submitLabel || 'Send')}</button>
    </form>
  </div>`;
  return section(s, inner);
}
