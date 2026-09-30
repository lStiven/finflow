/**
 * The in-app alerts, in a real browser and against the real stack.
 *
 *   just up               # emulator, seeded data, API and workers
 *   just web              # the frontend, in another terminal
 *   just e2e-alerts       # this
 *
 * The whole path runs for real: a movement written through the API goes out on
 * the bus, the alerts worker picks it up, asks Financial which budgets it counts
 * against, keeps it in the inbox, and the open page — polling — shows it.
 *
 * 1. **History is not news.** Opening the app shows no toast for what was
 *    already in the inbox.
 * 2. **A new purchase becomes a toast within the poll**, with the budget line
 *    — even for a merchant created by that very purchase — and the figure in
 *    that line is the one `/financial/budgets` reports for the same budget,
 *    not one the alert computed on its own.
 * 3. **«Ver» opens the movement.**
 * 4. **The bell counts what is unseen**, lists it, and opening it clears it.
 * 5. **Income carries no budget line.**
 *
 * Exits non-zero on the first mismatch, and removes what it wrote, including
 * when an assertion fails.
 */

import process from "node:process";
import { chromium } from "playwright";

const WEB = process.env.FINFLOW_WEB_URL ?? "http://localhost:5173";
const API = process.env.FINFLOW_API_URL ?? "http://localhost:8000";
const DEMO_EMAIL = process.env.FINFLOW_DEMO_EMAIL ?? "demo@finflow.local";
const DEMO_PASSWORD = process.env.FINFLOW_DEMO_PASSWORD ?? "una frase larga de verdad";

/**
 * Unmistakable in a seeded database — and new on every run, on purpose: a
 * counterparty nobody has seen is filed under its category *after* the
 * movement is committed, which is the race the alerts queue's delivery delay
 * exists for. A name reused from the last run would already be filed, and
 * would test nothing.
 */
const RUN = Date.now().toString(36).toUpperCase();
const PREFIX = "E2E AVISO";
const SPENT_TEXT = `${PREFIX} RESTAURANTE ${RUN}`;
const INCOME_TEXT = `${PREFIX} INGRESO ${RUN}`;
/** A budget `just seed` declares over `restaurants`. */
const BUDGET = "Restaurantes";
/** A poll every 15 s, then the worker's own queue: generous, not flaky. */
const ARRIVAL_MS = 45_000;

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
        Authorization: token,
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

