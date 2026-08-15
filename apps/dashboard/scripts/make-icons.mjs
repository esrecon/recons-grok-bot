// Generates the PWA icons (192/512) from the favicon motif using the
// preinstalled Chromium — no image libraries needed. Run after changing the
// mark: `node scripts/make-icons.mjs`.
import { chromium } from "@playwright/test";
import { writeFileSync } from "node:fs";

const svg = (size) => `
<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 100 100">
  <rect width="100" height="100" rx="22" fill="#0d0d0f"/>
  <path d="M50 20a30 30 0 1 0 .001 0Z" fill="#ffffff"/>
  <g fill="#0d0d0f">
    <rect x="37" y="42" width="7" height="16" rx="3.5" transform="rotate(-8 40.5 50)"/>
    <rect x="56" y="42" width="7" height="16" rx="3.5" transform="rotate(8 59.5 50)"/>
  </g>
</svg>`;

const browser = await chromium.launch({
  executablePath: process.env.PW_CHROMIUM || "/opt/pw-browsers/chromium",
});
for (const size of [192, 512]) {
  const page = await browser.newPage({ viewport: { width: size, height: size } });
  await page.setContent(
    `<body style="margin:0">${svg(size)}</body>`,
    { waitUntil: "load" },
  );
  const buf = await page.screenshot({ omitBackground: true });
  writeFileSync(new URL(`../public/icon-${size}.png`, import.meta.url), buf);
  console.log(`wrote public/icon-${size}.png`);
  await page.close();
}
await browser.close();
