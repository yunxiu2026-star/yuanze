(function () {
  'use strict';
  var wide = window.matchMedia('(min-width: 761px)');
  document.querySelectorAll('[data-responsive-filter]').forEach(function (panel) {
    panel.open = wide.matches;
    wide.addEventListener('change', function () { panel.open = wide.matches; });
  });
  var status = document.getElementById('ops-copy-status');
  document.querySelectorAll('[data-copy-url]').forEach(function (button) {
    button.addEventListener('click', async function () {
      try {
        await navigator.clipboard.writeText(button.dataset.copyUrl);
        if (status) status.textContent = '网址已复制，可粘贴到 Search Console 顶部检查框。';
      } catch (_) {
        if (status) status.textContent = '暂时无法使用剪贴板，请选择上方网址并手动复制。';
      }
    });
  });
})();
