import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const errors: string[] = [];
test.beforeEach(async ({ page }) => {
  errors.length = 0;
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(e.message));
});
test.afterEach(() => { expect(errors, `console errors: ${errors.join("\n")}`).toEqual([]); });

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  const serious = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
}

async function noHorizontalScroll(page: Page) {
  const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(sw).toBeLessThanOrEqual(cw);
}

async function runFixture(page: Page, ticker: string, mode: string, asOf: string) {
  await page.goto(`/new?ticker=${ticker}&mode=${mode}`);
  await page.getByText("Advanced: as-of date, transcript").click();
  await page.getByLabel("As of (historical, no look-ahead)").fill(asOf);
  await page.getByRole("button", { name: "Run research" }).click();
  await expect(page).toHaveURL(/\/reports\/rpt_/, { timeout: 30_000 });
}

test("watchlist: add ticker, see next earnings, axe clean", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Watchlist" })).toBeVisible();
  await page.getByLabel("Ticker to add").fill("NVDA");
  await page.getByRole("button", { name: "Add" }).click();
  await expect(page.getByRole("link", { name: "NVDA", exact: true })).toBeVisible();
  await expect(page.getByText("estimated").first()).toBeVisible();
  await noHorizontalScroll(page);
  await axe(page);
  await page.screenshot({ path: `test-results/watchlist-${test.info().project.name}.png`, fullPage: true });
});

test("run streams to a grounded report with hoverable sources", async ({ page }) => {
  await runFixture(page, "NVDA", "full_cycle", "2026-11-20");
  await expect(page.getByRole("heading", { name: "Decision brief" })).toBeVisible();
  const fact = page.locator("button.fact").first();
  await fact.hover();
  await expect(page.getByRole("tooltip")).toContainText("As of");
  await fact.click();
  await expect(page.locator("li[id^='src-'].ring-2")).toBeVisible();
  await expect(page.getByText("Research only — not investment advice").first()).toBeVisible();
  await noHorizontalScroll(page);
  await axe(page);
  await page.screenshot({ path: `test-results/report-${test.info().project.name}.png`, fullPage: true });
});

test("review approve is recorded in the audit trail; share link is public read-only", async ({ page, browser }) => {
  await runFixture(page, "WMT", "pre_only", "2026-11-18");
  await page.getByRole("tab", { name: "Review & audit" }).click();
  await page.getByLabel(/Notes/).fill("figures spot-checked");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Review: approved")).toBeVisible();
  await expect(page.getByText("review.approved")).toBeVisible();
  await page.getByRole("tab", { name: "Review & audit" }).click();
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]).catch(() => {});
  const shareResp = page.waitForResponse((r) => r.url().includes("/share") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Share" }).click();
  const { url } = await (await shareResp).json();
  const anon = await browser.newPage();
  await anon.goto(url);
  await expect(anon.getByRole("heading", { name: "Decision brief" })).toBeVisible();
  await expect(anon.getByText("shared read-only view")).toBeVisible();
  await expect(anon.getByRole("tab", { name: "Review & audit" })).toHaveCount(0);
  await anon.close();
});

test("keyboard only: command palette to a company profile", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.getByText("Skip to content")).toBeFocused();
  await page.keyboard.press("Control+k");
  await page.keyboard.type("NVDA");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/company\/NVDA/);
  await expect(page.getByRole("heading", { name: /NVDA/ })).toBeVisible();
  await expect(page.getByRole("table", { name: /Past earnings/ })).toBeVisible();
  await noHorizontalScroll(page);
  await axe(page);
});

test("scope guard: advice question gets a research-only redirect", async ({ page }) => {
  await page.goto("/new?ticker=NVDA&mode=pre_only");
  await page.getByRole("textbox", { name: /^Question/ }).fill("Should I buy NVDA before earnings?");
  await page.getByText("Advanced: as-of date, transcript").click();
  await page.getByLabel("As of (historical, no look-ahead)").fill("2026-11-14");
  await page.getByRole("button", { name: "Run research" }).click();
  await expect(page).toHaveURL(/\/reports\//, { timeout: 30_000 });
  await expect(page.getByRole("note")).toContainText("can't tell you what to buy or sell");
});

test("clarification chips for an unknown ticker", async ({ page }) => {
  await page.goto("/new?ticker=NVDAA&mode=pre_only");
  await page.getByText("Advanced: as-of date, transcript").click();
  await page.getByLabel("As of (historical, no look-ahead)").fill("2026-11-14");
  await page.getByRole("button", { name: "Run research" }).click();
  await expect(page.getByRole("group", { name: "Suggested answers" })).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "NVDA", exact: true }).click();
  await expect(page).toHaveURL(/\/reports\//, { timeout: 30_000 });
});
