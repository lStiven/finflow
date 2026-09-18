/**
 * Drive the budgets screen in a real browser, and check what is behind it.
 *
 *   just up               # emulator, seeded data, API and workers
 *   just web              # the frontend, in another terminal
 *   just e2e-budgets      # this
 *
 * A cap moves no money, and **that is the thing worth proving against a real
 * stack**: every balance, the net worth and the number of ledger rows are read
 * before and after, and this refuses to pass if putting a ceiling on a category
 * moved any of them. It is the same rule the bills suite checks for declaring,
 * and it is the rule this whole feature rests on — an app that quietly wrote a
 * row when somebody set a budget would be an app whose balances drift for a
 * reason nobody could ever find.
 *
 * Five things, and none of them can be checked against a double:
 *
 * 1. **Nothing capped is an empty screen, not a 404.** Unlike the monthly plan,
 *    no ceilings is an ordinary state.
 * 2. **Putting a cap moves nothing.** Balances, net worth and ledger count,
 *    before and after.
 * 3. **The traffic light is the ledger's answer, not the screen's.** The spent
 *    figure is rebuilt from `/financial/summary` grouped by category — never
 *    from the budgets endpoint's own components, because checking a figure
 *    against what it was computed from proves only that the server can
 *    subtract.
 * 4. **Spending moves the bar, and by exactly what was spent.**
 * 5. **This month's cap shadows the recurring one**, and dropping the exception
 *    leaves the recurring cap standing. Two rows, and the screen has to read
 *    the right one.
 *
 * Exits non-zero on the first mismatch. It cleans up after itself — the caps
 * and the movement it creates are removed at the end, including when an
 * assertion fails.
 */

import process from "node:process";
import { chromium } from "playwright";

const WEB = process.env.FINFLOW_WEB_URL ?? "http://localhost:5173";
const API = process.env.FINFLOW_API_URL ?? "http://localhost:8000";
const DEMO_EMAIL = process.env.FINFLOW_DEMO_EMAIL ?? "demo@finflow.local";
const DEMO_PASSWORD = process.env.FINFLOW_DEMO_PASSWORD ?? "una frase larga de verdad";

/**
 * The category this suite caps.
 *
 * `education` on purpose: it is one of the sixteen the app ships, so it is
 * always in the vocabulary, and `just seed` spends nothing in it — so the bar
 * starts empty and every figure below is this suite's own doing rather than
 * something it has to subtract the seed out of.
 */
const CATEGORY = "education";
const CATEGORY_LABEL = "Educación";
const LIMIT = "400.000";
const LIMIT_RAW = "400000";
/** Unmistakable in a seeded database, and never a real merchant's name. */
const SPENT_NOTE = "E2E Presupuesto";
/** 85% of the cap: past the default warning point and short of the ceiling. */
const SPENT_AMOUNT = "340000";

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

/** The API, as the signed-in person — the same token the page is using. */
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

  const { accessToken } = JSON.parse(raw);

  return accessToken.startsWith("Bearer ") ? accessToken : `Bearer ${accessToken}`;
}

/** Shut the onboarding modals — they are modal and swallow every click. */
async function dismissOnboarding(page) {
  const wrote = await page.evaluate(() => {
    const raw = window.localStorage.getItem("finflow.session");
    if (raw === null) return false;

    const { userId } = JSON.parse(raw);
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

    return true;
  });

  if (wrote) await page.goto(`${WEB}/`, { waitUntil: "networkidle" });
}

/**
 * Everything a cap must not touch, in one reading.
 *
 * Balances by account, the net worth per currency, and how many rows the ledger
 * holds. Compared whole rather than field by field, so a balance that moved by
 * a peso fails as loudly as one that moved by a million.
 */
async function untouchable(call) {
  const [accounts, transactions] = await Promise.all([
    call("/financial/accounts"),
    call("/financial/transactions?limit=1"),
  ]);

  return {
    balances: Object.fromEntries(
      accounts.accounts.map((account) => [account.id, account.balance]),
    ),
    netWorth: accounts.net_worth.map((entry) => [entry.currency, entry.total]),
    movements: transactions.total,
  };
}

/** What the ledger says went out of one category this month, in its own words. */
async function spentOn(call, category, currency = "COP") {
  const summary = await call(
    "/financial/summary?group_by=category&transfers=exclude&timezone=America/Bogota" +
      `&from=${monthStart()}&to=${monthEnd()}`,
  );
  const bucket = summary.groups.find((group) => group.key === category);
  const totals = (bucket?.totals ?? []).find((each) => each.currency === currency);

  return Number(totals?.outgoing ?? 0);
}

