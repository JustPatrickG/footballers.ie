/* ABROAD — 96th minute. Continuous composition, three cuts + title reveal. */
const { CompositionStage, useComposition, Shot, animate, Easing, clamp } = window;

const W = 1920, H = 1080;
const BG = '#0C0F10', RED = '#E2483C';
let YEL = '#F5C518', TEAL = '#35D4BF';
let YEL_BACK = '#8A6A0C';
const RED_BACK = '#7C2118';
const shade = (hex, k) => '#' + [1, 3, 5].map(i => Math.round(parseInt(hex.slice(i, i + 2), 16) * k).toString(16).padStart(2, '0')).join('');
const MONO = "'IBM Plex Mono', ui-monospace, monospace";
const DISP = "'Space Grotesk', 'Helvetica Neue', sans-serif";

/* --- exactly three motion helpers --- */
const MOTION = {
  glide: (from, to, start, end) => animate({ from, to, start, end, ease: Easing.easeOutCubic }),
  snap:  (from, to, start, end) => animate({ from, to, start, end, ease: Easing.easeOutExpo }),
  settle:(from, to, start, end) => animate({ from, to, start, end, ease: Easing.easeInOutQuart }),
};

const RAD = Math.PI / 180;
function kf(T, frames) {
  if (T <= frames[0][0]) return frames[0][1];
  for (let i = 0; i < frames.length - 1; i++) {
    const [t0, a] = frames[i], [t1, b] = frames[i + 1];
    if (T <= t1) {
      const u = Easing.easeInOutQuad(clamp((T - t0) / (t1 - t0), 0, 1));
      const o = {}; for (const k in a) o[k] = a[k] + (b[k] - a[k]) * u; return o;
    }
  }
  return frames[frames.length - 1][1];
}

/* ---------- figure ---------- */
function Seg({ a, b, thick, color, glow }) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const len = Math.hypot(dx, dy), ang = Math.atan2(dy, dx) / RAD;
  return <div style={{
    position: 'absolute', left: a[0], top: a[1] - thick / 2, width: len, height: thick,
    background: color, borderRadius: thick, transformOrigin: '0 50%',
    transform: `rotate(${ang}deg)`, boxShadow: glow,
  }} />;
}

function Figure({ x, y, s = 1, rot = 0, flip = false, color, back = 'rgba(0,0,0,0.55)', pose, glow }) {
  const L = { torso: 74 * s, ua: 48 * s, fa: 44 * s, th: 64 * s, sh: 58 * s, ft: 22 * s, head: 20 * s, neck: 16 * s };
  const p = pose;
  const pt = (o, len, deg) => [o[0] + len * Math.sin(deg * RAD), o[1] + len * Math.cos(deg * RAD)];
  const hip = [0, 0];
  const sho = pt(hip, L.torso, 180 - p.tl);
  const nk = pt(sho, L.neck, 180 - p.tl);
  const head = pt(nk, L.head, 180 - p.tl - (p.hd || 0));
  const elR = pt(sho, L.ua, p.arA), haR = pt(elR, L.fa, p.arA + p.arB);
  const elL = pt(sho, L.ua, p.alA), haL = pt(elL, L.fa, p.alA + p.alB);
  const knR = pt(hip, L.th, p.rlA), anR = pt(knR, L.sh, p.rlA + p.rlB), toR = pt(anR, L.ft, p.rlA + p.rlB + 70);
  const knL = pt(hip, L.th, p.llA), anL = pt(knL, L.sh, p.llA + p.llB), toL = pt(anL, L.ft, p.llA + p.llB + 70);
  const tk = 14 * s, tkT = 24 * s;
  return (
    <div style={{
      position: 'absolute', left: x, top: y, width: 0, height: 0,
      transform: `rotate(${rot}deg) scaleX(${flip ? -1 : 1})`, transformOrigin: '0 0',
    }}>
      {/* far limbs, darkened for depth */}
      <Seg a={hip} b={knL} thick={tk} color={back} />
      <Seg a={knL} b={anL} thick={tk * 0.85} color={back} />
      <Seg a={anL} b={toL} thick={tk * 0.8} color={back} />
      <Seg a={sho} b={elL} thick={tk * 0.85} color={back} />
      <Seg a={elL} b={haL} thick={tk * 0.72} color={back} />
      <Seg a={hip} b={sho} thick={tkT} color={color} glow={glow} />
      <Seg a={sho} b={nk} thick={tk} color={color} />
      <Seg a={hip} b={knR} thick={tk * 1.05} color={color} />
      <Seg a={knR} b={anR} thick={tk * 0.9} color={color} />
      <Seg a={anR} b={toR} thick={tk * 0.82} color={color} />
      <Seg a={sho} b={elR} thick={tk * 0.9} color={color} />
      <Seg a={elR} b={haR} thick={tk * 0.76} color={color} />
      <div style={{
        position: 'absolute', left: head[0] - L.head, top: head[1] - L.head,
        width: L.head * 2, height: L.head * 2, borderRadius: '50%', background: color, boxShadow: glow,
      }} />
    </div>
  );
}

