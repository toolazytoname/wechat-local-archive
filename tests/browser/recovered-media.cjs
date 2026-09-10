/* Only generated synthetic attachments; no user archive is opened. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net');
const {spawn,spawnSync}=require('node:child_process'),{once}=require('node:events');
const {chromium}=require(process.env.WLA_PLAYWRIGHT_MODULE||'playwright');
const cwd=path.resolve(__dirname,'../..'),root=fs.mkdtempSync(path.join(os.tmpdir(),'wla-media-browser-')),python=path.join(cwd,'.venv/bin/python');
let server,browser;const delay=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const p=spawnSync(python,['-c','from pathlib import Path; from tests.test_recovered_media import fixture; from wechat_export.recovered_media import recover_archive; import sys; recover_archive(*fixture(Path(sys.argv[1])))',root],{cwd,encoding:'utf8'});assert.equal(p.status,0,p.stderr);
 const sock=net.createServer();sock.listen(0,'127.0.0.1');await once(sock,'listening');const port=sock.address().port;await new Promise(r=>sock.close(r));const base=`http://127.0.0.1:${port}`;
 const log=fs.openSync(path.join(root,'server.log'),'w',0o600);
 server=spawn(python,['-m','wechat_export','launch','--export-dir',path.join(root,'archive'),'--port',String(port)],{cwd,env:{...process.env,WECHAT_EXPORT_DATA_ROOT:path.join(root,'runtime')},stdio:['ignore',log,log]});fs.closeSync(log);
 let ready=false;for(let i=0;i<200;i++){try{if((await fetch(base+'/api/bootstrap')).ok){ready=true;break;}}catch{}await delay(100);}assert(ready);
 browser=await chromium.launch({headless:true});const context=await browser.newContext({acceptDownloads:true});const external=[];
 await context.route('**/*',r=>{if(new URL(r.request().url()).origin!==base){external.push(r.request().url());return r.abort();}return r.continue();});
 const page=await context.newPage();await page.goto(base+'/?c=wxid_alice');
 const image=page.locator('[data-uid="1"] img');await image.waitFor();await page.waitForFunction(()=>{const i=document.querySelector('[data-uid="1"] img');return i&&i.complete&&i.naturalWidth>0;});
 assert((await page.locator('[data-uid="1"]').textContent()).includes('预览'));
 const popup=context.waitForEvent('page');await page.locator('[data-uid="1"]').getByText('查看大图').click();const large=await popup;await large.waitForLoadState();await large.close();
 const download=page.waitForEvent('download');await page.locator('[data-uid="2"]').getByText('下载', {exact:false}).click();const d=await download;assert.equal(d.suggestedFilename(),'report.pdf');assert(fs.readFileSync(await d.path()).toString().startsWith('%PDF-'));
 assert.deepEqual(external,[]);console.log(JSON.stringify({image_decoded:true,preview_label:true,large_image:true,file_download:true,remote_requests:0}));
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill();await once(server,'exit');}});
