(() => {
  'use strict';
  const cfg=window.B2B_SITE,ar=document.documentElement.lang==='ar',prefix=ar?'/ar':'',q=(s,r=document)=>r.querySelector(s),all=(s,r=document)=>[...r.querySelectorAll(s)];
  const storage={get(k){try{return sessionStorage.getItem(k)}catch{return null}},set(k,v){try{sessionStorage.setItem(k,v)}catch{}}};
  const menu=q('.menu-toggle'),nav=q('#site-nav');
  const closeMenu=()=>{nav.classList.remove('open');menu.setAttribute('aria-expanded','false')};
  menu.addEventListener('click',()=>{const open=menu.getAttribute('aria-expanded')!=='true';menu.setAttribute('aria-expanded',String(open));nav.classList.toggle('open',open)});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&nav.classList.contains('open')){closeMenu();menu.focus()}});
  document.addEventListener('focusin',e=>{if(!e.target.closest('.site-header'))closeMenu()});
  const consent=q('#consent-panel');let measurement=storage.get('b2b-measurement')==='granted';let trackerLoaded=false;
  function enableTracker(){
    if(!measurement||!cfg.analytics.enabled||trackerLoaded)return;trackerLoaded=true;
    const ga=cfg.analytics.ga4_id,gtm=cfg.analytics.gtm_id;
    if(ga&&/^G-[A-Z0-9]+$/.test(ga)){
      window.dataLayer=window.dataLayer||[];window.gtag=function(){window.dataLayer.push(arguments)};window.gtag('js',new Date());window.gtag('config',ga,{anonymize_ip:true});
      const s=document.createElement('script');s.src='https://www.googletagmanager.com/gtag/js?id='+ga;s.async=true;document.head.append(s);
    }else if(gtm&&/^GTM-[A-Z0-9]+$/.test(gtm)){
      window.dataLayer=window.dataLayer||[];window.dataLayer.push({'gtm.start':Date.now(),event:'gtm.js'});const s=document.createElement('script');s.src='https://www.googletagmanager.com/gtm.js?id='+gtm;s.async=true;document.head.append(s);
    }
  }
  if(cfg.analytics.enabled&&!storage.get('b2b-measurement'))consent.hidden=false;
  all('[data-consent]').forEach(b=>b.addEventListener('click',()=>{storage.set('b2b-measurement',b.dataset.consent);measurement=b.dataset.consent==='granted';consent.hidden=true;enableTracker()}));enableTracker();
  const measure=(event,detail)=>{if(measurement&&cfg.analytics.enabled&&cfg.analytics.ga4_id&&window.gtag)window.gtag('event',event,detail)};
  // Product catalog: preserve filter/hash/search/count and native detail links.
  const search=q('#catalog-search'),grid=q('#catalog-grid');
  function catalog(){
    if(!grid)return;const parts=location.hash.startsWith('#catalog/')?location.hash.slice(9).split('/'):['all'];const [family='all',category='']=parts;const term=search.value.trim().toLocaleLowerCase();let count=0;
    all('.product-card',grid).forEach(card=>{const show=(family==='all'||card.dataset.family===family)&&(!category||card.dataset.category===category)&&(!term||card.dataset.search.includes(term));card.hidden=!show;if(show)count++});
    q('#catalog-count').textContent=ar?'عدد المنتجات المرجعية: '+count:count+' product references';q('#catalog-empty').hidden=count!==0;
    all('a[data-family]').forEach(a=>a.setAttribute('aria-current',String(a.dataset.family===family)));
  }
  if(search){search.value=storage.get('b2b-catalog-query')||'';search.addEventListener('input',()=>{storage.set('b2b-catalog-query',search.value);catalog()});window.addEventListener('hashchange',catalog);catalog()}
  all('.language-link').forEach(a=>a.addEventListener('click',()=>{const url=new URL(a.href);url.hash=location.hash;a.href=url.href}));
  const hover=matchMedia('(hover: hover) and (pointer: fine)');
  all('.series').forEach(series=>{
    const image=q('.series-cover img',series);all('[data-preview]',series).forEach(chip=>{
      chip.addEventListener('pointerenter',()=>{if(hover.matches)image.src=chip.dataset.preview});
      chip.addEventListener('pointerleave',()=>{if(hover.matches)image.src=image.dataset.cover});
      chip.addEventListener('click',e=>{if(!hover.matches){e.preventDefault();image.src=chip.dataset.preview;all('[data-preview]',series).forEach(c=>c.classList.toggle('selected',c===chip))}});
    });series.addEventListener('pointerleave',()=>{if(hover.matches)image.src=image.dataset.cover});
  });
  const track=q('.process-track'),frames=all('.process-frame');let stage=0;
  function setStage(i){stage=Math.max(0,Math.min(frames.length-1,i));frames.forEach((f,n)=>f.classList.toggle('active',n===stage));all('[data-stage-go]').forEach(b=>b.setAttribute('aria-current',String(Number(b.dataset.stageGo)===stage)))}
  if(track){
    let ticking=false;const update=()=>{ticking=false;if(matchMedia('(prefers-reduced-motion: reduce)').matches)return;const r=track.getBoundingClientRect();setStage(Math.round(Math.max(0,Math.min(1,-r.top/(track.offsetHeight-innerHeight)))*(frames.length-1)))};
    window.addEventListener('scroll',()=>{if(!ticking){ticking=true;requestAnimationFrame(update)}},{passive:true});
    all('[data-stage-go]').forEach(b=>b.addEventListener('click',()=>{const i=Number(b.dataset.stageGo);setStage(i);if(!matchMedia('(prefers-reduced-motion: reduce)').matches)window.scrollTo({top:window.scrollY+track.getBoundingClientRect().top+i/(frames.length-1)*(track.offsetHeight-innerHeight),behavior:'smooth'})}));setStage(0);
  }
  const dialog=q('#sample-dialog'),contactKey='b2b-contact-v1';
  const contactNames=['name','company','email','country','whatsapp'];let contacts={};try{contacts=JSON.parse(storage.get(contactKey)||'{}')}catch{}
  all('[data-request-form]').forEach(form=>{
    contactNames.forEach(k=>{if(contacts[k])q('[name="'+k+'"]',form).value=contacts[k]});
    form.addEventListener('input',e=>{if(contactNames.includes(e.target.name)){contacts[e.target.name]=e.target.value;storage.set(contactKey,JSON.stringify(contacts))}});
  });
  q('.dialog-close').addEventListener('click',()=>dialog.close());dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close()}});
  let lastTrigger=null;dialog.addEventListener('close',()=>lastTrigger?.focus());
  function composeBrief(form){
    const selected={};all('[data-chip][aria-pressed="true"]',form).forEach(c=>selected[c.dataset.chipGroup]=c.dataset.chip);
    const input=q('[name=message]',form);const body=input.value.replace(/^\[Brief:[^\]\n]*\]\s*\n?/,'');
    input.value=(Object.keys(selected).length?'[Brief: '+['category','route','volume'].map(k=>selected[k]||'To confirm').join(' | ')+']\n':'')+body;
  }
  all('[data-chip]').forEach(c=>c.addEventListener('click',()=>{const form=c.closest('form');const was=c.getAttribute('aria-pressed')==='true';all('[data-chip-group="'+c.dataset.chipGroup+'"]',form).forEach(b=>b.setAttribute('aria-pressed','false'));c.setAttribute('aria-pressed',String(!was));composeBrief(form)}));
  all('[data-sample]').forEach(trigger=>trigger.addEventListener('click',e=>{
    e.preventDefault();
    lastTrigger=trigger;const form=q('form',dialog),message=q('[name=message]',form);form.hidden=false;q('.request-success',dialog).hidden=true;
    if(trigger.dataset.topic||trigger.dataset.guide)all('[data-chip]',form).forEach(c=>c.setAttribute('aria-pressed','false'));
    if(trigger.dataset.topic)message.value='[Brief: '+trigger.dataset.topic+']\n';
    if(trigger.dataset.guide)message.value='[Buyer guide: '+trigger.dataset.guide+']\n';
    dialog.showModal();q('[name=name]',form).focus();
  }));
  const guidePreset=new URLSearchParams(location.search).get('brief');
  if(guidePreset){const quote=q('[data-request-form="quote"]');if(quote)q('[name=message]',quote).value='[Buyer guide: '+guidePreset.replace(/[\[\]\r\n]/g,' ').slice(0,300)+']\n'}
  all('[data-reset-request]').forEach(button=>button.addEventListener('click',()=>{const area=button.closest('.request-success').parentElement;q('form',area).hidden=false;q('.request-success',area).hidden=true;q('[name=message]',area).value='';all('[data-chip]',area).forEach(c=>c.setAttribute('aria-pressed','false'))}));
  all('[data-request-form]').forEach(form=>{
    let pending=false,previous=null;
    form.addEventListener('submit',async e=>{
      e.preventDefault();if(pending||!form.reportValidity())return;const data=Object.fromEntries(new FormData(form));if(data.contact_url)return;
      const chips={};all('[data-chip][aria-pressed=true]',form).forEach(c=>chips[c.dataset.chipGroup]={label:c.dataset.chip,value:c.dataset.value});
      const isQuote=form.dataset.requestForm==='quote';const fields=isQuote?{
        name:data.name,company:data.company,work_email:data.email,country:data.country,whatsapp:data.whatsapp,series:chips.category?.value||'unsure',product_type:chips.category?.label||'Project to confirm',starting_point:'idea',product_direction:data.direction,formula_concept:'',texture_direction:'',packaging_direction:'',target_market:data.country,expected_quantity:/^\d/.test(chips.volume?.label||'')?(chips.volume.label.match(/[\d,]+/)||[''])[0]:'',launch_timeline:'To confirm',project_context:data.message,consent_inquiry:true,consent_upload:false
      }:{name:data.name,email:data.email,country:data.country,product:chips.category?.label||'Matched samples',message:'Company: '+data.company+'\nWhatsApp: '+(data.whatsapp||'Not provided')+'\nSample request:\n'+data.message,consent:true};
      const bare={source_route:location.pathname,source_context:{source_page:location.pathname},submitted_fields:fields};
      const payload=window.B2BAttribution.withAttribution(bare,{field:isQuote?'project_context':'message',limit:isQuote?800:4000});const fingerprint=JSON.stringify(payload);
      if(previous?.fingerprint!==fingerprint)previous={fingerprint,key:crypto.randomUUID?crypto.randomUUID():String(Date.now())+'-'+Math.random().toString(16).slice(2)};
      const error=q('.form-error',form),submit=q('[type=submit]',form);error.hidden=true;pending=true;submit.disabled=true;const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
      try{
        const response=await fetch('/api/v1/'+(isQuote?'product-brief':'sample-requests'),{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':previous.key},body:fingerprint,signal:controller.signal});const result=await response.json();
        const reference=result.inquiry_id||result.record_id;if(!response.ok||result.receipt_status!=='RECEIVED'||typeof reference!=='string'||!reference)throw new Error('receipt-unavailable');
        const success=q('.request-success',form.parentElement);form.hidden=true;success.hidden=false;q('.reference',success).textContent='Reference: '+reference;
        window.B2BAttribution.completeLead(result,isQuote?'product_brief':'sample_request',measure);
        const actions=q('.success-actions',success);actions.replaceChildren();const wa=new URL('https://wa.me/'+cfg.whatsapp);const topic=(data.message.match(/\[(?:Brief|Buyer guide):([^\]]+)\]/)||[])[1]||'matched samples';wa.searchParams.set('text','Hi '+cfg.name+', I am '+data.name+'. My request: '+topic+'. Could you help with sample pricing?');
        [[wa.href,ar?'تواصل الآن عبر WhatsApp':'Need urgent sample pricing? Chat on WhatsApp now','whatsapp'],[prefix+'/resources/',ar?'تصفح أدلة الشراء':'Browse MOQ & compliance guides','guides']].forEach(([href,label,kind])=>{const a=document.createElement('a');a.href=href;a.textContent=label;a.className='button '+(kind==='whatsapp'?'primary':'secondary');a.dataset.successAction=kind;if(kind==='whatsapp'){a.target='_blank';a.rel='noopener noreferrer'}actions.append(a)});
        success.scrollIntoView({block:'nearest',behavior:'auto'});
      }catch{error.textContent=ar?'تعذر إتمام الطلب. بياناتك محفوظة، يرجى المحاولة مرة أخرى.':'We could not confirm receipt. Your entries are kept; please try again.';error.hidden=false}
      finally{clearTimeout(timer);pending=false;submit.disabled=false}
    });
  });
})();
