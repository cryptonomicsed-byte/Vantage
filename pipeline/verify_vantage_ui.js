// verify_vantage_ui.js — Playwright visual verification for Vantage deploy
// Usage: node verify_vantage_ui.js [--quick]
//   --quick: screenshot only home + trading, skip deep clicks
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = 'https://omokoda.duckdns.org'; // or http://localhost:8001 on VPS
const QUICK = process.argv.includes('--quick');
const SCREENSHOT_DIR = '/tmp/vantage-verify';

const PAGES = [
  { name: 'home', path: '/', checks: ['All', 'Videos', 'Articles'] },
  { name: 'trading', path: '/trading', checks: ['Dashboard', 'Market Intel'] },
  { name: 'code', path: '/code', checks: [] },
  { name: 'video-studio', path: '/video', checks: [] },
  { name: 'swarm', path: '/swarm', checks: [] },
  { name: 'galaxy', path: '/galaxy', checks: [] },
];

const results = { passed: [], failed: [], warnings: [] };

function log(level, msg) {
  const prefix = { ok: '✅', fail: '❌', warn: '⚠️', info: '  ' }[level] || '  ';
  console.log(`${prefix} ${msg}`);
}

async function verify() {
  if (!fs.existsSync(SCREENSHOT_DIR)) fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });

  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-setuid-sandbox'] });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();

  try {
    // 1. HOME PAGE
    log('info', 'Navigating to home...');
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 15000 });
    await page.screenshot({ path: `${SCREENSHOT_DIR}/01-home.png`, fullPage: false });
    
    // Check for intel spam
    const pageText = await page.textContent('body');
    const intelCount = (pageText.match(/Intel Scan Complete/g) || []).length;
    if (intelCount > 3) {
      log('fail', `INTEL SPAM: ${intelCount} "Intel Scan Complete" posts on home page!`);
      results.failed.push(`Home feed: ${intelCount} intel posts visible (should be 0 on home feed)`);
    } else if (intelCount > 0) {
      log('warn', `${intelCount} intel posts visible on home page`);
      results.warnings.push(`${intelCount} intel posts on home page`);
    } else {
      log('ok', 'No intel spam on home page');
      results.passed.push('Home: no intel spam');
    }

    // Check content tabs exist — only tabs that actually exist in current UI
    const currentTabs = ['Feed', 'Videos', 'Audio'];
    for (const tab of currentTabs) {
      const hasTab = await page.$(`text=${tab}`);
      if (hasTab) {
        log('ok', `Tab "${tab}" found`);
        results.passed.push(`Home tab: ${tab}`);
      } else {
        log('warn', `Tab "${tab}" not found`);
        results.warnings.push(`Home tab "${tab}" missing`);
      }
    }

    // Check for content cards
    const cards = await page.$$('[class*="card"], [class*="Card"], article, [class*="broadcast"]');
    log(cards.length > 0 ? 'ok' : 'warn', `Home: ${cards.length} content cards found`);

    if (!QUICK) {
      // Click Videos tab
      const videoTabBtn = await page.$('text=Videos');
      if (videoTabBtn) {
        await videoTabBtn.click();
        await page.waitForTimeout(500);
        await page.screenshot({ path: `${SCREENSHOT_DIR}/01b-home-videos.png`, fullPage: false });
      }
    }

    // 2. TRADING PAGE
    log('info', 'Navigating to /trading...');
    await page.goto(`${BASE}/trading`, { waitUntil: 'networkidle', timeout: 15000 });
    await page.screenshot({ path: `${SCREENSHOT_DIR}/02-trading.png`, fullPage: false });

    // Check trading tabs — current UI uses Dashboard, Analytics, Portfolio
    for (const tab of ['Dashboard', 'Analytics', 'Portfolio']) {
      const hasTab = await page.$(`text=${tab}`);
      if (hasTab) {
        log('ok', `Trading tab "${tab}" found`);
        results.passed.push(`Trading tab: ${tab}`);
      } else {
        log('warn', `Trading tab "${tab}" not found`);
        results.warnings.push(`Trading tab "${tab}" missing`);
      }
    }

    // Check for live status indicator
    const statusBar = await page.$('[class*="status"], [class*="live"], [class*="refresh"]');
    if (statusBar) {
      log('ok', 'Live status/refresh indicator present');
      results.passed.push('Trading: live indicator');
    } else {
      log('warn', 'No live status indicator found');
    }

    if (!QUICK) {
      // Click Analytics tab (was Market Intel)
      const anTab = await page.$('text=Analytics');
      if (anTab) {
        await anTab.click();
        await page.waitForTimeout(800);
        await page.screenshot({ path: `${SCREENSHOT_DIR}/02b-trading-analytics.png`, fullPage: false });
      }
    }

    // 3. CODE PAGE
    log('info', 'Navigating to /code...');
    await page.goto(`${BASE}/code`, { waitUntil: 'networkidle', timeout: 15000 });
    await page.screenshot({ path: `${SCREENSHOT_DIR}/03-code.png`, fullPage: false });

    const repoCards = await page.$$('[class*="repo"], [class*="Repo"], [class*="cd-"]');
    log(repoCards.length > 0 ? 'ok' : 'warn', `Code: ${repoCards.length} repo elements found`);
    if (repoCards.length === 0) {
      results.warnings.push('Code page: no repo cards visible');
    }

    // 4. VIDEO STUDIO
    log('info', 'Navigating to /video...');
    await page.goto(`${BASE}/video`, { waitUntil: 'networkidle', timeout: 15000 });
    await page.screenshot({ path: `${SCREENSHOT_DIR}/04-video-studio.png`, fullPage: false });

    // 5. SWARM MAP
    log('info', 'Navigating to /swarm...');
    await page.goto(`${BASE}/swarm`, { waitUntil: 'networkidle', timeout: 15000 }).catch(() => {
      log('warn', 'Swarm page timed out (WebSocket may be connecting)');
      results.warnings.push('Swarm page load timeout');
    });
    await page.screenshot({ path: `${SCREENSHOT_DIR}/05-swarm.png`, fullPage: false });

    // 6. GALAXY
    log('info', 'Navigating to /galaxy...');
    await page.goto(`${BASE}/galaxy`, { waitUntil: 'networkidle', timeout: 15000 }).catch(() => {
      log('warn', 'Galaxy page timed out');
      results.warnings.push('Galaxy page load timeout');
    });
    await page.screenshot({ path: `${SCREENSHOT_DIR}/06-galaxy.png`, fullPage: false });

    // 7. SIDEBAR NAVIGATION (quick test)
    log('info', 'Testing sidebar navigation...');
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 10000 });
    const sidebarLinks = await page.$$('nav a, [class*="sidebar"] a, [class*="Sidebar"] a');
    log('info', `Found ${sidebarLinks.length} sidebar links`);

    // Check HTTPS / SSL
    const url = page.url();
    if (url.startsWith('https://')) {
      log('ok', 'HTTPS active');
      results.passed.push('HTTPS: active');
    } else {
      log('warn', 'Not using HTTPS');
    }

  } catch (err) {
    log('fail', `Verification error: ${err.message}`);
    results.failed.push(`Error: ${err.message}`);
  } finally {
    await browser.close();
  }

  // SUMMARY
  console.log('\n═══════════════════════════════════════');
  console.log('  VANTAGE UI VERIFICATION RESULTS');
  console.log('═══════════════════════════════════════');
  console.log(`  Passed:  ${results.passed.length}`);
  console.log(`  Warnings: ${results.warnings.length}`);
  console.log(`  Failed:  ${results.failed.length}`);
  
  if (results.passed.length > 0) {
    console.log('\n  --- PASSED ---');
    results.passed.forEach(p => console.log(`  ✅ ${p}`));
  }
  if (results.warnings.length > 0) {
    console.log('\n  --- WARNINGS ---');
    results.warnings.forEach(w => console.log(`  ⚠️  ${w}`));
  }
  if (results.failed.length > 0) {
    console.log('\n  --- FAILED ---');
    results.failed.forEach(f => console.log(`  ❌ ${f}`));
  }

  console.log(`\n  Screenshots: ${SCREENSHOT_DIR}/`);
  console.log(`    ls ${SCREENSHOT_DIR}/*.png`);
  console.log('═══════════════════════════════════════\n');

  return results.failed.length === 0 ? 0 : 1;
}

verify().then(code => process.exit(code));
