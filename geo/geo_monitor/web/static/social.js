(function () {
  'use strict';
  var status = document.getElementById('social-copy-status');
  var timer;
  document.querySelectorAll('[data-copy]').forEach(function (button) {
    button.addEventListener('click', async function () {
      try {
        await navigator.clipboard.writeText(button.dataset.copy);
        status.textContent = '链接已复制';
      } catch (_) {
        status.textContent = '暂时无法复制，请打开主页后复制地址栏链接。';
      }
      clearTimeout(timer);
      timer = setTimeout(function () { status.textContent = ''; }, 4500);
    });
  });
  if (window.location.hash === '#account-editor') {
    var editor = document.getElementById('account-editor');
    if (editor) editor.open = true;
  }
})();
