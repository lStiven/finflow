/**
 * Drive the merchants review queue in a real browser, and check the API.
 *
 *   just up               # emulator, seeded data, API and workers
 *   just web              # the frontend, in another terminal
 *   just e2e-comercios    # this
 *
 * UX-12 made both common answers to «¿es este negocio?» one tap, in the list:
 *
 * 1. **Each merchant waiting for review offers both answers in one row**, at
 *    44 px on a phone: its category as a control, and «Está bien».
 * 2. **«Está bien» confirms it** in the API, and a failure says so instead of
 *    leaving the row exactly where it was.
 * 3. **Choosing a category is the answer**: it saves at once, the API has
 *    the one chosen, correcting it also counts as reviewing it, and the
 *    screen says so above the list.
 * 4. Nothing scrolls sideways at 390 or 320 px, and nothing logs an error.
 *
 * Reviewing cannot be undone, so the seeded queue is left alone: this
 * registers its own person, approves their bank, and delivers two alerts
 * through the local webhook — the same seam the connect suite uses — so the
 * merchants it reviews are its own. That person stays in the emulator.
 */

import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const WEB = process.env.FINFLOW_WEB_URL ?? "http://localhost:5173";
const API = process.env.FINFLOW_API_URL ?? "http://localhost:8000";
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const RUN = Date.now().toString(36);

const BANK_DOMAIN = "an.notificacionesbancolombia.com";
const BANK_SENDER = `alertasynotificaciones@${BANK_DOMAIN}`;
/** How long the workers may take to turn an alert into a merchant. */
const ARRIVAL_MS = 90_000;

const steps = [];
let failures = 0;

function check(what, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  steps.push(`${ok ? "  ok  " : " FALLA"} ${what}`);

  if (!ok) {
    failures += 1;
    steps.push(`        esperaba: ${JSON.stringify(expected)}`);
    steps.push(`        recibió:  ${JSON.stringify(actual)}`);
  }

  return ok;
}

function note(text) {
  steps.push(`  ··   ${text}`);
}

async function reachable(url) {
  try {
    await fetch(url, { signal: AbortSignal.timeout(2000) });
    return true;
  } catch {
    return false;
  }
}

