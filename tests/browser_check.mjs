// Drives the CRM UI in headless Edge (DevTools protocol): clicks through Live Chat (stages, intervention, access), Contacts,
// Campaigns, collects JS errors, measures layout, takes screenshots. Needs one preview running (see run_browser_check.ps1):
//   tests/preview.py admin 8011      (the browser switches user by setting the session cookie to a username)
// Usage: node tests/browser_check.mjs <output folder>
import { spawn } from 'node:child_process';
import { writeFileSync, mkdirSync } from 'node:fs';

const out = process.argv[2]; mkdirSync(out, { recursive: true });
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const proc = spawn(EDGE, ['--headless=new', '--disable-gpu', '--remote-debugging-port=9340', `--user-data-dir=${out}/profile`, 'about:blank']);
let targets; for (let i = 0; i < 60 && !targets; i++) { await sleep(250); try { targets = await (await fetch('http://127.0.0.1:9340/json')).json(); } catch {} }
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r));
let id = 0; const pending = new Map(); const errors = [];
ws.addEventListener('message', e => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
  if (m.method === 'Page.javascriptDialogOpening') { cmd('Page.handleJavaScriptDialog', { accept: true, promptText: 'Test tag' }); return; }  // confirm()/prompt() would block forever
  if (m.method === 'Runtime.exceptionThrown') errors.push('exception: ' + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text).split('\n')[0]);
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push('console.error: ' + m.params.args.map(a => a.value ?? a.description).join(' ').slice(0, 200));
  if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error' && !/favicon|fonts\.g|403/.test((m.params.entry.url || '') + m.params.entry.text)) errors.push('log: ' + m.params.entry.text.slice(0, 160) + ' ' + (m.params.entry.url || ''));
});
const cmd = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = expr => Promise.race([
  cmd('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }).then(r => r.result?.result?.value),
  sleep(20000).then(() => { throw new Error('browser did not answer in 20s: ' + expr.slice(0, 140)); }),
]);
await cmd('Runtime.enable'); await cmd('Log.enable'); await cmd('Page.enable'); await cmd('Network.enable');

let fails = 0;
const check = (cond, label) => { console.log((cond ? 'PASS ' : 'FAIL ') + label); if (!cond) fails++; };
async function viewport(w, h, mobile) { await cmd('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile }); }
const A = 'http://127.0.0.1:8011/';   // not 'localhost': it is ~2s slower per request on this machine
async function asUser(name) { await cmd('Network.setCookie', { name: 'btree_session', value: name, url: A }); }
async function go(url) { await cmd('Page.navigate', { url: 'about:blank' }); await sleep(250); await cmd('Page.navigate', { url }); await sleep(2800); }
const click = sel => ev(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});if(!e)return false;e.click();return true})()`);
const clickText = (sel, text) => ev(`(()=>{const e=[...document.querySelectorAll(${JSON.stringify(sel)})].find(x=>x.textContent.includes(${JSON.stringify(text)}));if(!e)return false;e.click();return true})()`);
const text = sel => ev(`(document.querySelector(${JSON.stringify(sel)})||{}).textContent||''`);
const count = sel => ev(`document.querySelectorAll(${JSON.stringify(sel)}).length`);
const overflow = () => ev('document.documentElement.scrollWidth - innerWidth');
const inView = sel => ev(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});if(!e)return false;const r=e.getBoundingClientRect();return r.height>8&&r.top>=-1&&r.bottom<=innerHeight+1&&r.left>=-1&&r.right<=innerWidth+1})()`);
const rectOf = sel => ev(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}})()`);
const disp = sel => ev(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});return e?getComputedStyle(e).display:'missing'})()`);
const stageTab = s => clickText('#lcstages button', s);
const stageCount = async s => +(await ev(`(()=>{const b=[...document.querySelectorAll('#lcstages button')].find(x=>x.textContent.includes(${JSON.stringify(s)}));return b?b.querySelector('b').textContent:'-1'})()`));
async function shot(name, full = false) {
  const r = await cmd('Page.captureScreenshot', { format: 'png', captureBeyondViewport: full });
  writeFileSync(`${out}/${name}.png`, Buffer.from(r.result.data, 'base64'));
}
const mark = () => errors.length;
const noNewErrors = (from, label) => check(errors.length === from, `${label}: no JavaScript errors${errors.length > from ? ' -> ' + errors.slice(from).join(' | ') : ''}`);

