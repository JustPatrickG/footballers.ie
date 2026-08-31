// Share cards, drawn on demand.
//
// Every page on the site points its og:image at this endpoint with the few
// fields that card needs. Nothing is stored: the picture is drawn when a link
// is first shared and then held at the edge, so it can never fall out of step
// with the page.
//
// Match cards carry a match id and look the score up as they are drawn, which
// is why they are cached for a minute and everything else for a day.

// CommonJS on purpose: satori's text shaper locates its WebAssembly through
// __dirname, which an ES module does not have.
const satori = require('satori').default || require('satori');
const { Resvg, initWasm } = require('@resvg/resvg-wasm');
const { readFileSync } = require('fs');
const { FONTS } = require('./_fonts.js');
const { buildCard } = require('./_cards.js');

let wasmReady = null;
function ready() {
  if (!wasmReady) {
    wasmReady = initWasm(readFileSync(require.resolve('@resvg/resvg-wasm/index_bg.wasm')));
  }
  return wasmReady;
}

const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
           '(KHTML, like Gecko) Chrome/126.0 Safari/537.36';
const BADGE = id => `https://images.fotmob.com/image_resources/logo/teamlogo/${id}.png`;
const SITE = 'https://www.footballers.ie';

/* An image the card wants but cannot get back must not take the card with it:
   every fetch is bounded, and a failure just means initials instead. */
async function pic(url, ms = 3000) {
  if (!url) return '';
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), ms);
    const r = await fetch(url, { signal: ctrl.signal, headers: { 'User-Agent': UA } });
    clearTimeout(t);
    if (!r.ok) return '';
    const type = (r.headers.get('content-type') || 'image/png').split(';')[0];
    if (!type.startsWith('image/')) return '';
    const b = Buffer.from(await r.arrayBuffer());
    if (!b.length || b.length > 2_000_000) return '';
    return `data:${type};base64,${b.toString('base64')}`;
  } catch (e) {
    return '';
  }
}

const badge = id => (/^[0-9]{2,10}$/.test(String(id || '')) ? pic(BADGE(id)) : Promise.resolve(''));
const photo = slug => (/^[a-z0-9-]{2,60}$/.test(String(slug || ''))
  ? pic(`${SITE}/img/players/${slug}.png`) : Promise.resolve(''));

function initials(name) {
  const w = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (!w.length) return '';
  return (w.length === 1 ? w[0].slice(0, 3) : w[0][0] + w[w.length - 1][0]).toUpperCase();
}

/* live score for a match card, when the page handed us a match id */
async function liveScore(fmid) {
  if (!/^[0-9]{4,12}$/.test(String(fmid || ''))) return null;
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 3500);
    const r = await fetch(`${SITE}/api/live?ids=${fmid}`, { signal: ctrl.signal });
    clearTimeout(t);
    if (!r.ok) return null;
    const d = await r.json();
    return (d && d.matches && d.matches[fmid]) || null;
  } catch (e) {
    return null;
  }
}

const one = (q, k, max = 200) => String(q[k] == null ? '' : q[k]).slice(0, max);

