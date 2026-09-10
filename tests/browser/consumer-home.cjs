/* Real local browser acceptance; fixture data only, no account inspection. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net');
const {spawn}=require('node:child_process'),{once}=require('node:events');
const {chromium}=require(process.env.WLA_PLAYWRIGHT_MODULE||'playwright');
const checkout=path.resolve(__dirname,'../..'),root=fs.mkdtempSync(path.join(os.tmpdir(),'wla-consumer-home-'));
const delay=ms=>new Promise(r=>setTimeout(r,ms));let server,browser;
(async()=>{
 const runtime=path.join(root,'runtime');
 for(const name of ['20260909T0438-livedb','20260909T0802-livedb']){
  const dst=path.join(runtime,'exports',name);fs.mkdirSync(path.dirname(dst),{recursive:true});
  fs.cpSync(path.join(checkout,'examples/demo-export'),dst,{recursive:true,filter:p=>!['archive.sqlite','slices'].includes(path.basename(p))});
 }
 const sock=net.createServer();sock.listen(0,'127.0.0.1');await once(sock,'listening');const port=sock.address().port;await new Promise(r=>sock.close(r));
 const base=`http://127.0.0.1:${port}`;
 const log=fs.openSync(path.join(root,'server.log'),'w',0o600);
 server=spawn(path.join(checkout,'.venv/bin/python'),['-m','wechat_export','launch','--port',String(port)],{cwd:checkout,env:{...process.env,WECHAT_EXPORT_DATA_ROOT:runtime},stdio:['ignore',log,log]});fs.closeSync(log);
 let ready=false;for(let i=0;i<100;i++){if(server.exitCode!==null)throw Error('owned server exited');try{if((await fetch(base+'/')).ok){ready=true;break;}}catch(_){}await delay(100);}assert(ready);
 browser=await chromium.launch({headless:true});const context=await browser.newContext();const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const reply=(route,data)=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
 await page.route('**/api/setup/environment',route=>reply(route,{wechat_version:'4.1.13',wechat_build:'unverified-demo',tools:{},compatibility:{stages:{},adapter:{live_operations_eligible:false}}}));
 await page.route('**/api/setup/accounts',route=>reply(route,{accounts:[{account_id:'synthetic',dir_name:'synthetic_account_with_a_very_long_name_'+('x'.repeat(130)),has_db_storage:true,db_file_count:35}],status:'ok'}));
 await page.route('**/api/setup/materials',route=>reply(route,{materials:[]}));
 for(const [width,height] of [[1280,960],[390,844],[320,700]]){
  await page.setViewportSize({width,height});await page.goto(base);
  await page.locator('#home-open:not([disabled])').waitFor();
  assert(await page.locator('#setup-read-panel').isHidden());
  assert.equal(await page.locator('.archive-choice').count(),2);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  for(const card of await page.locator('.archive-choice').all()){
   const title=await card.locator('strong').boundingBox(),meta=await card.locator('.archive-choice-text > span').boundingBox();
   assert(title.y+title.height<=meta.y+1,'title overlaps metadata');
  }
  await page.screenshot({path:path.join(root,`home-${width}.png`),fullPage:true});
  await page.locator('#setup-read').click();await page.locator('.account-choice').waitFor();
  const account=page.locator('.account-choice');
  const a=await account.locator('strong').boundingBox(),b=await account.locator('span').boundingBox();
  assert(a.y+a.height<=b.y+1,'account label overlap');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.equal(await page.locator('#setup-technical').getAttribute('open'),null);
  await page.screenshot({path:path.join(root,`read-${width}.png`),fullPage:true});
  await page.locator('#setup-hide-read').click();assert(await page.locator('#setup-read-panel').isHidden());
 }
 await page.setViewportSize({width:1280,height:960});
 await page.locator('#home-export').click();await page.locator('#drawer:not(.hidden)').waitFor();
 assert.equal(await page.locator('#ex-scope').inputValue(),'all');
 await page.locator('#ex-preview').click();await page.locator('#ex-count').filter({hasText:'12 条'}).waitFor();
 await page.locator('#ex-close').click();await page.locator('#back-setup').click();await page.locator('#home-open:not([disabled])').waitFor();
 await page.locator('#home-open').click();await page.locator('#thread .row').first().waitFor();
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({synthetic:true,viewports:3,home_actions:true,account_overlap:false,archive_overlap:false,technical_details_collapsed:true,evidence_directory:root}));
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill('SIGTERM');await once(server,'exit');}});
