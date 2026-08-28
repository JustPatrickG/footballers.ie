// The share card every footballers.ie link shows when it lands in WhatsApp, X,
// Facebook or iMessage. One 1200x630 layout per kind of page, drawn in the
// site's own colours and type.
//
// Everything here is a plain object tree in satori's shape - no JSX, so the
// file runs as-is on Vercel with no build step.

const INK = '#0C0F10', PANEL = '#151A1B', PANEL2 = '#181E1F', LINE = '#242B2C';
const PAPER = '#EFF3F1', MUTE = '#77837F', SOFT = '#9AA5A1';
const TEAL = '#35D4BF', GOLD = '#F5C518', LIVE = '#FF5A48';
const G = 'Grotesk', M = 'Mono';

const el = (type, style, children) => ({ type, key: null, props: { style, children } });
const div = (style, children) => el('div', style, children);
const img = (src, style) => el('img', style, undefined) && { type: 'img', key: null, props: { src, style } };

// satori needs an explicit display on anything holding more than one child
const row = (style, children) => div({ display: 'flex', ...style }, children);
const col = (style, children) => div({ display: 'flex', flexDirection: 'column', ...style }, children);

const text = (s, style) => div({ display: 'flex', ...style }, String(s == null ? '' : s));

function wordmark(size = 31) {
  return row({ alignItems: 'baseline', fontFamily: G, fontWeight: 700, fontSize: size,
               letterSpacing: '-0.012em', color: PAPER },
    [ text('footballers'), text('.ie', { color: TEAL }) ]);
}

function tagChip(label) {
  return text(label, {
    fontFamily: M, fontSize: 15, letterSpacing: '0.18em', color: GOLD,
    border: `1px solid rgba(245,197,24,0.38)`, borderRadius: 7, padding: '8px 15px',
    textTransform: 'uppercase',
  });
}

function pill(label, live) {
  return row({ alignItems: 'center', gap: 10, background: PANEL, border: `1px solid ${LINE}`,
               borderRadius: 999, padding: '9px 19px', fontFamily: M, fontSize: 14,
               letterSpacing: '0.11em', color: SOFT, textTransform: 'uppercase' },
    (live ? [ div({ width: 9, height: 9, borderRadius: 9, background: LIVE, display: 'flex' }) ] : [])
      .concat([ text(label) ]));
}

function head(right) {
  return row({ justifyContent: 'space-between', alignItems: 'center' },
    [ wordmark(), right || div({ display: 'flex' }, '') ]);
}

function foot(left, right) {
  return row({ justifyContent: 'space-between', alignItems: 'flex-end', fontFamily: M,
               fontSize: 15, letterSpacing: '0.11em', color: MUTE, textTransform: 'uppercase' },
    [ text(left || ''), text(right == null ? 'footballers.ie' : right) ]);
}

// A crest we could not load falls back to the club's initials rather than a hole
function crest(src, size, initials, radius) {
  const r = radius == null ? Math.round(size * 0.2) : radius;
  if (src) {
    return div({ width: size, height: size, display: 'flex', alignItems: 'center',
                 justifyContent: 'center' },
      [ { type: 'img', key: null, props: { src, style: { width: size, height: size, objectFit: 'contain' } } } ]);
  }
  return text(initials || '', {
    width: size, height: size, borderRadius: r, background: PANEL, border: `1px solid ${LINE}`,
    alignItems: 'center', justifyContent: 'center', fontFamily: M,
    fontSize: Math.round(size * 0.27), color: '#5b665f',
  });
}

function portrait(src, size, initials, ring) {
  const base = { width: size, height: size, borderRadius: size, background: PANEL2,
                 border: `3px solid ${ring || LINE}`, display: 'flex', overflow: 'hidden',
                 alignItems: 'flex-end', justifyContent: 'center' };
  if (src) {
    return div(base, [ { type: 'img', key: null,
      props: { src, style: { width: size, height: size, objectFit: 'cover' } } } ]);
  }
  return text(initials || '', { ...base, alignItems: 'center', fontFamily: M,
    fontSize: Math.round(size * 0.3), color: MUTE });
}

