/* First advertised landing and receipt-confirmed conversion helpers. No contact fields are stored. */
(() => {
  'use strict';
  if (window.B2BAttribution) return;
  const STORAGE = 'b2b-ad-attribution-v1';
  const RECEIPTS = 'b2b-lead-events-v1';
  const keys = ['source', 'medium', 'campaign', 'term', 'content'];
  const clean = (value, max = 100) => String(value ?? '').replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, max);
  const tagText = (value, max = 100) => clean(value, max).replace(/[\[\]|]/g, ' ').replace(/\s+/g, ' ');
  let memory = null;
  const seen = new Set();
  function valid(value) {
    return value && value.version === 1 && typeof value.landing_page === 'string' && /^\/(?!\/)/.test(value.landing_page) &&
      value.utm && typeof value.utm === 'object' && (keys.some(k => value.utm[k]) || value.gclid);
  }
  function capture() {
    if (memory) return memory;
    try { const saved = JSON.parse(sessionStorage.getItem(STORAGE)); if (valid(saved)) return (memory = saved); } catch { /* Storage is optional. */ }
    const params = new URLSearchParams(location.search), utm = {};
    keys.forEach(k => { const value = clean(params.get('utm_' + k)); if (value) utm[k] = value; });
    const click = clean(params.get('gclid'), 200);
    const gclid = /^[A-Za-z0-9._~-]{1,200}$/.test(click) ? click : '';
    if (!Object.keys(utm).length && !gclid) return null;
    memory = { version: 1, landing_page: location.pathname.slice(0, 240), utm, ...(gclid ? { gclid } : {}) };
    try { sessionStorage.setItem(STORAGE, JSON.stringify(memory)); } catch { /* Keep the current-page attribution in memory. */ }
    return memory;
  }
  function adTag(value = capture()) {
    if (!valid(value)) return '';
    const source = tagText(value.utm.source || (value.gclid ? 'google' : 'unknown'));
    const campaign = tagText(value.utm.campaign || 'not-provided');
    return '[Ad Source: ' + source + '/' + campaign + ' | Keyword: ' + tagText(value.utm.term || 'not-provided') +
      ' | Landing: ' + tagText(value.landing_page, 240) + (value.utm.medium ? ' | Medium: ' + tagText(value.utm.medium) : '') +
      (value.gclid ? ' | GCLID: ' + tagText(value.gclid, 200) : '') + ']';
  }
  function withAttribution(payload, options = {}) {
    const attribution = capture();
    if (!valid(attribution)) return payload;
    const tag = adTag(attribution), context = { ...(payload.source_context || {}) };
    context.utm = { ...attribution.utm };
    context.landing_page = attribution.landing_page;
    context.first_landing_page = attribution.landing_page;
    context.ad_source_tag = tag;
    if (attribution.gclid) context.gclid = attribution.gclid; else delete context.gclid;
    const fields = { ...(payload.submitted_fields || {}) }, field = options.field || 'message', limit = options.limit || 4000;
    const original = String(fields[field] ?? '');
    if (!original.includes(tag)) {
      const combined = original + (original ? '\n' : '') + tag;
      // Never truncate the buyer's message or exceed the existing field contract.
      // GEO also reads the structured tag when a text field has no remaining capacity.
      if (combined.length <= limit) fields[field] = combined;
    }
    return { ...payload, source_context: context, submitted_fields: fields };
  }
  function completeLead(receipt, formId, measure) {
    const reference = receipt?.inquiry_id || receipt?.record_id;
    if (receipt?.receipt_status !== 'RECEIVED' || typeof reference !== 'string' || !reference.trim()) return false;
    const id = reference.slice(0, 160);
    try { const ids = JSON.parse(sessionStorage.getItem(RECEIPTS) || '[]'); if (Array.isArray(ids)) ids.slice(-100).forEach(x => { if (typeof x === 'string') seen.add(x); }); } catch { /* Memory fallback. */ }
    if (seen.has(id)) return false;
    seen.add(id);
    try { sessionStorage.setItem(RECEIPTS, JSON.stringify([...seen].slice(-100))); } catch { /* Conversion remains non-blocking. */ }
    try {
      window.dataLayer = window.dataLayer || [];
      window.dataLayer.push({ event: 'generate_lead', form_location: location.pathname });
    } catch { /* A broken tag manager must not break the thank-you state. */ }
    // Retain the site's one consent-gated direct GA4 event. The object above is the GTM/Ads integration hook.
    try { measure?.('generate_lead', { form_id: formId }); } catch { /* No contact values go to analytics. */ }
    return true;
  }
  function addSuccessActions(container, values = {}, includeReset = false) {
    if (!container) return;
    container.querySelector('[data-yz-success-actions]')?.remove();
    const ar = document.documentElement.lang === 'ar';
    const text = String(values.message || values.project_context || values.product_direction || '');
    const topic = (text.match(/\[Brief:\s*([^\[\]]+)\]/i)?.[1] || text.match(/\[Buyer guide:\s*([^\[\]]+)\]/i)?.[1] || values.product_type || values.product || values.product_idea || 'matched samples');
    const name = clean(values.name || values.contact_name || 'there', 120);
    const whatsapp = new URL('https://wa.me/' + (window.B2B_SITE?.whatsapp || '15555550100'));
    whatsapp.searchParams.set('text', 'Hi ' + (window.B2B_SITE?.name || 'Demo Works') + ', I am ' + name + '. My request: ' + clean(topic, 220) + '. Could you help with sample pricing?');
    const prefix = location.pathname.match(/^\/(ar|es|fr|id|ms|pt|ru|th|vi)\//)?.[1];
    const links = [[whatsapp.href, ar ? 'تحتاج سعراً عاجلاً للعينات؟ تواصل عبر WhatsApp الآن ↗' : 'Need urgent sample pricing? Chat on WhatsApp Now ↗', 'whatsapp'],
      [(prefix ? '/' + prefix : '') + '/resources/', ar ? 'تصفّح أدلة الحد الأدنى 1,000 وحدة والامتثال →' : 'Browse 1,000 MOQ & Compliance Guides →', 'guides']];
    if (includeReset) links.push([location.pathname, ar ? 'ابدأ طلباً آخر ↗' : 'Start another request ↗', 'reset']);
    const box = document.createElement('div'); box.className = 'yz-lead-followup'; box.dataset.yzSuccessActions = 'v1';
    links.forEach(([href, label, kind]) => {
      const a = document.createElement('a'); a.href = href; a.textContent = label; a.dataset.yzSuccessAction = kind;
      a.dataset.yzMapped = 'excluded'; a.className = 'button ' + (kind === 'whatsapp' ? 'button-primary' : 'button-secondary');
      if (kind === 'whatsapp') { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
      box.append(a);
    });
    const oldReset = container.querySelector('[data-yzi-new]');
    if (oldReset) oldReset.before(box); else container.append(box);
  }
  capture();
  window.B2BAttribution = Object.freeze({ capture, adTag, withAttribution, completeLead, addSuccessActions });
})();
