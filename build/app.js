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
          var slug = btn.dataset.fav;
          function apply() {
            toggle(slug);
            var all = document.querySelectorAll('[data-fav="' + slug + '"]');
            for (var j = 0; j < all.length; j++) paint(all[j]);
          }
          // unfollowing never asks; following asks once
          if (has(slug) || !window.FB_GATE) { apply(); return; }
          FB_GATE(slug, apply);
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
      var face2 = p.img ? 'img/players/' + slug + '.png' : '';
      var av2 = face2
        ? '<div class="pavatar sm"><img src="' + face2 + '" alt="" loading="lazy" ' +
          'onerror="this.parentNode.innerHTML=\'<span>' + p.ini + '</span>\'"></div>'
        : '<div class="pavatar sm"><span>' + p.ini + '</span></div>';
      rows += '<a class="plrow" href="player/' + slug + '.html">' + av2 +
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
          var slug = btn.dataset.fav;
          function apply() {
            toggle(slug); renderMine(); updateCount();
            var all = document.querySelectorAll('[data-fav="' + slug + '"]');
            for (var j = 0; j < all.length; j++) paint(all[j]);
          }
          if (has(slug) || !window.FB_GATE) { apply(); return; }
          FB_GATE(slug, apply);
        });
      })(stars[i]);
    }
  }

  document.addEventListener('favschange', function () { renderMine(); updateCount(); });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

/* ---------- MATCH CENTRE ----------
   Always on. What it shows depends on where "now" sits between matches:
     · anything live  -> always shown first
     · just finished  -> the result stays up until the halfway point to the next
                         kick-off, then the upcoming match takes over
   So if the next game is 7 days away, the last result sits there for 3½ days,
   then flips to "in 3 days" counting down to the next one. */
