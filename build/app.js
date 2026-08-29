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

  /* followed matches, same shape */
  var MKEY = 'fb_favm_v1';
  function mread() { try { return JSON.parse(localStorage.getItem(MKEY)) || []; } catch (e) { return []; } }
  function mwrite(list) { try { localStorage.setItem(MKEY, JSON.stringify(list)); } catch (e) {} document.dispatchEvent(new CustomEvent('favschange', { detail: list })); }
  function mhas(id) { return mread().indexOf(id) > -1; }
  function mtoggle(id) { var l = mread(), i = l.indexOf(id); if (i > -1) l.splice(i, 1); else l.push(id); mwrite(l); return l.indexOf(id) > -1; }

  /* analytics: no-ops when tracking is off or the script hasn't loaded */
  function track(ev, props) {
    try { if (window.posthog && posthog.capture) posthog.capture(ev, props || {}); } catch (e) {}
  }
  function identify(email) {
    try {
      if (!email || !window.posthog || !posthog.identify) return;
      var em = String(email).trim().toLowerCase();
      posthog.identify(em, { email: em });
    } catch (e) {}
  }
  window.FB_TRACK = track;
  window.FB_IDENTIFY = identify;

  window.FB = { read: read, write: write, has: has, toggle: toggle, mread: mread, mhas: mhas, mtoggle: mtoggle };

  /* keep the account (email + follows) in sync server-side. no password: nothing here is secret. */
  var syncT;
  function sync() {
    var email = window.FB_EMAIL && FB_EMAIL(); if (!email) return;
    clearTimeout(syncT);
    syncT = setTimeout(function () {
      try {
        fetch('/api/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: email, source: 'sync', players: read().join(';'), matches: mread().join(';') }) });
      } catch (e) {}
    }, 800);
  }
  document.addEventListener('favschange', sync);

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
            track(has(slug) ? 'player_unfollowed' : 'player_followed', { player: slug });
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
    var mstars = document.querySelectorAll('[data-favm]');
    for (var k = 0; k < mstars.length; k++) {
      (function (btn) {
        function mpaint() { var on = mhas(btn.dataset.favm); btn.classList.toggle('on', on); btn.setAttribute('aria-pressed', on ? 'true' : 'false');
          var sp = btn.querySelector('span'); if (sp) sp.textContent = on ? 'Following match' : 'Follow match'; }
        mpaint();
        btn.addEventListener('click', function (e) {
          e.preventDefault(); e.stopPropagation();
          var id = btn.dataset.favm;
          function apply() {
            track(mhas(id) ? 'match_unfollowed' : 'match_followed', { match: id });
            mtoggle(id); mpaint();
          }
          if (mhas(id) || !window.FB_GATE) { apply(); return; }
          FB_GATE(id, apply);
        });
      })(mstars[k]);
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
      if (empty) {
        var hid = 0; try { hid = +localStorage.getItem('fb_fs_hide') || 0; } catch (e) {}
        empty.style.display = (Date.now() - hid < 86400000) ? 'none' : 'flex';
        var x = document.getElementById('fsx');
        if (x && !x._wired) { x._wired = true; x.addEventListener('click', function () {
          try { localStorage.setItem('fb_fs_hide', String(Date.now())); } catch (e) {}
          empty.style.display = 'none'; }); }
      }
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
      var when = (window.FB_WHEN && p.nextdate) ? FB_WHEN(p.nextdate) : '';
      var line = when
        ? 'Playing ' + (p.nextopp || '') + (p.nextha ? ' (' + p.nextha + ')' : '') + ' ' + when
        : (p.next || 'No fixture listed');
      var rate = p.rating ? p.rating : '';
      var face2 = p.img ? 'img/players/' + slug + '.png' : '';
      var av2 = face2
        ? '<div class="pavatar sm"><img src="' + face2 + '" alt="" loading="lazy" ' +
          'onerror="this.parentNode.innerHTML=\'<span>' + p.ini + '</span>\'"></div>'
        : '<div class="pavatar sm"><span>' + p.ini + '</span></div>';
      rows += '<a class="plrow" href="player/' + slug + '.html">' + av2 +
              '<div class="nm">' + p.n + '<span class="cl">' + line + '</span></div>' +
              '<div class="mn">' + (rate
                 ? '<span class="rate ' + (parseFloat(rate) >= 7.3 ? 'hi' : parseFloat(rate) >= 6.5 ? 'md' : 'lo') + ' sm">' + rate + '</span>'
                 : '<span class="rate none">—</span>') + '</div>' +
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
            track(has(slug) ? 'player_unfollowed' : 'player_followed', { player: slug });
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

  var LIVE_STAMP = 0;   // when live.json was written (ms). 0 = unknown
  function liveMin(m) {
    var base = parseInt(m.minute, 10);
    if (isNaN(base)) return m.minute || '';
    var extra = (m._stamp || LIVE_STAMP) ? Math.floor((Date.now() - (m._stamp || LIVE_STAMP)) / 60000) : 0;
    if (extra < 0 || extra > 20) extra = 0;                      // stale feed: don't invent time
    var mm = base + extra;
    if (base <= 45 && mm > 45) mm = 45;                          // don't tick through half time
    if (base <= 90 && mm > 90 && base > 45) mm = 90;
    return String(mm) + (String(m.minute).indexOf('+') > -1 ? String(m.minute).slice(String(m.minute).indexOf('+')) : '');
  }
  function statusChip(m, now) {
    if (m.status === 'live') {
      return '<span class="mcstat live"><i></i>' + (m.minute ? liveMin(m) + "'" : 'LIVE') + '</span>';
    }
    if (m.status === 'ft' || ended(m) < now) {
      var ago = now - ended(m);
      var d = Math.floor(ago / 86400000);
      if (d >= 1) return '<span class="mcstat ft">FT · ' + d + (d === 1 ? ' day ago' : ' days ago') + '</span>';
      var h = Math.floor(ago / 3600000);
      if (h >= 1) return '<span class="mcstat ft">FT · ' + h + 'h ago</span>';
      return '<span class="mcstat ft">Full time</span>';
    }
    // Past kick-off but the live feed hasn't marked it live yet (its status is
    // still "scheduled"). Don't sit on "Kick-off" for two hours — estimate the
    // minute from the clock so an underway game reads as underway. The real
    // feed overwrites this the moment it arrives.
    if (now >= ko(m) && ended(m) >= now) {
      var el = Math.floor((now - ko(m)) / 60000);
      var lbl;
      if (el <= 45) lbl = "~" + Math.max(1, el) + "'";
      else if (el < 60) lbl = 'HT';
      else if (el < 105) lbl = "~" + (el - 15) + "'";      // knock off a 15-min break
      else lbl = "~90'";
      return '<span class="mcstat live"><i></i>' + lbl + '</span>';
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
      var time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
      return '<div class="mcko"><span class="mcday">' + day + '</span>' + time + '</div>';
    }
    return '<div class="mcscore">' + m.hs + '<span>–</span>' + m.as_ + '</div>';
  }

  /* Decide which matches deserve the two slots. */
  var NEARLY = 15 * 60 * 1000;   // a match counts as "on" from 15 minutes
                                 // before kick-off to 15 minutes after it ends

  function isOn(m, now) {
    if (m.status === 'live' && ended(m) > now) return true;
    return now >= ko(m) - NEARLY && now <= ended(m) + NEARLY;
  }

  /* Which two matches lead the page.
     Anything with a player you follow wins outright — that's the whole point
     of following someone. Then whatever is actually on. Then the generator's
     score, which weighs the competition, the biggest name in it, and how many
     of ours are involved. */
  function rank(all, now) {
    var favs = [];
    try { favs = JSON.parse(localStorage.getItem('fb_favs_v1')) || []; } catch (e) {}
    var mine = {};
    favs.forEach(function (s) { mine[s] = 1; });

    return all.map(function (m) {
      var yours = m.players.some(function (p) { return mine[p.slug]; });
      var on = isOn(m, now);
      var done = m.status === 'ft' || ended(m) <= now;
      var score = (m.pull || 0)
                + (yours ? 10000 : 0)
                + (on ? 5000 : 0)
                + (done ? 0 : 200);          // a game to come beats one gone by
      // among equals, soonest upcoming and most recent finished come first
      score -= Math.abs(now - ko(m)) / 36e5 * 0.4;
      return { m: m, score: score, on: on };
    }).sort(function (a, b) { return b.score - a.score; });
  }

  /* Which two lead the page:
       1. Anything live (or about to be / just finished) always wins.
       2. Otherwise the last result holds its spot until halfway to the next
          game, then the next game takes over — with the score picking WHICH
          result and WHICH next game, decayed by distance in time so a big
          match tomorrow beats a small one tonight, but not one next week. */
  function decayed(r, now) {
    var m = r.m;
    var hrs = Math.abs(now - (m.status === 'ft' || ended(m) <= now ? ended(m) : ko(m))) / 36e5;
    return r.score - hrs * 1.5;
  }
  function choose(all, now) {
    var ranked = rank(all, now);
    if (!ranked.length) return [];
    var on = ranked.filter(function (r) { return r.on; });
    if (on.length) {
      var rest = ranked.filter(function (r) { return !r.on; });
      return on.slice(0, 2).concat(rest).slice(0, 2).map(function (r) { return r.m; });
    }
    var past = [], future = [];
    ranked.forEach(function (r) {
      (r.m.status === 'ft' || ended(r.m) <= now ? past : future).push(r);
    });
    past.sort(function (a, b) { return decayed(b, now) - decayed(a, now); });
    // any game kicking off TODAY outranks any game on a later day, whatever
    // the fixture quality - nobody wants tomorrow's match while today has one
    function dayOf(t) { var d = new Date(t); return d.getFullYear() * 400 + d.getMonth() * 32 + d.getDate(); }
    var todayKey = dayOf(now);
    function bucket(r) { return dayOf(ko(r.m)) === todayKey ? 0 : 1; }
    future.sort(function (a, b) {
      if (bucket(a) !== bucket(b)) return bucket(a) - bucket(b);
      return decayed(b, now) - decayed(a, now);
    });
    var lastM = past[0] && past[0].m, nextM = future[0] && future[0].m;
    if (lastM && nextM) {
      var mid = ended(lastM) + (ko(nextM) - ended(lastM)) / 2;
      return now < mid ? [lastM, nextM] : [nextM, (future[1] && future[1].m) || lastM];
    }
    if (nextM) return future.slice(0, 2).map(function (r) { return r.m; });
    if (lastM) return past.slice(0, 2).map(function (r) { return r.m; });
    return [];
  }

  function avatarHtml(p, root) {
    var face = p.photo ? p.photo : (p.img ? root + 'img/players/' + p.slug + '.png' : '');
    return face
      ? '<div class="pavatar sm"><img src="' + face + '" alt="" loading="lazy" ' +
        'onerror="this.parentNode.innerHTML=\'<span>' + p.ini + '</span>\'"></div>'
      : '<div class="pavatar sm"><span>' + p.ini + '</span></div>';
  }
  function chip(p, root) {
    return '<a class="mcp" href="' + root + 'player/' + p.slug + '.html" title="' + p.n + '">' +
           avatarHtml(p, root) + '<span class="mcpn">' + p.n + '</span></a>';
  }
  function playersHtml(m, root, limit) {
    /* Squad lists are already ordered best-first by the generator, so showing
       three names and a count beats forty chips or a wall of faces. */
    if (m.sq) limit = (window.innerWidth <= 640 ? 2 : 3);
    var list = limit ? m.players.slice(0, limit) : m.players;
    var h = list.map(function (p) { return chip(p, root); }).join('');
    if (limit && m.players.length > limit) h += '<span class="mcmore">+' + (m.players.length - limit) + ' more</span>';
    return h;
  }

  function bdg(id, cls) {
    if (!id) return '<span class="badge ' + cls + ' generic"></span>';
    return '<img class="badge ' + cls + '" src="https://images.fotmob.com/image_resources/logo/teamlogo/' + id + '.png" alt="" loading="lazy" onerror="this.outerHTML=\'<span class=&quot;badge ' + cls + ' generic&quot;></span>\'">';
  }
  function card(m, now, limit, root) {
    root = root || '';
    return '<div class="mccard' + (m.loi || m.sq ? ' loi' : '') + '" data-href="' + root + 'match/' + m.id + '.html" role="link" tabindex="0">' +
           '<div class="mcrow">' +
             '<span class="mccomp">' + m.comp + '</span>' + statusChip(m, now) +
           '</div>' +
           '<div class="mcteams"><div class="mct">' + bdg(m.hb, 'sm') + m.home + '</div>' + score(m, now) +
           '<div class="mct right">' + m.away + bdg(m.ab, 'sm') + '</div></div>' +
           '<div class="mcplayers">' + playersHtml(m, root, limit) + '</div></div>';
  }

  function wireCards(box) {
    var cards = box.querySelectorAll('.mccard,.fxmatch,.fxnext');
    for (var c = 0; c < cards.length; c++) {
      (function (card) {
        function go(e) {
          if (e.target.closest && e.target.closest('a')) return;
          var h = card.getAttribute('data-href'), d = card.getAttribute('data-day');
          if (d) { fxSel = d; renderAll(); return; }
          if (h) location.href = h;
        }
        card.addEventListener('click', go);
        card.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(e); }
        });
      })(cards[c]);
    }
  }

  /* ---------- Fixtures page (FotMob-style) ----------
     One day at a time, a scrollable date strip on top, matches grouped by
     competition. Abroad / LOI / All filter. Opens on today. */
  var fxSel = null, fxFilter = 'abroad', fxStripBuilt = false;
  function dayKey(d) { return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate(); }
  function midRow(m, now) {
    var played = m.status === 'live' || m.status === 'ft' || ended(m) < now;
    if (m.status === 'live') return '<div class="mid sc lv">' + m.hs + ' – ' + m.as_ + '<br><small>' + (m.minute ? liveMin(m) + "'" : 'LIVE') + '</small></div>';
    if (played && m.hs !== '' && m.hs !== null && m.hs !== undefined) return '<div class="mid sc">' + m.hs + ' – ' + m.as_ + '</div>';
    if (played) return '<div class="mid">FT</div>';
    return '<div class="mid">' + new Date(m.kickoff).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) + '</div>';
  }
  function renderAll() {
    var box = document.getElementById('fxall'), strip = document.getElementById('fxdays');
    if (!box || !strip || !window.FB_MATCHES) return;
    var now = Date.now(), today = new Date(now), todayKey = dayKey(today);
    var pass = function (m) { return fxFilter === 'all' || (fxFilter === 'loi' ? !!m.loi : !m.loi); };
    var all = FB_MATCHES.filter(pass).sort(function (a, b) { return ko(a) - ko(b); });
    var byDay = {};
    all.forEach(function (m) { var k = dayKey(new Date(m.kickoff)); (byDay[k] = byDay[k] || []).push(m); });
    if (!fxSel) fxSel = todayKey;

    // date strip: 14 days back, 21 forward
    var html = '';
    for (var i = -14; i <= 21; i++) {
      var d = new Date(today.getFullYear(), today.getMonth(), today.getDate() + i);
      var k = dayKey(d), label;
      if (i === 0) label = 'Today'; else if (i === -1) label = 'Yesterday'; else if (i === 1) label = 'Tomorrow';
      else label = d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });
      html += '<button data-day="' + k + '" class="' + (k === fxSel ? 'on ' : '') + (byDay[k] ? 'has' : '') + '">' + label + '</button>';
    }
    strip.innerHTML = html;
    var on = strip.querySelector('.on');
    if (on && on.scrollIntoView) on.scrollIntoView({ inline: 'center', block: 'nearest' });

    if (!fxStripBuilt) {
      fxStripBuilt = true;
      strip.addEventListener('click', function (e) { var b = e.target.closest('button'); if (b) { fxSel = b.getAttribute('data-day'); renderAll(); } });
      var f = document.getElementById('fxfilt');
      if (f) f.addEventListener('click', function (e) { var b = e.target.closest('button'); if (!b) return;
        [].forEach.call(f.children, function (x) { x.classList.toggle('on', x === b); });
        fxFilter = b.getAttribute('data-f');
        if (window.FB_TRACK) FB_TRACK('fixtures_filter', { filter: fxFilter });
        renderAll(); });
    }


    var ms = byDay[fxSel] || [];
    if (!ms.length) {
      var sp = fxSel.split('-'), dayEnd = new Date(+sp[0], sp[1] - 1, +sp[2] + 1).getTime();
      var next = all.filter(function (m) { return ko(m) >= dayEnd; })[0];
      box.innerHTML = '<div class="fxnone">No matches this day</div>' +
        (next ? '<div style="text-align:center"><div class="fxnext" data-day="' + dayKey(new Date(next.kickoff)) + '" role="button" tabindex="0">' +
                '<span>' + next.home + '</span><span><b>' + new Date(next.kickoff).toLocaleDateString([], { weekday: 'long' }) + '</b>' +
                '<small>' + new Date(next.kickoff).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) + '</small></span>' +
                '<span>' + next.away + '</span></div></div>' : '');
      wireCards(box); return;
    }
    var comps = [], byComp = {};
    ms.forEach(function (m) { if (!byComp[m.comp]) { byComp[m.comp] = []; comps.push(m.comp); } byComp[m.comp].push(m); });
    box.innerHTML = comps.map(function (c) {
      var rows = byComp[c].map(function (m) {
        return '<div class="fxmatch' + (m.loi || m.sq ? ' loi' : '') + '" data-href="match/' + m.id + '.html" role="link" tabindex="0">' +
               '<div class="fxt"><div class="h">' + m.home + bdg(m.hb, 'sm') + '</div>' + midRow(m, now) + '<div class="a">' + bdg(m.ab, 'sm') + m.away + '</div></div>' +
               (m.ven ? '<div class="fxven">' + m.ven + '</div>' : '') +
               '<div class="fxp">' + playersHtml(m, '', 0) + '</div></div>';
      }).join('');
      return '<div class="fxcomp"><h4><span>' + c + '</span><small>' + byComp[c].length + (byComp[c].length === 1 ? ' match' : ' matches') + '</small></h4>' + rows + '</div>';
    }).join('');
    wireCards(box);

  }

  function renderHead() {
    var head = document.getElementById('mhead');
    if (!head || !window.FB_MATCHES || !FB_MATCHES.length) return;
    var m = FB_MATCHES[0], now = Date.now();
    var chip = document.getElementById('mchip'), sw = document.getElementById('mscorewrap');
    if (chip) chip.innerHTML = statusChip(m, now);
    if (sw) sw.innerHTML = (m.status !== 'scheduled' && m.hs !== '' && m.hs !== null && m.hs !== undefined)
      ? '<div class="mscore">' + m.hs + '<span>–</span>' + m.as_ + '</div>'
      : '<div class="mscore ko">' + new Date(m.kickoff).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) + '</div>';
  }
  function render() {
    renderAll(); renderHead();
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

    box.innerHTML = shown.map(function (m) { return card(m, now, 2, ''); }).join('');

    wireCards(box);

    var more = document.getElementById('mc-more');
    if (more) {
      if (all.length > shown.length) {
        more.style.display = '';
        more.textContent = 'See all →';
      } else {
        more.style.display = 'none';
      }
    }
  }

  /* Live scores. First choice: /api/live, which asks the source directly
     the moment a browser polls, edge-cached ~25s — genuinely live. Fallback:
     the old live.json file the scraper commits, for when the function is
     down. The old path alone is why scores froze for hours: it depends on
     GitHub running a schedule it frequently just doesn't run. */
  function liveWindow(m, now) {
    if (m.status === 'ft') return false;          // final is final
    if (m.status === 'live') return true;
    // "scheduled" long past kick-off means the built page is stale — keep
    // asking for up to five hours so a late visitor still gets the result
    return now >= ko(m) - 20 * 60 * 1000 && now - ko(m) <= 5 * 3600 * 1000;
  }

  /* Match pages: the api can send the goals and cards along with the score,
     and this redraws the Timeline section from them, so events show up while
     the game is still on instead of after the next site build. */
  /* While a game is live, mark the subs on the rendered teamsheet: red down
     arrow on whoever went off (pitch), green up arrow on whoever came on
     (bench). The teamsheet is the truth about who started, so if the pair
     reads backwards, flip it. */
  function applyLiveSubs(subs) {
    if (!subs.length) return;
    function norm(t) {
      return String(t || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
        .replace(/[^a-z ]/gi, '').toLowerCase().trim();
    }
    var pitch = {}, bench = {};
    Array.prototype.forEach.call(document.querySelectorAll('.pitch .lup'), function (el) {
      pitch[norm(el.getAttribute('title') || el.textContent)] = el;
    });
    Array.prototype.forEach.call(document.querySelectorAll('.benchcol .bp'), function (el) {
      bench[norm(el.textContent)] = el;
    });
    function mark(el, cls, min) {
      if (!el || el.querySelector('.' + cls)) return;
      var i = document.createElement('span');
      i.className = cls;
      i.textContent = (cls === 'soff' ? '\u25bc ' : '\u25b2 ') + min + '\u2032';
      el.appendChild(i);
      // Keep the tap-sheet in step. Without this the pitch shows the player
      // coming off while his own panel still reports no minutes for the match.
      var key = el.getAttribute('data-mstat');
      var st = key && window.FB_MSTATS && window.FB_MSTATS[key];
      if (st) st[cls] = min;
    }
    subs.forEach(function (e) {
      var off = norm(e.player), on = norm(e.sin);
      if (bench[off] || pitch[on]) {          // pair arrived backwards
        var t = off; off = on; on = t;
      }
      mark(pitch[off], 'soff', e.min);
      mark(bench[on], 'son', e.min);
    });
  }

  function paintTimeline(evs) {
    var box = document.getElementById('mtl');
    if (!box) return;
    function esc(t) { return String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
    function norm(t) {
      return String(t || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
        .replace(/[^a-z ]/gi, '').toLowerCase().trim();
    }
    var irish = [];
    var ST = window.FB_MSTATS || {};
    for (var k in ST) irish.push(norm(ST[k].n));
    function isIrish(name) {
      var n = norm(name);
      if (!n) return false;
      for (var i = 0; i < irish.length; i++) {
        var f = irish[i];
        if (f === n) return true;
        var a = f.split(' '), b = n.split(' ');
        if (a.length > 1 && b.length > 1 &&
            a[a.length - 1] === b[b.length - 1] && a[0].charAt(0) === b[0].charAt(0)) return true;
      }
      return false;
    }
    var LAB = { own_goal: 'own goal', missed_penalty: 'penalty missed', penalty: 'penalty',
                second_yellow: 'second yellow', red: 'red card', yellow: 'yellow card' };
    applyLiveSubs(evs.filter(function (e) { return e.type === 'sub'; }));
    var rows = '', hgoals = [], agoals = [];
    evs.forEach(function (e) {
      var t = e.type;
      if (t === 'sub') return;
      var side = e.home ? 'h' : 'a';
      var who = esc(e.player);
      if (isIrish(e.player)) who = '<b class="ir">' + who + '</b>';
      var ic = { yellow: '<i class="cd y"></i>', red: '<i class="cd r"></i>',
                 second_yellow: '<i class="cd y2"></i>', missed_penalty: '\u2715' }[t] || '\u26bd';
      var lab = LAB[t] && t !== 'goal' ? ' <small>' + LAB[t] + '</small>' : '';
      var cell = '<span class="evwho">' + who + '</span>' + lab;
      rows += '<div class="tl ' + side + ' ' + t + '"><div class="tlh">' + (side === 'h' ? cell : '') + '</div>'
        + '<div class="tlm">' + esc(e.min) + "'" + '<span class="tli">' + ic + '</span></div>'
        + '<div class="tla">' + (side === 'a' ? cell : '') + '</div></div>';
      if (t === 'goal' || t === 'own_goal' || t === 'penalty') {
        (e.home ? hgoals : agoals).push(esc(e.player) + ' ' + esc(e.min) + "'" + (t === 'own_goal' ? ' (og)' : ''));
      }
    });
    if (!rows) return;
    // Every goal is already a row above with its minute, so a scorer summary
    // underneath is the same information twice. The legend is kept because the
    // server rendered one and this replaces the whole box.
    box.innerHTML = '<div class="sec"><h2>Timeline</h2></div>'
      + '<div class="timeline">' + rows + '</div>'
      + '<div class="rmnote">Irish players in <b class="ir">green</b>.'
      + ' Goals, cards and missed penalties only.</div>';
  }

  async function refreshApi(now) {
    var want = (window.FB_MATCHES || []).filter(function (m) {
      return m.fmid && liveWindow(m, now);
    });
    if (!want.length) return true;            // nothing on — nothing to ask
    var ids = [];
    want.forEach(function (m) { if (ids.indexOf(m.fmid) < 0) ids.push(m.fmid); });
    var wantEv = !!document.getElementById('mtl');
    var res = await fetch('/api/live?ids=' + ids.slice(0, 20).join(',') + (wantEv ? '&full=1' : ''), { cache: 'no-store' });
    if (!res.ok) return false;
    var data = await res.json();
    if (!data || !data.matches) return false;
    var stamp = data.updated ? Date.parse(data.updated) : Date.now();
    want.forEach(function (m) {
      var l = data.matches[m.fmid];
      if (!l) return;
      m.status = l.status || m.status;
      if (l.minute !== undefined) { m.minute = l.minute; m._stamp = stamp; }
      if (l.hs !== null && l.hs !== undefined) m.hs = l.hs;
      if (l.as !== null && l.as !== undefined) m.as_ = l.as;
      if (l.ev && l.ev.length) paintTimeline(l.ev);
    });
    render();
    var el = document.querySelector('.updated');
    if (el && data.updated) el.setAttribute('data-stamp', data.updated);
    return true;
  }

  async function refreshLegacy() {
    var res = await fetch('/live.json?t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) return;
    var data = await res.json();
    var list = Array.isArray(data) ? data : (data.matches || []);
    if (!list.length) return;

    if (data.updated) { var st = Date.parse(data.updated); if (!isNaN(st)) LIVE_STAMP = st; }
    var byId = {};
    (window.FB_MATCHES || []).forEach(function (m) { byId[m.id] = m; });

    list.forEach(function (l) {
      var m = byId[l.id];
      if (!m) return;                       // only update what the site knows about
      if (l.status) m.status = l.status;
      if (l.minute !== undefined) m.minute = l.minute;
      if (l.hs !== undefined && l.hs !== null) m.hs = l.hs;
      if (l.as_ !== undefined && l.as_ !== null) m.as_ = l.as_;
      if (l.home_score !== undefined && l.home_score !== null) m.hs = l.home_score;
      if (l.away_score !== undefined && l.away_score !== null) m.as_ = l.away_score;
    });
    render();
    var stamp = document.querySelector('.updated');
    if (stamp && data.updated) stamp.setAttribute('data-stamp', data.updated);
  }

  async function refresh() {
    try {
      if (await refreshApi(Date.now())) return;
    } catch (e) { /* fall through to the file */ }
    try { await refreshLegacy(); } catch (e) { /* offline — the page still stands */ }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', render);
  else render();
  setInterval(render, 30000);

  refresh();
  setInterval(refresh, 30000);
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
    if (window.FB_IDENTIFY) FB_IDENTIFY(email);
    if (window.FB_TRACK) FB_TRACK('email_saved', { source: 'follow', player: slug || '' });
    try { localStorage.setItem(EKEY, email); } catch (e) {}
    // save it where we can see it
    try {
      fetch('/api/subscribe', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email, source: 'follow', players: slug || '' })
      });
    } catch (e) {}
    // and to the newsletter provider, if one is set up
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
      var time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
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
    return { kind: 'page', label: document.title.replace(' — footballers.ie', '') };
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
        if (window.FB_TRACK) FB_TRACK('report_sent', { type: wrap.querySelector('#rt').value, page: location.pathname });
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
  setInterval(render, 30000);
})();

