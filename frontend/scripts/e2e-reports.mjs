/**
 * Drive the reports screen in a real browser, and check where a column leads.
 *
 *   just up              # emulator, seeded data, API and workers
 *   just web             # the frontend, in another terminal
 *   just e2e-reportes    # this
 *
 * UX-11 made the run over time navigable: choosing a column offers its
 * movements. The run the page draws is captured as the page received it, so
 * the check is against exactly what was on screen:
 *
 * 1. **Choosing a column does not leave the screen** — on a phone that tap is
 *    also the one that shows the figures — and offers its movements below.
 * 2. **The list behind a column adds up to it**: the API's rows for the
 *    link's filter sum to the column's own incoming and outgoing, to the peso.
 * 3. **Balance opens both directions** of the period, and nothing else.
 * 4. Nothing scrolls sideways at 390 or 320 px, and nothing logs an error.
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
    // «Cómo funciona» is not what this suite reads.
    window.localStorage.setItem(`finflow.help.${userId}`, JSON.stringify(["reportes"]));
    return accessToken.startsWith("Bearer ") ? accessToken : `Bearer ${accessToken}`;
  });
}

const BOGOTA_OFFSET_SECONDS = 5 * 60 * 60;

/** A calendar day, as the epoch second it starts in Bogotá. */
function dayStart(day) {
  const [year, month, date] = day.split("-").map(Number);
  return Math.floor(Date.UTC(year, month - 1, date) / 1000) + BOGOTA_OFFSET_SECONDS;
}

/** Every page of a filtered list, summed by direction, in pesos. */
async function sumsOf(call, params) {
  const sums = { incoming: 0, outgoing: 0 };
  for (let offset = 0; ; offset += 100) {
    const page = await call(
      `/financial/transactions?${params}&limit=100&offset=${offset}`,
    );
    for (const movement of page.transactions) {
      if (movement.currency !== "COP") continue;
      sums[movement.direction] += Number(movement.amount);
    }
    if (offset + 100 >= page.total) break;
  }
  return sums;
}

function filtersOf(href) {
  const params = new URL(href, "http://x").searchParams;
  const query = new URLSearchParams();
  query.set("transfers", params.get("transfers") ?? "");
  if (params.get("direction")) query.set("direction", params.get("direction"));
  if (params.get("account")) query.set("account_id", params.get("account"));
  query.set("from", String(dayStart(params.get("from"))));
  query.set("to", String(dayStart(params.get("to")) + 86_400));
  return { params, query: query.toString() };
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
    if (message.text().includes("404 (Not Found)")) return;
    problems.push(`console: ${message.text()}`);
  });

  try {
    const call = client(await signIn(page));

    // The run the cashflow chart draws, exactly as the page received it.
    const flowArrived = page.waitForResponse(
      (response) =>
        response.url().includes("/financial/trends") &&
        response.url().includes("dimension=none") &&
        response.ok(),
    );
    await page.goto(`${WEB}/reportes`, { waitUntil: "networkidle" });
    const flow = await (await flowArrived).json();

    // The column with the most spending, so the check has something to add.
    const outgoingAt = (index) =>
      Number(flow.series[0]?.points[index]?.totals[0]?.outgoing ?? 0);
    const incomingAt = (index) =>
      Number(flow.series[0]?.points[index]?.totals[0]?.incoming ?? 0);
    let index = 0;
    for (let at = 0; at < flow.buckets.length; at++) {
      if (outgoingAt(at) > outgoingAt(index)) index = at;
    }
    note(`columna ${index + 1} de ${flow.buckets.length} (${flow.interval})`);

    const panel = page
      .locator("div.surface")
      .filter({ has: page.getByRole("heading", { name: "Flujo de caja" }) });
    check(
      "antes de elegir, la gráfica dice que se puede tocar",
      await panel.getByText("Toca una columna para ver sus movimientos.").isVisible(),
      true,
    );

    const column = panel.locator("button[aria-pressed]").nth(index);
    await column.click();
    check(
      "elegir una columna no sale de la pantalla",
      new URL(page.url()).pathname,
      "/reportes",
    );
    check(
      "y queda marcada como elegida",
      await column.getAttribute("aria-pressed"),
      "true",
    );

    const link = panel.getByRole("link", { name: /^Ver los movimientos de / });
    await link.waitFor({ timeout: 5_000 });
    const { params, query } = filtersOf(await link.getAttribute("href"));
    check("ofrece sus movimientos, sin traslados", params.get("transfers"), "exclude");

    const sums = await sumsOf(call, query);
    check(
      "y esas filas suman lo que dibuja la columna (ingresos y gastos)",
      [sums.incoming, sums.outgoing],
      [incomingAt(index), outgoingAt(index)],
    );

    await link.click();
    await page.waitForURL((url) => url.pathname === "/transacciones");
    check(
      "tocar el enlace abre la lista de ese periodo",
      new URL(page.url()).searchParams.get("from"),
      params.get("from"),
    );
    await page.goBack({ waitUntil: "networkidle" });

    // ---------------------------------------- Balance: both directions
    const balance = page.getByRole("link", { name: /^Balance/ });
    const balanceHref = await balance.getAttribute("href");
    const balanceParams = new URL(balanceHref, "http://x").searchParams;
    check(
      "«Balance» abre las dos direcciones del periodo, sin traslados",
      [balanceParams.has("direction"), balanceParams.get("transfers")],
      [false, "exclude"],
    );

    check("a 390 px Reportes no se va de lado", await sideways(page), false);
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
      ? "\ne2e reportes: todo bien. Cada columna abre lo que la forma, y suma lo mismo."
      : `\ne2e reportes: ${failures} problema(s).`,
  );
  process.exitCode = failures === 0 ? 0 : 1;
}

await main();
