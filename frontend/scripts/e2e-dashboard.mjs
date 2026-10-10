/**
 * Drive the dashboard in a real browser, and check where each figure leads.
 *
 *   just up             # emulator, seeded data, API and workers
 *   just web            # the frontend, in another terminal
 *   just e2e-resumen    # this
 *
 * UX-06 made every figure on Resumen open what it is made of. A link that
 * opens a list which does not add up to the figure is worse than no link, so
 * each one is checked against the API rather than against the screen:
 *
 * 1. **Patrimonio and Deuda open Cuentas**, which is where they are made.
 * 2. **Every category in the ring opens its own spending this month**, and the
 *    API's rows for that filter add up to exactly the bucket the ring draws.
 * 3. **Every account opens its movements**, one link per open account.
 * 4. **Comparisons say what they compare with**: the previous month by name,
 *    «a esta altura», because they stop at the same day of it.
 * 5. Nothing scrolls sideways at 390 or 320 px, and nothing logs an error.
 *
 * Read-only: it signs in as the seeded person and changes nothing.
 */

import process from "node:process";
import { chromium } from "playwright";

const WEB = process.env.FINFLOW_WEB_URL ?? "http://localhost:5173";
const API = process.env.FINFLOW_API_URL ?? "http://localhost:8000";
const DEMO_EMAIL = process.env.FINFLOW_DEMO_EMAIL ?? "demo@finflow.local";
const DEMO_PASSWORD = process.env.FINFLOW_DEMO_PASSWORD ?? "una frase larga de verdad";

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
  return async (path) => {
    const response = await fetch(`${API}${path}`, {
      headers: { Authorization: token },
    });
    if (!response.ok) throw new Error(`GET ${path} → ${response.status}`);
    return response.json();
  };
}

async function signIn(page) {
  await page.goto(`${WEB}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("Correo").fill(DEMO_EMAIL);
  await page.getByLabel("Contraseña").fill(DEMO_PASSWORD);
  await page.locator('form button[type="submit"]').click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
    timeout: 15_000,
  });

  return page.evaluate(() => {
    const { accessToken, userId } = JSON.parse(
      window.localStorage.getItem("finflow.session"),
    );
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
    return accessToken.startsWith("Bearer ") ? accessToken : `Bearer ${accessToken}`;
  });
}

/** A calendar day as Transacciones reads it, as the epoch the API takes. */
const BOGOTA_OFFSET_SECONDS = 5 * 60 * 60;

function dayStart(day) {
  const [year, month, date] = day.split("-").map(Number);
  return Math.floor(Date.UTC(year, month - 1, date) / 1000) + BOGOTA_OFFSET_SECONDS;
}

const sideways = (page) =>
  page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);

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
    // «No hay plan declarado» answers 404 on purpose.
    if (message.text().includes("404 (Not Found)")) return;
    problems.push(`console: ${message.text()}`);
  });

  try {
    const call = client(await signIn(page));
    await page.goto(`${WEB}/`, { waitUntil: "networkidle" });

    // ------------------------------------------------ Patrimonio and Deuda
    for (const label of ["Patrimonio", "Deuda"]) {
      const tile = page.getByRole("link", { name: new RegExp(`^${label}`) });
      check(
        `«${label}» lleva a Cuentas`,
        (await tile.count()) > 0 ? await tile.first().getAttribute("href") : null,
        "/cuentas",
      );
    }

    // ------------------------------------- every category, and its rows add up
    const legend = page.locator('a[href*="category="]');
    const links = await legend.evaluateAll((anchors) =>
      anchors.map((anchor) => anchor.getAttribute("href")),
    );
    const searches = links
      .map((href) => new URL(href, "http://x").searchParams)
      .filter((params) => params.has("category"));
    note(`${searches.length} categorías enlazadas en el anillo`);
    check(
      "el anillo tiene al menos una categoría con enlace",
      searches.length > 0,
      true,
    );

    const from = searches[0]?.get("from");
    const to = searches[0]?.get("to");
    const range =
      from && to ? `&from=${dayStart(from)}&to=${dayStart(to) + 86_400}` : "";
    const summary = await call(
      `/financial/summary?group_by=category&timezone=America/Bogota${range}`,
    );

    const agree = [];
    for (const params of searches) {
      const category = params.get("category");
      const rows = await call(
        `/financial/transactions?category=${encodeURIComponent(category)}` +
          `&direction=outgoing&transfers=exclude${range}&limit=100`,
      );
      const listed = rows.transactions
        .filter((movement) => movement.currency === "COP")
        .reduce((sum, movement) => sum + Number(movement.amount), 0);
      const bucket = summary.groups
        .find((group) => group.key === category)
        ?.totals.find((total) => total.currency === "COP");
      agree.push([
        category,
        params.get("direction"),
        params.get("transfers"),
        listed === Number(bucket?.outgoing ?? -1),
      ]);
    }
    check(
      "cada categoría abre sus gastos del mes, y esas filas suman lo que dibuja el anillo",
      agree.filter(
        ([, direction, transfers, adds]) =>
          !(direction === "outgoing" && transfers === "exclude" && adds),
      ),
      [],
    );

    // ------------------------------------------------- every account opens
    const accounts = await call("/financial/accounts");
    const accountLinks = await page
      .getByRole("link", { name: /: ver sus movimientos$/ })
      .evaluateAll((anchors) =>
        anchors.map((anchor) => new URL(anchor.href).searchParams.get("account")),
      );
    check(
      "cada cuenta abierta lleva a sus movimientos",
      [...accountLinks].sort(),
      accounts.accounts.map((account) => account.id).sort(),
    );

    // ----------------------------------------- comparisons name the month
    const previous = new Date();
    previous.setDate(1);
    previous.setMonth(previous.getMonth() - 1);
    const name = previous.toLocaleDateString("es-CO", { month: "long" });
    check(
      "la comparación del anillo dice contra qué mes",
      await page.getByText(`Frente a ${name}`).isVisible(),
      true,
    );
    const said = await page.getByText(`${name} a esta altura`).count();
    note(`«${name} a esta altura» aparece ${said} vez/veces`);

    // ------------------------------------------------- open one, it is that
    if (searches[0]) {
      await legend.first().click();
      await page.waitForURL((url) => url.pathname === "/transacciones");
      const category = new URL(page.url()).searchParams.get("category");
      const rows = await call(
        `/financial/transactions?category=${encodeURIComponent(category)}` +
          `&direction=outgoing&transfers=exclude${range}&limit=1`,
      );
      await page
        .getByText(
          `${rows.total} ${rows.total === 1 ? "movimiento" : "movimientos"} con los filtros aplicados`,
        )
        .waitFor({ timeout: 10_000 });
      check("tocar una categoría abre exactamente sus movimientos", true, true);
      await page.goBack({ waitUntil: "networkidle" });
    }

    check("a 390 px Resumen no se va de lado", await sideways(page), false);
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
      ? "\ne2e resumen: todo bien. Cada cifra abre lo que la forma, y suma lo mismo."
      : `\ne2e resumen: ${failures} problema(s).`,
  );
  process.exitCode = failures === 0 ? 0 : 1;
}

await main();