function Shadow({ x, y, w, o = 0.5 }) {
  return <div style={{
    position: 'absolute', left: x - w / 2, top: y - 14, width: w, height: 28, borderRadius: '50%',
    background: `radial-gradient(closest-side, rgba(0,0,0,${o}), rgba(0,0,0,0))`, filter: 'blur(6px)',
  }} />;
}

/* ---------- ball ---------- */
function ballAt(T, C) {
  const seg = (t0, t1, x0, y0, x1, y1, arc) => {
    const u = clamp((T - t0) / (t1 - t0), 0, 1);
    return { x: x0 + (x1 - x0) * u, y: y0 + (y1 - y0) * u - arc * Math.sin(u * Math.PI), vis: T >= t0 && T <= t1 + 0.02 };
  };
  if (T < C.Scales) {
    if (T < C.Kelleher + 0.70) return { x: 748, y: 772, vis: true };
    return seg(C.Kelleher + 0.70, C.Scales, 748, 772, 1980, -160, 220);
  }
  if (T < C.Parrott) {
    if (T < C.Scales + 0.62) return seg(C.Scales, C.Scales + 0.62, 180, -300, 886, 253, 40);
    return seg(C.Scales + 0.62, C.Parrott + 0.02, 886, 253, 2020, 40, 70);
  }
  if (T < C.Parrott + 0.70) return seg(C.Parrott, C.Parrott + 0.70, 440, 566, 900, 781, -40);
  if (T < C.Parrott + 1.35) return seg(C.Parrott + 0.70, C.Parrott + 1.35, 900, 781, 1385, 724, 135);
  if (T <= C.Parrott + 1.80) return seg(C.Parrott + 1.35, C.Parrott + 1.80, 1385, 724, 1742, 596, 90);
  return { x: 1742, y: 596 + Math.min(78, (T - (C.Parrott + 1.80)) * 150), vis: T < C.Flip };
}

function Ball({ T, C }) {
  const b = ballAt(T, C);
  if (!b.vis) return null;
  const ghosts = [];
  for (let i = 1; i <= 7; i++) {
    const g = ballAt(T - i * 0.020, C);
    if (!g.vis) continue;
    ghosts.push(<div key={i} style={{
      position: 'absolute', left: g.x - 16, top: g.y - 16, width: 32, height: 32, borderRadius: '50%',
      background: TEAL, opacity: 0.20 * (1 - i / 8), filter: 'blur(4px)',
    }} />);
  }
  return <React.Fragment>
    {ghosts}
    <div style={{
      position: 'absolute', left: b.x - 19, top: b.y - 19, width: 38, height: 38, borderRadius: '50%',
      background: 'radial-gradient(circle at 34% 30%, #ffffff 0%, #dff8f3 42%, ' + TEAL + ' 100%)',
      boxShadow: `0 0 34px ${TEAL}88, 0 0 90px ${TEAL}33`,
    }} />
  </React.Fragment>;
}