// ================= DESKTOP: admin =================
await viewport(1440, 900, false);
await asUser('admin');
let m0 = mark();
await go(A + '#Inbox');
check(await count('#lcstages button') === 4 && (await text('#lcstages')).replace(/\d/g, '').replace(/\s/g, '') === 'ActiveRequestingIntervenedClosed', 'Live Chat: four stage tabs (Active, Requesting, Intervened, Closed) with counts');
check((await text('.lc-view')).includes('Select a chat to continue!'), 'Live Chat: "Select a chat to continue!" start screen');
check(await count('#lcitems .conv') >= 1, 'Active tab lists chats handled by the bot');
await shot('01-livechat-start');
noNewErrors(m0, 'Live Chat start');

// ---- Requesting -> Intervened ----
m0 = mark();
const reqBefore = await stageCount('Requesting');
await stageTab('Requesting'); await sleep(900);
check(reqBefore >= 2 && await count('#lcitems .conv .cmark.warn') >= 2, `Requesting tab: contacts waiting for a counsellor (${reqBefore}), each marked with its waiting time`);
await clickText('#lcitems .conv', 'Tami'); await sleep(1600);
check((await text('.chat-h .cstat')).includes('Requesting') && (await text('.chat-h .cstat')).includes('waiting'), 'header badge: "Requesting · waiting …"');
check((await text('.cv-foot')).includes('asked for a counsellor') && await count('.cv-foot [data-act=intervene]') === 1, 'footer: contact is waiting, with the Intervene button');
check((await text('.msgs')).includes('Moved to Requesting: the contact asked for a counsellor'), 'the move to Requesting is tracked in the chat');
await shot('02-requesting');
await click('.cv-foot [data-act=intervene]'); await sleep(2200);
check(await count('.cv-foot .composer') === 1 && (await text('.msgs')).includes('User Intervened by Administrator (waited'), 'Intervene: composer appears; event "User Intervened by Administrator (waited …)"');
check(await ev(`document.querySelector('#lcstages button.on').textContent.includes('Intervened')`), 'the list follows the chat to the Intervened tab');
check((await text('#lcitems')).includes('Intervened by me'), 'Intervened tab: "Intervened by me" group');
check(await stageCount('Requesting') === reqBefore - 1, 'Requesting count dropped by one');
await ev(`(()=>{const i=document.querySelector('.cv-foot .txt');i.value='Hello Tami, this is the admissions team.';document.querySelector('.cv-foot form').requestSubmit()})()`); await sleep(1600);
check((await text('.msgs')).includes('Hello Tami, this is the admissions team.'), 'a reply sent from the composer');
check(await inView('.cv-foot') && await inView('.chat-h'), 'Live Chat: header and footer inside the visible panel');
const bub = await ev(`(()=>{const m=[...document.querySelectorAll('.m.out')].find(x=>x.textContent.includes('Hello Tami'));return m?m.getBoundingClientRect().height:-1})()`);
check(bub > 0 && bub < 95, `a one-line bubble is compact (${Math.round(bub)}px)`);
await shot('03-intervened-by-me');
noNewErrors(m0, 'Requesting -> Intervened');

// ---- group "others", take over (admin sees everyone's chats) ----
m0 = mark();
check((await text('#lcitems')).includes('Intervened by others'), 'admin also sees "Intervened by others"');
await clickText('#lcitems .conv', 'Sudharsan'); await sleep(1600);
check((await text('.cv-foot')).includes('This chat is Intervened By Arun') && await count('.cv-foot [data-act=takeover]') === 1, 'admin: "This chat is Intervened By Arun" + Take Over');
check((await text('.cv-prof')).includes('Activity') && (await text('.cv-prof')).includes('Intervened by Arun'), 'profile has the Activity timeline with who intervened');
await shot('04-others-takeover');
noNewErrors(m0, 'Others group');

// ---- Resolve -> Closed ----
m0 = mark();
await clickText('#lcitems .conv', 'Tami'); await sleep(1500);
await clickText('.chat-h [data-act]', 'Resolve'); await sleep(2000);
check((await text('.cv-foot')).includes('This chat is closed') && await count('.cv-foot [data-act=reopen]') === 1, 'Resolve: the chat shows as closed with a Reopen button');
check(await ev(`document.querySelector('#lcstages button.on').textContent.includes('Closed')`) && (await text('#lcitems')).includes('Tami'), 'the list follows the chat to the Closed tab');
check((await text('.msgs')).includes('Chat closed (resolved) by Administrator'), 'closing is tracked in the chat');
await shot('05-closed');
noNewErrors(m0, 'Resolve');

