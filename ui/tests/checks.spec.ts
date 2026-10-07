import { expect, test, type Page } from "@playwright/test";

async function workspace(page: Page, includeExcluded = false) {
  const stamp = new Date().toISOString();
  const devices = [
    {
      id: "source",
      revision: 1,
      label: "Observation host",
      status: "accepted",
      role: "probe",
      addresses: ["192.0.2.10"],
    },
    {
      id: "target",
      revision: 1,
      label: "Building switch",
      status: "accepted",
      role: "access",
      addresses: ["192.0.2.20"],
    },
  ];
  const source = {
    id: "source",
    revision: 1,
    source_address: "192.0.2.10",
    verified_local: true,
    status: "verified",
    binding: { interface: "eth0", index: 1, worker: "test-worker" },
  };
  const policies: any[] = [
    {
      id: "latency",
      revision: 1,
      check_id: "H02",
      source_id: "source",
      target_id: "target",
      target_address: "192.0.2.20",
      source: devices[0],
      target: devices[1],
      probe: source,
      enabled: true,
      interval_seconds: 300,
      parameters: {
        max_rtt_ms: 40,
        samples: 10,
        max_age_seconds: 900,
        window_seconds: 3600,
      },
      latest_result: {
        id: "result",
        status: "passed",
        observed_at: stamp,
        summary: "95th percentile RTT: 12 ms; limit 40 ms.",
        metrics: {
          received: 120,
          p95_rtt_ms: 12,
          median_rtt_ms: 4,
          window_start: stamp,
          window_end: stamp,
        },
        evidence_ids: ["e1", "e2"],
      },
    },
  ];
  const mutations: { path: string; data: any; method: string }[] = [];
  let excluded: any = includeExcluded
    ? {
        id: "excluded",
        revision: 3,
        label: "Excluded switch",
        addresses: ["192.0.2.30"],
        status: "excluded",
        reason: "Outside the managed network",
      }
    : null;
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname.slice(7),
      method = route.request().method();
    const data = route.request().postDataJSON();
    let result: any = { items: [] };
    if (method !== "GET") mutations.push({ path, data, method });
    if (path === "/setup/status") result = { configured: true };
    else if (path === "/session") result = { actor: "Test operator" };
    else if (path === "/overview")
      result = {
        counts: {},
        statuses: [],
        activity: [],
        runtime: [],
        at: stamp,
      };
    else if (path === "/network") result = { devices, edges: [] };
    else if (path === "/devices")
      result = { items: devices, next_cursor: null };
    else if (path === "/excluded-devices")
      result = { items: excluded ? [excluded] : [], next_cursor: null };
    else if (path === "/devices/excluded/disposition") {
      result = { ...excluded, ...data, revision: 4 };
      excluded = null;
    } else if (path === "/catalogue")
      result = { checks: [], rules: [], runbooks: [] };
    else if (path === "/installation/readiness") result = {};
    else if (path === "/probes") result = { items: [source] };
    else if (path === "/measurements")
      result = { items: policies, next_cursor: null };
    else if (path === "/check-policies" && method === "POST") {
      result = {
        ...data,
        id: "new",
        revision: 1,
        source: devices[0],
        target: devices.find((d) => d.id === data.target_id),
        probe: source,
      };
      policies.push(result);
    } else if (path.endsWith("/pause")) {
      policies[0].enabled = false;
      result = policies[0];
    } else if (path.endsWith("/review")) {
      const policy = policies.find((p) => path.includes(p.id))!;
      const {
        check_id,
        source_id,
        target_id,
        target_address,
        parameters,
        interval_seconds,
      } = policy;
      result = {
        id: "review",
        revision: 1,
        digest: "a".repeat(64),
        expires_at: stamp,
        content: {
          policy: {
            check_id,
            source_id,
            target_id,
            target_address,
            parameters,
            interval_seconds,
          },
          context: { probe: source },
          budget: {
            packets_per_run: parameters.samples,
            max_duration_seconds: 23,
          },
        },
      };
    } else if (path.endsWith("/approve")) {
      policies.at(-1).enabled = true;
      result = policies.at(-1);
    } else if (path.endsWith("/results"))
      result = { items: [policies[0].latest_result] };
    await route.fulfill({ json: result });
  });
  await page.goto("/#Network");
  await page.getByRole("tab", { name: "Checks", exact: true }).click();
  await expect(
    page.getByText("Every measurement has a point of view."),
  ).toBeVisible();
  return { mutations };
}

test("path measurements remain readable and expose pause and history", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const { mutations } = await workspace(page);
  await expect(
    page.getByText("95th percentile RTT: 12 ms; limit 40 ms."),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("measurements-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.locator(".measurement-card .badge")).toHaveText("paused");
  expect(mutations.some((m) => m.path.endsWith("/pause"))).toBeTruthy();
  await page.getByRole("button", { name: "Results", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("2 records");
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: testInfo.outputPath("measurements-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});

test("create, review and approve an explicit loss budget", async ({
  page,
}, testInfo) => {
  const { mutations } = await workspace(page);
  await page.getByRole("button", { name: "Create check", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByRole("combobox", { name: "Destination", exact: true })
    .selectOption("target");
  await dialog.getByLabel("Maximum accepted loss · %").fill("1");
  await dialog.getByRole("button", { name: "Save paused policy" }).click();
  const draft = page
    .locator(".measurement-card")
    .filter({ hasText: "Path reachability" });
  await draft.getByRole("button", { name: "Review and start" }).click();
  await expect(dialog).toContainText("Packet loss ≤ 1%");
  await expect(dialog).toContainText("10 ICMP packets every 300 seconds");
  await page.screenshot({
    path: testInfo.outputPath("measurement-review.png"),
  });
  await dialog
    .getByLabel("Confirm your application password")
    .fill("UI-test-only");
  await dialog
    .getByRole("button", { name: "Approve and start schedule" })
    .click();
  await expect(dialog).not.toBeVisible();
  const creation = mutations.find((m) => m.path === "/check-policies")!;
  expect(creation.data.enabled).toBe(false);
  expect(creation.data.target_address).toBe("192.0.2.20");
  expect(creation.data.parameters.max_loss_percent).toBe(1);
  expect(mutations.find((m) => m.path.endsWith("/approve"))?.data).toEqual({
    digest: "a".repeat(64),
    revision: 1,
  });
});

test("source verification includes the selected accepted identity and address", async ({
  page,
}) => {
  const { mutations } = await workspace(page);
  await page.getByRole("button", { name: "Verify a source" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Verify source", exact: true })
    .click();
  expect(mutations.find((m) => m.path === "/probe-requests")?.data).toEqual({
    source_id: "source",
    source_address: "192.0.2.10",
  });
});

test("excluded identities can be restored to draft with a reason", async ({
  page,
}) => {
  const { mutations } = await workspace(page, true);
  await page.getByRole("tab", { name: "Devices", exact: true }).click();
  await page.getByText("Excluded identities", { exact: true }).click();
  await page.getByRole("button", { name: /Excluded switch/ }).click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("Reason")
    .fill("Return this switch for topology review");
  await dialog
    .getByRole("button", { name: "Restore to draft inventory" })
    .click();
  await expect(dialog).not.toBeVisible();
  expect(
    mutations.find((m) => m.path === "/devices/excluded/disposition")?.data,
  ).toEqual({
    status: "draft",
    reason: "Return this switch for topology review",
  });
});
