const { chromium } = require('playwright');
const path = require('path');
const OUT = process.argv[2];
(async () => {
  const b = await chromium.launch();
  const scheme = process.argv[3] || 'dark';
  const p = await b.newPage({ viewport: { width: 980, height: 680 }, colorScheme: scheme, deviceScaleFactor: 2 });
  p.on('pageerror', e => console.log('PAGEERROR', e.message));
  p.on('console', m => { if (m.type()==='error') console.log('CONSOLE', m.text()); });
  await p.addInitScript({ path: path.join(__dirname, 'mock_setup.js') });
  const url = h => 'file://' + path.join(__dirname, 'setup_preview.html') + '#' + h;
  const fr = () => p.frameLocator('#f');
  const snap = async n => { await p.waitForTimeout(450); await p.screenshot({ path: `${OUT}/${scheme}-${n}.png` }); };
  // установка
  await p.goto(url('install')); await snap('1-install-welcome');
  await fr().locator('#btnCustom').click(); await snap('2-install-options');
  await fr().locator('[data-go=i-profile]').click(); await snap('3-install-profile');
  await fr().locator('#btnInstall').click(); await p.waitForTimeout(1100); await snap('4-install-progress');
  await p.waitForTimeout(2600); await snap('5-install-done');
  // удаление
  await p.goto('about:blank'); await p.goto(url('uninstall')); await snap('6-uninstall-welcome');
  await fr().locator('#btnUPick').click(); await snap('7-uninstall-choose');
  await fr().locator('[data-p=all]').click(); await snap('8-uninstall-all');
  await fr().locator('#btnUDo').click(); await snap('9-uninstall-confirm');
  await fr().locator('#btnUDo').click(); await p.waitForTimeout(900); await snap('10-uninstall-progress');
  await p.waitForTimeout(2500); await snap('11-uninstall-done');
  await b.close();
})();
