/* footballers.ie — favourites
   Stored in the browser (localStorage). No account needed.
   Powers: star toggles, the "Your players" rail, and the alerts page. */
(function () {
  var KEY = 'fb_favs_v1';

  function read() {
    try { return JSON.parse(localStorage.getItem(KEY)) || []; }
    catch (e) { return []; }
  }
  function write(list) {
    try { localStorage.setItem(KEY, JSON.stringify(list)); } catch (e) {}
    document.dispatchEvent(new CustomEvent('favschange', { detail: list }));
  }
  function has(slug) { return read().indexOf(slug) > -1; }
  function toggle(slug) {
    var list = read(), i = list.indexOf(slug);
    if (i > -1) list.splice(i, 1); else list.push(slug);
    write(list);
    return list.indexOf(slug) > -1;
  }

  window.FB = { read: read, write: write, has: has, toggle: toggle };

  /* ---- wire up every star on the page ---- */
  function paint(btn) {
    var on = has(btn.dataset.fav);
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.title = on ? 'Remove from your players' : 'Add to your players';
  }
  function init() {
    var stars = document.querySelectorAll('[data-fav]');
    for (var i = 0; i < stars.length; i++) {
      (function (btn) {
        paint(btn);
        btn.addEventListener('click', function (e) {
          e.preventDefault(); e.stopPropagation();
          toggle(btn.dataset.fav);
          var all = document.querySelectorAll('[data-fav="' + btn.dataset.fav + '"]');
          for (var j = 0; j < all.length; j++) paint(all[j]);
        });
      })(stars[i]);
    }
    renderMine();
    updateCount();
  }

  function updateCount() {
    var n = read().length;
    var els = document.querySelectorAll('[data-fav-count]');
    for (var i = 0; i < els.length; i++) els[i].textContent = n;
    var nav = document.getElementById('nav-mine');
    if (nav) nav.style.display = n ? '' : 'none';
  }

  /* ---- "Your players" rail on the homepage ---- */
  function renderMine() {
    var wrap = document.getElementById('myplayers');
    if (!wrap || !window.FB_PLAYERS) return;
    var favs = read();
    var empty = document.getElementById('myplayers-empty');

    if (!favs.length) {
      wrap.innerHTML = '';
      if (empty) empty.style.display = 'block';
      var sec = document.getElementById('myplayers-sec');
      if (sec) sec.style.display = 'none';
      return;
    }
    if (empty) empty.style.display = 'none';
    var sec2 = document.getElementById('myplayers-sec');
    if (sec2) sec2.style.display = '';

    var rows = '';
    favs.forEach(function (slug) {
      var p = window.FB_PLAYERS[slug];
      if (!p) return;
      var when = p.next ? p.next : 'No fixture listed';
      rows += '<a class="plrow" href="player/' + slug + '.html">' +
              '<div class="pavatar sm"><span>' + p.ini + '</span></div>' +
              '<div class="nm">' + p.n + ' <span class="cl">' + p.club + '</span></div>' +
              '<div class="ev">' + when + '</div>' +
              '<button class="star on" data-fav="' + slug + '" aria-pressed="true">★</button></a>';
    });
    wrap.innerHTML = rows;

    var stars = wrap.querySelectorAll('[data-fav]');
    for (var i = 0; i < stars.length; i++) {
      (function (btn) {
        btn.addEventListener('click', function (e) {
          e.preventDefault(); e.stopPropagation();
          toggle(btn.dataset.fav);
          renderMine(); updateCount();
          var all = document.querySelectorAll('[data-fav="' + btn.dataset.fav + '"]');
          for (var j = 0; j < all.length; j++) paint(all[j]);
        });
      })(stars[i]);
    }
  }

  document.addEventListener('favschange', function () { renderMine(); updateCount(); });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