/* ---------- pitch ---------- */
function Pitch() {
  return <div style={{ position: 'absolute', inset: -600, background: 'linear-gradient(178deg, #0D1311 0%, #101715 46%, #0B100E 100%)' }}>
    <div style={{
      position: 'absolute', inset: 0,
      background: 'repeating-linear-gradient(93deg, rgba(120,190,150,0.055) 0 190px, rgba(0,0,0,0.10) 190px 380px)',
    }} />
    <div style={{
      position: 'absolute', inset: 0, opacity: 0.5,
      background: 'repeating-linear-gradient(86deg, rgba(150,220,175,0.035) 0 2px, rgba(0,0,0,0) 2px 7px), repeating-linear-gradient(-84deg, rgba(0,0,0,0.16) 0 3px, rgba(0,0,0,0) 3px 9px)',
    }} />
    <div style={{
      position: 'absolute', inset: 0, opacity: 0.55,
      background: 'radial-gradient(38% 22% at 22% 62%, rgba(90,140,110,0.10), rgba(0,0,0,0) 70%), radial-gradient(30% 18% at 68% 78%, rgba(0,0,0,0.30), rgba(0,0,0,0) 70%), radial-gradient(44% 26% at 84% 40%, rgba(90,140,110,0.07), rgba(0,0,0,0) 70%)',
    }} />
    <div style={{
      position: 'absolute', inset: 0,
      background: 'radial-gradient(120% 72% at 50% 16%, rgba(245,197,24,0.13), rgba(12,15,16,0) 60%)',
    }} />
    <div style={{ position: 'absolute', left: 0, right: 0, top: 1400, height: 4, background: 'rgba(230,245,235,0.16)' }} />
    <div style={{ position: 'absolute', left: 0, right: 0, top: 1000, height: 2, background: 'rgba(230,245,235,0.07)' }} />
    <div style={{
      position: 'absolute', inset: 0,
      background: 'linear-gradient(to bottom, rgba(9,12,11,0.88) 0%, rgba(9,12,11,0) 32%, rgba(9,12,11,0) 60%, rgba(9,12,11,0.82) 100%)',
    }} />
  </div>;
}

function Goal({ T, C }) {
  const rip = clamp((T - (C.Parrott + 1.78)) / 0.5, 0, 1);
  return <div style={{ position: 'absolute', left: 1500, top: 380, width: 520, height: 440 }}>
    <div style={{
      position: 'absolute', inset: 0,
      background: 'repeating-linear-gradient(0deg, rgba(255,255,255,0.09) 0 1px, rgba(255,255,255,0) 1px 26px), repeating-linear-gradient(90deg, rgba(255,255,255,0.09) 0 1px, rgba(255,255,255,0) 1px 26px)',
      transform: 'perspective(900px) rotateY(-16deg)', transformOrigin: '0 50%',
    }} />
    <div style={{ position: 'absolute', left: 0, top: 0, width: 13, height: 440, background: 'rgba(255,255,255,0.88)' }} />
    <div style={{ position: 'absolute', left: 0, top: 0, width: 520, height: 13, background: 'rgba(255,255,255,0.72)' }} />
    <div style={{
      position: 'absolute', left: 200, top: 150, width: 300, height: 300, borderRadius: '50%',
      background: `radial-gradient(closest-side, rgba(255,255,255,${0.30 * rip * (1 - rip) * 4}), rgba(255,255,255,0))`,
      transform: `scale(${0.4 + rip * 1.3})`,
    }} />
  </div>;
}

