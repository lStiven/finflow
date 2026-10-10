/**
 * Connecting a bank, from a brand-new account to its first movement, in a
 * real browser and against the real stack.
 *
 *   just up               # emulator, seeded data, API and workers
 *   just web              # the frontend, in another terminal
 *   just e2e-connect      # this
 *
 * Runs on a phone-sized screen, because that is where the app is used. Every
 * step goes through the rendered page, and after each one the API is asked
 * directly and the two answers compared:
 *
 * 1. **A new account is welcomed once**, and starting lands on the first step.
 * 2. **Choosing a bank approves every domain it sends from**, and nothing
 *    reads as chosen until the server says so.
 * 3. **A webmail domain is refused** before it reaches the server; a pasted
 *    «Name <address>» is approved as that exact address; taking one away asks
 *    first.
 * 4. **The address on screen is the API's**, and «Copiar» puts exactly that on
 *    the clipboard.
 * 5. **Saying the address was added only starts a wait.** The step closes
 *    when Google's confirmation is on record — written here the way the
 *    ingest worker writes it — and the page notices on its own.
 * 6. **The filter is built from the approved senders**, the same text the
 *    intake accepts, and the tutorial walks its six slides.
 * 7. **The first alert is told apart from a movement**: the page celebrates
 *    only once the movement the alert produced exists, and shows that one.
 * 8. **A finished setup opens on its status, not on the guide**, and the mail
 *    a sender nobody approved is named there, with the one tap that lets it
 *    in.
 *
 * Also: no sideways scroll at any step, and no error on the page or from the
 * API beyond the 404s the dashboard asks for on purpose.
 *
 * The account is new on every run and lives in the emulator only; the next
 * `just up` starts from an empty one, so there is nothing to clean up.
 */

import { execFileSync } from "node:child_process";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const WEB = process.env.FINFLOW_WEB_URL ?? "http://localhost:5173";
const API = process.env.FINFLOW_API_URL ?? "http://localhost:8000";
const ROOT = fileURLToPath(new URL("../..", import.meta.url));
const PASSWORD = "una frase larga de verdad";
const RUN = Date.now().toString(36);
const EMAIL = `e2e-conectar-${RUN}@finflow.local`;

/** Every domain the Bancolombia card approves — the parser registry's own. */
const BANCOLOMBIA = [
  "an.notificacionesbancolombia.com",
  "ayn.notificacionesbancolombia.com",
  "bancolombia.com.co",
  "notificacionesbancolombia.com",
];
const BANK_SENDER = "alertasynotificaciones@an.notificacionesbancolombia.com";
/** The 404s the app asks for on purpose, by API path. */
const EXPECTED_404 = new Set(["/financial/plan", "/financial/allowance"]);
/** Google's confirmation is polled every 15 s; the first alert crosses two workers. */
const CONFIRMATION_MS = 40_000;
const ARRIVAL_MS = 60_000;

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

async function reachable(url) {
  try {
    await fetch(url, { signal: AbortSignal.timeout(2000) });
    return true;
  } catch {
    return false;
  }
}

/** The two facts no browser can produce. See `scripts/e2e_connect_fixture.py`. */
function fixture(fact, subject) {
  return execFileSync(
    "uv",
    ["run", "python", "scripts/e2e_connect_fixture.py", fact, subject],
    {
      cwd: ROOT,
      env: { ...process.env, ENV_FILE: ".env", PYTHONPATH: "src" },
      encoding: "utf8",
    },
  ).trim();
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

    if (!response.ok) {
      throw new Error(`${init.method ?? "GET"} ${path} → ${response.status}`);
    }

    return response.json();
  };
}

/** Exactly the text the guide builds: a domain as `@domain`, joined by OR. */
function expectedFilter(inbox) {
  return [
    ...inbox.allowed_domains.map((domain) => `@${domain}`),
    ...inbox.allowed_addresses,
  ].join(" OR ");
}

async function deliver(address, sender, body) {
  const response = await fetch(`${API}/ingestion/bank-notifications`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      recipient: address,
      message_id: `<e2e-conectar-${RUN}-${Math.random().toString(36).slice(2)}@finflow.local>`,
      sender,
      subject: "Notificación de compra",
      raw_content: body,
    }),
  });
  if (!response.ok) throw new Error(`webhook → ${response.status}`);
  return response.json();
}

async function scrollsSideways(page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
}