// ================= DESKTOP: agent (user-level access) =================
m0 = mark();
await asUser('priya');
await go(A + '#Inbox');
await stageTab('Intervened'); await sleep(900);
check((await text('#lcitems')).includes('Intervened by me') && !(await text('#lcitems')).includes('Intervened by others'), 'agent: only the "Intervened by me" group is shown');
check(!(await text('#lcitems')).includes('Sudharsan'), "agent: another person's intervened chat is not listed");
check((await text('#lcitems')).includes('Meena'), 'agent: her own chat (Meena) is listed');
await stageTab('Requesting'); await sleep(800);
check(await count('#lcitems .conv') >= 1, 'agent: the Requesting queue is shared, so she can pick one up');
await clickText('#lcitems .conv', 'Karthik'); await sleep(1500);
await click('.cv-foot [data-act=intervene]'); await sleep(1800);
check(await count('.cv-foot .composer') === 1, 'agent: picks up a requesting chat and can reply');
await shot('06-agent-view');
// locked: open Arun's chat through the contacts drawer
await go(A + '#Contacts');
await ev(`(()=>{const r=[...document.querySelectorAll('#ctable .crow')].find(x=>x.textContent.includes('Sudharsan'));r.querySelector('td:nth-child(2)').click()})()`); await sleep(2200);
check((await text('.drawer')).includes('being handled by Arun'), 'agent: a chat handled by someone else shows "being handled by Arun" instead of its content');
await shot('07-agent-locked');
await click('.drawer [data-close]');
check(await count('#nav [data-t=Users]') === 0 && await count('#nav [data-t=Sessions]') === 0, 'agent menu hides Users and Sessions');
check(await ev(`![...document.querySelectorAll('#nav button')].some(b=>/AI Agent/.test(b.textContent)) && !/\\d{8,}/.test(document.getElementById('status').textContent)`), 'sidebar: no AI Agent item and no phone number in the status box');
noNewErrors(m0, 'Agent access');

// ================= DESKTOP: manager =================
m0 = mark();
await asUser('maya');
await go(A + '#Inbox');
await stageTab('Intervened'); await sleep(900);
check((await text('#lcitems')).includes('Intervened by others') && (await text('#lcitems')).includes('Sudharsan'), "manager: sees everyone's intervened chats");
await clickText('#lcitems .conv', 'Sudharsan'); await sleep(1500);
await click('.cv-foot [data-act=takeover]'); await sleep(1900);
check(await count('.cv-foot .composer') === 1 && (await text('.msgs')).includes('Maya took over from Arun'), 'manager takes over: event "Maya took over from Arun"');
noNewErrors(m0, 'Manager');

// ---- Contacts ----
await asUser('admin');
m0 = mark();
await go(A + '#Contacts');
check(await count('#ctable .crow') >= 8, 'Contacts: table rows');
await click('#ctable input[data-sel]'); await ev(`document.querySelectorAll('#ctable input[data-sel]')[1].click()`); await sleep(400);
check(!(await ev(`document.getElementById('bulkbar').hidden`)) && (await text('#bulkbar')).includes('2 selected'), 'Contacts: bulk bar ("2 selected")');
await shot('08-contacts-bulk');
await click('#bclear'); await sleep(300);
await click('#ctable .crow td:nth-child(2)'); await sleep(2200);
check(await count('.drawer .cv-chat') === 1 && await inView('.drawer .cv-foot'), 'Contacts: drawer opens with chat + profile and a visible footer');
check(await disp('.drawer .prof-toggle') === 'none' && (await rectOf('.drawer .cv-prof')).x < 1440, 'drawer is wide enough: profile beside the chat');
await shot('09-contact-drawer');
await click('.drawer [data-close]'); await sleep(300);
for (const [tab, name] of [['Segments', '10-segments'], ['Lists', '11-lists'], ['Tags', '12-tags']]) {
  await clickText('#subseg button', tab); await sleep(1300);
  check(await count('#sub table') >= 1, `${tab}: page loads`);
  await shot(name);
}
await clickText('#subseg button', 'Segments'); await sleep(1000);
await click('#newseg'); await sleep(700);
check(await ev(`!![...document.querySelectorAll('.modal select[data-k=field] option')].find(o=>o.textContent==='Chat stage')`), 'segment builder offers the "Chat stage" condition');
await ev(`document.querySelector('.modal [data-x]').click()`);
noNewErrors(m0, 'Contacts');