/* ---------- poses ---------- */
const K_POSE = t => kf(t, [
  [0.00, { tl: -12, arA: -55, arB: 35, alA: 45, alB: 25, rlA: -52, rlB: 74, llA: 6, llB: 6, hd: 0 }],
  [0.70, { tl: 8, arA: -104, arB: 12, alA: 62, alB: 10, rlA: 40, rlB: 8, llA: -10, llB: 16, hd: 4 }],
  [1.60, { tl: 17, arA: -114, arB: 4, alA: 72, alB: 4, rlA: 86, rlB: -6, llA: -19, llB: 26, hd: 6 }],
]);
const S_POSE = t => kf(t, [
  [0.00, { tl: 16, arA: 34, arB: 44, alA: 22, alB: 44, rlA: 22, rlB: 62, llA: -22, llB: 62, hd: 4 }],
  [0.62, { tl: -16, arA: -122, arB: -8, alA: -92, alB: -18, rlA: -32, rlB: 72, llA: 32, llB: 22, hd: -14 }],
  [0.75, { tl: -6, arA: -112, arB: -2, alA: -80, alB: -12, rlA: -38, rlB: 78, llA: 34, llB: 26, hd: 12 }],
  [1.30, { tl: 2, arA: -96, arB: 4, alA: -66, alB: -6, rlA: -42, rlB: 82, llA: 36, llB: 30, hd: 8 }],
]);
const P_POSE = t => kf(t, [
  [0.00, { tl: 18, arA: -62, arB: 58, alA: 60, alB: 42, rlA: -46, rlB: 76, llA: 36, llB: 10, hd: 0 }],
  [0.23, { tl: 18, arA: 56, arB: 46, alA: -66, alB: 54, rlA: 42, rlB: 8, llA: -42, llB: 80, hd: 0 }],
  [0.44, { tl: 18, arA: -62, arB: 58, alA: 60, alB: 42, rlA: -50, rlB: 80, llA: 38, llB: 12, hd: 0 }],
  [0.65, { tl: 26, arA: -96, arB: 16, alA: 84, alB: 8, rlA: 45, rlB: 0, llA: -30, llB: 40, hd: 2 }],
  [1.90, { tl: 22, arA: -106, arB: 8, alA: 90, alB: 4, rlA: 30, rlB: 16, llA: -34, llB: 46, hd: 4 }],
]);
const G_POSE = t => kf(t, [
  [0.00, { tl: 20, arA: 30, arB: 30, alA: -30, alB: 30, rlA: 26, rlB: 40, llA: -26, llB: 40, hd: 0 }],
  [0.62, { tl: 2, arA: -150, arB: -8, alA: -132, alB: -6, rlA: 34, rlB: -8, llA: 46, llB: -10, hd: -8 }],
]);

/* ---------- split-flap ---------- */
const CHARSET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ*#';
function Flap({ ch, p, i }) {
  const N = 6;
  const k = Math.min(Math.floor(p * N), N - 1);
  const sub = clamp(p * N - k, 0, 1);
  const last = k >= N - 1;
  const glyph = p <= 0 ? ' ' : (last ? ch : CHARSET[(k * 11 + i * 17) % CHARSET.length]);
  const deg = -88 * (1 - Easing.easeOutQuart(sub));
  return <div style={{
    position: 'relative', width: 104, height: 142, perspective: 700,
    background: 'linear-gradient(180deg,#171B1D 0%, #101314 49.5%, #0B0E0F 50.5%, #14181A 100%)',
    border: '1px solid rgba(255,255,255,0.07)', borderRadius: 6, overflow: 'hidden',
    boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.05), 0 18px 40px rgba(0,0,0,0.5)',
  }}>
    <div style={{
      position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
      font: `700 92px/1 ${DISP}`, color: YEL, transform: `rotateX(${deg}deg)`, transformOrigin: '50% 50%',
      textShadow: `0 0 26px ${YEL}44`,
    }}>{glyph}</div>
    <div style={{ position: 'absolute', left: 0, right: 0, top: '50%', height: 2, background: 'rgba(0,0,0,0.7)' }} />
  </div>;
}

