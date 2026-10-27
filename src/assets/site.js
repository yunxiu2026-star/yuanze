// 云岫建站模版 · 交互：流程金线入场、移动端菜单、暂停动效。
(function () {
  document.documentElement.classList.add('js');

  // 流程金线：滚动到可见时加 .in-view，触发画线与依次淡入
  var paths = document.querySelectorAll('.facts-path');
  if (paths.length && 'IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add('in-view'); io.unobserve(e.target); }
      });
    }, { threshold: 0.35 });
    paths.forEach(function (el) { io.observe(el); });
  } else {
    paths.forEach(function (el) { el.classList.add('in-view'); });
  }

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