async function assemble(q) {
  const t = one(q, 't') || 'plain';

  if (t === 'article') {
    const slug = one(q, 'p', 60);
    return ['article', {
      tag: one(q, 'g', 40), headline: one(q, 'h', 160), standfirst: one(q, 's', 200),
      byline: one(q, 'b', 60),
      playerName: one(q, 'pn', 60),
      playerInitials: initials(one(q, 'pn', 60)),
      photo: slug ? await photo(slug) : '',
    }];
  }

  if (t === 'player') {
    const [ph, bd] = await Promise.all([photo(one(q, 'p', 60)), badge(one(q, 'cb', 10))]);
    const stats = [];
    const add = (label, v, accent) => { if (v !== '') stats.push({ label, value: v, accent }); };
    add('Apps', one(q, 'ap', 6)); add('Goals', one(q, 'gl', 6));
    add('Assists', one(q, 'as', 6)); add('Rating', one(q, 'rt', 6), true);
    return ['player', {
      name: one(q, 'n', 60), club: one(q, 'c', 50), league: one(q, 'l', 40),
      sub: one(q, 'x', 30), photo: ph, badge: bd,
      initials: initials(one(q, 'n', 60)), clubInitials: initials(one(q, 'c', 50)),
      stats, footNote: one(q, 'f', 40) || 'Player',
    }];
  }

  if (t === 'match') {
    const home = one(q, 'h', 50), away = one(q, 'a', 50);
    const [hb, ab, live] = await Promise.all([
      badge(one(q, 'hb', 10)), badge(one(q, 'ab', 10)), liveScore(one(q, 'fm', 12)),
    ]);
    let hs = one(q, 'hs', 3), as = one(q, 'as', 3), status = one(q, 'st', 12);
    if (live) {
      status = live.status || status;
      if (live.hs != null) hs = String(live.hs);
      if (live.as != null) as = String(live.as);
    }
    const isLive = status === 'live';
    const played = isLive || status === 'ft';
    const pillText = isLive
      ? ('Live' + (live && live.minute ? ' · ' + live.minute + '′' : ''))
      : one(q, 'c', 46);
    return ['match', {
      pill: pillText, live: isLive, home, away,
      homeBadge: hb, awayBadge: ab,
      homeInitials: initials(home), awayInitials: initials(away),
      score: played && hs !== '' && as !== '' ? [hs, as] : null,
      kickoff: one(q, 'k', 30),
      under: one(q, 'v', 46),
      footNote: one(q, 'f', 60),
    }];
  }

  if (t === 'club') {
    const slugs = one(q, 'p', 200).split(',').filter(Boolean).slice(0, 4);
    const [bd, ...faces] = await Promise.all([badge(one(q, 'cb', 10))].concat(slugs.map(photo)));
    return ['club', {
      name: one(q, 'n', 60), badge: bd, initials: initials(one(q, 'n', 60)),
      lede: one(q, 'l', 90),
      faces: faces.map(p => ({ photo: p })),
      more: /^[0-9]{1,4}$/.test(one(q, 'm', 4)) ? one(q, 'm', 4) : '',
      footNote: one(q, 'f', 40) || 'Club',
    }];
  }

  return ['plain', {
    name: one(q, 'n', 90), lede: one(q, 'l', 140),
    big: one(q, 'big', 2) === '1', glow: one(q, 'gw', 6) === 'gold' ? 'gold' : 'teal',
    footNote: one(q, 'f', 40), footRight: q.fr == null ? undefined : one(q, 'fr', 40),
  }];
}

module.exports = async function handler(req, res) {
  try {
    const q = req.query || {};
    const [kind, data] = await assemble(q);
    await ready();

    const svg = await satori(buildCard(kind, data), { width: 1200, height: 630, fonts: FONTS });
    // asPng() hands back a Uint8Array; res.send() would JSON-encode that into
    // {"0":137,"1":80,...} and every consumer would see a broken image. Buffer
    // it and write the bytes out directly.
    const png = Buffer.from(new Resvg(svg, { fitTo: { mode: 'width', value: 1200 } })
      .render().asPng());

    // a live match is worth re-drawing every minute; nothing else changes
    // without the page changing the URL it asks for
    const live = one(q, 't') === 'match' && one(q, 'fm', 12);
    res.setHeader('Content-Type', 'image/png');
    res.setHeader('Content-Length', String(png.length));
    res.setHeader('Cache-Control', live
      ? 'public, s-maxage=60, stale-while-revalidate=120'
      : 'public, s-maxage=86400, stale-while-revalidate=604800');
    res.statusCode = 200;
    return res.end(png);
  } catch (e) {
    // never hand back a broken image: fall back to the one static card, but
    // say why in the logs so a silent fallback is never a mystery
    console.error('og card failed:', (e && e.stack) || String(e));
    res.setHeader('Cache-Control', 'public, s-maxage=60');
    return res.redirect(302, `${SITE}/og-image.png`);
  }
};
