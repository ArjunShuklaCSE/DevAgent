import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/** When set, full-page screenshots for the README are written to docs/images. */
const SHOTS = process.env.DEVAGENT_SCREENSHOTS === "1";
const IMAGES = path.resolve(__dirname, "../../docs/images");

async function shot(page: Page, name: string) {
  if (!SHOTS) return;
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(IMAGES, `${name}.png`), fullPage: true });
}

const ISSUE = {
  title: "slugify crashes on titles without letters",
  body: 'slugify("") and slugify("!!!") raise IndexError: list index out of range.\n\nThe docstring says empty input gives an empty slug.',
  number: 3,
};

test("a scripted run is followed live, reviewed and approved in the browser", async ({
  page,
  request,
}) => {
  // Seed: the bundled `slugger` sample and a run on the scripted model.
  const repo = await request.post("/api/v1/repositories", { data: { sample: "slugger" } });
  expect(repo.status()).toBe(201);
  const created = await request.post("/api/v1/runs", {
    data: { repository_id: (await repo.json()).id, issue: ISSUE, model: "scripted" },
  });
  expect(created.status()).toBe(201);
  const runId: string = (await created.json()).id;

  // Home page: form and system status render.
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Fix an issue" })).toBeVisible();
  await expect(page.getByText("All systems operational")).toBeVisible();
  await expect(page.getByRole("link", { name: /slugify crashes/ }).first()).toBeVisible();

  // Run detail: live updates until the agent stops for approval.
  await page.goto(`/runs/${runId}`);
  await expect(page.getByRole("heading", { name: ISSUE.title })).toBeVisible();
  await expect(page.getByText(/^(Live|Connecting)$/).first()).toBeVisible();
  const timeline = page.getByRole("list", { name: "Run timeline" });
  await expect(timeline.getByText("Cloning")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByLabel("Terminal output")).toContainText("passed", { timeout: 120_000 });
  await expect(page.getByText("Awaiting approval").first()).toBeVisible({ timeout: 240_000 });
  await expect(timeline.getByText("Debugging")).toBeVisible();
  await expect(timeline.getByText("Validating")).toBeVisible();
  await page.getByLabel("Terminal output").evaluate((el) => el.scrollTo(0, 0));
  await shot(page, "run-detail");

  for (const [tab, expected] of [
    ["Tool calls", "edit_file"],
    ["LLM calls", "Pr writer"],
    ["Tests", "Reproduction"],
    ["Plan", "Reproduction"],
  ] as const) {
    await page.getByRole("tab", { name: tab }).click();
    await expect(page.getByText(expected).first()).toBeVisible();
    if (tab === "LLM calls") await shot(page, "run-llm-calls");
    if (tab === "Plan") await shot(page, "run-plan");
  }

  // Review: the diff, validation and the approval bound to the diff hash.
  await page.getByRole("link", { name: "Review and approve" }).click();
  await expect(page.getByText("slugger/slug.py").first()).toBeVisible();
  await expect(page.getByText("tests/test_slug_empty.py").first()).toBeVisible();
  await expect(page.getByText('return ""').first()).toBeVisible();
  await shot(page, "review");
  await page.getByRole("button", { name: "Unified" }).click();
  await page.getByLabel("Review comment").fill("Reproduced locally; the guard is right.");
  await page.getByRole("button", { name: "Approve" }).click();
  // A sample repository has no GitHub remote: the fix is delivered as a patch file.
  await expect(page.getByText("No pull request was opened")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/local sample repository/)).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download patch" }).click();
  expect((await download).suggestedFilename()).toBe("devagent-issue-3.patch");
  await shot(page, "review-approved");

  // History and the evaluation placeholder.
  await page.goto("/runs");
  await expect(page.getByRole("heading", { name: "Runs" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: ISSUE.title }).first()).toBeVisible();
  await shot(page, "runs");
  await page.goto("/");
  await expect(page.getByText("All systems operational")).toBeVisible();
  await shot(page, "home");
  await page.goto("/evaluation");
  await expect(page.getByText("No data yet")).toBeVisible();

  // Light theme toggle persists across navigation.
  await page.goto(`/runs/${runId}`);
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  await page.reload();
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  await expect(page.getByRole("heading", { name: ISSUE.title })).toBeVisible();
  await shot(page, "run-detail-light");
});

test("an unknown run shows an error state, not a blank page", async ({ page }) => {
  await page.goto("/runs/00000000-0000-0000-0000-000000000000");
  await expect(
    page.getByRole("alert").filter({ hasText: "Could not load this run" }),
  ).toContainText("not found");
});