async function main() {
  for (const [what, url] of [
    ["el frontend", WEB],
    ["la API", API],
  ]) {
    if (!(await reachable(url))) {
      console.error(
        `No encuentro ${what} en ${url}. Levanta \`just up\` y \`just web\`.`,
      );
      process.exitCode = 2;
      return;
    }
  }

  const registered = await fetch(`${API}/identity/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: EMAIL,
      password: PASSWORD,
      verification_token: fixture("ticket", EMAIL),
      name: "Conectar",
    }),
  });
  if (!registered.ok) throw new Error(`registro → ${registered.status}`);
  const call = client((await registered.json()).access_token);

  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  await context.grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: WEB,
  });
  const page = await context.newPage();
  const clipboard = () => page.evaluate(() => navigator.clipboard.readText());

  const problems = [];
  page.on("pageerror", (error) => problems.push(`error: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    // A failed request logs here without its address; the response listener
    // below is the one that decides whether it was one of the expected ones.
    if (message.text().startsWith("Failed to load resource")) return;
    problems.push(`console: ${message.text()}`);
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (!url.href.startsWith(API) || response.status() < 400) return;
    if (response.status() === 404 && EXPECTED_404.has(url.pathname)) return;
    problems.push(`API ${response.status()} ${url.pathname}`);
  });
  const sideways = [];
  async function noSideways(where) {
    if (await scrollsSideways(page)) sideways.push(where);
  }

  try {
    // 1 · Welcomed once, and «Comenzar» lands on the banks.
    await page.goto(`${WEB}/login`, { waitUntil: "domcontentloaded" });
    await page.getByLabel("Correo").fill(EMAIL);
    await page.getByLabel("Contraseña").fill(PASSWORD);
    await page.locator('form button[type="submit"]').click();
    await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
      timeout: 15_000,
    });
    const welcome = page.getByRole("dialog", { name: "Tus gastos, registrados solos" });
    await welcome.waitFor({ timeout: 10_000 });
    check("una cuenta nueva recibe la bienvenida", await welcome.isVisible(), true);
    await welcome.getByRole("button", { name: /Comenzar configuración/ }).click();
    await page.waitForURL(/\/conectar\?paso=1/, { timeout: 10_000 });
    check(
      "«Comenzar configuración» abre el primer paso",
      new URL(page.url()).search,
      "?paso=1",
    );

    // 2 · A bank, all of its domains, drawn only once the server has them.
    const next = page.getByRole("button", { name: /^Continuar/ });
    check("sin bancos no se puede seguir", await next.isDisabled(), true);
    const bancolombia = page.getByRole("button", { name: /^Bancolombia/ });
    await bancolombia.click();
    await page.getByText("Aceptando sus alertas").first().waitFor({ timeout: 10_000 });
    let inbox = await call("/identity/inbox");
    check(
      "elegir Bancolombia aprueba todos sus dominios",
      [...inbox.allowed_domains].sort(),
      BANCOLOMBIA,
    );
    check(
      "y la tarjeta queda marcada",
      await bancolombia.getAttribute("aria-pressed"),
      "true",
    );
    await noSideways("paso 1");

    // 3 · Webmail refused, a pasted sender cleaned, a removal asked first.
    await page.getByRole("button", { name: /Otro banco/ }).click();
    const field = page.getByLabel("¿Desde qué correo te escribe tu banco?");
    await field.fill("gmail.com");
    await page.getByRole("button", { name: "Agregar" }).click();
    const refusal = await page.getByRole("alert").first().innerText();
    check(
      "un dominio de correo personal se rechaza",
      refusal.includes("correo personal"),
      true,
    );
    inbox = await call("/identity/inbox");
    check("y no llega al servidor", inbox.allowed_addresses, []);

    await field.fill('"Mi Banco" <Alertas@MiBanco.com.co>');
    await page.getByRole("button", { name: "Agregar" }).click();
    const custom = page.getByRole("button", {
      name: /alertas@mibanco\.com\.co Agregado/,
    });
    await custom.waitFor({ timeout: 10_000 });
    inbox = await call("/identity/inbox");
    check(
      "«Nombre <dirección>» se aprueba como esa dirección exacta",
      inbox.allowed_addresses,
      ["alertas@mibanco.com.co"],
    );

    await custom.click();
    await page.getByRole("button", { name: "Cancelar" }).click();
    inbox = await call("/identity/inbox");
    check("cancelar no quita nada", inbox.allowed_addresses, [
      "alertas@mibanco.com.co",
    ]);
    await page
      .getByRole("button", { name: /alertas@mibanco\.com\.co Agregado/ })
      .click();
    await page.getByRole("button", { name: "Quitar", exact: true }).click();
    await custom.waitFor({ state: "detached", timeout: 10_000 });
    inbox = await call("/identity/inbox");
    check("quitar, confirmado, sí lo quita", inbox.allowed_addresses, []);

    // 4 · The address is the API's, and «Copiar» copies exactly it.
    await next.click();
    await page.waitForURL(/paso=2/, { timeout: 10_000 });
    const setup = await call("/ingestion/setup");
    const shown = (await page.locator("code").first().innerText()).trim();
    check("la dirección en pantalla es la de la API", shown, setup.address);
    await page.getByRole("button", { name: /Copiar dirección/ }).click();
    check("«Copiar dirección» la copia tal cual", await clipboard(), setup.address);
    check(
      "en un teléfono avisa que es un paso de computador",
      await page.getByText("Este paso se hace en un computador").isVisible(),
      true,
    );
    await noSideways("paso 2");

    // 5 · «Ya la agregué» waits; Google's confirmation closes it.
    await page.getByRole("button", { name: /Ya la agregué/ }).click();
    await page.getByText("Esperando confirmación de Gmail").waitFor({ timeout: 5_000 });
    const pending = await call("/ingestion/setup");
    check(
      "decir que se agregó no la da por confirmada",
      pending.steps.find((step) => step.key === "forwarding_confirmed")?.done,
      false,
    );
    fixture("confirm", setup.address);
    await page
      .getByText("¡Gmail confirmó tu dirección!")
      .waitFor({ timeout: CONFIRMATION_MS });
    check(
      "la pantalla se entera sola de la confirmación",
      await page.getByText("Inhabilitar el reenvío").first().isVisible(),
      true,
    );

    // 6 · The filter is the approved senders, and the tutorial walks.
    await page.getByRole("button", { name: /^Continuar/ }).click();
    await page.waitForURL(/paso=3/, { timeout: 10_000 });
    inbox = await call("/identity/inbox");
    const filter = (await page.locator("code").first().innerText()).trim();
    check("el filtro es el de los remitentes aprobados", filter, expectedFilter(inbox));
    await page.getByRole("button", { name: /Copiar filtro/ }).click();
    check(
      "«Copiar filtro» lo copia tal cual",
      await clipboard(),
      expectedFilter(inbox),
    );
    const tutorial = page.getByRole("region", {
      name: "Cómo crear el filtro en Gmail",
    });
    for (let slide = 0; slide < 5; slide += 1) {
      await tutorial.getByRole("button", { name: "Siguiente", exact: true }).click();
    }
    check(
      "el tutorial llega a su sexta pantalla",
      (
        await tutorial
          .getByText(/^\d de 6$/)
          .filter({ visible: true })
          .innerText()
      ).trim(),
      "6 de 6",
    );
    await tutorial.getByRole("button", { name: "Anterior", exact: true }).click();
    check(
      "y vuelve atrás",
      (
        await tutorial
          .getByText(/^\d de 6$/)
          .filter({ visible: true })
          .innerText()
      ).trim(),
      "5 de 6",
    );
    await noSideways("paso 3");

    await page.getByRole("button", { name: /Ya creé el filtro/ }).click();
    await page.waitForURL(/paso=4/, { timeout: 10_000 });
    await page.getByText("Esperando tu primera alerta").waitFor({ timeout: 10_000 });
    check(
      "el filtro queda como palabra de la persona, no como comprobado",
      await page.getByText("Lo marcaste tú").isVisible(),
      true,
    );
    await noSideways("paso 4");

    // 7 · The alert, and the movement it became.
    await deliver(
      setup.address,
      BANK_SENDER,
      "Bancolombia: Compraste $45.000 en EXITO SUPERINTER CALI con tu T.Cred *1234, el 08/10/2026 a las 10:15",
    );
    await page
      .getByRole("heading", { name: "¡Finflow ya está funcionando!" })
      .waitFor({ timeout: ARRIVAL_MS });
    const latest = await call("/financial/transactions?origin=bank_alert&limit=1");
    const movement = latest.transactions[0];
    check(
      "la tarjeta es el movimiento que dejó la alerta",
      await page
        .locator(`a[href="/transacciones/${movement?.id}"]`)
        .first()
        .isVisible(),
      true,
    );
    check("con su monto", movement?.amount, "45000");
    check(
      "y sin un diálogo encima diciendo lo mismo",
      await page.getByRole("dialog").count(),
      0,
    );

    // 8 · Finished: the status, not the guide — and a stranger named there.
    await page.getByRole("button", { name: "Ver el estado de la conexión" }).click();
    await page
      .getByRole("heading", { level: 1, name: "Tus bancos están conectados" })
      .waitFor({ timeout: 10_000 });
    check(
      "lista el correo que llegó como registrado",
      await page.getByText("Registrado", { exact: true }).first().isVisible(),
      true,
    );
    await noSideways("estado de la conexión");

    await deliver(setup.address, "alertas@otrobanco.com.co", "Su compra fue aprobada.");
    await page.reload({ waitUntil: "networkidle" });
    await page
      .getByRole("heading", { level: 1, name: "Se están descartando correos" })
      .waitFor({ timeout: 15_000 });
    await page.getByRole("button", { name: "Es de mi banco" }).first().click();
    await page
      .getByRole("heading", { level: 1, name: "Tus bancos están conectados" })
      .waitFor({ timeout: 10_000 });
    inbox = await call("/identity/inbox");
    check("«Es de mi banco» aprueba esa dirección exacta", inbox.allowed_addresses, [
      "alertas@otrobanco.com.co",
    ]);

    await page.goto(`${WEB}/conectar`, { waitUntil: "networkidle" });
    check(
      "volver a Conectar abre el estado, no la guía",
      await page.getByRole("heading", { level: 1 }).innerText(),
      "Tus bancos están conectados",
    );
    await page.goto(`${WEB}/`, { waitUntil: "networkidle" });
    check(
      "y el Resumen no vuelve a celebrar",
      await page.getByRole("dialog").count(),
      0,
    );

    check("ninguna pantalla se va de lado", sideways, []);
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
      ? "\ne2e conectar: todo bien. De una cuenta nueva a su primer movimiento, sin pasos falsos."
      : `\ne2e conectar: ${failures} problema(s).`,
  );
  process.exitCode = failures === 0 ? 0 : 1;
}

await main();