/* ---------- the site behind the gates ---------- */
function Site() {
  const card = k => <div key={k} style={{ flex: 1, background: '#111517', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 4, overflow: 'hidden' }}>
    <div style={{ height: 190, background: 'repeating-linear-gradient(135deg, rgba(255,255,255,0.05) 0 8px, rgba(255,255,255,0) 8px 16px)' }} />
    <div style={{ padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ font: `500 13px/1 ${MONO}`, letterSpacing: '0.16em', color: TEAL }}>{['SERIE A', 'EFL CHAMP.', 'EREDIVISIE', 'LA LIGA'][k]}</div>
      <div style={{ font: `600 26px/1 ${DISP}`, color: '#E8EAEA' }}>{['—', '—', '—', '—'][k]}</div>
      <div style={{ height: 8, width: '70%', background: 'rgba(255,255,255,0.07)' }} />
    </div>
  </div>;
  return <div style={{ position: 'absolute', inset: 0, background: BG, display: 'flex', flexDirection: 'column' }}>
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '38px 64px', borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
      <div style={{ font: `700 26px/1 ${DISP}`, letterSpacing: '0.2em', color: YEL }}>FOOTBALLERS</div>
      <div style={{ display: 'flex', gap: 44, font: `400 15px/1 ${MONO}`, letterSpacing: '0.18em', color: 'rgba(232,234,234,0.55)' }}>
        <span>PLAYERS</span><span>FIXTURES</span><span>MINUTES</span><span>TABLE</span>
      </div>
    </div>
    <div style={{ padding: '86px 64px 54px', display: 'flex', flexDirection: 'column', gap: 24 }}>
      <div style={{ font: `400 15px/1 ${MONO}`, letterSpacing: '0.3em', color: TEAL }}>MATCHDAY / 96'</div>
      <div style={{ font: `700 92px/0.94 ${DISP}`, letterSpacing: '-0.02em', color: '#F2F4F4', maxWidth: 1200 }}>Every Irish footballer, every league, every minute.</div>
    </div>
    <div style={{ display: 'flex', gap: 24, padding: '0 64px' }}>{[0, 1, 2, 3].map(card)}</div>
  </div>;
}

