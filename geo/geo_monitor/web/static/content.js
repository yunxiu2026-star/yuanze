(function () {
  'use strict';
  var dirty = new Set();
  var guarded = Array.from(document.querySelectorAll('form[data-protect-edits]'));
  function fingerprint(form) {
    return Array.from(form.querySelectorAll('input:not([type=hidden]),textarea,select')).map(function (field) {
      return [field.name, field.value];
    }).map(function (pair) { return JSON.stringify(pair); }).join('|');
  }
  function updateSaveStatus() {
    document.querySelectorAll('[data-save-status]').forEach(function (el) {
      el.textContent = dirty.size ? '有修改尚未保存' : '已打开保存版本';
      el.classList.toggle('is-dirty', dirty.size > 0);
    });
  }
  guarded.forEach(function (form) {
    var original = fingerprint(form);
    var initiallyUnsaved = form.dataset.unsavedInitial === '1';
    if (initiallyUnsaved) dirty.add(form);
    function track() {
      if (initiallyUnsaved || fingerprint(form) !== original) dirty.add(form); else dirty.delete(form);
      updateSaveStatus();
    }
    form.addEventListener('input', track);
    form.addEventListener('change', track);
    form.addEventListener('submit', function (event) {
      var otherEdits = Array.from(dirty).some(function (item) { return item !== form; });
      if (otherEdits && !mayLeave(function () { form.requestSubmit(event.submitter); })) { event.preventDefault(); return; }
      if (!event.defaultPrevented) dirty.clear();
    }, true);
  });
  updateSaveStatus();
  var leaveDialog = document.getElementById('unsaved-dialog');
  var pendingLeave;
  function mayLeave(action) {
    if (!dirty.size) return true;
    pendingLeave = action;
    leaveDialog.showModal();
    leaveDialog.querySelector('[data-stay]').focus();
    return false;
  }
  if (leaveDialog) {
    leaveDialog.querySelector('[data-stay]').addEventListener('click', function () { pendingLeave = null; leaveDialog.close(); });
    leaveDialog.addEventListener('cancel', function () { pendingLeave = null; });
    leaveDialog.querySelector('[data-leave]').addEventListener('click', function () {
      var action = pendingLeave; pendingLeave = null;
      dirty.clear(); updateSaveStatus(); leaveDialog.close();
      if (action) action();
    });
  }
  document.addEventListener('click', function (event) {
    var link = event.target.closest('a[href]');
    if (!link || link.target === '_blank' || event.ctrlKey || event.metaKey || event.shiftKey || event.button) return;
    var target = new URL(link.href, location.href);
    if (target.pathname === location.pathname && target.search === location.search && target.hash) return;
    if (!mayLeave(function () { window.location.assign(target.href); })) event.preventDefault();
  });
  document.querySelectorAll('form:not([data-protect-edits])').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      if (!mayLeave(function () { form.requestSubmit(event.submitter); })) event.preventDefault();
    }, true);
  });
  window.addEventListener('beforeunload', function (event) {
    if (dirty.size) { event.preventDefault(); event.returnValue = ''; }
  });
  var status = document.getElementById('opt-copy-status');
  var title = document.getElementById('draft-title');
  var timer;
  function count() {
    if (title) document.getElementById('title-count').textContent = Array.from(title.value).length + ' 个字符（仅供编辑参考）';
  }
  if (title) title.addEventListener('input', count);
  document.querySelectorAll('[data-use-title]').forEach(function (button) {
    button.addEventListener('click', function () {
      title.value = button.dataset.useTitle;
      title.dispatchEvent(new Event('input', {bubbles:true}));
      count();
      title.focus();
      status.textContent = '已填入所选标题，可继续编辑。';
      clearTimeout(timer); timer = setTimeout(function () { status.textContent = ''; }, 4000);
    });
  });
  document.querySelectorAll('[data-copy-editor]').forEach(function (button) {
    button.addEventListener('click', async function () {
      var fields = [['标题', 'draft-title'], ['H1', 'draft-h1'], ['描述', 'draft-description'], ['建议扩展词', 'draft-secondary'], ['内容提纲', 'draft-outline']];
      var text = fields.map(function (pair) { return pair[0] + '\n' + document.getElementById(pair[1]).value; }).join('\n\n');
      try {
        await navigator.clipboard.writeText(text);
        status.textContent = '当前编辑文案已复制。';
      } catch (_) {
        status.textContent = '暂时无法访问剪贴板，请在编辑框中选择并复制文字。';
      }
      clearTimeout(timer); timer = setTimeout(function () { status.textContent = ''; }, 4500);
    });
  });
})();

document.querySelectorAll('[data-copy-aeo]').forEach(function(button){button.addEventListener('click',async function(){var status=document.getElementById('opt-copy-status');try{var response=await fetch(button.dataset.copyAeo,{credentials:'same-origin'});if(!response.ok){var detail=await response.json().catch(function(){return {};});throw new Error(detail.detail||'导出未完成，请先核对审核状态与 FAQ。');}var text=await response.text();var output=document.querySelector('[data-aeo-output]');output.hidden=false;output.value=text;try{await navigator.clipboard.writeText(text);status.textContent='已复制审核版本；下方保留可手动复制的片段。';}catch(_){output.focus();output.select();status.textContent='片段已展开，请复制选中的内容。';}}catch(error){status.textContent=error.message;}});});