(function () {
  var MATCH_LEN = 2 * 60 * 60 * 1000;   // treat a match as ~2h long

  function ko(m) { return new Date(m.kickoff).getTime(); }
  function ended(m) { return ko(m) + MATCH_LEN; }

  function statusChip(m, now) {
    if (m.status === 'live') {
      return '<span class="mcstat live"><i></i>' + (m.minute ? m.minute + "'" : 'LIVE') + '</span>';
    }
    if (m.status === 'ft' || ended(m) < now) {
      var ago = now - ended(m);
      var d = Math.floor(ago / 86400000);
      if (d >= 1) return '<span class="mcstat ft">FT · ' + d + (d === 1 ? ' day ago' : ' days ago') + '</span>';
      var h = Math.floor(ago / 3600000);
      if (h >= 1) return '<span class="mcstat ft">FT · ' + h + 'h ago</span>';
      return '<span class="mcstat ft">Full time</span>';
    }
    var until = ko(m) - now;
    var days = Math.floor(until / 86400000);
    if (days >= 1) return '<span class="mcstat soon">in ' + days + (days === 1 ? ' day' : ' days') + '</span>';
    var hrs = Math.floor(until / 3600000);
    if (hrs >= 1) return '<span class="mcstat soon">in ' + hrs + (hrs === 1 ? ' hour' : ' hours') + '</span>';
    var mins = Math.max(0, Math.round(until / 60000));
    return '<span class="mcstat soon">' + (mins <= 1 ? 'Kick-off' : 'in ' + mins + ' min') + '</span>';
  }

  function score(m, now) {
    var played = m.status === 'live' || m.status === 'ft' || ended(m) < now;
    if (!played || m.hs === '' || m.hs === null || m.hs === undefined) {
      var d = new Date(m.kickoff);
      var day = d.toLocaleDateString([], { weekday: 'short' });
      var time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      return '<div class="mcko"><span class="mcday">' + day + '</span>' + time + '</div>';
    }
    return '<div class="mcscore">' + m.hs + '<span>–</span>' + m.as_ + '</div>';
  }

  /* Decide which matches deserve the two slots. */
  function choose(all, now) {
    var live = [], past = [], future = [];
    all.forEach(function (m) {
      if (m.status === 'live' && ended(m) > now) live.push(m);
      else if (m.status === 'ft' || ended(m) <= now) past.push(m);
      else future.push(m);
    });
    past.sort(function (a, b) { return ko(b) - ko(a); });     // most recent first
    future.sort(function (a, b) { return ko(a) - ko(b); });   // soonest first

    if (live.length) return live.concat(future).slice(0, 2);

    var lastM = past[0], nextM = future[0];

    if (lastM && nextM) {
      var midpoint = ended(lastM) + (ko(nextM) - ended(lastM)) / 2;
      // before halfway: the result still leads. after: the next game leads.
      return (now < midpoint) ? [lastM, nextM] : [nextM, lastM];
    }
    if (nextM) return future.slice(0, 2);
    if (lastM) return past.slice(0, 2);
    return [];
  }

  function render() {
    var sec = document.getElementById('mc-sec'), box = document.getElementById('mc');
    if (!sec || !box || !window.FB_MATCHES) return;
    var now = Date.now();
    var all = FB_MATCHES.slice();

    sec.style.display = '';                       // always on

    if (!all.length) {
      box.innerHTML = '<div class="emptybox">No fixtures loaded yet.</div>';
      var m0 = document.getElementById('mc-more');
      if (m0) m0.style.display = 'none';
      return;
    }

    var shown = choose(all, now);

    box.innerHTML = shown.map(function (m) {
      var players = m.players.slice(0, 2).map(function (p) {
        var face = p.photo ? p.photo : (p.img ? 'img/players/' + p.slug + '.png' : '');
        var av = face
          ? '<div class="pavatar sm"><img src="' + face + '" alt="" loading="lazy" ' +
            'onerror="this.parentNode.innerHTML=\'<span>' + p.ini + '</span>\'"></div>'
          : '<div class="pavatar sm"><span>' + p.ini + '</span></div>';
        return '<a class="mcp" href="player/' + p.slug + '.html" title="' + p.n + '">' +
               av + '<span class="mcpn">' + p.n + '</span></a>';
      }).join('');
      if (m.players.length > 2) {
        players += '<span class="mcmore">+' + (m.players.length - 2) + ' more</span>';
      }
      return '<div class="mccard" data-href="match/' + m.id + '.html" role="link" tabindex="0">' +
             '<div class="mcrow">' +
               '<span class="mccomp">' + m.comp + '</span>' + statusChip(m, now) +
             '</div>' +
             '<div class="mcteams"><div class="mct">' + m.home + '</div>' + score(m, now) +
             '<div class="mct right">' + m.away + '</div></div>' +
             '<div class="mcplayers">' + players + '</div></div>';
    }).join('');

    var cards = box.querySelectorAll('.mccard');
    for (var c = 0; c < cards.length; c++) {
      (function (card) {
        function go(e) {
          if (e.target.closest && e.target.closest('a')) return;
          location.href = card.getAttribute('data-href');
        }
        card.addEventListener('click', go);
        card.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(e); }
        });
      })(cards[c]);
    }

    var more = document.getElementById('mc-more');
    if (more) {
      if (all.length > shown.length) {
        more.style.display = '';
        more.textContent = 'See all ' + all.length + ' →';
      } else {
        more.style.display = 'none';
      }
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', render);
  else render();
  setInterval(render, 60000);
})();

/* ---------- FOLLOW PROMPT ----------
   A small toast that slides in the first time someone opens a player page,
   then again every 10th player after that. Dismissed by tapping or following. */
(function () {
  var KEY = 'fb_seen_players_v1', DISMISS = 'fb_prompt_off_v1';
  var slug = document.body.getAttribute('data-player');
  if (!slug) return;                                   // only on player pages
  if (localStorage.getItem(DISMISS) === '1') return;   // user closed it for good

  var seen;
  try { seen = JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) { seen = []; }
  if (seen.indexOf(slug) === -1) { seen.push(slug); }
  try { localStorage.setItem(KEY, JSON.stringify(seen)); } catch (e) {}

  var n = seen.length;
  if (!(n === 1 || n % 10 === 0)) return;
  if (window.FB && FB.has(slug)) return;               // already following this one

  function show() {
    var t = document.createElement('div');
    t.className = 'fbtoast';
    t.innerHTML =
      '<div class="fbt-star">★</div>' +
      '<div class="fbt-copy"><b>Follow this player</b>' +
      '<span>Tap the star to get their goals, ratings and kick-offs.</span></div>' +
      '<button class="fbt-x" aria-label="Dismiss">×</button>';
    document.body.appendChild(t);
    requestAnimationFrame(function () { t.classList.add('in'); });

    var starBtn = document.querySelector('.starbtn');
    if (starBtn) {
      starBtn.classList.add('pulse');
      setTimeout(function () { starBtn.classList.remove('pulse'); }, 4000);
    }

    function close(forever) {
      t.classList.remove('in');
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 400);
      if (forever) { try { localStorage.setItem(DISMISS, '1'); } catch (e) {} }
    }
    t.querySelector('.fbt-x').addEventListener('click', function (e) { e.stopPropagation(); close(true); });
    t.addEventListener('click', function () {
      if (starBtn) starBtn.click();
      close(false);
    });
    document.addEventListener('favschange', function () { close(false); });
    setTimeout(function () { close(false); }, 8000);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function(){ setTimeout(show, 700); });
  else setTimeout(show, 700);
})();