function client(token) {
  return async (path, init = {}) => {
    const response = await fetch(`${API}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        ...(init.headers ?? {}),
      },
    });

    if (!response.ok && response.status !== 204) {
      throw new Error(`${init.method ?? "GET"} ${path} → ${response.status}`);
    }

    return response.status === 204 ? null : response.json();
  };
}

/**
 * A whole account, through the registration use case rather than the
 * endpoint: the endpoint allows ten a quarter hour per address, which a full
 * browser run would exhaust on its own. See `scripts/e2e_connect_fixture.py`.
 */
function person(email, password) {
  return execFileSync(
    "uv",
    [
      "run",
      "python",
      "scripts/e2e_connect_fixture.py",
      "person",
      email,
      "--password",
      password,
    ],
    {
      cwd: ROOT,
      env: { ...process.env, ENV_FILE: ".env", PYTHONPATH: "src" },
      encoding: "utf8",
    },
  ).trim();
}

/** A bank alert, through the local testing webhook. Local only, by design. */
async function deliver(address, body) {
  const response = await fetch(`${API}/ingestion/bank-notifications`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      recipient: address,
      message_id: `<e2e-comercios-${RUN}-${Math.random().toString(36).slice(2)}@finflow.local>`,
      sender: BANK_SENDER,
      subject: "Notificación de compra",
      raw_content: body,
    }),
  });
  if (!response.ok) throw new Error(`webhook → ${response.status}`);
}

async function until(read, done, timeout = ARRIVAL_MS) {
  const deadline = Date.now() + timeout;
  for (;;) {
    const value = await read();
    if (done(value)) return value;
    if (Date.now() > deadline) return value;
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
}

const sideways = (page) =>
  page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);

/**
 * Picks an option in the app's own Select, the way a person does: open it by
 * its label, press the option. The list lives in a portal, so it is found on
 * the page, by the same label.
 */
async function choose(page, scope, label, pick) {
  await scope.getByRole("combobox", { name: label, exact: true }).click();
  const listbox = page.getByRole("listbox", { name: label, exact: true });
  const option =
    "value" in pick
      ? listbox.locator(`[role="option"][data-value="${pick.value}"]`)
      : "label" in pick
        ? listbox.getByRole("option", { name: pick.label, exact: true })
        : listbox.getByRole("option").nth(pick.index);
  await option.click();
}

async function main() {
  for (const [what, url] of [
    ["el frontend", WEB],
    ["la API", API],
  ]) {
    if (!(await reachable(url))) {
      console.error(
        `No hay nada escuchando en ${url} (${what}).\n` +
          "  just up     # emulador, datos de prueba, la API y los procesos\n" +
          "  just web    # el frontend, en otra terminal",
      );
      process.exitCode = 1;

      return;
    }
  }

  const email = `e2e-comercios-${RUN}@finflow.local`;
  const password = "una frase larga de prueba";
  const call = client(person(email, password));

  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    locale: "es-CO",
    timezoneId: "America/Bogota",
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  const problems = [];
  let forcing = false;
  page.on("pageerror", (error) => problems.push(`error: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    if (message.text().includes("404 (Not Found)")) return;
    if (forcing && message.text().includes("500")) return;
    problems.push(`console: ${message.text()}`);
  });

  try {
    // ------------------------------------- two merchants waiting for review
    await call("/identity/inbox", {
      method: "PATCH",
      body: JSON.stringify({ allowed_domains: [BANK_DOMAIN], allowed_addresses: [] }),
    });
    const { address } = await call("/ingestion/setup");
    await deliver(
      address,
      "Bancolombia: Compraste $45.000 en EXITO SUPERINTER CALI con tu T.Cred *1234, el 08/10/2026 a las 10:15",
    );
    await deliver(
      address,
      "Bancolombia: Compraste $18.900 en TIENDAS ARA 123 con tu T.Cred *1234, el 08/10/2026 a las 11:40",
    );

    const queue = await until(
      () => call("/merchants?needs_review=true&limit=10"),
      (view) => view.merchants.length >= 2,
    );
    check("las dos alertas dejan dos comercios por revisar", queue.merchants.length, 2);
    const [first, second] = queue.merchants;
    if (!first || !second) throw new Error("los comercios no llegaron a tiempo");
    note(`«${first.display_name}» y «${second.display_name}»`);

    // ---------------------------------------------------------------- the UI
    await page.goto(`${WEB}/login`, { waitUntil: "domcontentloaded" });
    await page.getByLabel("Correo").fill(email);
    await page.getByLabel("Contraseña").fill(password);
    await page.locator('form button[type="submit"]').click();
    await page.waitForURL((url) => !url.pathname.startsWith("/login"));
    await page.evaluate(() => {
      const { userId } = JSON.parse(window.localStorage.getItem("finflow.session"));
      window.localStorage.setItem(
        `finflow.onboarding.${userId}`,
        JSON.stringify({
          welcomeSeen: true,
          introSeen: true,
          addressCopied: true,
          gmailSubmitted: true,
          readyCelebrated: true,
        }),
      );
      window.localStorage.setItem(
        `finflow.help.${userId}`,
        JSON.stringify(["comercios"]),
      );
    });
    await page.goto(`${WEB}/comercios?review=true`, { waitUntil: "networkidle" });

    const okFirst = page.getByRole("button", {
      name: `Está bien ${first.display_name}`,
    });
    const categoryOfSecond = page.getByLabel(`Categoría de ${second.display_name}`, {
      exact: true,
    });
    const heights = [];
    for (const button of [okFirst, categoryOfSecond]) {
      const box = await button.boundingBox();
      heights.push(box ? Math.round(box.height) >= 44 : false);
    }
    check("cada comercio ofrece sus dos respuestas en su fila, a 44 px", heights, [
      true,
      true,
    ]);

    // ---------------------------------------- a failed confirm is said
    forcing = true;
    await page.route(`${API}/merchants/${first.id}/confirm`, (route) =>
      route.fulfill({ status: 500, contentType: "application/json", body: "{}" }),
    );
    await okFirst.click();
    const said = page.getByRole("alert");
    await said.first().waitFor({ timeout: 10_000 });
    check("si confirmar falla, la fila lo dice", await said.first().isVisible(), true);
    await page.unroute(`${API}/merchants/${first.id}/confirm`);
    forcing = false;

    // ------------------------------------------------- «Está bien» confirms
    await okFirst.click();
    const confirmed = await until(
      () => call(`/merchants/${first.id}`),
      (merchant) => merchant.needs_review === false,
      10_000,
    );
    check("«Está bien» lo confirma en la API", confirmed.needs_review, false);
    await okFirst.waitFor({ state: "detached", timeout: 10_000 }).catch(() => {});
    check("y sale de la cola en pantalla", await okFirst.count(), 0);

    // ------------------------------- choosing the category is the answer
    const before = (await call(`/merchants/${second.id}`)).category;
    const target = before === "restaurants" ? "education" : "restaurants";
    await choose(page, page, `Categoría de ${second.display_name}`, { value: target });
    const fixed = await until(
      () => call(`/merchants/${second.id}`),
      (merchant) => merchant.category === target,
      10_000,
    );
    check(
      "elegir la categoría la guarda en la API, y eso también lo revisa",
      [fixed.category, fixed.needs_review],
      [target, false],
    );
    await page
      .getByRole("status")
      .filter({ hasText: `Listo: ${second.display_name} quedó en` })
      .waitFor({ timeout: 10_000 })
      .catch(() => {});
    check(
      "y la pantalla lo dice arriba de la lista",
      await page
        .getByRole("status")
        .filter({ hasText: `Listo: ${second.display_name} quedó en` })
        .count(),
      1,
    );
    check("sin salir de la lista", new URL(page.url()).pathname, "/comercios");

    check("a 390 px Comercios no se va de lado", await sideways(page), false);
    await page.setViewportSize({ width: 320, height: 640 });
    check("ni a 320 px", await sideways(page), false);

    check("la pantalla no registró errores", problems, []);
  } catch (error) {
    failures += 1;
    steps.push(` FALLA ${error.message}`);
  } finally {
    await browser.close();
  }

  console.log(steps.join("\n"));
  console.log(
    failures === 0
      ? "\ne2e comercios: todo bien. Las dos respuestas de la cola, sin salir de la lista."
      : `\ne2e comercios: ${failures} problema(s).`,
  );
  process.exitCode = failures === 0 ? 0 : 1;
}

await main();
