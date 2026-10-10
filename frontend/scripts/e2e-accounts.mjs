/**
 * Drive the accounts screen in a real browser, and check what is behind it.
 *
 *   just up             # emulator, seeded data, API and workers
 *   just web            # the frontend, in another terminal
 *   just e2e-cuentas    # this
 *
 * What UX-08 changed, each checked against the API rather than the screen:
 *
 * 1. **The figures say what they are made of.** Patrimonio, Tienes and Debes
 *    are the API's net worth, and «Debes» names the loans it leaves out.
 * 2. **What an account still needs comes first.** One that emails with no
 *    card linked says so and opens the place to link it; linking there is
 *    stored on the account and the warning goes. A loan with no terms asks
 *    for them, says it is watched exactly once, and offers no alerts.
 * 3. **One section open at a time**, with 44 px targets, and the credit bar
 *    readable by a screen reader as the share of the limit in use.
 * 4. **The tab and the wizard step live in the address**: back undoes a tab,
 *    back walks the wizard, and a declared account exists in the API.
 * 5. Nothing scrolls sideways at 390 or 320 px, and nothing logs an error.
 *
 * Accounts cannot be deleted, so this registers its own person — like the
 * connect and budgets suites — and leaves them behind in the emulator rather
 * than adding three test accounts to the seeded one.
 */

import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const WEB = process.env.FINFLOW_WEB_URL ?? "http://localhost:5173";
const API = process.env.FINFLOW_API_URL ?? "http://localhost:8000";
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

const SAVINGS = "E2E Ahorros";
const CARD = "E2E Tarjeta";
const LOAN = "E2E Préstamo";

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