/* ---------- EMAIL GATE ----------
   The first time someone follows a player we ask for an email, so their list
   can be synced and alerts can be sent. Asked once per browser, then never again. */
(function () {
  var EKEY = 'fb_email_v1';

  function saved() { try { return localStorage.getItem(EKEY) || ''; } catch (e) { return ''; } }
  window.FB_EMAIL = saved;

  function send(email, slug) {
    try { localStorage.setItem(EKEY, email); } catch (e) {}
    var url = window.FB_SUBSCRIBE_URL;
    if (url) {
      try {
        fetch(url, {
          method: 'POST', mode: 'no-cors',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: 'email=' + encodeURIComponent(email) + '&players=' + encodeURIComponent(slug || '')
        });
      } catch (e) {}
    }
  }

  /* returns true if we can follow straight away */
  window.FB_GATE = function (slug, proceed) {
    if (saved()) { proceed(); return; }

    var wrap = document.createElement('div');
    wrap.className = 'fbgate';
    wrap.innerHTML =
      '<div class="fbg-card" role="dialog" aria-modal="true" aria-labelledby="fbg-t">' +
        '<button class="fbg-x" aria-label="Cancel">×</button>' +
        '<div class="fbg-star">★</div>' +
        '<h3 id="fbg-t">Save your players</h3>' +
        '<p>Pop in your email and we\'ll keep your list safe — and let you know ' +
           'when your players are about to play, score or get rated.</p>' +
        '<form class="fbg-form">' +
          '<input type="email" required placeholder="your@email.ie" aria-label="Email address" autocomplete="email">' +
          '<button type="submit">Follow</button>' +
        '</form>' +
        '<div class="fbg-fine">One email to set up. Unsubscribe any time.</div>' +
      '</div>';
    document.body.appendChild(wrap);
    requestAnimationFrame(function () { wrap.classList.add('in'); });
    var input = wrap.querySelector('input');
    setTimeout(function () { input.focus(); }, 260);

    function close() {
      wrap.classList.remove('in');
      setTimeout(function () { if (wrap.parentNode) wrap.parentNode.removeChild(wrap); }, 280);
      document.removeEventListener('keydown', onKey);
    }
    function onKey(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);

    wrap.querySelector('.fbg-x').addEventListener('click', close);
    wrap.addEventListener('click', function (e) { if (e.target === wrap) close(); });
    wrap.querySelector('.fbg-form').addEventListener('submit', function (e) {
      e.preventDefault();
      var v = input.value.trim();
      if (!v || v.indexOf('@') === -1) { input.focus(); return; }
      send(v, slug);
      close();
      proceed();
    });
  };
})();

/* ---------- BACK LINK ----------
   "← Back" returns to wherever you actually came from. We mark the session on
   each page view, so if this isn't the first page in the tab we can safely use
   history. A direct landing (shared link, search result) uses the href fallback. */
(function () {
  var NAV = 'fb_nav_v1';
  var visitedBefore = false;
  try {
    visitedBefore = sessionStorage.getItem(NAV) === '1';
    sessionStorage.setItem(NAV, '1');
  } catch (e) {}

  var links = document.querySelectorAll('.crumb[data-back]');
  if (!links.length) return;

  for (var i = 0; i < links.length; i++) {
    links[i].addEventListener('click', function (e) {
      if (visitedBefore && history.length > 1) {
        e.preventDefault();
        history.back();
      }
    });
  }
})();


/* ---------- NAV DATE ----------
   Written in the browser so it's right even if the site was built days ago. */
(function () {
  var el = document.getElementById('navdate');
  if (!el) return;
  var d = new Date();
  var mon = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  el.textContent = d.getDate() + ' ' + mon[d.getMonth()];
})();

/* ---------- ADMIN DOOR ----------
   Double-click the wordmark to sign in. Accounts and roles come from
   data/manual/accounts.csv. Remembered after the first sign-in.
   Note: this check runs in the browser — it keeps visitors out, it is not
   server-side security. */
