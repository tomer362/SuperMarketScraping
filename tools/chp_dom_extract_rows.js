#!/usr/bin/env node

/**
 * Extract CHP compare table rows from a real browser DOM using Playwright.
 *
 * Usage:
 *   node tools/chp_dom_extract_rows.js \
 *     --url "https://chp.co.il/main_page/compare_results?..." \
 *     --out output.json \
 *     [--screenshot output.png] \
 *     [--headless true|false]
 */

const fs = require("fs");
const path = require("path");

function getArg(name, fallback = null) {
  const idx = process.argv.indexOf(name);
  if (idx === -1 || idx + 1 >= process.argv.length) return fallback;
  return process.argv[idx + 1];
}

const url = getArg("--url");
const outPath = getArg("--out");
const screenshotPath = getArg("--screenshot");
const headlessArg = (getArg("--headless", "true") || "true").toLowerCase();
const headless = headlessArg !== "false";

if (!url || !outPath) {
  console.error("Missing required --url or --out argument.");
  process.exit(2);
}

const playwrightPath = path.resolve(
  __dirname,
  "../webapp/frontend/node_modules/playwright",
);

let chromium = null;
try {
  ({ chromium } = require(playwrightPath));
} catch (err) {
  console.error(`Failed to load Playwright from ${playwrightPath}`);
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(2);
}

const ZERO_WIDTH_RE = /[\u200b\u200c\u200d\ufeff\u200e\u200f]/g;

function cleanText(text) {
  if (text == null) return "";
  return String(text).replace(/\u00a0/g, " ").replace(ZERO_WIDTH_RE, "").trim();
}

function parseRowPrice(raw) {
  const cleaned = cleanText(raw).replace(/,/g, "");
  if (!cleaned) return null;
  const parsed = Number.parseFloat(cleaned);
  return Number.isFinite(parsed) ? parsed : null;
}

(async () => {
  const browser = await chromium.launch({ headless });
  const page = await browser.newPage();

  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForSelector("table.results-table", { timeout: 60000 });

    const payload = await page.evaluate(() => {
      const ZERO_WIDTH_RE_EVAL = /[\u200b\u200c\u200d\ufeff\u200e\u200f]/g;
      const cleanTextEval = (text) => {
        if (text == null) return "";
        return String(text)
          .replace(/\u00a0/g, " ")
          .replace(ZERO_WIDTH_RE_EVAL, "")
          .trim();
      };

      const parseRow = (tr, isOnline, index) => {
        const cells = tr.querySelectorAll("td");
        if (cells.length < 5) return null;

        const storeCell = cells[1];
        const storeAnchor = storeCell.querySelector("a");
        const dealCell = cells[3];
        const dealBtn = dealCell.querySelector("button.btn-discount");

        const col2 = cleanTextEval(cells[2].textContent || "");
        const rowPriceRaw = cleanTextEval(cells[4].textContent || "");
        const priceClean = rowPriceRaw.replace(/,/g, "");
        const parsedPrice = Number.parseFloat(priceClean);

        return {
          row_index: index,
          chain_name: cleanTextEval(cells[0].textContent || ""),
          store_name: cleanTextEval(storeCell.textContent || ""),
          store_url: storeAnchor ? storeAnchor.href : null,
          website: isOnline ? col2 : "",
          address: isOnline ? "" : col2,
          deal_price_text: dealBtn
            ? cleanTextEval(dealBtn.textContent || "")
            : cleanTextEval(dealCell.textContent || ""),
          deal_text: dealBtn
            ? String(dealBtn.getAttribute("data-discount-desc") || "")
            : "",
          row_price_raw: rowPriceRaw,
          row_price: Number.isFinite(parsedPrice) ? parsedPrice : null,
        };
      };

      const tableRows = (table, isOnline) => {
        if (!table) return [];
        const bodyRows = Array.from(table.querySelectorAll("tbody tr"));
        const mainRows = bodyRows.filter(
          (tr) => !(tr.classList && tr.classList.contains("display_when_narrow")),
        );
        return mainRows
          .map((tr, idx) => parseRow(tr, isOnline, idx))
          .filter(Boolean);
      };

      const tables = Array.from(document.querySelectorAll("table.results-table"));
      const physicalRows = tables.length >= 1 ? tableRows(tables[0], false) : [];
      const onlineRows = tables.length >= 2 ? tableRows(tables[1], true) : [];

      const codeInput = document.querySelector("#displayed_product_code");
      const nameInput = document.querySelector("#displayed_product_name_and_contents");

      return {
        url: window.location.href,
        title: document.title,
        fetched_at: new Date().toISOString(),
        product: {
          displayed_product_code: codeInput ? String(codeInput.value || "") : "",
          displayed_product_name_and_contents: nameInput
            ? String(nameInput.value || "")
            : "",
        },
        physical_rows: physicalRows,
        online_rows: onlineRows,
      };
    });

    const html = await page.content();
    payload.html = html;

    for (const row of payload.physical_rows) {
      row.row_price_raw = cleanText(row.row_price_raw);
      row.row_price = parseRowPrice(row.row_price);
      if (row.row_price === null) {
        row.row_price = parseRowPrice(row.row_price_raw);
      }
    }
    for (const row of payload.online_rows) {
      row.row_price_raw = cleanText(row.row_price_raw);
      row.row_price = parseRowPrice(row.row_price);
      if (row.row_price === null) {
        row.row_price = parseRowPrice(row.row_price_raw);
      }
    }

    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, JSON.stringify(payload, null, 2), "utf8");

    if (screenshotPath) {
      fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
      await page.screenshot({ path: screenshotPath, fullPage: true });
    }
  } finally {
    await browser.close();
  }
})().catch((err) => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