/* ---------- WHEN IS HE PLAYING ----------
   Turn a fixture date into "today at 17:00", "tomorrow at 19:45", "in 3 days". */
(function () {
  function parseWhen(s) {
    if (!s) return null;
    s = String(s).trim();
    var d = null;
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) d = new Date(s.length > 10 ? s : s + 'T00:00:00');
    if (!d || isNaN(d)) {
      // "26 Aug" style — assume the nearest such date from now
      var m = s.match(/^(\d{1,2})\s+([A-Za-z]{3})/);
      if (!m) return null;
      var mons = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'];
      var mi = mons.indexOf(m[2].toLowerCase());
      if (mi < 0) return null;
      var now = new Date();
      d = new Date(now.getFullYear(), mi, +m[1]);
      if (d - now < -1000 * 60 * 60 * 24 * 200) d.setFullYear(now.getFullYear() + 1);
    }
    return d;
  }

  function hasTime(s) { return /T\d{2}:\d{2}/.test(String(s || '')); }

  window.FB_WHEN = function (raw) {
    var d = parseWhen(raw);
    if (!d) return '';
    var now = new Date();
    var time = hasTime(raw)
      ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
      : '';

    var startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var startThat  = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var days = Math.round((startThat - startToday) / 86400000);

    if (days < 0)  return 'earlier';
    if (days === 0) return time ? 'today at ' + time : 'today';
    if (days === 1) return time ? 'tomorrow at ' + time : 'tomorrow';
    if (days < 7)  return 'in ' + days + ' days';
    if (days < 14) return 'in a week';
    if (days < 31) return 'in ' + Math.round(days / 7) + ' weeks';
    return 'in ' + Math.round(days / 30) + (Math.round(days / 30) === 1 ? ' month' : ' months');
  };

  /* homepage milestones: out of the pool the page ships, show the first four
     that are either a player you follow or a name people actually know. */
  (function () {
    var cards = document.querySelectorAll('.msgrid .mscard[data-slug]');
    if (!cards.length) return;
    var favs = {};
    try { (JSON.parse(localStorage.getItem('fb_favs_v1')) || []).forEach(function (s) { favs[s] = 1; }); } catch (e) {}
    var shown = 0;
    for (var i = 0; i < cards.length; i++) {
      var c = cards[i];
      var keep = favs[c.getAttribute('data-slug')] || c.getAttribute('data-fam') === '1';
      if (keep && shown < 4) { c.style.display = ''; shown++; }
    }
    if (!shown) {
      var sec = document.querySelector('.msgrid');
      if (sec) {
        var head = sec.previousElementSibling;
        sec.style.display = 'none';
        if (head && head.classList.contains('sec')) head.style.display = 'none';
      }
    }
  })();

  // player pages: "Playing Watford (A) tomorrow at 19:45"
  function paintFixtures() {
    var rows = document.querySelectorAll('.fxrow.when');
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var when = FB_WHEN(r.getAttribute('data-when'));
      var opp = r.getAttribute('data-opp') || '';
      var ha = r.getAttribute('data-ha') || '';
      var cell = r.querySelector('.fxwhen');
      if (!cell || !when) continue;
      cell.innerHTML = '<b>' + opp + '</b>' +
        (ha ? ' <span class="ha">' + ha + '</span>' : '') +
        '<span class="wsub">' + when + '</span>';
      // the data hasn't caught up yet: the game kicked off hours ago, so it
      // belongs in Recent matches, not here — hide it rather than lie
      var t = Date.parse(r.getAttribute('data-when'));
      if (!isNaN(t) && Date.now() - t > 150 * 60 * 1000) r.style.display = 'none';
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', paintFixtures);
  else paintFixtures();
  setInterval(paintFixtures, 60000);
})();


