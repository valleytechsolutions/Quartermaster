// Renders the Quartermaster logo and boot screen. Run: node tools/make_brand.js
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
const B = path.join(__dirname, '..', 'assets', 'brand');
const GOLD = '#F5B301', WHITE = '#FFFFFF', INK = '#000000', SW = 14;
const DGOLD = '#C48A00', CREAM = '#FFF6DC', CSHADE = '#E6D29A', SHINE = '#FFE08A';
function logo() {
  // Brass spyglass drawn along +x: eyepiece at left, objective at right. Each tube has a flat
  // highlight and shade band, and a brass collar ring at every joint.
  const st = `stroke="${INK}" stroke-width="${SW}" stroke-linejoin="round"`;
  const tube = (x, w, h, fill, shade, shine) =>
    `<rect x="${x}" y="${-h / 2}" width="${w}" height="${h}" rx="5" fill="${fill}" ${st}/>` +
    `<rect x="${x + 7}" y="${h / 2 - 7 - h * 0.26}" width="${w - 14}" height="${h * 0.26}" fill="${shade}"/>` +
    `<rect x="${x + 12}" y="${-h / 2 + 10}" width="${w - 24}" height="5" rx="2.5" fill="${shine}"/>`;
  const ring = (x, h) => `<rect x="${x - 9}" y="${-h / 2}" width="18" height="${h}" rx="6" fill="${GOLD}" ${st}/>` +
    `<rect x="${x - 3}" y="${-h / 2 + 9}" width="5" height="${h - 18}" rx="2.5" fill="${SHINE}"/>`;
  let s = '';
  s += tube(190, 86, 70, CREAM, CSHADE, WHITE);
  s += tube(108, 84, 54, GOLD, DGOLD, SHINE);
  s += tube(38, 72, 40, CREAM, CSHADE, WHITE);
  s += tube(2, 38, 26, GOLD, DGOLD, SHINE);
  s += ring(190, 82) + ring(108, 66) + ring(38, 52);
  // objective bell
  s += `<path d="M268 -37 L292 -47 Q300 -47 300 -39 L300 39 Q300 47 292 47 L268 37 Z" fill="${GOLD}" ${st}/>`;
  s += `<rect x="283" y="-33" width="6" height="66" rx="3" fill="${SHINE}"/>`;
  // eyepiece cap
  s += `<rect x="-8" y="-17" width="14" height="34" rx="5" fill="${DGOLD}" ${st}/>`;
  for (let i = 0; i < 3; i++) {
    const x = 326 + i * 28, h = 22 + i * 17;
    s += `<line x1="${x}" y1="${-h}" x2="${x}" y2="${h}" stroke="${INK}" stroke-width="${SW + 10}" stroke-linecap="round"/>`;
    s += `<line x1="${x}" y1="${-h}" x2="${x}" y2="${h}" stroke="${GOLD}" stroke-width="${SW - 5}" stroke-linecap="round"/>`;
  }
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
