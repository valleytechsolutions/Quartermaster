// Renders the Quartermaster logo and boot screen. Run: node tools/make_brand.js
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
const B = path.join(__dirname, '..', 'assets', 'brand');
const GOLD = '#F5B301', WHITE = '#FFFFFF', INK = '#000000', SW = 14;
function logo() {
  const parts = [[0, 34, 26, GOLD], [34, 70, 38, WHITE], [104, 80, 52, GOLD], [184, 92, 68, WHITE]];
  let s = '';
  for (const [x, w, h, f] of parts.slice().reverse())
    s += `<rect x="${x}" y="${-h / 2}" width="${w}" height="${h}" rx="6" fill="${f}" stroke="${INK}" stroke-width="${SW}" stroke-linejoin="round"/>`;
  s += `<rect x="270" y="-44" width="26" height="88" rx="9" fill="${GOLD}" stroke="${INK}" stroke-width="${SW}" stroke-linejoin="round"/>`;
  for (let i = 0; i < 3; i++)
    s += `<line x1="${320 + i * 27}" y1="${-(22 + i * 17)}" x2="${320 + i * 27}" y2="${22 + i * 17}" stroke="${INK}" stroke-width="${SW}" stroke-linecap="round"/>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400"><g transform="translate(200,206) rotate(-35) translate(-190,0)">${s}</g></svg>`;
}
const uri = f => 'data:image/png;base64,' + fs.readFileSync(path.join(B, f)).toString('base64');
(async () => {
  const svg = logo();
  fs.writeFileSync(path.join(B, 'quartermaster-logo.svg'), svg);
  const b = await chromium.launch();
  let p = await b.newPage({ viewport: { width: 1024, height: 1024 } });
  await p.setContent(`<body style="margin:0">${svg.replace('<svg ', '<svg width="1024" height="1024" ')}</body>`);
  await p.screenshot({ path: path.join(B, 'quartermaster-logo.png'), omitBackground: true });
  fs.writeFileSync(path.join(B, '_q.png'), fs.readFileSync(path.join(B, 'quartermaster-logo.png')));
  p = await b.newPage({ viewport: { width: 320, height: 170 }, deviceScaleFactor: 2 });
  await p.setContent(`<body style="margin:0;width:320px;height:170px;background:#fff;font-family:'Inter Display',Inter,Arial;overflow:hidden">
  <div style="position:absolute;left:0;right:0;top:8px;height:118px;display:flex;align-items:center;justify-content:center;gap:14px">
    <img src="${uri('bwm-logo.png')}" style="height:66px">
    <img src="${uri('_q.png')}" style="height:124px;margin:0 -8px">
    <img src="${uri('vts-logo.png')}" style="height:66px">
  </div>
  <div style="position:absolute;left:0;right:0;top:122px;text-align:center;font-weight:800;font-size:25px;letter-spacing:3.5px;color:#000">QUARTERMASTER</div>
  <div style="position:absolute;left:0;right:0;top:152px;text-align:center;font-weight:600;font-size:9px;letter-spacing:2px;color:#777877">WI-FI + BLUETOOTH WARDRIVER</div></body>`);
  await p.screenshot({ path: path.join(B, 'boot-preview@2x.png') });
  fs.unlinkSync(path.join(B, '_q.png'));
  await b.close();
})();