/* ---------- SIGNUP FORMS ----------
   Newsletter box and the alerts page post to /api/subscribe so the address
   lands in the admin, whether or not a mail provider is connected. */
(function () {
  var forms = document.querySelectorAll('form.nlform, form.alertform');
  for (var i = 0; i < forms.length; i++) {
    (function (f) {
      f.addEventListener('submit', async function (e) {
        var input = f.querySelector('input[type=email]');
        if (!input) return;
        var email = input.value.trim();
        if (!email || email.indexOf('@') === -1) return;
        e.preventDefault();

        var btn = f.querySelector('button[type=submit], button');
        var label = btn ? btn.textContent : '';
        if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

        var players = '';
        try { players = (JSON.parse(localStorage.getItem('fb_favs_v1')) || []).join(';'); } catch (err) {}

        try {
          var res = await fetch('/api/subscribe', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              email: email,
              source: f.classList.contains('alertform') ? 'alerts' : 'newsletter',
              players: players,
              prefs: [].map.call(document.querySelectorAll('#signup .alertopt input:checked'),
                function (c) { return c.name.replace('alert_', ''); }).join(';') || undefined
            })
          });
          var out = await res.json().catch(function () { return {}; });
          if (!res.ok) throw new Error(out.error || 'Could not save that.');
          if (window.FB_IDENTIFY) FB_IDENTIFY(email);
          if (window.FB_TRACK) FB_TRACK('newsletter_subscribed', { source: f.classList.contains('alertform') ? 'alerts' : 'newsletter' });
          f.innerHTML = '<div class="nlok">You\'re on the list. Thanks.</div>';
        } catch (ex) {
          if (btn) { btn.disabled = false; btn.textContent = label; }
          var note = f.querySelector('.nlnote') || document.createElement('div');
          note.className = 'nlnote'; note.style.display = 'block';
          note.textContent = ex.message || 'Could not save that. Try again shortly.';
          f.appendChild(note);
        }
      });
    })(forms[i]);
  }
})();

