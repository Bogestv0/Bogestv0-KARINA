const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const b = await chromium.launch();
  for (const scheme of ['light','dark']) {
    const p = await b.newPage({ viewport: { width: 1080, height: 760 }, colorScheme: scheme, deviceScaleFactor: 1 });
    p.on('pageerror', e => console.log('PAGEERROR', e.message));
    p.on('console', m => { if (m.type()==='error') console.log('CONSOLE', m.text()); });
    await p.addInitScript({ path: path.join(__dirname,'mock_ui.js') });
    await p.goto('file://' + path.join(__dirname,'..','ui','index.html'));
    await p.waitForTimeout(500);
    await p.evaluate(() => window.__demo());
    for (const t of ['home','commands','apps','settings']) {
      await p.click(`[data-tab=${t}]`); await p.waitForTimeout(250);
      await p.screenshot({ path: `/tmp/claude-0/-home-claude/28d47fe4-de58-5ac7-921b-c520133d9b63/scratchpad/shot-${scheme}-${t}.png` });
    }
    await p.close();
  }
  await b.close();
})();