async function signIn(page, email, password) {
  await page.goto(`${WEB}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("Correo").fill(email);
  await page.getByLabel("Contraseña").fill(password);
  await page.locator('form button[type="submit"]').click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
    timeout: 15_000,
  });
  // The onboarding dialogs are modal; this suite is about accounts.
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
  });
}

const sideways = (page) =>
  page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);

/** How a figure reads in Colombian pesos, digits only: `1.000.000`. */
const digits = (amount) => Number(amount).toLocaleString("es-CO");

async function main() {
  for (const [what, url] of [
    ["el frontend", WEB],
    ["la API", API],
  ]) {
    if (!(await reachable(url))) {
      console.error(
        `No hay nada escuchando en ${url} (${what}).\n` +
          "  just up     # emulador, datos de prueba y la API\n" +
          "  just web    # el frontend, en otra terminal",
      );
      process.exitCode = 1;

      return;
    }
  }

  const email = `e2e-cuentas-${Date.now()}@finflow.local`;
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
  page.on("pageerror", (error) => problems.push(`error: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    if (message.text().includes("404 (Not Found)")) return;
    problems.push(`console: ${message.text()}`);
  });

  try {
    const open = (body) =>
      call("/financial/accounts", {
        method: "POST",
        body: JSON.stringify({ currency: "COP", ...body }),
      });
    const savings = await open({
      name: SAVINGS,
      kind: "savings",
      bank: "bancolombia",
      opening_balance: "1000000",
    });
    await open({
      name: CARD,
      kind: "credit_card",
      bank: "bancolombia",
      instrument_kind: "credit_card",
      last_four: "4321",
      opening_balance: "500000",
      credit_limit: "2000000",
    });
    await open({ name: LOAN, kind: "loan", opening_balance: "10000000" });

    await signIn(page, email, password);
    await page.goto(`${WEB}/cuentas`, { waitUntil: "networkidle" });
    // «Cómo funciona» opens on a first visit; it is not what this suite reads.
    await page.getByRole("button", { name: "Entendido" }).click();

    // ------------------------------------------------- the three figures
    const worth = (await call("/financial/accounts")).net_worth[0];
    const strip = (await page.getByLabel("Tu posición").innerText()).replace(
      /\s+/g,
      " ",
    );
    check(
      "Patrimonio, Tienes y Debes son las cifras de la API",
      [worth.total, worth.assets, worth.liabilities].map((figure) =>
        strip.includes(digits(figure)),
      ),
      [true, true, true],
    );
    check(
      "y el préstamo vigilado se queda fuera de Patrimonio",
      Number(worth.total),
      1_000_000 - 500_000,
    );
    check(
      "«Debes» dice qué deja fuera",
      strip.includes("Tus tarjetas; sin el crédito que solo vigilas"),
      true,
    );

    const card = (name) =>
      page
        .locator("div.surface")
        .filter({ has: page.getByText(name, { exact: true }) });

    // -------------------------------------- the loan: terms first, watched once
    const loan = card(LOAN);
    check(
      "un préstamo sin condiciones pide sus intereses",
      await loan.getByText("Falta decir qué intereses te cobran").isVisible(),
      true,
    );
    check(
      "dice una sola vez que solo se vigila",
      await loan.getByText("Solo la vigilas", { exact: false }).count(),
      1,
    );
    check(
      "y no ofrece enlazar alertas que nunca llegan",
      await loan.getByRole("button", { name: /^Alertas/ }).count(),
      0,
    );

    // --------------------------------------- the card: the limit bar reads
    const bar = card(CARD).getByRole("progressbar", { name: "Cupo usado" });
    check(
      "la barra del cupo dice cuánto se usa (500.000 de 2.000.000)",
      await bar.getAttribute("aria-valuenow"),
      "25",
    );

    // ------------------------- savings: nothing reaches it, and fixing that
    const ahorro = card(SAVINGS);
    check(
      "una cuenta sin alertas lo dice primero",
      await ahorro.getByText("Ninguna alerta cae aquí todavía").isVisible(),
      true,
    );
    await ahorro.getByRole("button", { name: "Enlazar su tarjeta o cuenta" }).click();
    const alerts = ahorro.getByRole("button", { name: /^Alertas/ });
    check(
      "el aviso abre la sección de alertas",
      await alerts.getAttribute("aria-expanded"),
      "true",
    );

    const section = page.locator(`[id="${savings.id}-alerts"]`);
    await section.getByLabel("Últimos cuatro").fill("0530");
    await section.getByLabel("¿Cómo llegan esas alertas?").selectOption("account");
    await section.getByRole("button", { name: /^Enlazar/ }).click();
    await section.getByText("Enlazada.", { exact: false }).waitFor({ timeout: 10_000 });

    const linked = await call(`/financial/accounts/${savings.id}`);
    check("la API guardó la cuenta enlazada", linked.instruments.length, 1);
    check(
      "y el aviso se fue",
      await ahorro.getByText("Ninguna alerta cae aquí todavía").count(),
      0,
    );

    const unlink = section.getByRole("button", { name: /^Desenlazar/ });
    const box = await unlink.boundingBox();
    check(
      "desenlazar es un objetivo de 44 px",
      box ? [Math.round(box.width), Math.round(box.height)] : null,
      [44, 44],
    );

    await ahorro.getByRole("button", { name: "Ajustes" }).click();
    check(
      "abrir Ajustes cierra Alertas: una sección a la vez",
      [
        await alerts.getAttribute("aria-expanded"),
        await ahorro
          .getByRole("button", { name: "Ajustes" })
          .getAttribute("aria-expanded"),
      ],
      ["false", "true"],
    );

    check("a 390 px la pantalla no se va de lado", await sideways(page), false);
    await page.setViewportSize({ width: 320, height: 640 });
    check("ni a 320 px", await sideways(page), false);
    await page.setViewportSize({ width: 390, height: 844 });

    // ---------------------------------------- the tab lives in the address
    await page.getByRole("tab", { name: "Cerradas" }).click();
    await page.waitForURL((url) => url.searchParams.get("ver") === "cerradas");
    check(
      "«Cerradas» queda en la dirección",
      new URL(page.url()).searchParams.get("ver"),
      "cerradas",
    );
    await page.goBack({ waitUntil: "networkidle" });
    check(
      "y «atrás» vuelve a «Abiertas»",
      await page.getByRole("tab", { name: "Abiertas" }).getAttribute("aria-selected"),
      "true",
    );

    // ---------------------------------------- the wizard walks with back
    await page.goto(`${WEB}/cuentas/nueva`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: /^Efectivo/ }).click();
    await page.waitForURL((url) => url.searchParams.get("paso") === "2");
    check("elegir el tipo lleva al paso 2 en la dirección", true, true);
    await page.goBack();
    await page.waitForURL((url) => !url.searchParams.has("paso"));
    check(
      "«atrás» del navegador vuelve a elegir el tipo",
      await page.getByRole("button", { name: /^Efectivo/ }).isVisible(),
      true,
    );
    await page.getByRole("button", { name: /^Efectivo/ }).click();
    await page.getByRole("button", { name: "Continuar" }).click();
    await page.waitForURL((url) => url.searchParams.get("paso") === "3");
    await page.getByRole("button", { name: "Sí, crear la cuenta" }).click();
    await page.getByText("Listo, ya tienes Efectivo").waitFor({ timeout: 10_000 });

    const every = await call("/financial/accounts?scope=all");
    check(
      "la cuenta declarada existe en la API",
      every.accounts.some((account) => account.kind === "cash"),
      true,
    );

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
      ? "\ne2e cuentas: todo bien. Cada cuenta dice lo que le falta, y las cifras son las de la API."
      : `\ne2e cuentas: ${failures} problema(s).`,
  );
  process.exitCode = failures === 0 ? 0 : 1;
}

await main();
