// Small progressive enhancements: auto-submit selects, chart tooltip.
(function () {
  var root = document.documentElement;
  var saved;
  try { saved = localStorage.getItem('b2b-theme'); } catch (_) {}
  root.dataset.theme = saved === 'dark' ? 'dark' : 'light';
  var theme = document.querySelector('.theme-toggle');
  function syncTheme() {
    if (!theme) return;
    var dark = root.dataset.theme === 'dark';
    theme.textContent = dark ? '浅色模式' : '深色模式';
    theme.setAttribute('aria-label', dark ? '切换浅色模式' : '切换深色模式');
    theme.setAttribute('aria-pressed', String(dark));
    var themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) themeColor.content = dark ? '#071d1a' : '#f5f1e8';
  }
  syncTheme();
  if (theme) theme.addEventListener('click', function () {
    root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('b2b-theme', root.dataset.theme); } catch (_) {}
    syncTheme();
  });
  var menu = document.querySelector('.menu-toggle');
  var sidebar = document.querySelector('.side');
  var scrim = document.querySelector('.workspace-scrim');
  var menuButtons = document.querySelectorAll('[aria-controls="workspace-sidebar"]');
  var desktop = window.matchMedia('(min-width: 1101px)');
  var navCollapsed = false;
  try { navCollapsed = localStorage.getItem('b2b-nav-collapsed') === '1'; } catch (_) {}
  function setMenu(open, persist) {
    if (!sidebar) return;
    sidebar.hidden = !open;
    sidebar.classList.toggle('open', open);
    root.dataset.navState = desktop.matches && open ? 'expanded' : 'collapsed';
    if (scrim) scrim.hidden = !open || desktop.matches;
    menuButtons.forEach(function (button) { button.setAttribute('aria-expanded', String(open)); });
    if (menu) menu.setAttribute('aria-label', open ? '收起文字导航' : '展开文字导航');
    if (desktop.matches && persist) {
      navCollapsed = !open;
      try { localStorage.setItem('b2b-nav-collapsed', navCollapsed ? '1' : '0'); } catch (_) {}
    }
  }
  function closeMenu() { setMenu(false, true); }
  if (menu && sidebar) {
    setMenu(desktop.matches && !navCollapsed, false);
    desktop.addEventListener('change', function () { setMenu(desktop.matches && !navCollapsed, false); });
    menuButtons.forEach(function (button) { button.addEventListener('click', function () { setMenu(sidebar.hidden, true); if (!desktop.matches && !sidebar.hidden) sidebar.querySelector('a').focus(); }); });
    var navClose = sidebar.querySelector('.nav-close');
    if (navClose) navClose.addEventListener('click', function () { closeMenu(); menu.focus(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !sidebar.hidden) { closeMenu(); menu.focus(); } });
    if (scrim) scrim.addEventListener('click', closeMenu);
    document.addEventListener('click', function (e) { if (!desktop.matches && !sidebar.hidden && !sidebar.contains(e.target) && !e.target.closest('[aria-controls="workspace-sidebar"]')) closeMenu(); });
  }
  document.querySelectorAll('.nav a.on').forEach(function (a) { a.setAttribute('aria-current', 'page'); });
  document.querySelectorAll('.analytics-rail a.on').forEach(function (a) { a.setAttribute('aria-current', 'page'); });
  document.querySelectorAll('.table-wrap').forEach(function (el) { el.tabIndex = 0; el.setAttribute('role', 'region'); el.setAttribute('aria-label', '数据表格，可横向滚动'); });
  document.querySelectorAll('form[method="post"]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      if (event.defaultPrevented) return;
      if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) { event.preventDefault(); return; }
      var button = form.querySelector('button[type="submit"]');
      if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); button.textContent = '正在处理…'; }
    });
  });
  // Queue pages refresh only while a request is pending, without losing entered form values.
  if (document.querySelector('[data-run-queue="pending"]')) window.setTimeout(function () {
    if (!document.hidden && !document.querySelector('form:focus-within')) window.location.reload();
  }, 5000);
  document.querySelectorAll("select[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { el.form && el.form.submit(); });
  });

  var SERIES = ["--series-1", "--series-2", "--series-3", "--series-4"];

  document.querySelectorAll("[data-chart]").forEach(function (wrap) {
    var svg = wrap.querySelector("svg");
    var tip = wrap.querySelector(".tooltip");
    var cross = svg && svg.querySelector(".crosshair");
    if (!svg || !tip || !cross) return;

    function show(col) {
      var x = parseFloat(col.getAttribute("data-x"));
      cross.setAttribute("x1", x);
      cross.setAttribute("x2", x);
      cross.classList.add("on");
      tip.textContent = "";
      var head = document.createElement("b");
      head.textContent = col.getAttribute("data-date") + " · 第 " + col.getAttribute("data-run") + " 次";
      tip.appendChild(head);
      col.getAttribute("data-vals").split("|").forEach(function (pair, i) {
        var k = pair.lastIndexOf(":");
        var name = pair.slice(0, k), val = pair.slice(k + 1);
        var row = document.createElement("div");
        row.className = "row";
        var left = document.createElement("span");
        var sw = document.createElement("i");
        sw.className = "swatch";
        sw.style.background = "var(" + SERIES[i % SERIES.length] + ")";
        left.appendChild(sw);
        left.appendChild(document.createTextNode(name));
        var right = document.createElement("span");
        right.textContent = val === "" ? "—" : val + "%";
        row.appendChild(left);
        row.appendChild(right);
        tip.appendChild(row);
      });
      tip.hidden = false;
      var box = svg.getBoundingClientRect();
      var scale = box.width / svg.viewBox.baseVal.width;
      var px = x * scale;
      var left = px + 12;
      if (left + tip.offsetWidth > box.width) left = px - tip.offsetWidth - 12;
      tip.style.left = Math.max(0, left) + "px";
      tip.style.top = "8px";
    }
    function hide() { tip.hidden = true; cross.classList.remove("on"); }

    svg.querySelectorAll(".hover-col").forEach(function (col) {
      col.addEventListener("mouseenter", function () { show(col); });
      col.addEventListener("click", function () { show(col); });
      col.setAttribute('tabindex', '0');
      col.setAttribute('role', 'button');
      col.setAttribute('aria-label', col.getAttribute('data-date') + '，第 ' + col.getAttribute('data-run') + ' 次检测');
      col.addEventListener('focus', function () { show(col); });
      col.addEventListener('blur', hide);
      col.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); show(col); } });
    });
    svg.addEventListener("mouseleave", hide);
  });
})();
