/* Opt-in real Chromium smoke against an owned, isolated synthetic-only server.
   WLA_PLAYWRIGHT_MODULE=/absolute/path/to/playwright node tests/browser/guided-ui.cjs
   No real WeChat, account discovery, native chooser, or external AI requests.
*/
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {spawn, spawnSync} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require(process.env.WLA_PLAYWRIGHT_MODULE || 'playwright');
const checkout = path.resolve(__dirname, '../..');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wla-browser-smoke-'));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let server, browser;
(async () => {
  // An alternate interpreter alone is insufficient: cwd may still import source.
  // Installed acceptance explicitly isolates Python and runs outside checkout.
  const installed = Boolean(process.env.WLA_INSTALLED_PYTHON);
  const python = process.env.WLA_INSTALLED_PYTHON || process.env.WLA_PYTHON || path.join(checkout, '.venv/bin/python');
  const pythonFlags = installed ? ['-I'] : [];
  const serverCwd = installed ? root : checkout;
  if (installed) {
    const probe = spawnSync(python, ['-I', '-c',
      'import pathlib,sys,wechat_export; p=pathlib.Path(wechat_export.__file__).resolve(); assert sys.flags.isolated; assert not p.is_relative_to(pathlib.Path(sys.argv[1]).resolve()), "Installed mode imported checkout"; print(p)',
      checkout], {cwd: root, encoding:'utf8'});
    assert.equal(probe.status, 0, 'installed package isolation failed: ' + probe.stderr);
    assert(probe.stdout.trim(), 'missing installed import evidence');
  }
  const reservation = net.createServer();
  reservation.listen(0, '127.0.0.1'); await once(reservation, 'listening');
  const port = reservation.address().port; await new Promise(resolve => reservation.close(resolve));
  const base = `http://127.0.0.1:${port}`;
  const log = fs.openSync(path.join(root, 'server.log'), 'w', 0o600);
  const archive = path.join(root, 'synthetic-archive');
  fs.cpSync(path.join(checkout, 'examples/demo-export'), archive, {recursive:true,
    filter: source => !['slices', 'archive.sqlite', 'archive.sqlite-wal', 'archive.sqlite-shm'].includes(path.basename(source))});
  server = spawn(python,
    [...pythonFlags, '-m', 'wechat_export', 'launch', '--export-dir', archive, '--port', String(port)], {
      cwd: serverCwd, env: {...process.env, WECHAT_EXPORT_DATA_ROOT: path.join(root, 'data')},
      stdio: ['ignore', log, log],
    });
  fs.closeSync(log);
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (server.exitCode !== null) throw new Error('Owned server exited before ready');
    try {
      const response = await fetch(base + '/api/bootstrap');
      if (response.ok) { ready = true; break; }
    } catch (_) {}
    await delay(100);
  }
  assert(ready, 'owned server not ready');
  browser = await chromium.launch({headless: true});
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  for (const [width, height] of [[1280,900], [390,740], [320,568], [740,360]]) {
    await page.setViewportSize({width, height});
    await page.goto(base);
    await page.locator('#chat-title').filter({hasText:'Alice'}).waitFor();
    await page.locator('.row').first().waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('#thread img')].every(img => img.complete));
    await page.waitForFunction(() => { const n = document.querySelector('#thread'); return n.scrollHeight - n.clientHeight - n.scrollTop <= 1; });
    assert(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight + 1), 'page must not grow with thread');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'horizontal overflow');
    if (width <= 800) {
      await page.locator('#open-rail').click();
      assert.equal(await page.evaluate(() => document.activeElement.id), 'conv-q');
      await page.keyboard.press('Escape');
      assert.equal(await page.evaluate(() => document.activeElement.id), 'open-rail');
      await page.locator('#open-rail').click();
      await page.locator('#rail button').filter({hasText:'Studio'}).click();
      await page.locator('#chat-title').filter({hasText:'Studio'}).waitFor();
      assert.equal(await page.locator('#rail').evaluate(node => node.classList.contains('open')), false);
      assert.equal(await page.locator('#stage').evaluate(node => node.inert), false);
    }
    await page.locator('#open-export').click();
    assert.equal(await page.evaluate(() => document.activeElement.id), 'ex-scope');
    assert(await page.locator('#archive').evaluate(node => node.inert));
    const bounds = await page.locator('.drawer-card').boundingBox();
    assert(bounds.y >= 0 && bounds.y + bounds.height <= height + 1, 'drawer exceeds viewport');
    await page.locator('#ex-scope').selectOption('selected');
    assert(await page.locator('#ex-go').isDisabled(), 'empty selection must not become all');
    await page.locator('#ex-scope').selectOption('all');
    await page.locator('#ex-preview').click();
    await page.locator('#ex-count').filter({hasText:'12 条'}).waitFor();
    await page.locator('#ex-readable').check();
    await page.locator('#ex-preview').click();
    await page.locator('#ex-count').filter({hasText:'数量预览：6 条'}).waitFor();
    assert((await page.locator('#ex-count').textContent()).includes('同范围 12 条，可读内容过滤排除 6 条'));
    await page.locator('#ex-readable').uncheck();
    await page.locator('#ex-preview').click();
    await page.locator('#ex-count').filter({hasText:'数量预览：12 条'}).waitFor();
    for (let i = 0; i < 24; i++) {
      await page.keyboard.press('Tab');
      assert(await page.evaluate(() => document.querySelector('#drawer').contains(document.activeElement)), 'Tab escaped dialog');
    }
    for (let i = 0; i < 24; i++) {
      await page.keyboard.press('Shift+Tab');
      assert(await page.evaluate(() => document.querySelector('#drawer').contains(document.activeElement)), 'Shift+Tab escaped dialog');
    }
    await page.screenshot({path: path.join(root, `drawer-${width}x${height}.png`)});
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'open-export');
    assert.equal(await page.locator('#archive').evaluate(node => node.inert), false);
    await page.screenshot({path: path.join(root, `reader-${width}x${height}.png`)});
  }
  // Deliberate message failure must show a recoverable alert, not hang loading.
  await page.setViewportSize({width:1280,height:900});
  await page.route('**/api/messages?*', route => route.fulfill({status:503, contentType:'application/json', body:JSON.stringify({error:'Synthetic retryable failure'})}), {times:1});
  await page.reload();
  await page.locator('#archive-error-message').filter({hasText:'Synthetic retryable failure'}).waitFor();
  await page.locator('#archive-retry').click();
  await page.locator('.row').first().waitFor();
  assert(await page.locator('#archive-error').isHidden());
  // Invalid dates produce inline errors, then can be corrected without reload.
  await page.locator('#open-export').click();
  await page.locator('#ex-since').fill('not-a-date');
  await page.locator('#ex-preview').click();
  await page.waitForFunction(() => !document.querySelector('#ex-count').textContent.includes('数量预览'));
  await page.locator('#ex-since').fill('');
  await page.locator('#ex-scope').selectOption('all');
  await page.locator('#ex-format').selectOption('jsonl');
  await page.locator('#ex-preview').click();
  await page.locator('#ex-count').filter({hasText:'12 条'}).waitFor();
  for (const format of ['jsonl', 'csv', 'md', 'html']) {
    await page.locator('#ex-format').selectOption(format);
    await page.locator('#ex-go').click();
    await page.locator('#ex-result').filter({hasText:'已写出 12 条'}).waitFor({timeout:30000});
  }
  // Inspect only this process's synthetic export, never an existing user's data/.
  const slices = path.join(archive, 'slices');
  const outputs = fs.readdirSync(slices).filter(name => !name.startsWith('.'));
  const manifests = outputs.map(name => path.join(slices, name, 'manifest.json')).filter(p => fs.existsSync(p));
  assert.equal(manifests.length, 4);
  for (const filename of manifests) {
    const manifest = JSON.parse(fs.readFileSync(filename));
    assert.equal(manifest.count, 12); assert.equal(manifest.source_kind, 'live-db');
    assert.equal(manifest.backup2_coverage, 'unverified');
    assert(fs.existsSync(path.join(path.dirname(filename), 'coverage.json')));
    assert(fs.readFileSync(manifest.path).length > 0);
  }
  assert.deepEqual(errors, [], 'unhandled browser errors');
  console.log(JSON.stringify({synthetic:true, installed_package:installed, isolated_python:installed, viewports:4, keyboard:true, recovery:true, exported:12, formats:4, evidence_directory:root}));
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  if (server && server.exitCode === null) { server.kill('SIGTERM'); await once(server, 'exit'); }
});
