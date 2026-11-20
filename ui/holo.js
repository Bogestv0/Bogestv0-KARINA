/* Голограмма Карины. Снаружи одна функция: Holo.setState(name) (+ Holo.smile()).
   Режимы: idle, called (покой с редким взглядом в сторону), think, speak, smile, sleep, wake.
   ВАЖНО: проекторы закреплены. Качается только персонаж и лучи; два основания проекторов
   рисуются отдельным неподвижным слоем (.h-proj) и вырезаются из качающегося слоя маской. */
(function () {
  'use strict';

  /* ===================== НАСТРОЙКИ (правятся здесь) ===================== */
  const CFG = {
    size: 760,                      // сторона кадров, px (все кадры строго одного размера)
    bright: 0.9, sat: 1.1,          // яркость и насыщенность кадров; если свечения много, уменьшать bright
    sway: { x: 0.4, y: 0.7, rot: 0.25, scale: 1.01, scaleAmp: 0.004,   // % / % / градусы
            sx: 0.7, sy: 0.9, sr: 0.5 },                               // скорости
    settle: { time: 360, rate: 16 },       // возврат к базе перед сменой режима (мс, скорость)
    idle:  { blinkMin: 4000, blinkMax: 8000, blinkHold: 150, rateIn: 10, rateOut: 7,
             glanceMin: 9000, glanceMax: 16000 },          // «called»: редкий взгляд в сторону
    speak: { rate: 12, holdMin: 120, holdSpan: 150, poses: [0, 1, 2, 2, 3, 4, 4, 5, 6, 6] },
    think: { rate: 1.7, tilt: 1.8, out: 3000, back: 1700, tiltRate: 1.8, tiltX: 0.8 },
    smile: { rate: 2, peak: 4, holdMs: 2600 },             // пик на 4-м кадре из 5; потом возврат в покой
    sleep: { closeMs: 600, closeRate: 3, fadeRate: 2.5 },
    wake:  { fadeRate: 2.5, closedMs: 1100, openMs: 900, closedRate: 6, openRate: 2 },
    // зоны проекторов в долях кадра (центр и радиусы овала, который остаётся НЕПОДВИЖНЫМ)
    projectors: [ { x: 0.068, y: 0.915, rx: 0.078, ry: 0.088 },
                  { x: 0.932, y: 0.915, rx: 0.078, ry: 0.088 } ],
    projFeather: 0.62               // с какой доли радиуса начинается мягкий край
  };
  const DIR = 'holo/';
  const LAD = {
    mouth: [0, 1, 2, 3, 4, 5, 6].map(i => 'm' + i),
    lookA: [0, 1, 2, 3, 4].map(i => 'ea' + i),
    lookB: [0, 1, 2, 3, 4].map(i => 'eb' + i),
    smile: [0, 1, 2, 3, 4].map(i => 's' + i)
  };
  /* ======================================================================= */

  const wrap = document.getElementById('holoWrap');
  if (!wrap) { window.Holo = { setState() {}, smile() {} }; return; }
  const stage = wrap.parentElement;
  const S = CFG.size;
  const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  // --- слои-вспышки: сон (весь кадр) и моргание (область глаз) ---
  const Z = { z0: wrap.querySelector('[data-k=z0]'), k13: wrap.querySelector('[data-k=k13]'), k15: wrap.querySelector('[data-k=k15]') };
  const W = {}, T = {}, R = {};
  for (const k in Z) { W[k] = 0; T[k] = 0; R[k] = 6; }
  const proj = stage.querySelector('.h-proj');
  stage.style.setProperty('--hf', `brightness(${CFG.bright}) saturate(${CFG.sat})`);

  // --- маска: вырезать основания из качающегося слоя, показать их в неподвижном ---
  (function maskSetup() {
    const f = Math.round(CFG.projFeather * 100);
    const g = (inv) => CFG.projectors.map(p =>
      `radial-gradient(ellipse ${p.rx * 100}% ${p.ry * 100}% at ${p.x * 100}% ${p.y * 100}%,` +
      (inv ? `#000 ${f}%,transparent 100%)` : `transparent ${f}%,#000 100%)`));
    // качающийся слой: прозрачен там, где стоит проектор (пересечение дыр)
    wrap.style.webkitMaskImage = wrap.style.maskImage = g(false).join(',');
    wrap.style.webkitMaskComposite = 'source-in'; wrap.style.maskComposite = 'intersect';
    // неподвижный слой: виден только в зонах проекторов (объединение)
    if (proj) proj.style.webkitMaskImage = proj.style.maskImage = g(true).join(',');
  })();

  // --- канвас для лесенок (рот, взгляд, улыбка) ---
  const cv = document.getElementById('holoCv');
  const cx = cv.getContext('2d');
  cv.width = cv.height = S; cv.style.opacity = 0;
  const IM = {};
  for (const k in LAD) IM[k] = LAD[k].map(n => { const i = new Image(); i.src = DIR + 'mo/' + n + '.webp'; return i; });
  let lad = 'mouth', pos = 0, tgt = 0, prate = 8, drawn = '';
  function setLad(n, t, rate) { if (lad !== n) { lad = n; pos = 0; drawn = ''; } tgt = t; prate = rate; }
  function draw() {
    const a = IM[lad], f = Math.max(0, Math.min(a.length - 1, pos)), i = Math.min(a.length - 2, f | 0), u = f - i, key = lad + Math.round(f * 30);
    if (key === drawn) return;
    if (!a[i].complete || !a[i + 1].complete || !a[i].naturalWidth) return;
    drawn = key;
    cx.clearRect(0, 0, S, S); cx.globalAlpha = 1; cx.drawImage(a[i], 0, 0, S, S);
    if (u > .01) { cx.globalAlpha = u; cx.drawImage(a[i + 1], 0, 0, S, S); }
    cv.style.opacity = pos < .02 ? 0 : 1;
  }

  // --- главный цикл ---
  let TILT = 0, TT = 0, last = performance.now(), t0 = last;
  const SW = CFG.sway;
  function tick(now) {
    const dt = Math.min(.05, (now - last) / 1000); last = now;
    for (const k in Z) {
      const d = T[k] - W[k]; W[k] += d * (1 - Math.exp(-R[k] * dt));
      if (Math.abs(d) < .002) W[k] = T[k];
      Z[k].style.opacity = W[k].toFixed(3);
    }
    pos += (tgt - pos) * (1 - Math.exp(-prate * dt)); if (Math.abs(tgt - pos) < .004) pos = tgt;
    draw();
    TILT += (TT - TILT) * (1 - Math.exp(-CFG.think.tiltRate * dt));
    const s = (now - t0) / 1000, k = reduce ? 0 : 1;
    wrap.style.transform = `translate(${(Math.sin(s * SW.sx) * SW.x + TILT * CFG.think.tiltX) * k}%,${Math.sin(s * SW.sy) * SW.y * k}%) rotate(${(Math.sin(s * SW.sr) * SW.rot + TILT * CFG.think.tilt) * k}deg) scale(${1 + (SW.scale - 1) * k + Math.sin(s * SW.sy) * SW.scaleAmp * k})`;
    // основания проекторов гаснут вместе с кадром сна
    if (proj) proj.style.filter = `brightness(${(CFG.bright - .3 * W.z0).toFixed(3)}) saturate(${CFG.sat})`;
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  // --- режимы ---
  const sleepMs = ms => new Promise(r => setTimeout(r, ms));
  let ep = 0, cur = null;
  const lay = (k, t, r) => { T[k] = t; R[k] = r; };
  function settle() { tgt = 0; prate = CFG.settle.rate; TT = 0; lay('k13', 0, 9); lay('k15', 0, 9); }
  async function blink(e) { lay('k13', 1, CFG.idle.rateIn); await sleepMs(CFG.idle.blinkHold); lay('k13', 0, CFG.idle.rateOut); }
  async function idle(e, glance) {
    let nextGlance = performance.now() + CFG.idle.glanceMin + Math.random() * (CFG.idle.glanceMax - CFG.idle.glanceMin), d = 1;
    while (e === ep) {
      await sleepMs(CFG.idle.blinkMin + Math.random() * (CFG.idle.blinkMax - CFG.idle.blinkMin));
      if (e !== ep) return;
      if (glance && performance.now() > nextGlance) {      // редкий взгляд в сторону
        const L = d > 0 ? 'lookA' : 'lookB'; d = -d;
        setLad(L, 3, 2.2); await sleepMs(1400); if (e !== ep) return;
        setLad(L, 0, 2.2); await sleepMs(800); if (e !== ep) return;
        nextGlance = performance.now() + CFG.idle.glanceMin + Math.random() * (CFG.idle.glanceMax - CFG.idle.glanceMin);
      } else await blink(e);
    }
  }
  async function speak(e) {
    lad = 'mouth'; let prev = 0; const P = CFG.speak.poses;
    while (e === ep) {
      let p; do { p = P[Math.random() * P.length | 0]; } while (Math.abs(p - prev) < 1.5 && p !== 0);
      prev = p; setLad('mouth', p, CFG.speak.rate);
      await sleepMs(CFG.speak.holdMin + Math.random() * CFG.speak.holdSpan);
    }
  }
  async function think(e) {
    let d = 1;
    while (e === ep) {
      const L = d > 0 ? 'lookA' : 'lookB';
      setLad(L, 4, CFG.think.rate); TT = d; await sleepMs(CFG.think.out); if (e !== ep) return;
      setLad(L, 0, CFG.think.rate); TT = 0; await sleepMs(CFG.think.back); d = -d;
    }
  }
  async function smile(e) {
    setLad('smile', CFG.smile.peak, CFG.smile.rate);
    await sleepMs(CFG.smile.holdMs); if (e !== ep) return;
    go('idle', true);                      // после улыбки возвращаемся в покой
  }
  async function sleepM(e) {
    lay('k15', 1, CFG.sleep.closeRate); await sleepMs(CFG.sleep.closeMs); if (e !== ep) return;
    lay('z0', 1, CFG.sleep.fadeRate);
  }
  async function wake(e) {
    const c = CFG.wake;
    lay('z0', 0, c.fadeRate); lay('k15', 1, c.closedRate); await sleepMs(c.closedMs); if (e !== ep) return;
    lay('k15', 0, c.openRate); await sleepMs(c.openMs); if (e === ep) { cur = 'idle'; idle(e, false); }
  }
  const MODES = { idle: e => idle(e, false), called: e => idle(e, true), think: think, speak: speak, smile: smile, sleep: sleepM, wake: wake };

  function go(k, force) {
    if (!MODES[k] || (k === cur && !force)) return;
    const wasSleep = cur === 'sleep';
    cur = k; ep++; const e = ep;
    settle();
    let delay = k === 'wake' ? 0 : CFG.settle.time;
    if (wasSleep && k !== 'sleep' && k !== 'wake') {
      if (k === 'idle' || k === 'called') k = 'wake';          // выход из сна в покой: полное пробуждение
      else {                                                   // сразу говорит/думает: быстро открыть глаза и начать
        lay('z0', 0, 4); lay('k15', 1, 6); delay = 700;
        setTimeout(() => { if (e === ep) lay('k15', 0, 6); }, 350);
      }
    } else if (k !== 'wake') lay('z0', 0, k === 'sleep' ? CFG.sleep.fadeRate : 5);
    setTimeout(() => { if (e === ep) MODES[k](e); }, delay);
  }

  // приложение -> голограмма
  const ALIAS = { idle: 'idle', called: 'called', confirm: 'called', thinking: 'think', think: 'think', speaking: 'speak', speak: 'speak',
                  paused: 'sleep', sleeping: 'sleep', sleep: 'sleep', wake: 'wake', smile: 'smile', loading: 'idle', error: 'idle' };
  window.Holo = {
    setState(name) { const m = ALIAS[name] || 'idle'; if (cur === 'smile' && m === 'idle') return; go(m); },
    smile() { if (cur !== 'sleep' && cur !== 'speak') go('smile', true); },
    config: CFG
  };
  go('idle');
})();
