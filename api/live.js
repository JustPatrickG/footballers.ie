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
//
// GET /api/live?ids=5988076&full=1  additionally returns ev: a timeline of
// goals and cards for each match, so match pages can paint events live. Only
// match pages ask for it (one id), so the extra weight stays off the homepage.

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
    const raw = String(lt.short || lt.long || '');
    // keep half-time as a label rather than stripping it to an empty string
    minute = /ht|half/i.test(raw) ? 'HT' : raw.replace(/[^0-9+]/g, '');
  }
  if (st === 'scheduled') { hs = null; as = null; }
  return { hs: hs == null ? null : +hs, as: as == null ? null : +as,
           status: st, minute };
}

function parseEvents(data) {
  // Find the matchFacts events list: an array whose items look like
  // {type, timeStr, isHome, player:{name}} - same shape the scraper reads.
  let list = null;
  (function hunt(obj) {
    if (list || !obj || typeof obj !== 'object') return;
    if (Array.isArray(obj)) { for (const it of obj) hunt(it); return; }
    const ev = obj.events;
    if (Array.isArray(ev) && ev.length &&
        ev.some(e => e && typeof e === 'object' && 'timeStr' in e && 'type' in e)) {
      list = ev; return;
    }
    for (const k in obj) hunt(obj[k]);
  })(data);
  if (!list) return [];

  const out = [];
  for (const e of list) {
    if (!e || typeof e !== 'object') continue;
    let type = null;
    if (e.type === 'Goal') {
      type = e.ownGoal ? 'own_goal'
        : (/pen/i.test(String(e.goalDescriptionKey || e.goalDescription || '')) ? 'penalty' : 'goal');
    } else if (e.type === 'Card') {
      const c = String(e.card || '');
      type = c === 'Red' ? 'red' : (c === 'YellowRed' ? 'second_yellow' : 'yellow');
    } else if (e.type === 'MissedPenalty') {
      type = 'missed_penalty';
    } else if (e.type === 'Substitution') {
      // fotmob's swap pair: coming on first, going off second
      const sw = Array.isArray(e.swap) ? e.swap : [];
      const nm = x => (x && (x.name || x.nameStr)) ? String(x.name || x.nameStr) : '';
      if (sw.length === 2 && (nm(sw[0]) || nm(sw[1]))) {
        out.push({
          min: String(e.timeStr == null ? '' : e.timeStr),
          type: 'sub',
          player: nm(sw[1]),           // off
          sin: nm(sw[0]),              // on
          home: e.isHome ? 1 : 0,
          assist: ''
        });
      }
      continue;
    }
    if (!type) continue;
    const name = (e.player && (e.player.name || e.player.profileUrl)) || e.nameStr || '';
    out.push({
      min: String(e.timeStr == null ? '' : e.timeStr),
      type,
      player: typeof name === 'string' ? name : '',
      home: e.isHome ? 1 : 0,
      assist: e.assistStr ? String(e.assistStr).replace(/^assist by /i, '') : ''
    });
  }
  return out;
}

// While people are watching a live match, this function is getting hit every
// ~30s anyway - so occasionally use one of those hits to poke the GitHub
// matchday workflow, whose own cron is the unreliable part. Fire-and-forget,
// probability-gated so fotmob traffic stays what it was.
function kickWorkflow() {
  const token = process.env.GITHUB_TOKEN, repo = process.env.GITHUB_REPO;
  if (!token || !repo) return Promise.resolve();
  const roll = Math.random();
  // usually the quick matchday pass; now and then the full refresh, because
  // GitHub's own cron for both has proven it can sleep for hours
  const wf = roll < 0.12 ? 'matchday.yml' : (roll < 0.15 ? 'refresh.yml' : null);
  if (!wf) return Promise.resolve();
  return fetch(
    'https://api.github.com/repos/' + repo + '/actions/workflows/' + wf + '/dispatches',
    { method: 'POST',
      headers: { Authorization: 'Bearer ' + token,
                 Accept: 'application/vnd.github+json',
                 'User-Agent': 'footballers-ie-live' },
      body: JSON.stringify({ ref: 'main' }) }
  ).then(r => console.log('kick ' + wf + ':', r.status))
   .catch(e => console.log('kick ' + wf + ' failed:', String(e)));
}

export default async function handler(req, res) {
  const raw = String(req.query.ids || '');
  const ids = [...new Set(raw.split(',').map(s => s.trim())
    .filter(s => /^[0-9]{4,12}$/.test(s)))].slice(0, 20);

  if (!ids.length) {
    res.setHeader('Cache-Control', 's-maxage=60');
    return res.status(400).json({ error: 'ids required' });
  }

  const full = String(req.query.full || '') === '1';
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
      const data = await r.json();
      const parsed = parseMatch(data);
      if (parsed) {
        if (full) parsed.ev = parseEvents(data);
        out[id] = parsed;
      }
    } catch (e) { /* one bad match never breaks the rest */ }
  }));

  // a live (or just-finished) match means the site's CSVs want refreshing too
  if (Object.values(out).some(m => m.status !== 'scheduled')) {
    try { await kickWorkflow(); } catch (e) {}
  }

  // the edge serves everyone from this for 25s; stale for 30 more while
  // the next one is fetched, so nobody ever waits on fotmob directly
  res.setHeader('Cache-Control', 's-maxage=25, stale-while-revalidate=30');
  res.setHeader('Access-Control-Allow-Origin', '*');
  return res.status(200).json({
    updated: new Date().toISOString(),
    matches: out
  });
}
