const puppeteer = require('puppeteer-core');
const path = require('path');
require('fs').mkdirSync(path.join(__dirname, 'frames'), { recursive: true });
(async () => {
  const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: 'new', userDataDir: path.join(require('os').tmpdir(), 'night-shift-video-chrome'), args: ['--hide-scrollbars'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
  await page.goto('file://' + path.join(__dirname, 'player.html'));
  const only = process.argv.slice(2).map(Number);
  const fps = 30, total = await page.evaluate(() => window.DURATION);
  const times = only.length ? only : Array.from({ length: Math.round(total * fps) }, (_, i) => i / fps);
  for (let i = 0; i < times.length; i++) {
    await page.evaluate(t => window.render(t), times[i]);
    const name = only.length ? `still-${times[i]}.png` : `frames/${String(i).padStart(5, '0')}.jpg`;
    await page.screenshot({ path: path.join(__dirname, name), type: only.length ? 'png' : 'jpeg', quality: only.length ? undefined : 94 });
  }
  await browser.close();
})();