async function signIn(page) {
  await page.goto(`${WEB}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("Correo").fill(DEMO_EMAIL);
  await page.getByLabel("Contraseña").fill(DEMO_PASSWORD);
  await page.locator('form button[type="submit"]').click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
    timeout: 15_000,
  });

  const raw = await page.evaluate(() => window.localStorage.getItem("finflow.session"));
  if (raw === null) throw new Error("Entró pero no dejó sesión en el navegador");

  const { accessToken, userId } = JSON.parse(raw);

  await page.evaluate((id) => {
    window.localStorage.setItem(
      `finflow.onboarding.${id}`,
      JSON.stringify({
        welcomeSeen: true,
        introSeen: true,
        addressCopied: true,
        gmailSubmitted: true,
        readyCelebrated: true,
      }),
    );
  }, userId);

  const token = accessToken.startsWith("Bearer ")
    ? accessToken
    : `Bearer ${accessToken}`;

  // Everything already in the inbox counts as seen, so the badge below is this
  // suite's own doing. By id, the way the bell remembers.
  const inbox = await client(token)("/alerts/inbox?limit=50");
  await page.evaluate(
    ({ id, ids }) =>
      window.localStorage.setItem(`finflow.alerts.seen.${id}`, JSON.stringify(ids)),
    { id: userId, ids: inbox.entries.map((entry) => entry.id) },
  );

  return token;
}

/** Money the way the screen prints it, minus `Intl`'s no-break space. */
function pesos(amount) {
  return `$ ${Number(amount).toLocaleString("es-CO", { maximumFractionDigits: 0 })}`;
}

function plain(text) {
  return text.replace(/ /g, " ");
}

/** Every movement any run of this suite wrote, and no other. */
async function sweep(call) {
  const left = await call(
    `/financial/transactions?search=${encodeURIComponent(PREFIX)}&limit=200`,
  );
  for (const movement of left.transactions ?? []) {
    if (!movement.counterparty.startsWith(PREFIX)) continue;
    await call(`/financial/transactions/${movement.id}`, { method: "DELETE" });
  }
}

async function main() {
  for (const [what, url] of [
    ["el frontend", WEB],
    ["la API", API],
  ]) {
    if (!(await reachable(url))) {
      console.error(
        `No hay nada escuchando en ${url} (${what}).\n` +
          "  just up     # emulador, datos de prueba, la API y los workers\n" +
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

  let call;

  try {
    call = client(await signIn(page));
    await sweep(call);

    // 1 · History is not news.
    await page.goto(`${WEB}/`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2_000);
    check(
      "abrir la app no lanza avisos del historial",
      await page.locator("[data-sonner-toast]").count(),
      0,
    );

    // 2 · A purchase under a budget arrives as a toast, with its line.
    const now = Math.floor(Date.now() / 1000) - 60;
    const spent = await call("/financial/transactions", {
      method: "POST",
      body: JSON.stringify({
        direction: "outgoing",
        amount: "12345",
        currency: "COP",
        occurred_at: now,
        counterparty: SPENT_TEXT,
        category: "restaurants",
      }),
    });

    const toast = page.locator("[data-sonner-toast]", { hasText: SPENT_TEXT });
    await toast.waitFor({ timeout: ARRIVAL_MS });
    const toastText = plain(await toast.innerText());
    check(
      "el aviso dice que fue un gasto y cuánto",
      toastText.includes("Gasto $ 12.345"),
      true,
    );

    // A deployment without budgets answers 404 here, and then there is no
    // line to check — only that the alert still arrived.
    const budgets = await call("/financial/budgets?timezone=America/Bogota").catch(
      () => ({ budgets: [] }),
    );
    const restaurants = budgets.budgets.find((budget) => budget.name === BUDGET);
    if (restaurants) {
      const expected =
        Number(restaurants.remaining) >= 0
          ? `${BUDGET}: te quedan ${pesos(restaurants.remaining)} de ${pesos(restaurants.limit)}`
          : `${BUDGET}: vas ${pesos(-Number(restaurants.remaining))} por encima del tope de ${pesos(restaurants.limit)}`;
      check(
        "y la línea del presupuesto es la de /financial/budgets",
        toastText.includes(expected),
        true,
      );
    } else {
      steps.push(
        `  ··   el seed no trae el presupuesto «${BUDGET}»; línea no comprobada`,
      );
    }

    // 3 · «Ver» opens the movement.
    await toast.getByRole("button", { name: "Ver" }).click();
    await page.waitForURL((url) => url.pathname === `/transacciones/${spent.id}`, {
      timeout: 10_000,
    });
    check(
      "«Ver» abre el movimiento",
      new URL(page.url()).pathname,
      `/transacciones/${spent.id}`,
    );

    // 4 · The bell counts it, lists it, and opening clears the count.
    // After the detail screen has settled: while it loads, the bell on screen
    // is still the previous screen's, and a click on it opens a panel that
    // goes away with it an instant later.
    await page.waitForLoadState("networkidle");
    await page.getByRole("heading", { level: 1 }).waitFor();
    const bell = page.locator("button[data-alerts-bell]:visible");
    const label = await bell.getAttribute("aria-label");
    check("la campana cuenta lo que no se ha visto", /sin ver/.test(label ?? ""), true);
    await bell.click();
    const panel = page.getByRole("dialog", { name: "Avisos recientes" });
    await panel.waitFor();
    check("y lo lista", (await panel.innerText()).includes(SPENT_TEXT), true);
    await page.keyboard.press("Escape");
    check("abrirla la deja en cero", await bell.getAttribute("aria-label"), "Avisos");

    // 5 · Income: a toast, and no budget line.
    await call("/financial/transactions", {
      method: "POST",
      body: JSON.stringify({
        direction: "incoming",
        amount: "50000",
        currency: "COP",
        occurred_at: now,
        counterparty: INCOME_TEXT,
      }),
    });
    const income = page.locator("[data-sonner-toast]", { hasText: INCOME_TEXT });
    await income.waitFor({ timeout: ARRIVAL_MS });
    const incomeText = plain(await income.innerText());
    check(
      "un ingreso se anuncia como ingreso",
      incomeText.includes("Ingreso $ 50.000"),
      true,
    );
    check("y sin línea de presupuesto", /te quedan|tope/.test(incomeText), false);

    check("la pantalla no registró errores", problems, []);
  } catch (error) {
    failures += 1;
    steps.push(` FALLA ${error.message}`);
  } finally {
    if (call) {
      try {
        await sweep(call);
      } catch {
        steps.push("  ··   no pude borrar los movimientos de prueba");
      }
    }

    await browser.close();
  }

  console.log(steps.join("\n"));
  console.log(
    failures === 0
      ? "\ne2e avisos: todo bien. Lo que llega a Telegram también llega a la app."
      : `\ne2e avisos: ${failures} problema(s).`,
  );
  process.exitCode = failures === 0 ? 0 : 1;
}

await main();