// ---- Campaigns ----
m0 = mark();
await go(A + '#Campaigns');
check(await count('#ctab .crow') >= 2, 'Campaigns: list');
await click('#ctab .crow'); await sleep(1800);
const tops = await ev(`[...document.querySelectorAll('.drawer .grid.six .stat')].map(e=>Math.round(e.getBoundingClientRect().top))`);
check(tops.length === 6 && new Set(tops).size === 1, 'Campaign detail: six stat tiles in one row');
await shot('13-campaign-detail');
await click('.drawer [data-close]'); await sleep(300);
// contacts from a file: country codes are checked and the errors are shown
await click('#newc'); await sleep(800);
check(await ev(`(()=>{const d=document.getElementById('ncdrop').getBoundingClientRect(),b=document.getElementById('ncbrowse').getBoundingClientRect();return d.height>=150&&d.width>300&&b.width>60&&b.height>20&&getComputedStyle(document.getElementById('ncbrowse')).visibility==='visible'})()`), 'campaign upload: large drop zone with a visible Browse button');
await ev(`document.querySelector('.upl').scrollIntoView({block:'center'})`); await sleep(300); await shot('13a-upload-empty');
await ev(`(()=>{const dt=new DataTransfer();dt.items.add(new File(['Name,Phone\\nAsha,+91 98765 43210\\nRavi,9876543211\\nMeena,919876543212\\nX,abc\\n'],'leads.csv',{type:'text/csv'}));document.getElementById('ncdrop').dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));})()`);   // drag and drop
await sleep(1500);
check(await ev(`document.querySelectorAll('.upl-sum .pill.ok').length===1 && /2 valid/.test(document.querySelector('.upl-sum .pill.ok').textContent)`), 'campaign upload: 2 valid numbers found');
check(await ev(`(()=>{const e=document.querySelector('.upl-err');return !!e && /country code/.test(e.textContent) && /abc/.test(e.textContent)})()`), 'campaign upload: errors listed (missing country code, letters)');
check(await ev(`document.getElementById('ncfilters').classList.contains('off') && /2 contacts from the file/.test(document.getElementById('ncaud').textContent)`), 'campaign upload: file replaces the other audience filters');
await ev(`document.querySelector('.upl').scrollIntoView({block:'center'})`); await sleep(300);
await shot('13b-campaign-upload');
await ev(`document.getElementById('ncrm').click()`); await sleep(400);
check(await ev(`!document.getElementById('ncfilters').classList.contains('off') && !document.querySelector('.upl-err')`), 'campaign upload: removing the file restores the audience filters');
await ev(`(()=>{const dt=new DataTransfer();dt.items.add(new File(['x'],'pic.png'));const i=document.getElementById('ncfile');i.files=dt.files;i.dispatchEvent(new Event('change'));})()`); await sleep(1000);
check(await ev(`/Upload a CSV/.test(document.querySelector('.upl-err')?.textContent||'')`), 'campaign upload: unsupported file type shows an error');
await ev(`document.querySelector('[data-x]')?.click()`); await sleep(300);
m0 = mark();   // the console logs the 422 we provoked on purpose above
noNewErrors(m0, 'Campaigns');

// ================= WIDE =================
await viewport(1920, 1000, false);
m0 = mark();
await go(A + '#Inbox');
await stageTab('Closed'); await sleep(800);
await clickText('#lcitems .conv', 'Tami'); await sleep(1500);
check(await disp('.chat-h .prof-toggle') === 'none' && (await rectOf('.cv-prof')).w > 250, 'wide screen: profile panel sits beside the chat');
await shot('14-wide');
noNewErrors(m0, 'Wide');

// ================= PHONE =================
await viewport(390, 844, true);
m0 = mark();
await go(A + '#Inbox');
check(await overflow() === 0 && await count('#lcstages button') === 4, 'phone: stage tabs fit, no sideways scroll');
await shot('20-phone-list');
await stageTab('Requesting'); await sleep(800);
await click('#lcitems .conv'); await sleep(1600);
check(await ev(`document.getElementById('lc').classList.contains('detail')`) && await overflow() === 0 && await inView('.cv-foot'), 'phone: tapping a chat opens it full screen with its footer visible');
await shot('21-phone-chat');
await click('.chat-h .prof-toggle'); await sleep(500);
check(await ev(`document.querySelector('.cv').classList.contains('prof-open')`), 'phone: Profile opens the profile panel');
await shot('22-phone-profile');
noNewErrors(m0, 'Phone Live Chat');
for (const [tab, name] of [['Contacts', '23-phone-contacts'], ['Campaigns', '24-phone-campaigns']]) {
  m0 = mark();
  await go(A + '#' + tab);
  check(await overflow() === 0, `phone ${tab}: no sideways scroll`);
  await shot(name, true);
  noNewErrors(m0, 'Phone ' + tab);
}
ws.close(); proc.kill();
console.log(`\n${fails ? fails + ' FAILED' : 'all browser checks passed'}`);
process.exit(fails ? 1 : 0);