(function () {
  var SESS = 'fb_session_v1';
  var mark = document.querySelector('.mark');
  if (!mark) return;

  function adminUrl() {
    var inSub = /\/(player|club|news|match)\//.test(location.pathname);
    return (inSub ? '../' : '') + 'build/admin.html';
  }
  function session() {
    try { return JSON.parse(localStorage.getItem(SESS) || 'null'); } catch (e) { return null; }
  }

  async function sha256(s) {
    if (window.crypto && crypto.subtle && window.isSecureContext) {
      var buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
      return Array.from(new Uint8Array(buf)).map(function (b) {
        return b.toString(16).padStart(2, '0');
      }).join('');
    }
    return sha256js(s);
  }
  function sha256js(ascii) {
    function rr(n, x) { return (x >>> n) | (x << (32 - n)); }
    var K = [], H = [], i, j, p = 2, primes = [];
    function isP(n){ for(var d=2;d*d<=n;d++) if(n%d===0) return false; return true; }
    while (primes.length < 64) { if (isP(p)) primes.push(p); p++; }
    for (i = 0; i < 64; i++) K[i] = (Math.pow(primes[i], 1/3) % 1 * Math.pow(2,32)) | 0;
    for (i = 0; i < 8; i++) H[i] = (Math.pow(primes[i], 1/2) % 1 * Math.pow(2,32)) | 0;
    var bytes = unescape(encodeURIComponent(ascii)), len = bytes.length, words = [];
    for (i = 0; i < len; i++) words[i>>2] |= bytes.charCodeAt(i) << ((3 - i % 4) * 8);
    words[len>>2] |= 0x80 << ((3 - len % 4) * 8);
    words[((len + 8 >> 6) + 1) * 16 - 1] = len * 8;
    var w = [], a,b,c,d,e,f,g,h,t1,t2;
    for (j = 0; j < words.length; j += 16) {
      a=H[0];b=H[1];c=H[2];d=H[3];e=H[4];f=H[5];g=H[6];h=H[7];
      for (i = 0; i < 64; i++) {
        w[i] = i < 16 ? (words[j+i]|0) :
          (rr(17,w[i-2])^rr(19,w[i-2])^(w[i-2]>>>10)) + (w[i-7]|0) +
          (rr(7,w[i-15])^rr(18,w[i-15])^(w[i-15]>>>3)) + (w[i-16]|0);
        t1 = h + (rr(6,e)^rr(11,e)^rr(25,e)) + ((e&f)^(~e&g)) + K[i] + (w[i]|0);
        t2 = (rr(2,a)^rr(13,a)^rr(22,a)) + ((a&b)^(a&c)^(b&c));
        h=g;g=f;f=e;e=(d+t1)|0;d=c;c=b;b=a;a=(t1+t2)|0;
      }
      H[0]=(H[0]+a)|0;H[1]=(H[1]+b)|0;H[2]=(H[2]+c)|0;H[3]=(H[3]+d)|0;
      H[4]=(H[4]+e)|0;H[5]=(H[5]+f)|0;H[6]=(H[6]+g)|0;H[7]=(H[7]+h)|0;
    }
    return H.map(function(x){ return ((x>>>0).toString(16)).padStart(8,'0'); }).join('');
  }

  function open() {
    if (session()) { location.href = adminUrl(); return; }

    var wrap = document.createElement('div');
    wrap.className = 'fbgate';
    wrap.innerHTML =
      '<div class="fbg-card" role="dialog" aria-modal="true">' +
        '<button class="fbg-x" aria-label="Close">×</button>' +
        '<h3>Sign in</h3><p>Staff access.</p>' +
        '<form class="fbg-form" style="flex-direction:column">' +
          '<input type="email" id="ae" placeholder="Email" autocomplete="username" required>' +
          '<input type="password" id="ap" placeholder="Password" autocomplete="current-password" required>' +
          '<button type="submit" style="width:100%">Sign in</button>' +
        '</form>' +
        '<div class="nlnote" id="aerr" style="display:none">Wrong email or password.</div>' +
      '</div>';
    document.body.appendChild(wrap);
    requestAnimationFrame(function () { wrap.classList.add('in'); });
    setTimeout(function () { var e = wrap.querySelector('#ae'); if (e) e.focus(); }, 240);

    function close() {
      wrap.classList.remove('in');
      setTimeout(function () { if (wrap.parentNode) wrap.parentNode.removeChild(wrap); }, 280);
      document.removeEventListener('keydown', onKey);
    }
    function onKey(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);
    wrap.querySelector('.fbg-x').addEventListener('click', close);
    wrap.addEventListener('click', function (e) { if (e.target === wrap) close(); });

    wrap.querySelector('.fbg-form').addEventListener('submit', async function (e) {
      e.preventDefault();
      var em = wrap.querySelector('#ae').value.trim().toLowerCase();
      var pw = wrap.querySelector('#ap').value;
      var h = await sha256(em + ':' + pw);
      var accounts = window.FB_ACCOUNTS || [];
      var found = null;
      for (var i = 0; i < accounts.length; i++) {
        if ((accounts[i].email || '').toLowerCase() === em && accounts[i].hash === h) { found = accounts[i]; break; }
      }
      if (found) {
        try {
          localStorage.setItem(SESS, JSON.stringify({
            email: found.email, name: found.name, role: found.role, hash: h
          }));
        } catch (err) {}
        close();
        location.href = adminUrl();
      } else {
        wrap.querySelector('#aerr').style.display = 'block';
        wrap.querySelector('#ap').value = '';
        wrap.querySelector('#ap').focus();
      }
    });
  }

  var clicks = 0, timer = null;
  mark.addEventListener('click', function (e) {
    e.preventDefault();
    clicks++;
    if (clicks === 1) {
      timer = setTimeout(function () { clicks = 0; location.href = mark.getAttribute('href'); }, 260);
    } else {
      clearTimeout(timer); clicks = 0; open();
    }
  });
  var hold;
  mark.addEventListener('touchstart', function () {
    hold = setTimeout(function () { clicks = 0; clearTimeout(timer); open(); }, 650);
  }, {passive:true});
  ['touchend','touchmove','touchcancel'].forEach(function (ev) {
    mark.addEventListener(ev, function () { clearTimeout(hold); }, {passive:true});
  });
})();

