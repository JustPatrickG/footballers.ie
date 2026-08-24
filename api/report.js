// Vercel serverless function: takes a public report and files it as a GitHub issue.
// Needs env vars in Vercel → Settings → Environment Variables:
//   GITHUB_TOKEN  – a token with "repo" scope (never exposed to the browser)
//   GITHUB_REPO   – e.g. JustPatrickG/footballers.ie

const LABELS = {
  missing: 'missing player',
  wrong:   'wrong data',
  club:    'wrong club',
  match:   'match issue',
  other:   'other'
};

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'POST only' });
  }

  const token = process.env.GITHUB_TOKEN;
  const repo  = process.env.GITHUB_REPO;
  if (!token || !repo) {
    return res.status(500).json({ error: 'Reporting is not configured yet.' });
  }

  let body = req.body;
  if (typeof body === 'string') { try { body = JSON.parse(body); } catch { body = {}; } }

  const type    = String(body.type || 'other').slice(0, 40);
  const details = String(body.details || '').trim().slice(0, 4000);
  const page    = String(body.page || '').slice(0, 300);
  const subject = String(body.subject || '').slice(0, 200);
  const email   = String(body.email || '').trim().slice(0, 200);

  if (details.length < 5) {
    return res.status(400).json({ error: 'Please add a bit more detail.' });
  }

  // crude spam guard: honeypot field must stay empty
  if (body.website) return res.status(200).json({ ok: true });

  const title = subject
    ? `[${type}] ${subject}`
    : `[${type}] ${details.slice(0, 60)}${details.length > 60 ? '…' : ''}`;

  const lines = [
    details,
    '',
    '---',
    page ? `**Page:** ${page}` : '',
    email ? `**Reporter:** ${email}` : '**Reporter:** not given',
    `**Received:** ${new Date().toISOString()}`
  ].filter(Boolean).join('\n');

  try {
    const r = await fetch(`https://api.github.com/repos/${repo}/issues`, {
      method: 'POST',
      headers: {
        Authorization: `token ${token}`,
        Accept: 'application/vnd.github+json',
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        title,
        body: lines,
        labels: ['report', LABELS[type] || 'other']
      })
    });
    if (!r.ok) {
      const t = await r.text();
      return res.status(502).json({ error: 'Could not file the report.', detail: t.slice(0, 200) });
    }
    const issue = await r.json();
    return res.status(200).json({ ok: true, number: issue.number });
  } catch (e) {
    return res.status(500).json({ error: 'Could not file the report.' });
  }
}
