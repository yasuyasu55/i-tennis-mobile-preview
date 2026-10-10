const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const data = JSON.parse(fs.readFileSync(path.join(root, 'data/tournaments.json')));
const server = http.createServer((req, res) => {
  const name = decodeURIComponent(req.url.split('?')[0]);
  const file = path.join(root, name === '/' ? 'index.html' : name);
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
  res.setHeader('Content-Type', file.endsWith('.html') ? 'text/html' : file.endsWith('.js') ? 'application/javascript' : file.endsWith('.json') ? 'application/json' : 'image/png');
  res.end(fs.readFileSync(file));
});
(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const url = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({executablePath:process.env.TEST_CHROMIUM_PATH || undefined,args:['--no-sandbox']});
  const page = await browser.newPage({viewport:{width:390,height:844}});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto(url);
    await page.waitForFunction(() => tournamentDataCache && document.querySelector('#tSearch'));
    await page.click('#navResearch');
    await page.locator('#tFilters').evaluate(el => el.open = true);
    assert.deepEqual(await page.locator('input[data-kind="area"]').evaluateAll(bs => bs.map(b => b.value)), ['浜松','豊橋・豊川・蒲郡','岡崎・安城・刈谷・西尾','愛知県協会掲載']);
    assert.equal(await page.locator('#tAreaOutside').count(), 0);
    assert.equal(await page.locator('.tcard').count(), 74);
    const group = page.locator('input[data-kind="area"][value="豊橋・豊川・蒲郡"]');
    await group.check();
    assert.equal(await page.locator('.tcard').count(), 74, 'draft must not apply until search');
    await page.click('#tSaveSearch');
    await page.click('#tSearch');
    assert.equal(await page.locator('.tcard').count(), 35);
    await page.reload();
    await page.waitForFunction(() => tournamentDataCache && document.querySelector('#tSearch'));
    assert.equal(await page.locator('.tcard').count(), 35);
    await page.evaluate(() => {
      const st = tDefaultState(); st.areas = {'豊橋':true};
      tSaveSearchPreferences(st);
      localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify(['aichi-2026-2f561970']));
    });
    await page.reload();
    await page.waitForFunction(() => tournamentDataCache && document.querySelector('#tSearch'));
    await page.click('#navResearch');
    await page.locator('#tFilters').evaluate(el => el.open = true);
    assert.equal(await group.evaluate(b => b.indeterminate), true);
    assert.deepEqual(await page.evaluate(() => tSelected(tReadPanel().areas)), ['豊橋']);
    assert.equal(await page.locator('.tcard').count(), 20, 'legacy city preference remains narrow');
    await page.click('#tSaveSearch');
    assert.deepEqual(await page.evaluate(() => tSelected(JSON.parse(localStorage.getItem(SEARCH_PREFERENCES_KEY)).areas)), ['豊橋']);
    assert.deepEqual(await page.evaluate(() => favoriteIds), ['aichi-2026-2f561970']);
    await group.check();
    assert.deepEqual(await page.evaluate(() => tSelected(tReadPanel().areas).sort()), ['豊川','豊橋','蒲郡'].sort());
    await page.click('#tReset');
    assert.equal(await page.locator('.tcard').count(), 74);
    assert.equal(await page.evaluate(() => localStorage.getItem(SEARCH_PREFERENCES_KEY)), null);
    // Future source data activates assigned positions without empty placeholders.
    const future = await page.evaluate(() => tAreaGroups({tournaments:['浜松','新城','田原','岡崎','安城','豊田','名古屋','岐阜','三重','静岡','長野'].map(v => ({event_area:v}))}));
    assert.deepEqual(future.find(g=>g.label==='東三河その他').values, ['新城','田原']);
    assert.equal(future.filter(g=>g.outside).length,4);
    const fixture = {tournaments:[{event_area:'新城'},{event_area:'田原'},{event_area:'岐阜'}]};
    await page.evaluate(fixture => {
      tState=tDefaultState(); tState.areas={'岐阜':true};
      document.querySelector('#tFilters').outerHTML=tPanelHtml(fixture); tSyncPanel(); tBindPanel();
    }, fixture);
    assert.equal(await page.locator('#tAreaOutside').evaluate(el=>el.open),true);
    assert.deepEqual(await page.evaluate(()=>tSelected(tReadPanel().areas)),['岐阜']);
    for (const width of [320,360,390,1100]) {
      await page.setViewportSize({width,height:900});
      await page.locator('#tFilters').evaluate(el=>el.open=true);
      for (const size of ['standard','large','larger']) {
        await page.evaluate(size=>{ document.documentElement.setAttribute('data-text-size',size); },size);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${width}/${size} overflow`);
      }
    }
    await page.goto(url);
    await page.waitForFunction(()=>tournamentDataCache && document.querySelector('#tSearch'));
    await page.click('#navResearch');
    await page.locator('#tFilters').evaluate(el=>el.open=true);
    await page.setViewportSize({width:390,height:1100});
    await page.screenshot({path:path.join(root,'region-layout-390.png'),fullPage:false});
    assert.deepEqual(errors,[]);
    assert.equal(data.tournaments.length,74);
    console.log('PASS: grouped filtering, search-button behavior, legacy preferences, favorites, future regions, reset, 4 widths, no JS errors');
  } finally { await browser.close(); server.close(); }
})().catch(e=>{ console.error(e); server.close(); process.exitCode=1; });