function frame(children, glow) {
  const kids = [];
  if (glow !== 'none') {
    kids.push(div({ position: 'absolute', right: -140, top: -200, width: 880, height: 880,
      display: 'flex',
      backgroundImage: `radial-gradient(circle at center, ${glow === 'gold'
        ? 'rgba(245,197,24,0.16)' : 'rgba(53,212,191,0.18)'} 0%, rgba(12,15,16,0) 62%)` }));
  }
  kids.push(col({ position: 'relative', width: 1200, height: 630, padding: '56px 64px',
                  justifyContent: 'space-between' }, children));
  return col({ width: 1200, height: 630, background: INK, color: PAPER, fontFamily: G,
               position: 'relative', overflow: 'hidden' }, kids);
}

const mid = (children) => div({ display: 'flex', flex: 1, alignItems: 'center' }, children);

/* ---------------------------------------------------------------- cards -- */

function articleCard(d) {
  const hasFace = !!(d.photo || d.playerName);
  const left = col({ maxWidth: hasFace ? 620 : 930 }, [
    text(d.headline || '', { fontFamily: G, fontWeight: 700, fontSize: hasFace ? 58 : 62,
      lineHeight: 1.05, letterSpacing: '-0.024em' }),
    d.standfirst
      ? text(d.standfirst, { marginTop: 20, fontSize: 22, lineHeight: 1.42, color: SOFT,
          maxWidth: hasFace ? 560 : 800 })
      : div({ display: 'flex' }, ''),
  ]);
  const body = hasFace
    ? row({ flex: 1, alignItems: 'center', justifyContent: 'space-between', gap: 40 }, [
        left,
        col({ alignItems: 'center', gap: 18 }, [
          portrait(d.photo, 300, d.playerInitials, 'rgba(53,212,191,0.45)'),
          text(d.playerName || '', { fontFamily: M, fontSize: 15, letterSpacing: '0.14em',
            color: SOFT, textTransform: 'uppercase' }),
        ]),
      ])
    : mid([left]);
  return frame([ head(d.tag ? tagChip(d.tag) : null), body, foot(d.byline) ],
               hasFace ? 'teal' : 'gold');
}

function playerCard(d) {
  const stats = (d.stats || []).map((s, i) =>
    col({ background: PANEL, border: `1px solid ${LINE}`, borderRadius: 13,
          padding: '15px 23px', minWidth: 116 }, [
      text(s.value, { fontFamily: G, fontWeight: 700, fontSize: 37, lineHeight: 1,
        letterSpacing: '-0.022em', color: s.accent ? TEAL : PAPER }),
      text(s.label, { marginTop: 8, fontFamily: M, fontSize: 12, letterSpacing: '0.15em',
        color: MUTE, textTransform: 'uppercase' }),
    ]));
  const meta = [];
  if (d.club) meta.push(crest(d.badge, 31, d.clubInitials, 7));
  if (d.club) meta.push(text(d.club));
  if (d.league) { meta.push(text('·', { color: '#3a4340' })); meta.push(text(d.league)); }
  if (d.sub) { meta.push(text('·', { color: '#3a4340' })); meta.push(text(d.sub)); }
  return frame([
    head(null),
    mid([ row({ alignItems: 'center', gap: 46 }, [
      portrait(d.photo, 252, d.initials),
      col({}, [
        text(d.name || '', { fontFamily: G, fontWeight: 700, fontSize: 64, lineHeight: 1,
          letterSpacing: '-0.026em' }),
        row({ marginTop: 17, alignItems: 'center', gap: 11, fontFamily: M, fontSize: 18,
          letterSpacing: '0.05em', color: SOFT }, meta),
        stats.length ? row({ marginTop: 30, gap: 13 }, stats) : div({ display: 'flex' }, ''),
      ]),
    ]) ]),
    foot(d.footNote || 'Player'),
  ], 'teal');
}

