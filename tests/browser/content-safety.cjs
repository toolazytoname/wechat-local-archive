/* Synthetic actual-browser content and standalone-file acceptance. No real data. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn,spawnSync}=require('node:child_process');
const {once}=require('node:events');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.WLA_PLAYWRIGHT_MODULE||'playwright');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'wla-content-browser-'));
const checkout=path.resolve(__dirname,'../..');
const python=process.env.WLA_PYTHON||path.join(checkout,'.venv/bin/python');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let browser,server;
(async()=>{
  const fixtureResult=spawnSync(python,['-m','tests.browser.make_safety_fixture','--root',root],{cwd:checkout,encoding:'utf8'});
  if(fixtureResult.status!==0)throw Error(fixtureResult.stderr);
  const fixture=JSON.parse(fs.readFileSync(path.join(root,'fixture.json')));
  const reserve=net.createServer();reserve.listen(0,'127.0.0.1');await once(reserve,'listening');
  const port=reserve.address().port;await new Promise(resolve=>reserve.close(resolve));
  const base=`http://127.0.0.1:${port}`;
  const log=fs.openSync(path.join(root,'server.log'),'w',0o600);
  server=spawn(python,['-m','wechat_export','launch','--export-dir',fixture.archive,'--port',String(port)],
    {cwd:checkout,env:{...process.env,WECHAT_EXPORT_DATA_ROOT:path.join(root,'runtime')},stdio:['ignore',log,log]});
  fs.closeSync(log);
  let ready=false;
  for(let i=0;i<100;i++){
    if(server.exitCode!==null)throw Error('owned server exited');
    try{if((await fetch(base+'/api/bootstrap')).ok){ready=true;break;}}catch(_){}
    await delay(100);
  }
  assert(ready);
  browser=await chromium.launch({headless:true});
  const context=await browser.newContext({viewport:{width:1280,height:900}});
  const external=[],dialogs=[],errors=[];
  await context.route('**/*',route=>{
    const url=route.request().url();
    if(url.startsWith('http:')||url.startsWith('https:')){
      if(new URL(url).origin!==base){external.push(url);return route.abort();}
    }
    return route.continue();
  });
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',async d=>{dialogs.push(d.type());await d.dismiss();});
  await page.goto(base+'/?c=wxid_alice');await page.locator('#chat-title').filter({hasText:'Alice'}).waitFor();
  await page.locator('[data-uid="voice"]').filter({hasText:'语音'}).waitFor();
  await page.locator('[data-uid="video"]').filter({hasText:'视频未取得'}).waitFor();
  const text=await page.locator('#thread').textContent();
  assert(!text.includes('SYNTHETIC_CREDENTIAL'));
  assert(!text.includes('<sysmsg')&&!text.includes('<unknown_payload')&&!text.includes('<!DOCTYPE msg'));
  assert(text.includes('Alice:\nplease keep this line'));
  assert(text.includes('Quoted safe content')&&text.includes('Forwarded safe content'));
  assert.equal(await page.evaluate(()=>globalThis.wlaAttack),undefined);
  assert.equal(await page.locator('#thread script, #thread iframe, #thread form').count(),0);
  // Explicit navigation only: declining opens nothing; accepting opens one
  // isolated tab. The existing route aborts the synthetic remote trap before I/O.
  const openLink = page.locator('[data-uid="link"] .open-original-link');
  await openLink.click(); // default dialog handler dismisses
  assert.deepEqual(external, []);
  assert.deepEqual(dialogs, ['confirm']);
  dialogs.length = 0;
  page.removeAllListeners('dialog');
  page.once('dialog', async dialog => { assert.equal(dialog.type(), 'confirm'); await dialog.accept(); });
  const popupPromise = context.waitForEvent('page');
  const requestPromise = context.waitForEvent('request', request => request.url().startsWith('https://must-not-fetch.invalid/'));
  await openLink.click();
  const popup = await popupPromise;
  await requestPromise;
  // Wait for the route callback, not a guessed navigation timeout.
  for (let i = 0; i < 100 && external.length === 0; i++) await delay(10);
  assert.equal(external.length, 1);
  assert.equal(await popup.evaluate(() => window.opener === null), true);
  await popup.close();
  external.length = 0;
  page.on('dialog', async dialog => {dialogs.push(dialog.type()); await dialog.dismiss();});
  // Delay an actual old-conversation search response, then switch conversations.
  let release,arrived;
  const gate=new Promise(resolve=>{release=resolve;});
  const arrival=new Promise(resolve=>{arrived=resolve;});
  await page.route('**/api/search?*',async route=>{
    const response=await route.fetch();arrived();await gate;await route.fulfill({response});
  },{times:1});
  await page.locator('#msg-q').fill('needle');
  await Promise.race([arrival,delay(10000).then(()=>{throw Error('search request did not arrive');})]);
  await page.locator('#rail button').filter({hasText:'Studio'}).click();
  await page.locator('#chat-title').filter({hasText:'Studio'}).waitFor();
  const responded=page.waitForResponse(r=>r.url().includes('/api/search?'));
  release();await (await responded).finished();
  await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  assert(await page.locator('#search-hits').isHidden());
  assert((await page.locator('#thread').textContent()).includes('needle-beta-room'));
  assert(!(await page.locator('#thread').textContent()).includes('needle-alpha-private'));
  assert.deepEqual(external,[]);assert.deepEqual(dialogs,[]);assert.deepEqual(errors,[]);
  await context.close();
  // Prove offline files work after the originating service has actually stopped.
  server.kill('SIGTERM');await once(server,'exit');server=null;
  const offline=await browser.newContext({offline:true});
  const offlineRequests=[];
  offline.on('request',request=>{if(/^https?:/.test(request.url()))offlineRequests.push(request.url());});
  for(const filename of [fixture.outputs.html,fixture.outputs.standalone]){
    for(const width of [1280,390]){
      const p=await offline.newPage();await p.setViewportSize({width,height:740});
      p.on('dialog',async d=>{dialogs.push(d.type());await d.dismiss();});
      p.on('pageerror',e=>errors.push(e.message));
      await p.goto(pathToFileURL(filename).href);await p.locator('.row').first().waitFor();
      assert.equal(await p.locator('.row').count(),fixture.record_count);
      const contents=await p.locator('body').textContent();
      assert(!contents.includes('SYNTHETIC_CREDENTIAL'));
      assert(contents.includes('please keep this line'));
      assert.equal(await p.locator('script,iframe,object,embed,form,img,audio,video').count(),0);
      const anchors = p.locator('a[href]');
      assert.equal(await anchors.count(), 1);
      assert.equal(await anchors.first().getAttribute('href'), 'https://must-not-fetch.invalid/resource');
      assert.equal(await anchors.first().getAttribute('rel'), 'noopener noreferrer');
      assert.equal(await anchors.first().getAttribute('referrerpolicy'), 'no-referrer');
      assert.equal(await p.evaluate(()=>globalThis.wlaAttack),undefined);
      assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      const csp=await p.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content');
      assert(csp.includes("default-src 'none'"));
      await p.screenshot({path:path.join(root,`offline-${path.basename(filename)}-${width}.png`)});
      await p.close();
    }
  }
  assert.deepEqual(offlineRequests,[]);assert.deepEqual(dialogs,[]);assert.deepEqual(errors,[]);
  await offline.close();
  console.log(JSON.stringify({synthetic:true,records:fixture.record_count,live_external_requests:external.length,
    offline_http_requests:offlineRequests.length,script_execution:false,stale_search_ignored:true,manual_link_navigation_verified:true,
    offline_files:2,offline_viewports:2,server_stopped_before_offline:true,evidence_directory:root}));
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();
  if(server&&server.exitCode===null){server.kill('SIGTERM');await once(server,'exit');}
});
