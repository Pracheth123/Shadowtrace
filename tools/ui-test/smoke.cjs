// Offline browser journey: real HTTP/WebSocket transport, explicitly mocked AI.
//
//   Development (Vite dev server on :5173 proxied to the API on :8000):
//     cd tools/ui-test && npm ci && npx playwright install chromium && npm test
//   Production build (built assets served by the API process on :8000):
//     cd client && npm run build && cd ../tools/ui-test && UI_TEST_PRODUCTION=1 npm test
//
// Synthetic data only, isolated storage in a temp directory, MOCK_LLM=1 with
// blank provider keys. Output: logs/ui-smoke/ (screenshots, results.json).
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const out = path.join(root, 'logs', 'ui-smoke'); fs.mkdirSync(out, {recursive:true});
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'shadowtrace-ui-'));
const log = (name) => fs.openSync(path.join(temp, name + '.log'), 'w');
const python = process.env.UI_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const production = process.env.UI_TEST_PRODUCTION === '1';
const APP_URL = production ? 'http://127.0.0.1:8000' : 'http://127.0.0.1:5173';
const axePath = process.env.AXE_SCRIPT_PATH || require.resolve('axe-core/axe.min.js');
const env = {...process.env, APP_ENV:'test', ALLOW_MOCK_PROVIDERS:'1', MOCK_LLM:'1', GROQ_API_KEY:'', DEEPGRAM_API_KEY:'', GUEST_RETENTION_DAYS:'0', SESSION_IDLE_SECONDS:'40', SESSION_IDLE_WARNING_S:'36', PYTHONPATH:path.join(root,'src'), DATA_DIR:path.join(temp,'data'), SESSION_LOG_DIR:path.join(temp,'logs'), REPORT_DIR:path.join(temp,'reports'), INTAKE_DIR:path.join(temp,'intake'), STORE_PATH:path.join(temp,'store.sqlite')};
// Static mount is only a test harness. Production still needs its reverse proxy.
const args = production ? ['-c', `from interview import server; from fastapi.staticfiles import StaticFiles; import uvicorn; server.app.router.routes = [r for r in server.app.router.routes if getattr(r, 'path', None) != '/']; server.app.mount('/', StaticFiles(directory=${JSON.stringify(path.join(root,'client/dist'))}, html=True)); uvicorn.run(server.app, host='127.0.0.1', port=8000)`] : ['-m','uvicorn','interview.server:app','--host','127.0.0.1','--port','8000'];
const backend = spawn(python, args, {cwd:root,env,stdio:['ignore',log('api'),log('api-error')]});
const vite = production ? null : spawn(process.execPath,[path.join(root,'client/node_modules/vite/bin/vite.js'),'--host','127.0.0.1'],{cwd:path.join(root,'client'),env:{...process.env,VITE_WS_HOST:'127.0.0.1:8000'},stdio:['ignore',log('vite'),log('vite-error')]});
for (const child of [backend,vite].filter(Boolean)) child.on('error',error=>console.error('Server launch failed:',error.message));
let browser;const results={run:{started_at:new Date().toISOString(),mode:production?'production-build':'vite-dev',app_url:APP_URL,git_sha:gitSha(),node:process.version,note:'Offline: MOCK_LLM=1, no provider keys, synthetic data. Not evidence of live AI or voice behaviour.'},checks:[],a11y:{},errors:[]};
function gitSha(){try{return require('node:child_process').execSync('git rev-parse --short HEAD',{cwd:root}).toString().trim();}catch{return 'unknown';}}
async function check(name,fn){await fn();results.checks.push(name);console.log('PASS',name);}
async function a11y(page,name){await page.addScriptTag({path:axePath});const r=await page.evaluate(()=>axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));results.a11y[name]=r.violations.map(v=>({id:v.id,impact:v.impact,description:v.description,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary})).slice(0,12)}));console.log('A11Y',name,JSON.stringify(results.a11y[name]));}
async function voiceStates(){
 const ctx=await browser.newContext({viewport:{width:1280,height:900}});const p=await ctx.newPage();p.on('pageerror',e=>results.errors.push(e.message));
 const real=await (await fetch('http://127.0.0.1:8000/health')).json();
 const withVoice=(verified)=>({...real,providers:{...real.providers,voice:'deepgram',verified:{groq:null,deepgram:verified}}});
 const stub=async(body,status=200)=>{await p.unroute('**/health');await p.route('**/health',r=>body===null?r.abort():r.fulfill({status,contentType:'application/json',body:JSON.stringify(body)}));};
 const speak=()=>p.getByRole('radio',{name:'Speak',exact:true});
 const stateIs=(k)=>p.waitForFunction(k=>document.querySelector('#voice-status')?.dataset.voiceState===k,k,{timeout:10000});
 await stub(null);await p.goto(APP_URL+'/#setup');
 await check('Voice state: unreachable backend is explained, Speak disabled, Check again recovers',async()=>{await stateIs('unreachable');assert(!(await speak().isEnabled()));await p.locator('#voice-status').getByText('can’t reach the interview service',{exact:false}).waitFor();await p.unroute('**/health');await p.locator('#voice-status').getByRole('button',{name:'Check again'}).click();await stateIs('not_configured');});
 await check('Voice state: missing speech configuration is explained with Type selected; operator note only in development',async()=>{assert.equal(await p.getByRole('radio',{name:'Type',exact:true}).getAttribute('aria-checked'),'true');assert(await p.locator('#voice-status').getByText('Voice isn’t available on this server',{exact:false}).isVisible());assert.equal(await p.getByTestId('voice-dev-note').count(),production?0:1);if(!production)assert.match(await p.getByTestId('voice-dev-note').innerText(),/DEEPGRAM_API_KEY/);assert(!(await speak().isEnabled()));assert.equal(await speak().getAttribute('aria-describedby'),'voice-status');});
 await stub({error:'down'},503);await p.reload();
 await check('Voice state: an HTTP 503 from /health is treated as unreachable',async()=>{await stateIs('unreachable');assert(!(await speak().isEnabled()));});
 await stub(withVoice(false));await p.reload();
 await check('Voice state: a failed provider verification disables Speak with a typing recovery',async()=>{await stateIs('provider_failed');assert(!(await speak().isEnabled()));await p.locator('#voice-status').getByText('Choose Type to continue',{exact:false}).waitFor();});
 await stub(withVoice(null));await p.reload();
 await check('Voice state: configured speech enables Speak (configuration, not proof) and Speak can be chosen',async()=>{await stateIs('available');assert(await speak().isEnabled());await speak().click();assert.equal(await speak().getAttribute('aria-checked'),'true');await p.getByText('You will need microphone access',{exact:false}).first().waitFor();});
 await p.screenshot({path:out+'/setup-voice-available.png'});
 await ctx.close();
 const denied=await browser.newContext({viewport:{width:1280,height:900}});
 await denied.addInitScript(()=>{const q=navigator.permissions&&navigator.permissions.query.bind(navigator.permissions);if(navigator.permissions)navigator.permissions.query=async(d)=>d&&d.name==='microphone'?{state:'denied',addEventListener(){},removeEventListener(){}}:q(d);});
 const dp=await denied.newPage();dp.on('pageerror',e=>results.errors.push(e.message));
 await dp.route('**/health',r=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify(withVoice(null))}));await dp.goto(APP_URL+'/#setup');
 await check('Voice state: browser microphone denial disables Speak and says how to recover',async()=>{await dp.waitForFunction(()=>document.querySelector('#voice-status')?.dataset.voiceState==='mic_blocked');assert(!(await dp.getByRole('radio',{name:'Speak',exact:true}).isEnabled()));await dp.getByText('Microphone access is blocked for this site',{exact:false}).waitFor();});
 await dp.screenshot({path:out+'/setup-voice-mic-blocked.png'});await a11y(dp,'setup-voice-blocked');
 await denied.close();
}
(async()=>{try{
 for(const url of [APP_URL+'/', 'http://127.0.0.1:8000/health']){let ready=false;for(let i=0;i<80;i++){try{if((await fetch(url)).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,250));}assert(ready,url);}
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||undefined,args:process.env.CHROMIUM_PATH ?['--no-sandbox','--disable-dev-shm-usage','--no-zygote','--single-process','--in-process-gpu','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'] : [],headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',e=>results.errors.push(e.message));
 await page.goto(APP_URL);
 await check('Homepage loads and identifies product',async()=>{await page.getByRole('heading',{name:/You’ve done/}).waitFor();assert.match(await page.title(),/Shadowtrace/);});
 // Let the ~1.2 s hero entrance finish so screenshots and contrast checks see settled content.
 await page.waitForTimeout(1500);
 await page.screenshot({path:out+'/homepage-desktop.png',fullPage:true});await page.screenshot({path:out+'/homepage-hero.png'});await a11y(page,'home-desktop');
 await check('Round preview works by mouse and keyboard',async()=>{await page.getByRole('button',{name:/Experience becomes a question/}).click();await page.waitForFunction(()=>document.querySelector('.story')?.dataset.stage==='2');await page.waitForFunction(()=>document.querySelector('[data-card=interview]')&&!document.querySelector('[data-card=interview]').inert);await page.waitForTimeout(900);await page.getByRole('tab',{name:'HR',exact:true}).click();await page.getByText('What was your part in the launch',{exact:false}).waitFor();await page.getByRole('tab',{name:'HR',exact:true}).press('ArrowRight');await page.getByText('What did you personally own, and how did you check the result?',{exact:false}).first().waitFor();await page.evaluate(()=>scrollTo(0,0));});
 await check('FAQ opens without navigating away',async()=>{await page.getByText('Do I need a GitHub repository?',{exact:true}).click();await page.getByText('No. Start with a resume',{exact:false}).waitFor({state:'visible',timeout:2000});});
 for(const width of [390,320,768]){await page.setViewportSize({width,height:844});await page.goto(APP_URL);await check(`Homepage has no horizontal overflow at ${width}px`,async()=>{const overflow=await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(e=>{const r=e.getBoundingClientRect();return r.right>innerWidth+1||r.left< -1;}).map(e=>({tag:e.tagName,class:e.className,width:e.getBoundingClientRect().width,left:e.getBoundingClientRect().left,right:e.getBoundingClientRect().right})).slice(0,10));if(overflow.length)console.log('OVERFLOW',width,JSON.stringify(overflow));assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`Overflow at ${width}`);});if(width===390){await page.waitForTimeout(1500);await page.screenshot({path:out+'/homepage-mobile.png',fullPage:true});await a11y(page,'home-mobile');}}
 await page.setViewportSize({width:1440,height:1000});await page.getByRole('button',{name:'Prepare my interview',exact:true}).click();
 await page.getByText('Development mode.',{exact:true}).waitFor();
 await check('Setup labels are associated with their controls',async()=>{assert.equal(await page.getByLabel('Target role',{exact:true}).count(),1);assert.equal(await page.getByLabel('Or describe your background',{exact:true}).count(),1);});
 await check('Unavailable voice cannot be chosen with arrow keys',async()=>{const type=page.getByRole('radio',{name:'Type',exact:true});await type.press('ArrowLeft');assert.equal(await type.getAttribute('aria-checked'),'true');assert(!(await page.getByRole('radio',{name:'Speak',exact:true}).isEnabled()));});
 await page.screenshot({path:out+'/setup-desktop.png',fullPage:true});await a11y(page,'setup');
 await voiceStates();
 await check('Setup validates missing target role',async()=>{await page.getByRole('button',{name:'Prepare my interview',exact:true}).click();await page.getByRole('alert').getByText('Add the role you are preparing for.').waitFor();});
 await page.getByLabel('Target role',{exact:true}).fill('Backend engineer');
 await page.getByLabel('Or describe your background',{exact:true}).fill('I built a Python reconciliation service for payment settlements. I designed the PostgreSQL schema, wrote retry logic and worked with the support team on the launch.');
 await page.getByLabel('Seniority',{exact:true}).selectOption('junior');await page.getByRole('radio',{name:'Coach',exact:true}).click();
 await check('Setup requires processing consent',async()=>{await page.getByRole('button',{name:'Prepare my interview',exact:true}).click();await page.getByRole('alert').getByText('Please read and confirm how your documents and speech are processed.').waitFor();});
 await page.getByRole('checkbox').check();
 await check('Live setup summary follows entered role',async()=>{await page.getByRole('complementary',{name:'Your interview summary'}).getByRole('heading',{name:'Backend engineer'}).waitFor();});
 await page.setViewportSize({width:390,height:844});await check('Setup has no horizontal overflow at 390px',async()=>assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)));await page.screenshot({path:out+'/setup-mobile.png',fullPage:true});await page.setViewportSize({width:1440,height:1000});
 await page.getByRole('button',{name:'Prepare my interview',exact:true}).click();
 await check('Real mock backend processes intake and opens source review',async()=>{await page.getByRole('heading',{name:'Your experience, in focus.'}).waitFor({timeout:20000});await page.getByRole('heading',{name:'Check what the interviewers will ask about'}).waitFor();});
 await page.screenshot({path:out+'/source-review.png',fullPage:true});await a11y(page,'review');
 await page.getByRole('button',{name:'Save and start the interview'}).click();
 await check('Interview opens over the real WebSocket',async()=>{await page.getByRole('heading',{name:'Interview in progress'}).waitFor();await page.getByLabel('Your answer',{exact:true}).waitFor();await page.waitForFunction(()=>document.querySelector('.question-text')?.textContent && !document.querySelector('.question-text')?.textContent.includes('Waiting'));});
 await page.screenshot({path:out+'/interview-room.png',fullPage:true});await a11y(page,'room');
 for(let i=0;i<40;i++){
  if(await page.getByRole('button',{name:'See your feedback',exact:true}).isVisible())break;
  const answer=page.getByLabel('Your answer',{exact:true});if(!(await answer.isEnabled()))break;
  const count=await page.locator('.transcript-line[data-speaker="you"]').count();
  await answer.fill(`I owned the Python reconciliation service. I chose PostgreSQL for transactions, added idempotency keys to prevent duplicate settlements, and checked launch ${i+1} with support.`);
  await page.getByRole('button',{name:'Send answer',exact:true}).click();
  await page.waitForFunction(c=>document.querySelectorAll('.transcript-line[data-speaker="you"]').length>c,count,{timeout:10000});
  await page.waitForTimeout(160);
 }
 console.log('ANSWER_COUNT',await page.locator('.transcript-line[data-speaker="you"]').count());
 await check('Interview reaches natural completion (all rounds) without an explicit end',async()=>{await page.getByRole('button',{name:'See your feedback',exact:true}).waitFor({timeout:15000});await page.getByText('The interview has finished.',{exact:false}).waitFor();assert.equal(await page.locator('.session-warning').count(),0);});
 await page.getByRole('button',{name:'See your feedback',exact:true}).click();
 await check('Feedback route opens and displays labelled placeholder report',async()=>{await page.getByRole('heading',{name:'Your answers, in perspective.'}).waitFor();await page.getByText('this report was produced by a placeholder.',{exact:false}).waitFor({timeout:20000});assert.match(page.url(),/#results\//);});
 await page.screenshot({path:out+'/feedback.png',fullPage:true});await a11y(page,'feedback');
 const originalReport=page.url();
 await page.setViewportSize({width:390,height:844});await check('Feedback has no horizontal overflow at 390px',async()=>assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)));await page.screenshot({path:out+'/feedback-mobile.png',fullPage:true});await page.setViewportSize({width:1440,height:1000});
 await page.getByRole('button',{name:'This feedback seems wrong',exact:true}).first().click();
 await page.getByLabel('What is wrong?',{exact:false}).fill('I described my individual contribution in the next answer.');
 await page.getByRole('button',{name:'Dispute this finding',exact:true}).click();
 await check('Dispute is stored and shown against the finding',async()=>{await page.getByText('You disputed this',{exact:true}).first().waitFor();});
 await page.getByRole('button',{name:'Withdraw dispute',exact:true}).click();await page.getByText('Dispute withdrawn',{exact:true}).first().waitFor();
 await page.getByRole('button',{name:'Practise this gap',exact:true}).first().click();await page.getByRole('button',{name:'Set up practice',exact:true}).click();
 await check('Targeted practice opens from stored feedback',async()=>{await page.getByRole('heading',{name:/Practise:/}).waitFor();assert.match(page.url(),/#practice\//);assert(!(await page.getByRole('radio',{name:'Speak',exact:true}).isEnabled()));});
 const practiceUrl=page.url();await page.getByRole('button',{name:'Show the checklist',exact:true}).click();await page.getByText('Opening it labels your next attempt',{exact:false}).waitFor();await page.waitForTimeout(5500);
 await page.screenshot({path:out+'/practice.png',fullPage:true});await a11y(page,'practice');
 await page.reload();await check('Targeted practice and coaching survive a reload',async()=>{await page.getByRole('heading',{name:/Practise:/}).waitFor();await page.getByRole('button',{name:'Start coached attempt',exact:true}).waitFor();});
 await page.getByRole('checkbox').check();await page.getByRole('button',{name:'Start coached attempt',exact:true}).click();await page.getByLabel('Your answer',{exact:true}).waitFor();await page.waitForFunction(()=>document.querySelector('.question-text')?.textContent&&!document.querySelector('.question-text')?.textContent.includes('Waiting'));
 await check('Idle warning from the server watchdog appears and “I’m still here” clears it',async()=>{const w=page.locator('.session-warning');await w.waitFor({timeout:12000});assert.match(await w.innerText(),/Are you still there\?/);await page.screenshot({path:out+'/room-idle-warning.png'});await page.getByRole('button',{name:'I’m still here',exact:true}).click();await w.waitFor({state:'detached',timeout:3000});});
 await page.getByLabel('Your answer',{exact:true}).fill('I owned the Python reconciliation service. I chose PostgreSQL for transactional consistency, added idempotency keys, and checked settlement mismatches with the support team before launch.');await page.getByRole('button',{name:'Send answer',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('.transcript-line[data-speaker="you"]').length>0);await page.getByRole('button',{name:'End interview',exact:true}).click();await page.getByText('this report was produced by a placeholder.',{exact:false}).waitFor({timeout:20000});await page.goto(practiceUrl);
 await check('Practice attempt reaches before-and-after comparison',async()=>{await page.getByRole('heading',{name:'Before and after',exact:true}).waitFor();});
 await page.goto(originalReport);await page.getByText('this report was produced by a placeholder.',{exact:false}).waitFor();
 
 await page.getByRole('tab',{name:'History',exact:true}).click();await check('Completed interview appears in history',async()=>{await page.getByRole('cell').filter({hasText:'Backend engineer'}).first().waitFor();});
 await page.reload();await check('History/report route survives a reload',async()=>{await page.getByRole('heading',{name:'Your answers, in perspective.'}).waitFor();});
 // Reload defaults to report. Authenticated downloads must retrieve stored data.
 const downloadButton=page.getByRole('button',{name:'Transcript',exact:true});await downloadButton.waitFor({timeout:15000});await check('Authenticated transcript download works',async()=>{const pending=page.waitForEvent('download');await downloadButton.click();const dl=await pending;assert((await dl.path()));});
 const scorecardButton=page.getByRole('button',{name:'Scorecard',exact:true});await scorecardButton.waitFor({timeout:15000});await check('Authenticated scorecard download works',async()=>{const pending=page.waitForEvent('download');await scorecardButton.click();const dl=await pending;const file=await dl.path();assert(file&&fs.readFileSync(file,'utf8').includes('<'));});
 const oldToken=await page.evaluate(()=>localStorage.getItem('shadowtrace.guest_token'));
 await check('Delete my data removes the guest, reports a manifest and the old key stops working',async()=>{await page.getByRole('button',{name:'Delete my data',exact:true}).click();await page.getByRole('button',{name:'Yes, delete',exact:true}).click();await page.getByText('Your data was deleted',{exact:true}).waitFor();await page.waitForFunction(()=>!localStorage.getItem('shadowtrace.guest_token'));const status=await page.evaluate(async t=>(await fetch('http://127.0.0.1:8000/api/me',{headers:{Authorization:'Bearer '+t}})).status,oldToken);assert.equal(status,401);await page.waitForLoadState('load');});
 await page.waitForTimeout(1800);
 const fresh=page;await fresh.evaluate(()=>localStorage.clear());let created=0;fresh.on('request',r=>{if(r.url().endsWith('/api/guest')&&r.method()==='POST')created++;});await fresh.goto(APP_URL+'/#history');await fresh.reload();await fresh.getByText('No interviews yet.',{exact:false}).waitFor();
 await check('Concurrent history panels create only one guest',async()=>assert.equal(created,1));
 const token=await fresh.evaluate(()=>localStorage.getItem('shadowtrace.guest_token'));assert(token);await fresh.route('**/api/me',r=>r.fulfill({status:503,contentType:'application/json',body:'{"error":"Temporary test outage"}'}));await fresh.reload();await fresh.getByText('Temporary test outage',{exact:true}).first().waitFor();await check('Temporary API failure preserves guest identity',async()=>assert.equal(await fresh.evaluate(()=>localStorage.getItem('shadowtrace.guest_token')),token));
 for(const [name,violations] of Object.entries(results.a11y))assert.equal(violations.length,0,'Accessibility: '+name);assert.deepEqual(results.errors,[]);console.log('PAGE_ERRORS',JSON.stringify(results.errors));
 console.log('ALL SMOKE CHECKS PASSED:',results.checks.length);results.run.passed=true;
}finally{results.run.finished_at=new Date().toISOString();results.run.checks_passed=results.checks.length;fs.writeFileSync(out+'/results.json',JSON.stringify(results,null,2));if(browser)await browser.close();backend.kill();vite?.kill();}})().catch(e=>{console.error(e);process.exitCode=1});
