// 云岫建站模版 · 交互：移动端菜单、暂停动效。
(function () {
  var toggle = document.querySelector('.nav-toggle');
  var nav = document.getElementById('site-nav');
  if (toggle && nav) {
    toggle.addEventListener('click', function () {
      var open = nav.classList.toggle('open');
      toggle.setAttribute('aria-expanded', String(open));
    });
  }

  var motion = document.querySelector('[data-motion-toggle]');
  if (!document.querySelector('[data-marquee]')) {
    if (motion) motion.hidden = true;
    return;
  }
  var paused = false;
  try { paused = localStorage.getItem('motion-paused') === '1'; } catch (e) {}
  function apply() {
    document.body.classList.toggle('paused', paused);
    if (motion) motion.setAttribute('aria-pressed', String(paused));
  }
  apply();
  if (motion) {
    motion.addEventListener('click', function () {
      paused = !paused;
      try { localStorage.setItem('motion-paused', paused ? '1' : '0'); } catch (e) {}
      apply();
    });
  }
})();
