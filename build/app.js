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

/* ---------- MATCH CENTRE ----------
   Shows a match only if it kicks off within the next hour, is in progress,
   or finished less than an hour ago. Hides the whole section otherwise.
   Filtered in the browser so the window follows the visitor's clock. */
(function () {
  var WINDOW_MS = 60 * 60 * 1000;

  function within(m, now) {
    var ko = new Date(m.kickoff).getTime();
    if (isNaN(ko)) return false;
    var since = now - ko;
    // live: only while the match could plausibly still be on
    if (m.status === 'live') return since >= -WINDOW_MS && since < (2.75 * 60 * 60 * 1000);
    // finished: show for about an hour after the final whistle
    if (m.status === 'ft')   return since >= 0 && since < (3 * 60 * 60 * 1000);
    // upcoming: appears once it's an hour out or less
    return (ko - now) <= WINDOW_MS && ko > now;
  }

  function statusChip(m, now) {
    if (m.status === 'live') return '<span class="mcstat live"><i></i>' + (m.minute ? m.minute + "'" : 'LIVE') + '</span>';
    if (m.status === 'ft')   return '<span class="mcstat ft">FT</span>';
    var mins = Math.max(0, Math.round((new Date(m.kickoff).getTime() - now) / 60000));
    return '<span class="mcstat soon">' + (mins <= 1 ? 'Kick-off' : 'in ' + mins + ' min') + '</span>';
  }

  function score(m) {
    if (m.status === 'scheduled' || m.hs === '' || m.hs === null)
      return '<div class="mcko">' + new Date(m.kickoff).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) + '</div>';
    return '<div class="mcscore">' + m.hs + '<span>–</span>' + m.as_ + '</div>';
  }

  function render() {
    var sec = document.getElementById('mc-sec'), box = document.getElementById('mc');
    if (!sec || !box || !window.FB_MATCHES) return;
    var now = Date.now();
    var live = FB_MATCHES.filter(function (m) { return within(m, now); });

    if (!live.length) { sec.style.display = 'none'; return; }
    sec.style.display = '';

    // live games first, then kicking off soon, then finished
    var rank = { live: 0, scheduled: 1, ft: 2 };
    live.sort(function (a, b) {
      var d = (rank[a.status] === undefined ? 9 : rank[a.status]) -
              (rank[b.status] === undefined ? 9 : rank[b.status]);
      if (d !== 0) return d;
      return new Date(a.kickoff) - new Date(b.kickoff);
    });

    var shown = live.slice(0, 3);
    box.innerHTML = shown.map(function (m) {
      var players = m.players.slice(0, 2).map(function (p) {
        return '<a class="mcp" href="player/' + p.slug + '.html" title="' + p.n + '">' +
               '<div class="pavatar sm"><span>' + p.ini + '</span></div>' +
               '<span class="mcpn">' + p.n + '</span></a>';
      }).join('');
      if (m.players.length > 2) {
        players += '<span class="mcmore">+' + (m.players.length - 2) + ' more</span>';
      }
      return '<div class="mccard">' +
             '<div class="mcrow">' +
               '<span class="mccomp">' + m.comp + '</span>' + statusChip(m, now) +
             '</div>' +
             '<div class="mcteams"><div class="mct">' + m.home + '</div>' + score(m) +
             '<div class="mct right">' + m.away + '</div></div>' +
             '<div class="mcplayers">' + players + '</div></div>';
    }).join('');

    var more = document.getElementById('mc-more');
    if (more) {
      if (live.length > 3) {
        more.style.display = '';
        more.textContent = 'See all ' + live.length + ' →';
      } else {
        more.style.display = 'none';
      }
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', render);
  else render();
  setInterval(render, 60000);   // re-check every minute so it appears/disappears on its own
})();
