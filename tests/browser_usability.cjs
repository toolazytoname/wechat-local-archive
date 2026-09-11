// Run with PLAYWRIGHT_MODULE=/absolute/path/to/playwright node tests/browser_usability.cjs
// The fixture and all model responses are synthetic. All external traffic is blocked.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process'),{once}=require('node:events');
let server,browser;const out=path.resolve(process.env.UX_OUTPUT || 'data/reports/usability-20260910');fs.mkdirSync(out,{recursive:true});
(async()=>{
 const socket=net.createServer();socket.listen(0,'127.0.0.1');await once(socket,'listening');const port=socket.address().port;await new Promise(r=>socket.close(r));
 const base=`http://127.0.0.1:${port}`,log=fs.openSync(path.join(out,'synthetic-server.log'),'w');
 server=spawn('.venv/bin/python',['-m','tests.usability_fixture',String(port)],{stdio:['ignore',log,log]});fs.closeSync(log);
 for(let i=0;i<150;i++){try{if((await fetch(base+'/api/bootstrap')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}});
 const errors=[],requests=[];let outside=0;
 await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==base){outside++;return route.abort();}return route.continue();});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')requests.push({url:r.url(),body:r.postData()});});
 await page.goto(base);await page.locator('#product:not(.hidden)').waitFor();
 await page.locator('[data-page="self"]').click();
 await page.getByRole('button',{name:'确认记录中的本人身份',exact:true}).click();
 const generate=page.getByRole('button',{name:'生成我的画像',exact:true});await generate.waitFor();
 await page.waitForFunction(()=>!document.querySelector('#panel-self .profile-start-actions button')?.disabled);
 assert.equal(await page.getByRole('button',{name:'生成本机原话',exact:true}).count(),0);
 assert.equal(await page.getByRole('button',{name:'查看资料范围',exact:true}).count(),0);
 await page.screenshot({path:path.join(out,'self.png'),fullPage:true});
 await page.locator('#panel-self .ai-options > summary').click();
 await page.getByRole('button',{name:'测试连接（仅发送虚构文字）',exact:true}).click();await page.getByText('连接成功，可以生成。测试没有使用你的聊天。').waitFor();
 await page.locator('#panel-self input[value="grok_cli"]').check();await generate.click();await page.getByRole('button',{name:'同意发送并开始生成'}).click();
 await page.locator('#panel-self [role=alert]').filter({hasText:'AI 登录或密钥已失效'}).waitFor();
 assert(!(await page.locator('#panel-self .action-feedback').innerText()).includes('private provider'));
 await page.locator('#panel-self input[value="byok"]').check();
 await page.locator('#panel-self .ai-options > summary').click();
 const runs=()=>requests.filter(r=>r.url.endsWith('/api/profiles/runs')).length;
 await generate.click();await page.getByRole('dialog').waitFor();assert.equal(runs(),1);
 await page.getByRole('button',{name:'暂不发送'}).click();assert.equal(runs(),1);
 await generate.click();await page.getByRole('button',{name:'同意发送并开始生成'}).click();
 await page.locator('#panel-self .action-feedback').filter({hasText:'画像已保存'}).waitFor({timeout:15000});assert.equal(runs(),2);
 await page.locator('#panel-self').getByRole('button',{name:'导出这份报告'}).click();await page.getByText('已导出 Markdown、JSON 和离线网页。').waitFor();
 await page.locator('[data-page="friend"]').click();await page.getByRole('button',{name:/Alice.*条记录/}).waitFor();
 await page.getByRole('searchbox',{name:'搜索好友'}).fill('Bob');assert.equal(await page.locator('.friend-list .person').count(),1);
 await page.getByRole('searchbox',{name:'搜索好友'}).fill('Alice');await page.getByRole('button',{name:/Alice.*条记录/}).click();
 await page.waitForFunction(()=>!document.querySelector('#panel-friend .profile-start-actions button')?.disabled);
 await page.screenshot({path:path.join(out,'friend.png'),fullPage:true});
 await page.getByRole('button',{name:'生成好友画像',exact:true}).click();await page.getByRole('dialog').waitFor();assert((await page.getByRole('dialog').innerText()).includes('Alice'));
 await page.getByRole('button',{name:'同意发送并开始生成'}).click();await page.locator('#panel-friend .action-feedback').filter({hasText:'画像已保存'}).waitFor({timeout:15000});
 const friendCall=requests.filter(r=>r.url.endsWith('/api/profiles/runs')).at(-1);assert.equal(JSON.parse(friendCall.body).scope.conversation_id,'wxid_alice');
 // Leave and return: the selected friend's saved report should reappear.
 await page.locator('[data-page="self"]').click();await page.locator('[data-page="friend"]').click();await page.getByRole('button',{name:/Alice.*条记录/}).click();await page.locator('#panel-friend').getByText('最近的画像').waitFor();
 await page.locator('[data-page="learning"]').click();await page.locator('#learning-status').waitFor({state:'attached'});
 await page.waitForFunction(()=>document.querySelector('[aria-label="选择收藏会话"]')?.options.length>1);
 assert(await page.getByRole('button',{name:'整理这个会话',exact:true}).isDisabled());
 await page.getByRole('combobox',{name:'选择收藏会话'}).selectOption('room@chatroom');await page.getByRole('button',{name:'整理这个会话',exact:true}).click();
 await page.locator('#learning-status').filter({hasText:'整理完成'}).waitFor();assert.equal(await page.locator('.library-list .article').count(),1);
 await page.getByRole('searchbox',{name:'搜索学习资料'}).fill('不存在的条目');await page.getByText('没有匹配的资料').waitFor();
 await page.getByRole('searchbox',{name:'搜索学习资料'}).fill('');await page.locator('.library-list .article').waitFor();
 await page.screenshot({path:path.join(out,'learning.png'),fullPage:true});
 // An export failure must be visible, restore the button, and allow a successful retry.
 await page.route('**/api/insights/exports',route=>route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'测试导出失败',code:'export_failed'})}),{times:1});
 await page.getByRole('button',{name:'导出学习资料',exact:true}).click();await page.getByRole('alert').filter({hasText:'测试导出失败'}).waitFor();
 await page.getByRole('button',{name:'导出学习资料',exact:true}).click();await page.getByRole('button',{name:'打开导出文件夹'}).waitFor();
 await page.locator('.library-list .article').click();await page.locator('#panel-reader').getByRole('button',{name:'打开原链接'}).waitFor();
 for(const width of [1024,390]){await page.setViewportSize({width,height:900});await page.locator('[data-page="friend"]').click();await page.getByRole('button',{name:/Alice.*条记录/}).click();await page.waitForTimeout(200);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(out,`friend-${width}.png`),fullPage:true});}
 assert.deepEqual(errors,[]);assert.equal(outside,0);
 fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({self_generated:true,synthetic_connection_test:true,provider_failure_actionable:true,approval_cancel_sends_nothing:true,friend_search_and_generation:true,friend_report_reopened:true,collection_selected_and_imported:true,search:true,export_failure_visible_and_retry:true,responsive_widths:[1440,1024,390],page_errors:errors,external_requests:outside},null,2));
 console.log('PASS: self, approval, friend selection, saved report, collection, export retry, responsive layout; no external traffic');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill();await once(server,'exit');}});
