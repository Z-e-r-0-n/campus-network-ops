import { expect, test, type Page } from "@playwright/test";

async function workspace(page: Page) {
  const stamp = new Date().toISOString(),
    mutations: any[] = [];
  const finding = {
    id: "finding",
    revision: 1,
    status: "open",
    title: "Time reporting needs review",
    rule_id: "C09",
    device_id: "switch",
    summary: "Reporting does not match the reviewed policy.",
  };
  const diagnosis: any = {
    id: "diagnosis",
    revision: 1,
    digest: "a".repeat(64),
    status: "preview",
    created_at: stamp,
    current: true,
    expires_at: stamp,
    packet: {
      devices: [{ id: "switch", label: "Building switch" }],
      observations: [
        {
          citation: "configuration_facts:sample",
          device_id: "switch",
          observed_at: stamp,
          fresh: true,
          facts: { ntp_syslog_delivery_verified: false },
        },
      ],
      gaps: ["No physical inspection is included"],
    },
    routes: [
      { id: "route", name: "Reviewed free provider", model: "example/model" },
    ],
    limits: {
      maximum_calls: 1,
      seconds_per_call: 60,
      output_tokens_per_call: 3000,
    },
    attempts: [],
  };
  let diagnoses: any[] = [];
  const routes: any[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.slice(7),
      method = route.request().method(),
      data = route.request().postDataJSON();
    if (method !== "GET") mutations.push({ path, data });
    let result: any = { items: [] };
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
    else if (path === "/network") result = { devices: [], edges: [] };
    else if (path === "/catalogue")
      result = { checks: [], rules: [], runbooks: [] };
    else if (path === "/installation/readiness") result = {};
    else if (path === "/connectors")
      result = {
        items: [
          {
            id: "gateway",
            name: "OmniRoute",
            kind: "omniroute",
            endpoint: "http://127.0.0.1:9000/v1",
            status: "connected",
          },
        ],
      };
    else if (path === "/recommendations") result = { items: [finding] };
    else if (path === "/diagnosis-history/recommendation/finding")
      result = { items: diagnoses };
    else if (path === "/diagnosis-previews") {
      diagnoses = [diagnosis];
      result = diagnosis;
    } else if (path === "/diagnoses/diagnosis/start") {
      diagnosis.status = "complete";
      diagnosis.revision++;
      diagnosis.attempts = [
        {
          id: "attempt",
          route_id: "route",
          status: "received",
          started_at: stamp,
        },
      ];
      diagnosis.output = {
        summary:
          "Delivery failed; compare the destination with accepted intent.",
        hypotheses: [
          {
            explanation:
              "The destination or its reachability may differ from the accepted policy.",
            confidence: "low",
            citations: ["configuration_facts:sample"],
          },
        ],
        alternatives: [],
        missing_information: ["Current configured destination"],
        next_steps: [
          {
            kind: "review_configuration",
            device_id: "switch",
            description:
              "Collect current time settings before preparing a change.",
            citations: ["configuration_facts:sample"],
            runbook_id: "RB02",
          },
        ],
      };
      result = diagnosis;
    } else if (path === "/ai-routes" && method === "POST") {
      result = {
        id: "route",
        revision: 1,
        digest: "b".repeat(64),
        status: "draft",
        eligible: false,
        content: data,
      };
      routes.push(result);
    } else if (path === "/ai-routes") result = { items: routes };
    else if (path === "/ai-routes/route/approve") {
      routes[0].status = "approved";
      routes[0].eligible = true;
      routes[0].revision++;
      result = routes[0];
    } else if (path === "/ai-routes/route/pause") {
      routes[0].status = "paused";
      routes[0].eligible = false;
      result = routes[0];
    }
    await route.fulfill({ json: result });
  });
  return mutations;
}

test("review evidence before sending and read a cited assessment", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const mutations = await workspace(page);
  await page.goto("/#Configuration%20review");
  await page
    .getByRole("button", { name: /Time reporting needs review/ })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Prepare diagnosis" }).click();
  await expect(
    dialog.getByText("Provider order", { exact: true }),
  ).toBeVisible();
  expect(mutations.some((m) => m.path.endsWith("/start"))).toBeFalsy();
  await expect(
    dialog.getByText("ntp syslog delivery verified", { exact: true }),
  ).toBeVisible();
  await dialog
    .getByLabel("Application password", { exact: true })
    .fill("UI-test-only");
  await dialog.getByRole("button", { name: "Send reviewed evidence" }).click();
  await expect(
    dialog.getByRole("heading", { name: "Model assessment" }),
  ).toBeVisible();
  await expect(
    dialog.getByText("low model confidence", { exact: true }),
  ).toBeVisible();
  await dialog.locator(".ai-citations a").first().click();
  await expect(dialog.locator(".evidence-cards")).toBeVisible();
  await page.screenshot({ path: info.outputPath("diagnosis-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: info.outputPath("diagnosis-mobile.png") });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  expect(mutations.find((m) => m.path.endsWith("/start"))?.data).toEqual({
    digest: "a".repeat(64),
    revision: 1,
  });
  expect(
    mutations.some((m) => /repair-plans|executions/.test(m.path)),
  ).toBeFalsy();
  expect(errors).toEqual([]);
});

test("configure, approve and pause a reviewed provider route", async ({
  page,
}, info) => {
  const mutations = await workspace(page);
  await page.goto("/#Settings");
  await page.getByText("Add a provider route", { exact: true }).click();
  const panel = page.locator(".ai-routes");
  await panel
    .getByLabel("Name", { exact: true })
    .fill("Reviewed free provider");
  await panel.getByLabel("OmniRoute connection").selectOption("gateway");
  await panel.getByLabel("Exact request model").fill("example/model");
  await panel.getByLabel("Expected response model").fill("model");
  await panel.getByLabel("Expected provider identifier").fill("example");
  await panel
    .getByLabel("Provider free-tier terms")
    .fill("https://example.org/free");
  await panel
    .getByLabel("Review expiry (within 30 days)")
    .fill("2026-10-10T12:00");
  await panel
    .getByLabel("How free-only routing and billing were verified")
    .fill(
      "Verified a dedicated free connection and disabled all paid billing.",
    );
  await panel
    .getByLabel("I verified the dedicated gateway configuration")
    .check();
  await panel.getByRole("button", { name: "Prepare route review" }).click();
  await panel
    .getByLabel("Application password to approve this route")
    .fill("UI-test-only");
  await panel.getByRole("button", { name: "Approve route" }).click();
  await expect(panel.getByText("Eligible", { exact: true })).toBeVisible();
  await page.screenshot({
    path: info.outputPath("ai-routes.png"),
    fullPage: true,
  });
  await panel.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(panel.getByText("paused", { exact: true })).toBeVisible();
  expect(
    mutations.find((m) => m.path === "/ai-routes")?.data
      .free_only_gateway_verified,
  ).toBe(true);
});
