(function () {
  'use strict';
  var input = document.getElementById('workspace-search');
  var results = document.getElementById('workspace-search-results');
  if (!input || !results) return;
  var pages = Array.from(document.querySelectorAll('.nav a')).map(function (a) { return {title:a.textContent.trim(),url:a.getAttribute('href')}; });
  var aliases = {'/analytics':'ga4 analytics 流量 用户 会话 页面','/search-console':'search console gsc 搜索 曝光 排名',
    '/inquiries':'询盘 线索 客户 接收 失败 403 generate_lead 转化 事件','/indexing':'收录 索引 核心 页面 google noindex canonical',
    '/social':'facebook instagram linkedin whatsapp 社交','/social/meta':'facebook instagram meta 粉丝 互动 授权',
    '/optimize':'seo title 标题 关键词 文案 优化 草稿 审核','/ai-overview':'ai 推荐 模拟 概览',
    '/ai':'openai ai 推荐 监测 问题 检测 密钥 费用 记录','/runs':'任务 检测 运行','/google/imports':'csv excel 导入 google 报表',
    '/knowledge':'企业 知识库 产品 资料 文档 上传 分类 搜索 审核 认证 FAQ',
    '/settings':'设置 修改密码 账号 安全 password security','/settings/password':'旧密码 新密码 确认密码 password'};
  var active = -1;
  function hide() { results.hidden = true; input.setAttribute('aria-expanded','false'); input.removeAttribute('aria-activedescendant'); active=-1; }
  function add(title, url, subtitle) {
    var link = document.createElement('a');link.href=url;link.id='workspace-result-'+results.children.length;
    link.setAttribute('role','option');link.setAttribute('aria-selected','false');link.tabIndex=-1;
    var name = document.createElement('span');name.textContent=title;link.appendChild(name);
    if (subtitle) {var small=document.createElement('small');small.textContent=subtitle;link.appendChild(small);}
    results.appendChild(link);
  }
  function show() {
    var q=input.value.trim().slice(0,200);results.replaceChildren();active=-1;
    if (!q) { hide();return; }
    var needle=q.toLocaleLowerCase();
    pages.filter(function(p){return (p.title+' '+(aliases[p.url.split(/[?#]/)[0]]||'')).toLocaleLowerCase().includes(needle);}).slice(0,6).forEach(function(p){add(p.title,p.url,'打开报告或功能');});
    add('查找 AI 检测问题：'+q,(input.form.getAttribute('action')||'/results')+'?'+new URLSearchParams({q:q}),document.body.dataset.workspaceProfile==='growth'?'按问题文字或问题 ID 筛选监测清单':'按问题文字或问题 ID 筛选检测记录');
    add('查找标题优化信号：'+q,'/optimize?'+new URLSearchParams({q:q}),'按已有检索词筛选');
    if(document.body.dataset.workspaceProfile==='growth')add('搜索企业知识库：'+q,'/knowledge?'+new URLSearchParams({q:q}),'查找已审核资料的标题、标签和正文');
    results.hidden=false;input.setAttribute('aria-expanded','true');
  }
  input.addEventListener('input',show);input.addEventListener('focus',function(){if(input.value.trim())show();});
  input.addEventListener('keydown',function(e){
    var links=Array.from(results.querySelectorAll('a'));
    if(e.key==='Escape'){hide();return;}
    if((e.key==='ArrowDown'||e.key==='ArrowUp')&&links.length){e.preventDefault();results.hidden=false;input.setAttribute('aria-expanded','true');active=(active+(e.key==='ArrowDown'?1:-1)+links.length)%links.length;links.forEach(function(a,i){a.classList.toggle('active',i===active);a.setAttribute('aria-selected',String(i===active));});input.setAttribute('aria-activedescendant',links[active].id);links[active].scrollIntoView({block:'nearest'});}
    if(e.key==='Enter'&&!results.hidden&&links.length){e.preventDefault();links[active<0?0:active].click();}
  });
  document.addEventListener('keydown',function(e){if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();input.focus();input.select();}});
  document.addEventListener('click',function(e){if(!input.form.contains(e.target))hide();});
  input.form.addEventListener('focusout',function(){setTimeout(function(){if(!input.form.contains(document.activeElement))hide();},0);});
  document.addEventListener('click',function(e){var account=document.querySelector('.workspace-account');if(account&&!account.contains(e.target))account.open=false;});
  document.addEventListener('keydown',function(e){var account=document.querySelector('.workspace-account');if(e.key==='Escape'&&account&&account.open){account.open=false;account.querySelector('summary').focus();}});
})();
