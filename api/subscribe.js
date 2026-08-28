// Saves a signup so it appears in the admin. Writes to a CSV in a repo via the
// GitHub API — the same pattern as reporting.
//
// IMPORTANT: email addresses are personal data. Point this at a PRIVATE repo.
// Set SUBSCRIBERS_REPO to a private repo (e.g. JustPatrickG/footballers-private).
// If it is unset, the endpoint refuses rather than risk writing emails somewhere public.

const PATH = 'subscribers.csv';

async function ghGet(repo, token) {
  const r = await fetch(`https://api.github.com/repos/${repo}/contents/${PATH}`, {
    headers: { Authorization: `token ${token}`, Accept: 'application/vnd.github+json' }
  });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error('read failed ' + r.status);
  return r.json();
}

export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });

  const token = process.env.GITHUB_TOKEN;
  const repo  = process.env.SUBSCRIBERS_REPO;
  if (!token || !repo) {
    return res.status(500).json({
      error: 'Signups are not configured yet.',
      detail: 'Set SUBSCRIBERS_REPO to a private repository.'
    });
  }

  let body = req.body;
  if (typeof body === 'string') { try { body = JSON.parse(body); } catch { body = {}; } }

  const email = String(body.email || '').trim().toLowerCase().slice(0, 200);
  const source = String(body.source || 'site').slice(0, 40);
  const players = String(body.players || '').slice(0, 2000);
  const action = String(body.action || 'save');   // save | get | delete

  if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return res.status(400).json({ error: 'That email doesn\'t look right.' });
  }
  if (body.website) return res.status(200).json({ ok: true });   // honeypot

  try {
    const cur = await ghGet(repo, token);
    let text = cur
      ? Buffer.from(cur.content, 'base64').toString('utf8')
      : 'email,signed_up,source,players\n';

    const esc = v => /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
    const matches = String(body.matches || '').slice(0, 2000);
    const nowIso = new Date().toISOString();

    // one row per email. a repeat signup / follow-sync updates the row in place
    // (this is the whole "account": email + what they follow. no password — nothing here is secret)
    let lines = text.split('\n').filter(l => l.length);
    if (!lines[0].startsWith('email,')) lines.unshift('email,signed_up,source,players,matches,updated,prefs');
    if (lines[0] === 'email,signed_up,source,players') lines[0] = 'email,signed_up,source,players,matches,updated,prefs';
    if (lines[0] === 'email,signed_up,source,players,matches,updated') lines[0] += ',prefs';
    const idx = lines.findIndex((l, i) => i > 0 && l.split(',')[0].trim().toLowerCase() === email);
    const cells = idx > -1 ? lines[idx].split(',') : [];

    if (action === 'get') {
      if (idx === -1) return res.status(404).json({ error: 'No account under that address.' });
      return res.status(200).json({
        ok: true, email,
        signed_up: cells[1] || '',
        players: (cells[3] || '').replace(/^"|"$/g, ''),
        matches: (cells[4] || '').replace(/^"|"$/g, ''),
        prefs:   (cells[6] || '').replace(/^"|"$/g, '')
      });
    }

    if (action === 'delete') {
      if (idx === -1) return res.status(200).json({ ok: true, gone: true });
      lines.splice(idx, 1);
      const textOut = lines.join('\n') + '\n';
      const del = await fetch(`https://api.github.com/repos/${repo}/contents/${PATH}`, {
        method: 'PUT',
        headers: { Authorization: `token ${token}`, Accept: 'application/vnd.github+json',
                   'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: 'Account deleted (user request)',
          content: Buffer.from(textOut, 'utf8').toString('base64'),
          ...(cur ? { sha: cur.sha } : {})
        })
      });
      if (!del.ok) throw new Error('delete failed ' + del.status);
      return res.status(200).json({ ok: true, gone: true });
    }

    const prefs = String(body.prefs === undefined ? (cells[6] || '').replace(/^"|"$/g, '') : body.prefs).slice(0, 200);
    const keepPlayers = body.players === undefined && idx > -1 ? (cells[3] || '').replace(/^"|"$/g, '') : players;
    const keepMatches = body.matches === undefined && idx > -1 ? (cells[4] || '').replace(/^"|"$/g, '') : matches;
    const row = [email, idx > -1 ? (cells[1] || nowIso) : nowIso, source, esc(keepPlayers), esc(keepMatches), nowIso, esc(prefs)].join(',');
    if (idx > -1) lines[idx] = row; else lines.push(row);
    text = lines.join('\n') + '\n';

    const put = await fetch(`https://api.github.com/repos/${repo}/contents/${PATH}`, {
      method: 'PUT',
      headers: { Authorization: `token ${token}`, Accept: 'application/vnd.github+json',
                 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: `Signup: ${email}`,
        content: Buffer.from(text, 'utf8').toString('base64'),
        ...(cur ? { sha: cur.sha } : {})
      })
    });
    if (!put.ok) throw new Error('write failed ' + put.status);
    return res.status(200).json({ ok: true });
  } catch (e) {
    return res.status(500).json({ error: 'Could not save that. Try again shortly.' });
  }
}