/* ---------- THEME TOGGLE ----------
   Two looks: the original, and Pitch (near-black + green). Saved locally,
   applied in <head> before paint so there's no flash. */
(function () {
  var btn = document.getElementById('themetog');
  if (!btn) return;
  var label = btn.querySelector('.tlabel');

  function current() {
    return document.documentElement.getAttribute('data-theme') === 'pitch' ? 'pitch' : 'original';
  }
  function paint() {
    if (label) label.textContent = current() === 'pitch' ? 'Original' : 'Pitch';
    btn.setAttribute('aria-pressed', current() === 'pitch' ? 'true' : 'false');
  }
  btn.addEventListener('click', function () {
    var next = current() === 'pitch' ? '' : 'pitch';
    if (next) document.documentElement.setAttribute('data-theme', next);
    else document.documentElement.removeAttribute('data-theme');
    try {
      if (next) localStorage.setItem('fb_theme', next);
      else localStorage.removeItem('fb_theme');
    } catch (e) {}
    paint();
  });
  paint();
})();

/* ---- tap a player on a match page -> their numbers for THIS match ------- */
(function () {
  var STATS = window.FB_MSTATS;
  if (!STATS) return;

  var EVN = { sub_on: '▲ Came on', sub_off: '▼ Subbed off',
              goal: '⚽ Goal', own_goal: '⚽ Own goal', penalty: '⚽ Penalty',
              missed_penalty: '✕ Penalty missed', yellow: 'Yellow card',
              red: 'Red card', second_yellow: 'Second yellow' };

  function esc(t) { return String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }

  var wrap = null;
  function close() { if (wrap) { wrap.remove(); wrap = null; } }

  function face(d) {
    var src = d.u ? '' : (d.photo ? d.photo : (d.img ? '../img/players/' + d._slug + '.png' : ''));
    return src
      ? '<div class="pavatar sm"><img src="' + src + '" alt="" onerror="this.parentNode.innerHTML=\'<span>' + d.ini + '</span>\'"></div>'
      : '<div class="pavatar sm"><span>' + d.ini + '</span></div>';
  }

  function open(slug) {
    var d = STATS[slug];
    if (!d) return;
    d._slug = slug;
    close();

    var played = d.mins !== undefined && d.mins !== null && String(d.mins) !== '';
    var rows;
    if (d.u) {
      // not one of ours: name, shirt, minutes worked out from the sub times,
      // and whatever they did in the game below
      rows = played
        ? '<div class="msnote">' + (d.son ? 'On as a sub — about ' : 'Played ')
          + esc(d.mins) + ' minutes.</div>'
        : (d.son || d.soff ? '' : '<div class="msnote">Unused this match.</div>');
    } else if (played) {
      rows = '<div class="msgrid2">'
        + '<div><b>' + esc(d.mins) + "'" + '</b><span>played</span></div>'
        + '<div><b>' + (d.g || 0) + '</b><span>goals</span></div>'
        + '<div><b>' + (d.a || 0) + '</b><span>assists</span></div>'
        + '<div><b>' + (d.rating ? esc(d.rating) : '—') + '</b><span>rating</span></div>'
        + '</div>';
    } else {
      var m0 = (window.FB_MATCHES || [])[0];
      var done = m0 && (m0.status === 'ft' || (Date.parse(m0.kickoff) && Date.now() - Date.parse(m0.kickoff) > 2 * 36e5));
      // The teamsheet knows more than the stats feed does mid-match: whether he
      // started, and when he went off. Say that rather than claiming nothing.
      var started = !!document.querySelector('.pitch [data-mstat="' + slug + '"]');
      var note;
      if (d.son && d.soff)  note = 'On ' + esc(d.son) + "', off " + esc(d.soff) + "'";
      else if (d.soff)      note = 'Started, off on ' + esc(d.soff) + "'";
      else if (d.son)       note = 'Came on on ' + esc(d.son) + "'";
      else if (started && !done) note = 'Started — still on';
      else note = done ? 'Full-time stats for this game are still syncing'
                       : 'No minutes recorded for this match';
      rows = '<div class="msnote">' + note
        + (d.srating ? ' — season so far:' : '.') + '</div>'
        + '<div class="msgrid2">'
        + '<div><b>' + (d.sap || 0) + '</b><span>apps</span></div>'
        + '<div><b>' + (d.sg || 0) + '</b><span>goals</span></div>'
        + '<div><b>' + (d.sa || 0) + '</b><span>assists</span></div>'
        + '<div><b>' + (d.srating ? esc(d.srating) : '—') + '</b><span>avg rating</span></div>'
        + '</div>';
    }

    var list = (d.evs || []).slice();
    var hasSub = list.some(function (e) { return e.type === 'sub_on' || e.type === 'sub_off'; });
    if (!hasSub) {
      if (d.son) list.push({ min: d.son, type: 'sub_on' });
      if (d.soff) list.push({ min: d.soff, type: 'sub_off' });
    }
    list.sort(function (a, b) {
      return (parseInt(a.min, 10) || 0) - (parseInt(b.min, 10) || 0);
    });
    var evs = list.map(function (e) {
      return '<div class="msev"><b>' + esc(e.min) + "'" + '</b>' + (EVN[e.type] || esc(e.type)) + '</div>';
    }).join('');

    wrap = document.createElement('div');
    wrap.className = 'mssheetwrap';
    wrap.innerHTML =
      '<div class="mssheet" role="dialog" aria-label="' + esc(d.n) + ' in this match">'
      + '<div class="mshead">' + face(d)
      + '<div>' + (d.u
          ? '<span class="msname">' + esc(d.n) + '</span>'
          : '<a class="msname" href="../player/' + slug + '.html">' + esc(d.n) + ' →</a>')
      + '<div class="msmeta">' + esc(d.club)
      + (d.num ? ' · #' + esc(d.num) : '')
      + (d.pos && d.pos !== '—' ? ' · ' + esc(d.pos) : '') + '</div></div>'
      + '<button class="msx" aria-label="Close">×</button></div>'
      + rows
      + (evs ? '<div class="msevs">' + evs + '</div>' : '')
      + (d.u ? '' : '<div class="msfine">Tap the name for the full profile.</div>')
      + '</div>';
    document.body.appendChild(wrap);
    wrap.addEventListener('click', function (e) { if (e.target === wrap || e.target.closest('.msx')) close(); });
  }

  document.addEventListener('click', function (e) {
    var el = e.target.closest && e.target.closest('[data-mstat]');
    if (!el) return;
    e.preventDefault();
    open(el.getAttribute('data-mstat'));
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
})();
