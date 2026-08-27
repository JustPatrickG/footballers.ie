// Vercel serverless function: live scores straight from the source, on demand.
//
// The old pipeline went GitHub cron -> scraper -> commit live.json -> raw
// CDN -> browser. GitHub fires a */5 schedule whenever it feels like it -
// gaps of eleven HOURS are in the run history - so scores simply stopped.
// This cuts every link out of that chain: the browser asks us, we ask the
// source, the edge caches the answer for ~25 seconds so any number of
// visitors cost one upstream request per match per half-minute.
//
// GET /api/live?ids=5988076,5988080   (fotmob match ids, max 20)
// -> { updated, matches: { "5988076": { hs, as, status, minute } } }
//
// status is "scheduled" | "live" | "ft". minute is "67" or "45+2", live only.

const UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/126.0 Safari/537.36';

function parseMatch(data) {
  // Same hunt the scraper does: find the {teams:[..,..], status:{...}} pair.
  function hunt(obj) {
    if (Array.isArray(obj)) {
      for (const it of obj) { const r = hunt(it); if (r) return r; }
      return null;
    }
    if (obj && typeof obj === 'object') {
      const t = obj.teams, s = obj.status;
      if (Array.isArray(t) && t.length === 2 && s && typeof s === 'object'
          && t.every(x => x && typeof x === 'object' && 'name' in x)) {
        return [t, s];
      }
      for (const k in obj) { const r = hunt(obj[k]); if (r) return r; }
    }
    return null;
  }
  const hit = hunt(data);
  if (!hit) return null;
  const [teams, status] = hit;
  let hs = teams[0].score, as = teams[1].score;
  if (hs == null || as == null) {
    const m = /^\s*(\d+)\s*-\s*(\d+)/.exec(String(status.scoreStr || ''));
    if (m) { hs = +m[1]; as = +m[2]; }
  }
  const st = status.finished ? 'ft' : (status.started ? 'live' : 'scheduled');
  let minute = '';
  const lt = status.liveTime;
  if (st === 'live' && lt && typeof lt === 'object') {
    minute = String(lt.short || lt.long || '').replace(/[^0-9+]/g, '');
  }
  if (st === 'scheduled') { hs = null; as = null; }
  return { hs: hs == null ? null : +hs, as: as == null ? null : +as,
           status: st, minute };
}

export default async function handler(req, res) {
  const raw = String(req.query.ids || '');
  const ids = [...new Set(raw.split(',').map(s => s.trim())
    .filter(s => /^[0-9]{4,12}$/.test(s)))].slice(0, 20);

  if (!ids.length) {
    res.setHeader('Cache-Control', 's-maxage=60');
    return res.status(400).json({ error: 'ids required' });
  }

  const out = {};
  await Promise.all(ids.map(async id => {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 8000);
      const r = await fetch(
        'https://www.fotmob.com/api/data/matchDetails?matchId=' + id,
        { headers: { 'User-Agent': UA, 'Accept-Language': 'en-GB,en;q=0.9' },
          signal: ctrl.signal });
      clearTimeout(t);
      if (!r.ok) return;
      const parsed = parseMatch(await r.json());
      if (parsed) out[id] = parsed;
    } catch (e) { /* one bad match never breaks the rest */ }
  }));

  // the edge serves everyone from this for 25s; stale for 30 more while
  // the next one is fetched, so nobody ever waits on fotmob directly
  res.setHeader('Cache-Control', 's-maxage=25, stale-while-revalidate=30');
  res.setHeader('Access-Control-Allow-Origin', '*');
  return res.status(200).json({
    updated: new Date().toISOString(),
    matches: out
  });
}
