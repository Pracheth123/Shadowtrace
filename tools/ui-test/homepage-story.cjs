// "Papers to Practice" homepage story check. Serves the production build with
// `vite preview` (no backend), drives the scroll story in Chromium, records
// videos (forward and reverse scrolling) and stage screenshots, and asserts that
// the illustrative scene makes no network requests and never asks for the
// microphone or camera.
//
//   cd client && npm run build
//   cd ../tools/ui-test && npm run homepage
//
// Output: logs/ui-smoke/homepage/ (screenshots, *.webm recordings, results.json).
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const out = path.join(root, 'logs', 'ui-smoke', 'homepage');
fs.rmSync(out, { recursive: true, force: true }); fs.mkdirSync(out, { recursive: true });
const URL = 'http://127.0.0.1:4173/';
const axePath = require.resolve('axe-core/axe.min.js');
const results = { checks: [], measurements: {}, a11y: {}, errors: [], sceneRequests: [] };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function check(name, fn) { await fn(); results.checks.push(name); console.log('PASS', name); }
async function a11y(page, name) {
  await page.addScriptTag({ path: axePath });
  const r = await page.evaluate(() => axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } }));
  results.a11y[name] = r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.map((n) => n.target).slice(0, 8) }));
  console.log('A11Y', name, JSON.stringify(results.a11y[name]));
}
const trapDevices = () => { window.__deviceRequests = 0; if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = async () => { window.__deviceRequests++; throw new Error('blocked by test'); }; };
const stage = (page) => page.locator('.story').getAttribute('data-stage');
// Scroll the window to a given local progress of the story section.
const toProgress = (page, v) => page.evaluate((v) => { const s = document.querySelector('.story'); const top = s.getBoundingClientRect().top + scrollY; scrollTo(0, top + v * (s.offsetHeight - innerHeight)); }, v);
const snapshot = (page) => page.evaluate(() => ({
  papers: [...document.querySelectorAll('.paper-flight')].map((el) => { const r = el.getBoundingClientRect(); return { kind: el.querySelector('.paper').className.replace('paper paper-', ''), cx: Math.round(r.x + r.width / 2), cy: Math.round(r.y + r.height / 2), o: +getComputedStyle(el).opacity }; }),
  interview: +getComputedStyle(document.querySelector('[data-card=interview]')).opacity,
  feedback: +getComputedStyle(document.querySelector('[data-card=feedback]')).opacity,
  stageTop: Math.round(document.querySelector('.story-stage').getBoundingClientRect().top),
}));
const relative = (snap) => { const r = snap.papers.find((p) => p.kind === 'resume'); return snap.papers.map((p) => ({ ...p, dx: p.cx - r.cx, dy: p.cy - r.cy })); };
// Visible cards must stay inside the stage; the CTA must be the topmost element at its centre.
const layoutProblems = (page) => page.evaluate(() => {
  const issues = []; const st = document.querySelector('.story-stage').getBoundingClientRect();
  for (const card of document.querySelectorAll('.story-card-wrap')) {
    if (+getComputedStyle(card).opacity < 0.5) continue;
    const r = card.getBoundingClientRect();
    if (r.left < st.left - 2 || r.right > st.right + 2 || r.top < st.top - 2 || r.bottom > st.bottom + 2) issues.push(`${card.dataset.card} card outside stage ${JSON.stringify([Math.round(r.left - st.left), Math.round(r.top - st.top), Math.round(st.right - r.right), Math.round(st.bottom - r.bottom)])}`);
  }
  const cta = [...document.querySelectorAll('.story-copy button')].find((b) => b.textContent.includes('Prepare my interview'));
  const c = cta.getBoundingClientRect();
  const cy = c.top + c.height / 2;
  if (cy > 0 && cy < innerHeight) { const hit = document.elementFromPoint(c.left + c.width / 2, cy); if (!cta.contains(hit)) issues.push('CTA covered by ' + (hit && hit.className)); }
  return issues;
});

