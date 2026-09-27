// Renders docs/images/src/banner.html to docs/images/banner.png (README header).
// Run: cd frontend && node scripts/render-banner.mjs
import { chromium } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const browser = await chromium.launch(
  process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
    : {},
);
const page = await browser.newPage({
  viewport: { width: 1280, height: 400 },
  deviceScaleFactor: 2,
});
await page.goto(`file://${path.join(root, "docs/images/src/banner.html")}`);
await page.screenshot({ path: path.join(root, "docs/images/banner.png") });
await browser.close();
console.log("wrote docs/images/banner.png");