/* ---------- LOCAL KICK-OFF TIMES ----------
   Match pages are built with UTC timestamps; show them in the visitor's own
   timezone so they match the homepage. */
(function () {
  var els = document.querySelectorAll('[data-ko]');
  for (var i = 0; i < els.length; i++) {
    (function (el) {
      var d = new Date(el.getAttribute('data-ko'));
      if (isNaN(d)) return;
      var time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      if (el.classList.contains('ko-time')) {
        el.textContent = time;
      } else {
        el.textContent = time + ' · ' + d.toLocaleDateString([], {
          weekday: 'short', day: 'numeric', month: 'short'
        });
      }
    })(els[i]);
  }
})();

/* ---------- REPORT A PROBLEM ----------
   A small button on every page. Knows which page it's on, so a report from a
   player page arrives already tagged with that player. */
(function () {
  function context() {
    var slug = document.body.getAttribute('data-player');
    if (slug) {
      var h1 = document.querySelector('.pdname');
      return { kind: 'player', label: h1 ? h1.textContent.trim() : slug };
    }
    if (/\/match\//.test(location.pathname)) {
      var t = document.querySelectorAll('.mteam');
      return { kind: 'match', label: t.length > 1 ? t[0].textContent.trim() + ' v ' + t[1].textContent.trim() : 'this match' };
    }
    if (/\/club\//.test(location.pathname)) {
      var c = document.querySelector('h1');
      return { kind: 'club', label: c ? c.textContent.trim() : 'this club' };
    }
    return { kind: 'page', label: document.title.replace(' — FOOTBALLERS', '') };
  }

  function open(preset) {
    var ctx = context();
    var wrap = document.createElement('div');
    wrap.className = 'fbgate';
    wrap.innerHTML =
      '<div class="fbg-card rep" role="dialog" aria-modal="true">' +
        '<button class="fbg-x" aria-label="Close">×</button>' +
        '<h3>Report a problem</h3>' +
        '<p>Spotted a missing player or something wrong? Tell us and we\'ll fix it.</p>' +
        '<form class="repform">' +
          '<label>What\'s wrong</label>' +
          '<select id="rt">' +
            '<option value="missing">A player is missing</option>' +
            '<option value="wrong">Wrong stats or details</option>' +
            '<option value="club">Wrong club or league</option>' +
            '<option value="match">Something wrong with a match</option>' +
            '<option value="other">Something else</option>' +
          '</select>' +
          '<label>Details</label>' +
          '<textarea id="rd" rows="4" placeholder="' +
            (ctx.kind === 'player' ? 'e.g. he signed for a new club last week' :
             ctx.kind === 'match'  ? 'e.g. the score is wrong' :
             'e.g. Séamus Coleman isn\'t listed') + '"></textarea>' +
          '<label>Your email <span class="opt">optional — only if you want a reply</span></label>' +
          '<input type="email" id="re" placeholder="you@email.ie" autocomplete="email">' +
          '<input type="text" id="rw" class="hp" tabindex="-1" autocomplete="off" aria-hidden="true">' +
          '<button type="submit">Send report</button>' +
        '</form>' +
        '<div class="repctx">About: <b>' + ctx.label + '</b></div>' +
        '<div class="nlnote" id="rerr" style="display:none"></div>' +
      '</div>';
    document.body.appendChild(wrap);
    requestAnimationFrame(function () { wrap.classList.add('in'); });
    if (preset) { var sel = wrap.querySelector('#rt'); if (sel) sel.value = preset; }
    setTimeout(function () { var d = wrap.querySelector('#rd'); if (d) d.focus(); }, 240);

    function close() {
      wrap.classList.remove('in');
      setTimeout(function () { if (wrap.parentNode) wrap.parentNode.removeChild(wrap); }, 280);
      document.removeEventListener('keydown', onKey);
    }
    function onKey(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);
    wrap.querySelector('.fbg-x').addEventListener('click', close);
    wrap.addEventListener('click', function (e) { if (e.target === wrap) close(); });

    wrap.querySelector('.repform').addEventListener('submit', async function (e) {
      e.preventDefault();
      var err = wrap.querySelector('#rerr');
      var details = wrap.querySelector('#rd').value.trim();
      if (details.length < 5) { err.style.display = 'block'; err.textContent = 'Add a bit more detail.'; return; }
      var btn = wrap.querySelector('.repform button');
      btn.disabled = true; btn.textContent = 'Sending…';
      try {
        var res = await fetch('/api/report', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            type: wrap.querySelector('#rt').value,
            details: details,
            email: wrap.querySelector('#re').value.trim(),
            website: wrap.querySelector('#rw').value,
            subject: ctx.label,
            page: location.pathname + location.search
          })
        });
        var out = await res.json().catch(function () { return {}; });
        if (!res.ok) throw new Error(out.error || 'Could not send.');
        wrap.querySelector('.fbg-card').innerHTML =
          '<div class="repdone"><div class="tick">✓</div><h3>Thanks</h3>' +
          '<p>That\'s logged. We\'ll get it sorted.</p></div>';
        setTimeout(close, 1900);
      } catch (ex) {
        btn.disabled = false; btn.textContent = 'Send report';
        err.style.display = 'block';
        err.textContent = ex.message || 'Could not send. Try again later.';
      }
    });
  }

  window.FB_REPORT = open;

  // floating button on every page
  var b = document.createElement('button');
  b.className = 'repbtn';
  b.setAttribute('aria-label', 'Report a problem');
  b.innerHTML = '<span>!</span> Report';
  b.addEventListener('click', function () { open(); });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { document.body.appendChild(b); });
  } else { document.body.appendChild(b); }

  // "player missing?" prompt when a search finds nothing
  document.addEventListener('DOMContentLoaded', function () {
    var empty = document.getElementById('empty');
    if (!empty) return;
    var obs = new MutationObserver(function () {
      if (empty.style.display !== 'none' && !empty.querySelector('.repmiss')) {
        var a = document.createElement('button');
        a.className = 'repmiss';
        a.textContent = 'Player missing? Tell us →';
        a.addEventListener('click', function () { open('missing'); });
        empty.appendChild(a);
      }
    });
    obs.observe(empty, { attributes: true, attributeFilter: ['style'] });
  });
})();

/* ---------- LAST UPDATED ----------
   The build stamps the newest data file; turn it into "updated 4 hours ago". */
(function () {
  var el = document.querySelector('.updated');
  if (!el) return;
  var stamp = el.getAttribute('data-stamp');
  var d = new Date(stamp);
  if (isNaN(d)) { el.textContent = ''; return; }

  function render() {
    var mins = Math.round((Date.now() - d.getTime()) / 60000);
    var rel;
    if (mins < 1)        rel = 'just now';
    else if (mins < 60)  rel = mins + (mins === 1 ? ' minute ago' : ' minutes ago');
    else if (mins < 1440) {
      var h = Math.floor(mins / 60);
      rel = h + (h === 1 ? ' hour ago' : ' hours ago');
    } else {
      var days = Math.floor(mins / 1440);
      rel = days + (days === 1 ? ' day ago' : ' days ago');
    }
    var when = d.toLocaleString([], {
      day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'
    });
    el.textContent = 'Data updated ' + rel + ' · ' + when;
  }
  render();
  setInterval(render, 60000);
})();