/* ---------- action layer (rendered twice, clipped into two gates) ---------- */
function Action({ T, C, total, hud }) {
  /* camera */
  let fx, fy, s;
  if (T < C.Scales) { fx = MOTION.glide(560, 640, C.Kelleher, C.Scales)(T); fy = 700; s = MOTION.glide(1.22, 1.38, C.Kelleher, C.Scales)(T); }
  else if (T < C.Parrott) { fx = MOTION.glide(930, 1030, C.Scales, C.Parrott)(T); fy = MOTION.glide(505, 385, C.Scales, C.Parrott)(T); s = MOTION.glide(1.52, 1.36, C.Scales, C.Parrott)(T); }
  else { fx = MOTION.glide(830, 1520, C.Parrott, C.Parrott + 2.2)(T); fy = MOTION.glide(700, 645, C.Parrott, C.Parrott + 2.2)(T); s = MOTION.glide(1.00, 1.52, C.Parrott, C.Parrott + 2.2)(T); }

  const flash = T >= C.Impact && T < C.Impact + 0.30 ? Math.pow(1 - (T - C.Impact) / 0.30, 1.6) : 0;
    const jump = kf(T - C.Scales, [[0, { y: 0 }], [0.62, { y: -186 }], [0.75, { y: -164 }], [1.30, { y: 6 }]]).y;
  const pX = 700 + 496 * clamp((T - (C.Parrott + 0.70)) / 0.65, 0, 1);
  const gk = kf(T - (C.Parrott + 0.95), [[0, { x: 1636, y: 632, r: 4 }], [0.62, { x: 1524, y: 508, r: -78 }]]);

  return <div style={{ position: 'absolute', inset: 0, overflow: 'hidden', background: BG }}>
    <div style={{
      position: 'absolute', left: 0, top: 0, width: W, height: H, transformOrigin: '0 0',
      transform: `translate(${W / 2 - fx * s}px, ${H / 2 - fy * s}px) scale(${s})`,
    }}>
      <Pitch />
      <Shot from={C.Kelleher} to={C.Scales}>
        <Shadow x={560} y={806} w={300} />
        <Figure x={560} y={610} s={1.62} color={YEL} back={YEL_BACK} pose={K_POSE(T - C.Kelleher)} glow={`0 0 44px ${YEL}30`} />
      </Shot>
      <Shot from={C.Scales} to={C.Parrott}>
        <Shadow x={980} y={806} w={260} o={0.38 - jump / 900} />
        <Figure x={980} y={615 + jump} s={1.66} color={YEL} back={YEL_BACK} pose={S_POSE(T - C.Scales)} glow={`0 0 44px ${YEL}30`} />
      </Shot>
      <Shot from={C.Parrott} to={C.Flip}>
        <Goal T={T} C={C} />
        <Shot from={C.Parrott + 0.93} to={C.Flip}>
          <Shadow x={gk.x} y={806} w={300} o={0.32} />
          <Figure x={gk.x} y={gk.y} s={1.6} rot={gk.r} flip color={RED} back={RED_BACK} pose={G_POSE(T - (C.Parrott + 0.95))} glow={`0 0 40px ${RED}30`} />
        </Shot>
        <Shot from={C.Parrott + 0.68} to={C.Flip}>
          <Shadow x={pX} y={806} w={280} />
          <Figure x={pX} y={610} s={1.6} color={YEL} back={YEL_BACK} pose={P_POSE(T - (C.Parrott + 0.70))} glow={`0 0 44px ${YEL}30`} />
        </Shot>
      </Shot>
      <Ball T={T} C={C} />
    </div>

    {hud ? <React.Fragment>
    <div style={{ position: 'absolute', left: 78, top: 68, display: 'flex', alignItems: 'center', gap: 14, opacity: clamp(1 - (T - C.Impact) / 0.2, 0, 1) }}>
      <div style={{ width: 9, height: 9, borderRadius: '50%', background: RED, boxShadow: `0 0 14px ${RED}` }} />
      <div style={{ font: `500 22px/1 ${MONO}`, letterSpacing: '0.26em', color: 'rgba(240,242,242,0.78)' }}>95'</div>
      <div style={{ width: 44, height: 1, background: 'rgba(255,255,255,0.2)' }} />
      <div style={{ font: `400 18px/1 ${MONO}`, letterSpacing: '0.26em', color: 'rgba(240,242,242,0.4)' }}>HUN 2–3 IRL</div>
    </div>
    {[['KELLEHER', 'GK / IRELAND', C.Kelleher, C.Scales], ['SCALES', 'CB / IRELAND', C.Scales, C.Parrott], ['PARROTT', 'ST / IRELAND', C.Parrott, C.Impact]].map(([n, r, a, b]) => {
      const on = T >= a && T < b;
      const u = clamp((T - a) / 0.26, 0, 1);
      return <div key={n} style={{
        position: 'absolute', left: 78, bottom: 92, display: on ? 'flex' : 'none', alignItems: 'flex-end', gap: 18,
        opacity: u * clamp((b - T) / 0.12, 0, 1), transform: `translateY(${(1 - Easing.easeOutQuart(u)) * 22}px)`,
      }}>
        <div style={{ width: 4, height: 54, background: YEL }} />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ font: `400 14px/1 ${MONO}`, letterSpacing: '0.3em', color: TEAL }}>{r}</div>
          <div style={{ font: `700 46px/1 ${DISP}`, letterSpacing: '0.06em', color: '#F2F4F4' }}>{n}</div>
        </div>
      </div>;
    })}
    </React.Fragment> : null}

    {hud ? (() => {
      const lines = [
        [C.Kelleher + 0.10, C.Scales, "One last throw of the dice. It's all on this from Caoimhin Kelleher.", false],
        [C.Scales + 0.06, C.Parrott, 'Scales is up after it.', false],
        [C.Parrott + 0.06, C.Parrott + 1.30, 'Scales wins the header and\u2009\u2014', false],
        [C.Parrott + 1.30, C.Impact + 0.34, "THERE'S A CHANCEEE", true],
      ];
      const cur = lines.find(([a, b]) => T >= a && T < b);
      if (!cur) return null;
      const [a, b, text, big] = cur;
      const u = clamp((T - a) / 0.22, 0, 1);
      return <div style={{
        position: 'absolute', left: 0, right: 0, bottom: big ? 196 : 208, display: 'flex', justifyContent: 'center',
        padding: '0 200px', opacity: u * clamp((b - T) / 0.14, 0, 1),
        transform: `translateY(${(1 - Easing.easeOutQuart(u)) * 14}px)`,
      }}>
        {big ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14 }}>
            <div style={{
              font: `700 76px/1 ${DISP}`, letterSpacing: '-0.005em', color: YEL,
              textShadow: `0 0 56px ${YEL}66, 0 6px 30px rgba(0,0,0,0.7)`,
              transform: `scale(${1 + (1 - Easing.easeOutQuart(u)) * 0.14})`,
            }}>{text}</div>
            <div style={{ width: 300 * Easing.easeOutQuart(u), height: 3, background: YEL, opacity: 0.85 }} />
          </div>
        ) : (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 20, padding: '18px 34px 18px 26px',
            background: 'linear-gradient(90deg, rgba(8,11,10,0.86), rgba(8,11,10,0.62))',
            borderTop: '1px solid rgba(255,255,255,0.10)', backdropFilter: 'blur(3px)',
            boxShadow: '0 22px 48px rgba(0,0,0,0.55)',
          }}>
            <div style={{ width: 10, height: 10, background: YEL, flex: 'none' }} />
            <div style={{
              font: `400 30px/1.3 ${MONO}`, letterSpacing: '0.03em', textAlign: 'left',
              color: 'rgba(238,241,240,0.95)', textWrap: 'pretty',
            }}>{text}</div>
          </div>
        )}
      </div>;
    })() : null}

    {/* title */}
    <div style={{
      position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
      opacity: clamp((T - C.Flip + 0.08) / 0.12, 0, 1),
    }}>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 34, transform: `scale(${MOTION.settle(1, 1.09, C.Gates, total)(T)})` }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end' }}>
          {'FOOTBALLERS'.split('').map((ch, i) => <Flap key={i} ch={ch} i={i}
            p={clamp((T - (C.Flip + i * 0.045)) / 0.52, 0, 1)} />)}
          <div style={{
            font: `700 44px/1 ${DISP}`, color: TEAL, paddingBottom: 12, marginLeft: 4,
            textShadow: `0 0 28px ${TEAL}55`,
            opacity: MOTION.snap(0, 1, C.Flip + 0.62, C.Flip + 0.86)(T) * (1 - MOTION.glide(0, 1, C.Gates - 0.28, C.Gates - 0.04)(T)),
            transform: `translateY(${MOTION.glide(-26, 0, C.Flip + 0.62, C.Flip + 0.92)(T) + MOTION.glide(0, 120, C.Gates - 0.28, C.Gates + 0.02)(T)}px) rotate(${MOTION.glide(0, 16, C.Gates - 0.28, C.Gates + 0.02)(T)}deg)`,
          }}>.ie</div>
        </div>
        <div style={{ font: `400 17px/1 ${MONO}`, letterSpacing: '0.52em', color: 'rgba(240,242,242,0.42)', paddingLeft: '0.52em' }}>IRISH FOOTBALL</div>
      </div>
    </div>

    <div style={{ position: 'absolute', inset: 0, background: '#fff', opacity: flash * 0.92, mixBlendMode: 'screen' }} />
    <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: 'repeating-linear-gradient(0deg, rgba(0,0,0,0.16) 0 1px, rgba(0,0,0,0) 1px 3px)' }} />
    <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: 'radial-gradient(120% 88% at 50% 50%, rgba(0,0,0,0) 42%, rgba(0,0,0,0.72) 100%)' }} />
  </div>;
}