function matchCard(d) {
  const team = (name, badge, ini) => col({ flex: 1, alignItems: 'center', gap: 19 }, [
    crest(badge, 106, ini, 22),
    text(name || '', { fontFamily: G, fontWeight: 700, fontSize: 33, lineHeight: 1.12,
      letterSpacing: '-0.018em', maxWidth: 320, textAlign: 'center' }),
  ]);
  let centre;
  if (d.score) {
    centre = col({ alignItems: 'center' }, [
      row({ alignItems: 'center', gap: 19, fontFamily: G, fontWeight: 700, fontSize: 94,
        lineHeight: 1, letterSpacing: '-0.032em' }, [
        text(d.score[0]), text('–', { color: '#3a4340', fontSize: 58 }), text(d.score[1]),
      ]),
      d.under ? text(d.under, { marginTop: 16, fontFamily: M, fontSize: 16,
        letterSpacing: '0.13em', color: MUTE, textTransform: 'uppercase' })
              : div({ display: 'flex' }, ''),
    ]);
  } else {
    centre = col({ alignItems: 'center' }, [
      text(d.kickoff || '', { fontFamily: G, fontWeight: 700, fontSize: 52, lineHeight: 1,
        letterSpacing: '-0.022em' }),
      d.under ? text(d.under, { marginTop: 13, fontFamily: M, fontSize: 15,
        letterSpacing: '0.15em', color: MUTE, textTransform: 'uppercase' })
              : div({ display: 'flex' }, ''),
    ]);
  }
  return frame([
    head(pill(d.pill || '', d.live)),
    mid([ row({ width: '100%', alignItems: 'center', justifyContent: 'space-between', gap: 26 }, [
      team(d.home, d.homeBadge, d.homeInitials),
      centre,
      team(d.away, d.awayBadge, d.awayInitials),
    ]) ]),
    foot(d.footNote),
  ], 'teal');
}

function clubCard(d) {
  const faces = (d.faces || []).map((f, i) =>
    div({ marginLeft: i ? -19 : 0, display: 'flex' }, [ portrait(f.photo, 78, f.initials, INK) ]));
  if (d.more) {
    faces.push(text('+' + d.more, { marginLeft: -19, width: 78, height: 78, borderRadius: 78,
      background: PANEL, border: `3px solid ${INK}`, alignItems: 'center',
      justifyContent: 'center', fontFamily: M, fontSize: 19, color: MUTE }));
  }
  return frame([
    head(null),
    mid([ col({}, [
      row({ alignItems: 'center', gap: 28 }, [
        d.badge || d.initials ? crest(d.badge, 112, d.initials, 22) : div({ display: 'flex' }, ''),
        text(d.name || '', { fontFamily: G, fontWeight: 700, fontSize: 74, lineHeight: 1.03,
          letterSpacing: '-0.03em', maxWidth: 640 }),
      ]),
      d.lede ? text(d.lede, { marginTop: 21, fontSize: 25, color: SOFT }) : div({ display: 'flex' }, ''),
      faces.length ? row({ marginTop: 32 }, faces) : div({ display: 'flex' }, ''),
    ]) ]),
    foot(d.footNote || 'Club'),
  ], 'teal');
}

function plainCard(d) {
  return frame([
    head(null),
    mid([ col({}, [
      text(d.name || '', { fontFamily: G, fontWeight: 700, fontSize: d.big ? 86 : 74,
        lineHeight: d.big ? 1 : 1.03, letterSpacing: d.big ? '-0.034em' : '-0.03em',
        maxWidth: 820 }),
      d.lede ? text(d.lede, { marginTop: 21, fontSize: 25, color: SOFT, maxWidth: 760 })
             : div({ display: 'flex' }, ''),
    ]) ]),
    foot(d.footNote, d.footRight),
  ], d.glow || 'teal');
}

export const CARDS = {
  article: articleCard,
  player: playerCard,
  match: matchCard,
  club: clubCard,
  plain: plainCard,
};

export function buildCard(kind, data) {
  return (CARDS[kind] || plainCard)(data || {});
}