/** Today as a plain calendar day, in the zone the whole app reads. */
function todayIso() {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/Bogota" });
}

function thisMonth() {
  return todayIso().slice(0, 7);
}

/**
 * The month's bounds as epoch seconds, half-open like every window here.
 *
 * **In Bogotá, not in UTC.** The endpoint reads the month where its owner
 * lives, so a window built from UTC midnight starts five hours early — and for
 * the last five Bogotá hours of a month the two disagree about which month it
 * is, which would fail this suite for a reason that has nothing to do with the
 * code. Colombia has no daylight saving, so the offset is the constant it looks
 * like.
 */
const BOGOTA_OFFSET_SECONDS = 5 * 60 * 60;

function monthStart() {
  const [year, month] = thisMonth().split("-").map(Number);

  return Math.floor(Date.UTC(year, month - 1, 1) / 1000) + BOGOTA_OFFSET_SECONDS;
}

function monthEnd() {
  const [year, month] = thisMonth().split("-").map(Number);

  return Math.floor(Date.UTC(year, month, 1) / 1000) + BOGOTA_OFFSET_SECONDS;
}

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

    // The browser logs a failed request as a console error without saying
    // which one, and the dashboard asks for 404s on purpose: «no hay plan
    // declarado» is the answer both plan endpoints give. The responses
    // themselves are watched below, where the URL *is* known.
    if (message.text().includes("404 (Not Found)")) return;

    problems.push(`console: ${message.text()}`);
  });

  // The 404s this app asks for, by path. The budgets endpoint is deliberately
  // not one of them — an empty month answers 200, and a 404 from it would be a
  // regression this has to catch rather than allow.
  const EXPECTED_404 = ["/financial/plan", "/financial/allowance"];
  page.on("response", (response) => {
    if (response.status() !== 404) return;
    const path = new URL(response.url()).pathname;
    if (EXPECTED_404.includes(path)) return;

    problems.push(`404 inesperado: ${path}`);
  });

  let call;
  let spentId = null;

  try {
    const token = await signIn(page);
    call = client(token);
    await dismissOnboarding(page);

    // Caps left behind by an earlier run would make the first assertion pass
    // for the wrong reason.
    await call(`/financial/budgets/${CATEGORY}`, { method: "DELETE" });
    await call(`/financial/budgets/${CATEGORY}?month=${thisMonth()}`, {
      method: "DELETE",
    });

    // ------------------------------------ an uncapped category is 200, not 404
    // A month far in the future, so the only caps in it are the recurring ones
    // — which govern every month by design, and are what the seed leaves. The
    // check is the status and the shape: unlike the monthly plan, having no
    // ceilings is an ordinary state and must never be reported as missing.
    const far = await fetch(
      `${API}/financial/budgets?timezone=America/Bogota&month=2099-01`,
      { headers: { Authorization: token } },
    );
    check("un mes lejano responde 200 y no 404", far.status, 200);

    const farBody = await far.json();
    check(
      "con las tres listas puestas, aunque estén vacías",
      [
        Array.isArray(farBody.budgets),
        Array.isArray(farBody.totals),
        Array.isArray(farBody.suggestions),
      ],
      [true, true, true],
    );
    check(
      "y sin el tope que este suite aún no ha puesto",
      farBody.budgets.some((budget) => budget.category === CATEGORY),
      false,
    );

    // ------------------------------------------------------ nothing moves
    const before = await untouchable(call);
    note(`antes: ${before.movements} movimientos, patrimonio ${before.netWorth}`);

    await page.goto(`${WEB}/presupuestos`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Poner un tope" }).click();
    await page.getByLabel("Categoría").selectOption(CATEGORY);
    // By role and exact: `getByLabel("Tope")` also matches every card's
    // «Cambiar el tope de X» and «Quitar el tope de X», which are accessible
    // names and not fields.
    await page.getByRole("textbox", { name: "Tope", exact: true }).fill(LIMIT);
    await page.getByRole("button", { name: "Poner el tope" }).click();

    const card = page.getByText(CATEGORY_LABEL, { exact: true });
    await card.waitFor({ timeout: 10_000 });
    check(
      "poner un tope desde la pantalla lo deja a la vista",
      await card.isVisible(),
      true,
    );

    const stored = await call("/financial/budgets?timezone=America/Bogota");
    const mine = stored.budgets.find((budget) => budget.category === CATEGORY);
    check("el servidor guardó el tope que se tecleó", mine?.limit, LIMIT_RAW);
    check(
      "y se repite cada mes, que es lo que el interruptor decía",
      mine?.recurring,
      true,
    );
    check("avisando al 80 %, que es el valor por defecto", mine?.warn_at, 80);

    check("poner un tope no movió un solo peso", await untouchable(call), before);

    // ---------------------------- the traffic light is the ledger's answer
    check(
      "sin gasto en la categoría el tope está en verde",
      [mine?.state, Number(mine?.spent)],
      ["ok", await spentOn(call, CATEGORY)],
    );

    // ------------------------------ spending moves it, by exactly that much
    const entered = await call("/financial/transactions", {
      method: "POST",
      body: JSON.stringify({
        direction: "outgoing",
        amount: SPENT_AMOUNT,
        currency: "COP",
        occurred_at: Math.floor(Date.now() / 1000),
        counterparty: SPENT_NOTE,
        category: CATEGORY,
      }),
    });
    spentId = entered.id;

    const after = await call("/financial/budgets?timezone=America/Bogota");
    const spent = after.budgets.find((budget) => budget.category === CATEGORY);
    check(
      "gastar sube lo gastado por exactamente lo gastado",
      Number(spent?.spent),
      await spentOn(call, CATEGORY),
    );
    check("y pasado el 80 % el semáforo se pone en ámbar", spent?.state, "warning");
    check(
      "con lo que queda reportado hasta el peso",
      spent?.remaining,
      String(Number(LIMIT_RAW) - Number(SPENT_AMOUNT)),
    );

    await page.reload({ waitUntil: "networkidle" });
    check(
      "y la pantalla lo dice con las mismas palabras",
      await page.getByText("Vas por el 85%").isVisible(),
      true,
    );

    // ------------------------------------- this month's cap shadows the other
    await call("/financial/budgets", {
      method: "PUT",
      body: JSON.stringify({
        category: CATEGORY,
        limit: "1000000",
        currency: "COP",
        month: thisMonth(),
        warn_at: 80,
      }),
    });

    const shadowed = await call("/financial/budgets?timezone=America/Bogota");
    const exception = shadowed.budgets.filter((budget) => budget.category === CATEGORY);
    check("la excepción del mes tapa al tope de siempre", exception.length, 1);
    check("y es la que la pantalla lee", exception[0]?.limit, "1000000");
    check("con el gasto reasignado, vuelve a verde", exception[0]?.state, "ok");

    await page.reload({ waitUntil: "networkidle" });
    check(
      "la pantalla dice que ese tope es solo de este mes",
      await page.getByText("Solo este mes").isVisible(),
      true,
    );

    // ------------------------- dropping the exception leaves the other alone
    await call(`/financial/budgets/${CATEGORY}?month=${thisMonth()}`, {
      method: "DELETE",
    });

    const back = await call("/financial/budgets?timezone=America/Bogota");
    const recurring = back.budgets.find((budget) => budget.category === CATEGORY);
    check(
      "quitar la excepción deja el tope de siempre donde estaba",
      [recurring?.limit, recurring?.recurring],
      [LIMIT_RAW, true],
    );

    // --------------------------------------------- and none of it moved money
    await call(`/financial/transactions/${spentId}`, { method: "DELETE" });
    spentId = null;
    check(
      "borrado el movimiento de prueba, todo está como al principio",
      await untouchable(call),
      before,
    );

    check("la pantalla no registró errores", problems, []);
  } catch (error) {
    failures += 1;
    steps.push(` FALLA ${error.message}`);
  } finally {
    // Whatever happened, the seeded database goes back as it was.
    if (call) {
      try {
        await call(`/financial/budgets/${CATEGORY}`, { method: "DELETE" });
        await call(`/financial/budgets/${CATEGORY}?month=${thisMonth()}`, {
          method: "DELETE",
        });

        if (spentId) {
          await call(`/financial/transactions/${spentId}`, { method: "DELETE" });
        }

        // Sweeps anything an earlier run leaked, by the name only this suite
        // ever uses.
        const left = await call(
          `/financial/transactions?search=${encodeURIComponent(SPENT_NOTE)}`,
        );
        for (const movement of left.transactions ?? []) {
          await call(`/financial/transactions/${movement.id}`, { method: "DELETE" });
        }
      } catch {
        note("no pude limpiar del todo — revisa los topes y el movimiento de prueba");
      }
    }

    await browser.close();
  }

  console.log(steps.join("\n"));
  console.log(
    failures === 0
      ? "\ne2e presupuestos: todo bien. Un tope no mueve un peso, y el mes lee el tope que le toca."
      : `\ne2e presupuestos: ${failures} problema(s).`,
  );
  process.exitCode = failures === 0 ? 0 : 1;
}

await main();