function Piece({ hud }) {
  const { T, CUES: C, authoredTotal } = useComposition();
  const shake = T >= C.Impact && T < C.Impact + 0.42
    ? Math.pow(1 - (T - C.Impact) / 0.42, 2) * 26 : 0;
  const ph = T - C.Impact;
  const sx = shake * Math.sin(ph * 118), sy = shake * Math.sin(ph * 91 + 1.7), sr = shake * 0.055 * Math.sin(ph * 74);
  const open = MOTION.settle(0, 1, C.Gates, authoredTotal)(T);
  const seam = clamp((T - (C.Flip + 0.2)) / 0.3, 0, 1) * (1 - open);

  const half = (side) => (
    <div style={{
      position: 'absolute', left: side === 'L' ? 0 : W / 2, top: 0, width: W / 2, height: H, overflow: 'hidden',
      transform: `translateX(${(side === 'L' ? -1 : 1) * open * (W / 2 + 30)}px)`,
      boxShadow: open > 0.01 ? `0 0 90px rgba(0,0,0,0.9)` : 'none',
    }}>
      <div style={{ position: 'absolute', left: side === 'L' ? 0 : -W / 2, top: 0, width: W, height: H }}>
        <Action T={T} C={C} total={authoredTotal} hud={hud} />
      </div>
      <div style={{
        position: 'absolute', top: 0, bottom: 0, [side === 'L' ? 'right' : 'left']: 0, width: 3,
        background: YEL, opacity: seam * 0.9, boxShadow: `0 0 40px ${YEL}`,
      }} />
    </div>
  );

  return <div style={{ position: 'absolute', inset: 0, background: BG, overflow: 'hidden' }}>
    <div style={{ position: 'absolute', inset: 0, transform: `scale(${MOTION.glide(1.05, 1, C.Gates, authoredTotal)(T)})` }}>
      <Site />
    </div>
    <div style={{ position: 'absolute', inset: 0, transform: `translate(${sx}px, ${sy}px) rotate(${sr}deg) scale(${1 + shake / 900})` }}>
      {half('L')}{half('R')}
    </div>
    <div style={{
      position: 'absolute', right: 74, bottom: 66, font: `400 15px/1 ${MONO}`, letterSpacing: '0.34em',
      color: 'rgba(240,242,242,0.34)', opacity: clamp(1 - (T - C.Impact) / 0.25, 0, 1),
    }}>CLICK TO SKIP</div>
  </div>;
}

function AbroadIntro(props) {
  YEL = props.accent || '#F5C518';
  YEL_BACK = shade(YEL, 0.56);
  TEAL = props.accent2 || '#35D4BF';
  const hud = props.hud !== false;
  const skip = () => {
    const el = document.querySelector('[data-om-exportable-video-with-duration-secs]');
    if (el) el.dispatchEvent(new CustomEvent('data-om-seek-to-time-frame', { detail: { time: 7.8, sync: true }, bubbles: false }));
  };
  return <div style={{ position: 'absolute', inset: 0, cursor: 'pointer' }} onClick={skip}>
    <CompositionStage width={W} height={H} bg={BG} scenes={window.OM_SCENES} playback={window.OM_PLAYBACK}>
      <Piece hud={hud} />
    </CompositionStage>
  </div>;
}

window.AbroadIntro = AbroadIntro;
