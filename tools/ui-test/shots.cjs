// Quick visual pass over the production build: screenshots of the homepage
// (desktop, phone, reduced motion) and the setup page at several widths.
// Uses `vite preview` plus the offline mock backend. Development aid only;
// the assertions live in smoke.cjs and homepage-story.cjs.
//
//   cd client && npm run build && cd ../tools/ui-test && node shots.cjs [outDir]
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const root = path.resolve(__dirname, '../..');
const out = path.resolve(process.argv[2] || path.join(root, 'logs', 'ui-shots'));
fs.mkdirSync(out, { recursive: true });
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'shadowtrace-shots-'));
const python = process.env.UI_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const env = { ...process.env, APP_ENV: 'test', ALLOW_MOCK_PROVIDERS: '1', MOCK_LLM: '1', GROQ_API_KEY: '', DEEPGRAM_API_KEY: '', GUEST_RETENTION_DAYS: '0', PYTHONPATH: path.join(root, 'src'), DATA_DIR: path.join(temp, 'data'), SESSION_LOG_DIR: path.join(temp, 'logs') };
const api = spawn(python, ['-m', 'uvicorn', 'interview.server:app', '--host', '127.0.0.1', '--port', '8000'], { cwd: root, env, stdio: 'ignore' });
const web = spawn(process.execPath, [path.join(root, 'client/node_modules/vite/bin/vite.js'), 'preview', '--host', '127.0.0.1', '--port', '4174', '--strictPort'], { cwd: path.join(root, 'client'), env: { ...process.env, VITE_WS_HOST: '127.0.0.1:8000' }, stdio: 'ignore' });
const URL = 'http://127.0.0.1:4174/';
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  let browser;
  try {
    for (const u of [URL, 'http://127.0.0.1:8000/health']) { for (let i = 0; i < 80; i++) { try { if ((await fetch(u)).ok) break; } catch {} await wait(250); } }
    browser = await chromium.launch();
    for (const [w, h, name, rm] of [[1440, 900, 'desktop'], [390, 844, 'phone'], [1440, 900, 'desktop-reduced', 'reduce']]) {
      const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: rm || 'no-preference' });
      const p = await ctx.newPage();
      await p.goto(URL); await wait(1800);
      await p.screenshot({ path: `${out}/home-${name}.png` });
      await p.screenshot({ path: `${out}/home-${name}-full.png`, fullPage: true });
      await ctx.close();
    }
    for (const [w, h] of [[1440, 900], [390, 844]]) {
      const ctx = await browser.newContext({ viewport: { width: w, height: h } });
      const p = await ctx.newPage();
      await p.goto(URL + '#setup'); await wait(2500);
      await p.screenshot({ path: `${out}/setup-${w}.png`, fullPage: true });
      await ctx.close();
    }
    console.log('wrote', out);
  } finally { if (browser) await browser.close(); api.kill(); web.kill(); }
})().catch((e) => { console.error(e); process.exitCode = 1; });
