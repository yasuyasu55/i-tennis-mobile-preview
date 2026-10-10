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
    const areas = ['浜松','豊橋','豊川','蒲郡','岡崎','愛知県協会掲載'];
    assert.deepEqual(await page.locator('input[data-kind="area"]').evaluateAll(bs => bs.map(b => b.value)), areas);
    assert.equal(await page.locator('#tAreaOutside').count(), 0);
    assert.equal(await page.locator('.tcard').count(),74);
    for (const area of areas) {
      await page.locator('#tFilters').evaluate(el=>el.open=true);
      await page.locator(`input[data-kind="area"][value="${area}"]`).check();
      assert.equal(await page.locator('.tcard').count(),74,'checkbox changes only the draft');
      await page.click('#tSearch');
      assert.equal(await page.locator('.tcard').count(),data.tournaments.filter(t=>t.event_area===area).length);
      await page.locator('#tFilters').evaluate(el=>el.open=true);
      await page.click('#tReset');
    }
    await page.evaluate(() => {
      const st=tDefaultState();st.areas={'豊橋':true};tSaveSearchPreferences(st);
      localStorage.setItem(FAVORITES_STORAGE_KEY,JSON.stringify(['aichi-2026-2f561970']));
    });
    await page.reload();
    await page.waitForFunction(()=>tournamentDataCache && document.querySelector('#tSearch'));
    await page.click('#navResearch');
    await page.locator('#tFilters').evaluate(el=>el.open=true);
    assert.equal(await page.locator('input[data-kind="area"][value="豊橋"]').isChecked(),true);
    assert.equal(await page.locator('.tcard').count(),20);
    assert.deepEqual(await page.evaluate(()=>tSelected(tReadPanel().areas)),['豊橋']);
    assert.deepEqual(await page.evaluate(()=>favoriteIds),['aichi-2026-2f561970']);
    await page.click('#tSaveSearch');
    assert.deepEqual(await page.evaluate(()=>tSelected(JSON.parse(localStorage.getItem(SEARCH_PREFERENCES_KEY)).areas)),['豊橋']);
    await page.locator('input[data-kind="area"][value="豊川"]').check();
    await page.click('#tSearch');
    assert.equal(await page.locator('.tcard').count(),28);
    await page.locator('#tFilters').evaluate(el=>el.open=true);
    await page.click('#tReset');
    assert.equal(await page.locator('.tcard').count(),74);
    await page.evaluate(()=>{let st=tDefaultState();st.areas={'豊橋':true,'豊川':true,'蒲郡':true};tSaveSearchPreferences(st);});
    await page.reload();await page.waitForFunction(()=>tournamentDataCache && document.querySelector('#tSearch'));
    await page.click('#navResearch');await page.locator('#tFilters').evaluate(el=>el.open=true);
    assert.equal(await page.locator('.tcard').count(),35);
    assert.deepEqual(await page.locator('input[data-kind="area"]:checked').evaluateAll(bs=>bs.map(b=>b.value)),['豊橋','豊川','蒲郡']);
    await page.click('#tReset');
    const future=await page.evaluate(()=>tAreaSections({tournaments:['浜松','新城','田原','岡崎','安城','豊田','名古屋','岐阜','三重','静岡','長野'].map(v=>({event_area:v}))}));
    assert.equal(future.inside.includes('新城'),true);assert.equal(future.inside.includes('田原'),true);
    assert.deepEqual(future.outside,['岐阜','三重','静岡','長野']);
    for(const width of [320,360,390,1100]) {
      await page.setViewportSize({width,height:900});await page.locator('#tFilters').evaluate(el=>el.open=true);
      for(const size of ['standard','large','larger']) {
        await page.evaluate(size=>document.documentElement.setAttribute('data-text-size',size),size);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${width}/${size} overflow`);
      }
    }
    await page.setViewportSize({width:390,height:1100});
    await page.evaluate(()=>document.documentElement.removeAttribute('data-text-size'));
    await page.screenshot({path:path.join(root,'region-layout-390.png'),fullPage:false});
    assert.deepEqual(errors,[]);
    assert.equal(data.tournaments.length,74);
    console.log('PASS: individual region filtering for all 6 areas; single/multiple saved city selections; favorites; future cities; reset; 4 widths and 3 text sizes; no JS errors');
  } finally { await browser.close(); server.close(); }
})().catch(e=>{ console.error(e); server.close(); process.exitCode=1; });