// Visibility and inertness of both story cards, read together.
const cardStates = (page) => page.evaluate(() => [...document.querySelectorAll('.story-card-wrap')].map((el) => ({ card: el.dataset.card, opacity: +getComputedStyle(el).opacity, inert: el.inert, ariaHidden: el.getAttribute('aria-hidden') === 'true' })));
// Press Tab (or Shift+Tab) n times from the current focus; record where focus lands.
const tabWalk = async (page, n, shift = false) => {
  const seen = [];
  for (let i = 0; i < n; i++) {
    await page.keyboard.press(shift ? 'Shift+Tab' : 'Tab');
    seen.push(await page.evaluate(() => { const a = document.activeElement; const wrap = a.closest('.story-card-wrap'); return { text: (a.textContent || '').trim().slice(0, 40), role: a.getAttribute('role'), inCard: wrap ? wrap.dataset.card : null, cardOpacity: wrap ? +getComputedStyle(wrap).opacity : null, body: a === document.body }; }));
  }
  return seen;
};

const server = spawn(process.execPath, [path.join(root, 'client/node_modules/vite/bin/vite.js'), 'preview', '--host', '127.0.0.1', '--port', '4173', '--strictPort'], { cwd: path.join(root, 'client'), stdio: 'ignore' });
let browser;
(async () => { try {
  let ready = false; for (let i = 0; i < 80 && !ready; i++) { try { ready = (await fetch(URL)).ok; } catch {} if (!ready) await wait(250); }
  assert(ready, 'vite preview did not start; run `npm run build` in client first');
  browser = await chromium.launch({ headless: true });

  // ================= Desktop 1440×900, recorded
  const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 }, recordVideo: { dir: out, size: { width: 1440, height: 900 } } });
  await desktop.addInitScript(trapDevices);
  const page = await desktop.newPage();
  page.on('pageerror', (e) => results.errors.push(e.message));
  const onRequest = (r) => { if (!r.url().startsWith(URL + 'assets/') && r.url() !== URL) results.sceneRequests.push(r.method() + ' ' + r.url()); };
  page.on('request', onRequest);
  await page.goto(URL);
  await page.waitForTimeout(300); await page.screenshot({ path: out + '/desktop-load-300ms.png' });

  await check('Load: headline and papers enter once, then settle', async () => {
    const early = await page.locator('.paper-enter').last().evaluate((el) => +getComputedStyle(el).opacity);
    await page.waitForTimeout(1500);
    const settled = await page.evaluate(() => document.getAnimations().filter((a) => a.playState === 'running' && a.effect?.target?.closest?.('.story') && !a.effect.target.closest('.story-scroll-hint')).length);
    const endless = await page.evaluate(() => document.getAnimations().filter((a) => a.effect?.getComputedTiming().iterations === Infinity).length);
    assert.equal(endless, 0, 'no endless animations');
    results.measurements.paperOpacityAt300ms = early;
    assert(early < 1, 'papers still entering at 300 ms'); assert.equal(settled, 0, 'no ongoing animation after load');
    assert.equal(await page.locator('.story').getAttribute('data-mode'), 'pinned');
  });

  const A = await snapshot(page); await page.screenshot({ path: out + '/desktop-A-scattered.png' });
  await check('Headline, navigation and CTA are readable and clickable on the first screen', async () => {
    assert.deepEqual(await layoutProblems(page), []);
    assert(await page.getByRole('heading', { level: 1 }).isVisible());
    assert(await page.locator('.home-nav').isVisible());
    assert.equal(await page.locator('.story-clip').getAttribute('aria-hidden'), 'true');
    assert.equal(await page.locator('.story-clip').evaluate((el) => getComputedStyle(el).pointerEvents), 'none');
  });

  await check('Section is ~200svh and the stage stays pinned while scrolling', async () => {
    const ratio = await page.evaluate(() => document.querySelector('.story').offsetHeight / innerHeight);
    results.measurements.sectionHeightInViewports = +ratio.toFixed(2);
    assert(ratio >= 1.8 && ratio <= 2.2);
    await toProgress(page, 0.3); await wait(200); const t1 = (await snapshot(page)).stageTop;
    await toProgress(page, 0.7); await wait(200); const t2 = (await snapshot(page)).stageTop;
    results.measurements.stageTopAt30and70 = [t1, t2]; assert(Math.abs(t1 - t2) <= 1, 'sticky stage did not move');
  });

  await toProgress(page, 0.35); await wait(400);
  const B = await snapshot(page); await page.screenshot({ path: out + '/desktop-B-gathered.png' });
  await check('A → B: papers fly from scattered positions and gather into a stack', async () => {
    assert.equal(await stage(page), '1');
    const spreadA = Math.max(...relative(A).map((p) => Math.hypot(p.dx, p.dy)));
    const spreadB = Math.max(...relative(B).map((p) => Math.hypot(p.dx, p.dy)));
    const travel = Math.max(...A.papers.map((p, i) => Math.hypot(p.cx - B.papers[i].cx, p.cy - B.papers[i].cy)));
    results.measurements.spreadA = Math.round(spreadA); results.measurements.spreadB = Math.round(spreadB); results.measurements.maxPaperTravelAtoB = Math.round(travel);
    assert(spreadA > 200 && spreadB < 70 && travel > 150, "scattered → stacked");
    assert.equal(await page.locator('.paper-highlight').first().evaluate((el) => getComputedStyle(el).transform), 'none'); // scaleX(1): fully highlighted
  });
  await check('A → B path is curved, not straight', async () => {
    // Midpoint of the move: x has travelled further than y, so the path bows.
    await toProgress(page, 0.17); await wait(200);
    const mid = await snapshot(page); const i = A.papers.findIndex((p) => p.kind === 'projects');
    const fx = (mid.papers[i].cx - A.papers[i].cx) / (B.papers[i].cx - A.papers[i].cx);
    const fy = (mid.papers[i].cy - A.papers[i].cy) / (B.papers[i].cy - A.papers[i].cy);
    results.measurements.midpointProgressXY = [+fx.toFixed(2), +fy.toFixed(2)];
    assert(fx - fy > 0.2, 'x leads y at the midpoint');
  });

  await toProgress(page, 0.64); await wait(500);
  const C = await snapshot(page); await page.screenshot({ path: out + '/desktop-C-interview.png' });
  await check('C: the stack stays, the interview card arrives and a connector links statement → question', async () => {
    assert.equal(await stage(page), '2'); assert.equal(C.interview, 1); assert.equal(C.feedback, 0);
    assert(C.papers.every((p) => p.o === 1));
    const path = page.locator('.story-connector path');
    assert.equal(await path.evaluate((el) => getComputedStyle(el).opacity), '1');
    assert.deepEqual(await layoutProblems(page), []);
  });

  await check('Tabs: pointer + arrows/Home/End, tab↔panel links, distinct questions from one background', async () => {
    const tabs = page.getByRole('tab'); assert.equal(await tabs.count(), 3);
    const questions = new Set();
    for (const name of ['HR', 'Hiring manager', 'Specialist']) {
      await page.getByRole('tab', { name, exact: true }).click();
      const panel = page.getByRole('tabpanel');
      assert.equal(await panel.getAttribute('aria-labelledby'), await page.getByRole('tab', { name, exact: true }).getAttribute('id'));
      assert.equal(await page.getByRole('tab', { name, exact: true }).getAttribute('aria-selected'), 'true');
      questions.add(await panel.locator('.story-question').innerText());
      assert.equal(await panel.locator('.story-source').innerText(), '“I helped launch a new service.”');
    }
    assert.equal(questions.size, 3);
    await page.getByRole('tab', { name: 'HR', exact: true }).click();
    await page.keyboard.press('ArrowRight'); assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Hiring manager');
    await page.keyboard.press('End'); assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Specialist');
    await page.keyboard.press('Home'); assert.equal(await page.evaluate(() => document.activeElement.textContent), 'HR');
    await page.keyboard.press('ArrowLeft'); assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Specialist');
    await page.getByRole('tab', { name: 'Hiring manager', exact: true }).click();
  });

  await toProgress(page, 0.93); await wait(500);
  const D = await snapshot(page); await page.screenshot({ path: out + '/desktop-D-feedback.png' });
  await check('D: supporting papers leave, coaching quotes the CANDIDATE ANSWER (not the resume)', async () => {
    assert.equal(await stage(page), '3'); assert.equal(D.feedback, 1);
    assert(D.papers.filter((p) => p.kind !== 'resume').every((p) => p.o === 0));
    const quote = (await page.locator('.story-quote').innerText()).replace(/[“”]/g, '');
    const answer = await page.locator('.story-answer').innerText();
    assert.equal(quote, 'checked support requests after launch'); assert(answer.includes(quote)); assert(!'I helped launch a new service.'.includes(quote));
    assert.match(await page.locator('.story-coaching').innerText(), /what changed in those requests/);
    assert(!/\d+%|score|hired|guarantee/i.test(await page.locator('.story-feedback').innerText()), 'no metrics, scores or outcomes');
    assert.deepEqual(await layoutProblems(page), []);
    assert(await page.getByText('Illustrative example — not a live assessment', { exact: true }).isVisible());
  });

  await check('Reverse scrolling restores the composition exactly', async () => {
    await toProgress(page, 0.64); await wait(300);
    const back = await snapshot(page); assert.equal(await stage(page), '2');
    assert(back.papers.every((p, i) => Math.abs(p.cx - C.papers[i].cx) <= 1 && Math.abs(p.cy - C.papers[i].cy) <= 1));
    await page.evaluate(() => scrollTo(0, 0)); await wait(300);
    const top = await snapshot(page); assert.equal(await stage(page), '0');
    assert(top.papers.every((p, i) => Math.abs(p.cx - A.papers[i].cx) <= 1 && Math.abs(p.cy - A.papers[i].cy) <= 1));
  });

  await check('Rapid jumps and landing between holds give coherent states', async () => {
    for (const v of [0.93, 0.05, 0.5, 0.21, 0.77, 0.12, 1, 0]) {
      await toProgress(page, v); await page.waitForTimeout(60);
      const s = await snapshot(page);
      assert(s.papers.every((p) => Number.isFinite(p.cx) && Number.isFinite(p.cy) && p.o >= 0 && p.o <= 1));
      assert.deepEqual(await layoutProblems(page), [], `layout at ${v}`);
    }
  });

  await check('Native wheel, Page Down and End keys scroll normally (no hijacking or snapping)', async () => {
    // Release focus from the tablist (tabs rightly own Home/End) before testing page keys.
    await page.evaluate(() => { document.activeElement?.blur(); scrollTo(0, 0); }); await page.mouse.move(700, 500);
    await page.mouse.wheel(0, 300); await wait(300);
    const afterWheel = await page.evaluate(() => scrollY); assert(Math.abs(afterWheel - 300) <= 2, `wheel moved ${afterWheel}`);
    await page.locator('body').press('PageDown'); await wait(500);
    const afterPage = await page.evaluate(() => scrollY); assert(afterPage > afterWheel + 300);
    await wait(700); assert.equal(await page.evaluate(() => scrollY), afterPage, 'no snapping after scroll stops');
    await page.locator('body').press('End'); // Chromium animates long keyboard scrolls
    await page.waitForFunction(() => scrollY + innerHeight >= document.documentElement.scrollHeight - 2, null, { timeout: 5000 });
    await page.evaluate(() => scrollTo(0, 0)); await wait(300);
  });

  await check('Step buttons jump to each stage; the interview card becomes focusable only once it is on stage', async () => {
    for (const [i, name] of [[1, /Start with your experience/], [3, /Find your next clearer answer/], [0, /You’ve done the work\./]]) {
      await page.getByRole('button', { name }).click(); await page.waitForFunction((i) => document.querySelector('.story').dataset.stage === String(i), i, { timeout: 4000 });
    }
    // Stage 0: the hidden card is inert, so focus() is refused (it used to succeed and pull the card on stage).
    // It is also out of the accessibility tree, so role queries cannot find it at all.
    assert.equal(await page.getByRole('tab').count(), 0);
    await page.locator('[data-card=interview] [role=tab]').nth(1).evaluate((el) => el.focus());
    assert.notEqual(await page.evaluate(() => document.activeElement.getAttribute('role')), 'tab', 'hidden tab took focus');
    await page.getByRole('button', { name: /Experience becomes a question/ }).click();
    await page.waitForFunction(() => document.querySelector('.story').dataset.stage === '2', null, { timeout: 4000 });
    await page.waitForFunction(() => !document.querySelector('[data-card=interview]').inert, null, { timeout: 4000 });
    await page.getByRole('tab', { name: 'Hiring manager', exact: true }).focus();
    assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Hiring manager');
  });

  await check('Pinned: inert/aria-hidden always match card visibility at every stage', async () => {
    for (const v of [0.02, 0.35, 0.64, 0.93, 0.5, 0.17, 0.78]) {
      await toProgress(page, v); await wait(250);
      const cards = await cardStates(page);
      for (const c of cards) assert.equal(c.inert, c.opacity <= 0.5, `${c.card} at ${v}: opacity ${c.opacity} inert ${c.inert}`);
      for (const c of cards) assert.equal(c.ariaHidden, c.inert, `${c.card} aria-hidden at ${v}`);
    }
  });

  await check('Pinned: focus inside the interview card moves to a step button when the card hides', async () => {
    await toProgress(page, 0.64); await wait(300);
    await page.getByRole('tab', { name: 'Hiring manager', exact: true }).focus();
    await page.evaluate(() => scrollTo(0, 0)); await wait(400);
    const where = await page.evaluate(() => ({ inSteps: !!document.activeElement.closest('.story-steps'), body: document.activeElement === document.body, inCard: !!document.activeElement.closest('.story-card-wrap') }));
    assert.deepEqual(where, { inSteps: true, body: false, inCard: false });
  });

  await check('Skip visual story moves focus past the scene without changing the hash route', async () => {
    await page.evaluate(() => scrollTo(0, 0)); await wait(200);
    const hash = await page.evaluate(() => location.hash);
    await page.getByRole('button', { name: 'Skip visual story' }).focus(); await page.keyboard.press('Enter'); await wait(1200);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'how-heading');
    assert.equal(await page.evaluate(() => location.hash), hash);
    const top = await page.locator('#how-heading').evaluate((el) => el.getBoundingClientRect().top);
    assert(top >= 0 && top < 400, `next section in view (${top})`);
  });

  await check('Targeted-practice comparison and FAQ still work', async () => {
    const toggle = page.getByRole('button', { name: 'See an example rewrite', exact: true });
    await toggle.scrollIntoViewIfNeeded(); await wait(700); await toggle.click();
    await page.getByText('Original wording', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Back to the practice note', exact: true }).click();
    const summary = page.getByText('Can I type instead of speaking?', { exact: true });
    await summary.scrollIntoViewIfNeeded(); await wait(700); await summary.click();
    await page.getByText('Yes. Choose typing during setup', { exact: false }).waitFor({ state: 'visible', timeout: 2000 });
  });

  await check('The scene made no API, WebSocket, guest or third-party requests and no device requests', async () => {
    page.off('request', onRequest); // the real CTAs below are expected to call the API
    assert.deepEqual(results.sceneRequests, []);
    assert.equal(await page.evaluate(() => window.__deviceRequests), 0);
  });

  await page.evaluate(() => scrollTo(0, 0)); await toProgress(page, 0.64); await wait(500); await a11y(page, 'desktop-stage-C');
  await page.evaluate(() => scrollTo(0, 0)); await wait(300);

  await check('Real CTAs open setup and history', async () => {
    await page.getByRole('button', { name: 'Prepare my interview' }).click(); await page.waitForFunction(() => location.hash === '#setup');
    // Route cleanup: the story unmounts with no inert residue, and comes back coherent.
    assert.equal(await page.locator('.story').count(), 0); assert.equal(await page.locator('[inert]').count(), 0);
    await page.goBack(); await page.waitForFunction(() => document.querySelector('.story')?.dataset.stage);
    for (const c of await cardStates(page)) assert.equal(c.inert, c.opacity <= 0.5, `after back: ${c.card}`);
    await page.goto(URL); await page.locator('.home-nav').getByRole('button', { name: 'Your history' }).click(); await page.waitForFunction(() => location.hash === '#history');
  });
  const desktopVideo = page.video(); await desktop.close();
  fs.renameSync(await desktopVideo.path(), out + '/story-desktop-checks.webm');

  // ================= Desktop recording: a human-paced scroll down and back up
  const rec = await browser.newContext({ viewport: { width: 1440, height: 900 }, recordVideo: { dir: out, size: { width: 1440, height: 900 } } });
  const rp = await rec.newPage(); await rp.goto(URL); await rp.waitForTimeout(1800); await rp.mouse.move(980, 480);
  const wheel = async (steps, dy, pause = 60) => { for (let i = 0; i < steps; i++) { await rp.mouse.wheel(0, dy); await rp.waitForTimeout(pause); } };
  await wheel(18, 24); await rp.waitForTimeout(1100);                 // A → B: papers gather
  await wheel(13, 24); await rp.waitForTimeout(800);                  // B → C: interview card + connector
  await rp.getByRole('tab', { name: 'HR', exact: true }).click(); await rp.waitForTimeout(800);
  await rp.getByRole('tab', { name: 'Specialist', exact: true }).click(); await rp.waitForTimeout(800);
  await rp.getByRole('tab', { name: 'Hiring manager', exact: true }).click(); await rp.waitForTimeout(600);
  await wheel(10, 24); await rp.waitForTimeout(1200);                 // C → D: coaching card
  await wheel(10, 30); await rp.waitForTimeout(700);                  // the section releases into the page
  await wheel(30, -30, 55); await rp.waitForTimeout(900);             // reverse: D → C → B
  await wheel(16, -30, 55); await rp.waitForTimeout(1000);            // back to scattered
  const recVideo = rp.video(); await rec.close();
  fs.renameSync(await recVideo.path(), out + '/story-desktop-scroll.webm');

  // ================= Widths, short desktop, resize
  const layouts = [[320, 700, 'compact'], [390, 844, 'compact'], [768, 1024, 'compact'], [1024, 768, 'pinned'], [1440, 900, 'pinned'], [1440, 700, 'compact']];
  for (const [w, h, mode] of layouts) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h } }); const p = await ctx.newPage();
    await p.goto(URL); await p.waitForTimeout(1500);
    await check(`${w}×${h}: ${mode} layout, no overflow, cards fit, CTA clear at every stage`, async () => {
      assert.equal(await p.locator('.story').getAttribute('data-mode'), mode);
      for (let s = 0; s < 4; s++) {
        if (mode === 'pinned') { await toProgress(p, [0.02, 0.35, 0.64, 0.93][s]); await wait(350); }
        else { await p.locator('.story-steps button').nth(s).click(); await wait(1400); }
        assert.equal(await stage(p), String(s));
        assert(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'horizontal overflow');
        assert.deepEqual(await layoutProblems(p), [], `stage ${s}`);
        await p.locator('.story-stage').screenshot({ path: `${out}/w${w}x${h}-stage${'ABCD'[s]}.png` });
      }
      const clipped = await p.evaluate(() => [...document.querySelectorAll('.story button, .home-page > header button, .home-page > header a')].filter((el) => el.offsetParent !== null && getComputedStyle(el.closest('.story-card-wrap') || el).opacity !== '0').map((el) => ({ el, r: el.getBoundingClientRect() })).filter(({ el, r }) => r.width < 24 || r.height < 24 || el.scrollWidth > el.clientWidth + 2).map(({ el }) => el.textContent.trim().slice(0, 30)));
      assert.deepEqual(clipped, []);
    });
    await p.evaluate(() => scrollTo(0, 0)); await wait(200); await p.screenshot({ path: `${out}/w${w}x${h}-first-screen.png` });
    if (w === 390) await a11y(p, 'mobile-390');
    await ctx.close();
  }

  await check('Resize across modes keeps a coherent composition', async () => {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } }); const p = await ctx.newPage();
    await p.goto(URL); await p.waitForTimeout(1500); await toProgress(p, 0.64); await wait(300);
    for (const [w, h, mode] of [[1100, 800, 'pinned'], [900, 900, 'compact'], [1440, 900, 'pinned']]) {
      await p.setViewportSize({ width: w, height: h }); await wait(700);
      assert.equal(await p.locator('.story').getAttribute('data-mode'), mode);
      assert(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      assert.deepEqual(await layoutProblems(p), [], `after resize to ${w}×${h}`);
    }
    await ctx.close();
  });

  // ================= Mobile, recorded: labelled manual controls
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, recordVideo: { dir: out, size: { width: 390, height: 844 } } });
  await mobile.addInitScript(trapDevices);
  const mp = await mobile.newPage(); const mobileRequests = [];
  mp.on('request', (r) => { if (!r.url().startsWith(URL + 'assets/') && r.url() !== URL) mobileRequests.push(r.url()); });
  await mp.goto(URL); await mp.waitForTimeout(1500);
  await check('Mobile: compact unpinned stage, three papers, tap through all four stages and back', async () => {
    assert.equal(await mp.locator('.story').getAttribute('data-mode'), 'compact');
    assert.notEqual(await mp.locator('.story-sticky').evaluate((el) => getComputedStyle(el).position), 'sticky');
    assert.equal(await mp.locator('.paper-flight').count(), 3);
    await mp.locator('.story-stage').scrollIntoViewIfNeeded(); await wait(300);
    const steps = mp.locator('.story-steps button');
    for (const s of [0, 1, 2, 3, 2, 1, 0]) {
      await steps.nth(s).tap(); await wait(1300);
      assert.equal(await steps.nth(s).getAttribute('aria-current'), 'step');
      if (s === 2) { await mp.getByRole('tab', { name: 'Specialist', exact: true }).tap(); await mp.getByText('How did you plan the rollout', { exact: false }).waitFor(); await mp.getByRole('tab', { name: 'Hiring manager', exact: true }).tap(); }
      await mp.screenshot({ path: `${out}/mobile-stage-${s + 1}.png` });
    }
    assert.deepEqual(mobileRequests, []); assert.equal(await mp.evaluate(() => window.__deviceRequests), 0);
  });
  const mobileVideo = mp.video(); await mobile.close();
  fs.renameSync(await mobileVideo.path(), out + '/story-mobile.webm');

  // ================= Compact keyboard focus: the regression axe missed
  await check('Compact stage 4: Tab and Shift+Tab never reach the invisible interviewer card', async () => {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } }); const p = await ctx.newPage();
    p.on('pageerror', (e) => results.errors.push(e.message));
    await p.goto(URL); await p.waitForTimeout(1500);
    await p.getByRole('button', { name: /Find your next clearer answer/ }).click(); await wait(1500);
    assert.equal(await stage(p), '3');
    const cards = Object.fromEntries((await cardStates(p)).map((c) => [c.card, c]));
    assert.equal(cards.interview.opacity, 0); assert.equal(cards.interview.inert, true); assert.equal(cards.interview.ariaHidden, true);
    assert.equal(cards.feedback.opacity, 1); assert.equal(cards.feedback.inert, false);
    // The hidden tablist is gone from the accessibility tree.
    assert.equal(await p.getByRole('tab').count(), 0);
    await p.getByRole('button', { name: 'Skip visual story' }).focus();
    const forward = await tabWalk(p, 4);
    results.measurements.compactStage4TabOrder = forward.map((f) => f.text);
    assert(forward.every((f) => !f.inCard && !f.body && f.role !== 'tab'), 'Tab reached hidden card: ' + JSON.stringify(forward));
    const back = await tabWalk(p, 4, true);
    assert(back.every((f) => !f.inCard && !f.body && f.role !== 'tab'), 'Shift+Tab reached hidden card: ' + JSON.stringify(back));
    assert(back.some((f) => f.text === 'Skip visual story'), 'Shift+Tab returns to the skip action');
    // Stage 3 again: the card is visible and its tabs are reachable straight after the skip action.
    await p.getByRole('button', { name: /Experience becomes a question/ }).click(); await wait(1500);
    assert.equal((await cardStates(p)).find((c) => c.card === 'interview').inert, false);
    await p.getByRole('button', { name: 'Skip visual story' }).focus();
    const visible = await tabWalk(p, 1);
    assert.equal(visible[0].role, 'tab'); assert.equal(visible[0].inCard, 'interview');
    await p.keyboard.press('ArrowRight'); assert.equal(await p.evaluate(() => document.activeElement.textContent), 'Specialist');
    // Focus inside the card while a step control hides it: focus lands on a step button, not <body>.
    await p.evaluate(() => document.querySelectorAll('.story-steps button')[0].click()); await wait(1500);
    const where = await p.evaluate(() => ({ inSteps: !!document.activeElement.closest('.story-steps'), body: document.activeElement === document.body }));
    assert.deepEqual(where, { inSteps: true, body: false });
    await ctx.close();
  });

  // ================= Reduced motion
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: 'reduce' }); const p = await ctx.newPage();
    await p.goto(URL); await p.waitForTimeout(200);
    await check(`Reduced motion ${w}px: static four-step story, no flight, rotation or pinning; tabs and CTA work`, async () => {
      assert.equal(await p.locator('.story').getAttribute('data-mode'), 'static');
      assert.equal(await p.locator('.paper-flight').count(), 0);
      assert.equal(await p.locator('.story-static-panel').count(), 4);
      assert.equal(await p.locator('.story [inert], .story [aria-hidden=true] [role=tab]').count(), 0, 'static panels stay focusable');
      assert.equal(await p.locator('.reveal[data-reveal=pending]').count(), 0, 'reduced motion: no section waits hidden for a scroll reveal');
      assert.notEqual(await p.locator('.story-sticky').evaluate((el) => getComputedStyle(el).position), 'sticky');
      const moving = await p.evaluate(() => [...document.querySelectorAll('.story *')].filter((el) => { const t = getComputedStyle(el).transform; return t !== 'none' && !/^matrix\(1, 0, 0, 1, 0, 0\)$/.test(t); }).length);
      assert.equal(moving, 0, 'no transformed (rotated or offset) elements');
      assert.equal(await p.locator('.hero-line').first().evaluate((el) => getComputedStyle(el).animationName), 'none');
      await p.getByRole('tab', { name: 'Specialist', exact: true }).click();
      await p.getByText('released it in stages', { exact: false }).first().waitFor();
      assert(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await p.screenshot({ path: `${out}/reduced-motion-${w}.png`, fullPage: true });
      if (w === 1440) await a11y(p, 'reduced-motion-desktop');
      await p.getByRole('button', { name: 'Prepare my interview' }).click(); await p.waitForFunction(() => location.hash === '#setup');
    });
    await ctx.close();
  }

  for (const [name, v] of Object.entries(results.a11y)) assert.equal(v.length, 0, 'Accessibility: ' + name);
  assert.deepEqual(results.errors, []);
  console.log('ALL HOMEPAGE STORY CHECKS PASSED:', results.checks.length);
} finally {
  fs.writeFileSync(out + '/results.json', JSON.stringify(results, null, 2));
  if (browser) await browser.close(); server.kill();
} })().catch((e) => { console.error(e); process.exitCode = 1; });
