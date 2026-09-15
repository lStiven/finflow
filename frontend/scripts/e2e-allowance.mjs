/**
 * Drive the allowance card in a real browser, and check the arithmetic.
 *
 *   just up                 # emulator, seeded data, API and workers
 *   just web                # the frontend, in another terminal
 *   just e2e-allowance      # this
 *
 * This screen shows **the most dangerous number in the app**: if it lies once,
 * nobody looks at it again. So what is checked here is not that a card renders
 * — it is that the figure on it is the one the parts add up to, and that it
 * moves by exactly the right amount when money moves.
 *
 * Four things, and none of them can be checked against a double:
 *
 * 1. **Absent until declared.** With no plan the card is an invitation, not a
 *    zero — a zero on this number reads as "you have nothing left to spend",
 *    which is a real situation and not the one somebody is in before they have
 *    told the app anything.
 * 2. **The number is the subtraction.** `available` has to equal the income
 *    less the savings, less what the month has spent, less what the declared
 *    bills still owe — computed here from the API's own `/summary` and
 *    `/bills`, never from the allowance's own components, so a server that
 *    subtracted the wrong figure is caught rather than confirmed.
 * 3. **A paid charge is subtracted once.** Confirming a bill moves it out of
 *    what is owed and into what is spent, and the allowance must not move at
 *    all: the money was already accounted for. This is the mistake the whole
 *    use case exists to avoid, and it is invisible in the payload.
 * 4. **Spending moves it by exactly what was spent.**
 *
 * Exits non-zero on the first mismatch. It cleans up after itself: the plan it
 * declares is removed at the end, including when an assertion fails.
 */

import process from "node:process";
import { chromium } from "playwright";

const WEB = process.env.FINFLOW_WEB_URL ?? "http://localhost:5173";
const API = process.env.FINFLOW_API_URL ?? "http://localhost:8000";
const DEMO_EMAIL = process.env.FINFLOW_DEMO_EMAIL ?? "demo@finflow.local";
const DEMO_PASSWORD = process.env.FINFLOW_DEMO_PASSWORD ?? "una frase larga de verdad";

const INCOME = "9.000.000";
const INCOME_RAW = "9000000";
const SAVINGS = "1.000.000";
const SAVINGS_RAW = "1000000";
/** Unmistakable in a seeded database, and never a real merchant's name. */
const SPENT_NOTE = "E2E Disponible";
const SPENT_AMOUNT = "250000";
const BILL_NAME = "E2E Disponible factura";
const BILL_AMOUNT = "120000";

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
 * What the month spent and what its bills still owe, from the two endpoints
 * the card is *not* reading.
 *
 * The whole point: the expected allowance is rebuilt from `/summary` and
 * `/bills` rather than from `/allowance`'s own breakdown. Checking a figure
 * against the components it was computed from proves only that the server can
 * add; checking it against the two screens it claims to agree with is what
 * catches it subtracting the wrong one of the bills' two totals.
 */
async function monthState(call, currency = "COP") {
  const [summary, bills] = await Promise.all([
    call(`/financial/summary?group_by=month&transfers=exclude&timezone=America/Bogota`),
    call("/financial/bills?timezone=America/Bogota"),
  ]);
  // In the same zone the server buckets by. Read in UTC this would name the
  // next month for the last five hours of every month, and the check would
  // fail for a reason that has nothing to do with the code.
  const month = todayIso().slice(0, 7);
  const bucket = summary.groups.find((group) => group.key === month);
  const totals = (bucket?.totals ?? []).find((each) => each.currency === currency);
  const bill = bills.totals.find((each) => each.currency === currency);

  return {
    spent: Number(totals?.outgoing ?? 0),
    outstanding: Number(bill?.outstanding ?? 0),
  };
}

/** Today as a plain calendar day, in the zone the whole app reads. */
function todayIso() {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/Bogota" });
}

function expectedAvailable(month) {
  return Number(INCOME_RAW) - Number(SAVINGS_RAW) - month.spent - month.outstanding;
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
    // which one, and this app asks for 404s on purpose: «no hay plan
    // declarado» is the answer both plan endpoints give. So the generic line
    // is ignored here and the responses themselves are watched below — where
    // the URL *is* known, so a real missing resource still fails.
    if (message.text().includes("404 (Not Found)")) return;

    problems.push(`console: ${message.text()}`);
  });

  // The 404s this app asks for, by path. Anything else answering 404 is a
  // regression and is reported with the URL that caused it.
  const EXPECTED_404 = ["/financial/plan", "/financial/allowance"];
  page.on("response", (response) => {
    if (response.status() !== 404) return;
    const path = new URL(response.url()).pathname;
    if (EXPECTED_404.includes(path)) return;

    problems.push(`404 inesperado: ${path}`);
  });

  let call;
  let spentId = null;
  let billId = null;

  try {
    const token = await signIn(page);
    call = client(token);
    await dismissOnboarding(page);

    // A plan left behind by an earlier run would make the first assertion
    // pass for the wrong reason.
    await call("/financial/plan", { method: "DELETE" });

    // ------------------------------------------------- absent until declared
    await page.goto(`${WEB}/`, { waitUntil: "networkidle" });
    check(
      "sin plan, la tarjeta invita en vez de mostrar un cero",
      await page.getByText("¿Cuánto puedes gastar este mes?").isVisible(),
      true,
    );
    check(
      "y el servidor dice que no hay nada declarado",
      (await fetch(`${API}/financial/allowance`, { headers: { Authorization: token } }))
        .status,
      404,
    );

    // --------------------------------------------------------- declare, UI
    await page.getByLabel("Esperas que entren").fill(INCOME);
    await page.getByLabel("Quieres guardar").fill(SAVINGS);
    await page.getByRole("button", { name: "Calcular lo que me queda" }).click();

    const card = page.getByText("Te queda para gastar");
    await card.waitFor({ timeout: 10_000 });
    check("declarar el mes hace aparecer el número", await card.isVisible(), true);

    const stored = await call("/financial/plan");
    check("el servidor guardó lo que se tecleó", stored.expected_income, INCOME_RAW);
    check("y lo que se quiere guardar", stored.savings_target, SAVINGS_RAW);

    // ------------------------------------ integrity: the number is the sum
    let month = await monthState(call);
    let allowance = await call("/financial/allowance?timezone=America/Bogota");
    note(`gastado ${month.spent}, facturas sin pagar ${month.outstanding}`);
    check(
      "el disponible es exactamente la resta",
      Number(allowance.available),
      expectedAvailable(month),
    );
    check(
      "y sus partes son las que el resto de la app reporta",
      [Number(allowance.spent), Number(allowance.committed)],
      [month.spent, month.outstanding],
    );

    // --------------------------------- integrity: spending moves it by that
    const before = Number(allowance.available);
    const entered = await call("/financial/transactions", {
      method: "POST",
      body: JSON.stringify({
        direction: "outgoing",
        amount: SPENT_AMOUNT,
        currency: "COP",
        occurred_at: Math.floor(Date.now() / 1000),
        counterparty: SPENT_NOTE,
      }),
    });
    spentId = entered.id;

    allowance = await call("/financial/allowance?timezone=America/Bogota");
    check(
      "gastar baja el disponible por exactamente lo gastado",
      before - Number(allowance.available),
      Number(SPENT_AMOUNT),
    );

    // ----------------------------- integrity: a paid charge counts once
    // The one mistake the use case exists to avoid, and the one that is
    // invisible in the payload: confirming a charge moves it from "owed" to
    // "spent", and the total must not budge.
    //
    // The bill is declared here rather than found among the seeded ones. A
    // search for "some outstanding charge" quietly finds nothing the moment
    // the seed has answered for all of them — and a check that skips itself
    // is a check that is not being run. This one matters too much for that.
    const bill = await call("/financial/bills", {
      method: "POST",
      body: JSON.stringify({
        name: BILL_NAME,
        amount: BILL_AMOUNT,
        currency: "COP",
        cadence: "monthly",
        starts_on: todayIso(),
        direction: "outgoing",
      }),
    });
    billId = bill.id;

    const owing = await call("/financial/allowance?timezone=America/Bogota");
    check(
      "declarar una factura baja el disponible por lo que se debe",
      Number(allowance.available) - Number(owing.available),
      Number(BILL_AMOUNT),
    );

    const steady = Number(owing.available);
    await call(`/financial/bills/${billId}/occurrences/${todayIso()}/pay`, {
      method: "POST",
      body: "{}",
    });

    const settled = await call("/financial/allowance?timezone=America/Bogota");
    check(
      "confirmarla no lo vuelve a bajar: ya estaba contada",
      Number(settled.available),
      steady,
    );
    check(
      "solo cambia de lado — sale de lo que se debe y entra en lo gastado",
      [
        Number(owing.committed) - Number(settled.committed),
        Number(settled.spent) - Number(owing.spent),
      ],
      [Number(BILL_AMOUNT), Number(BILL_AMOUNT)],
    );

    await call(`/financial/bills/${billId}/occurrences/${todayIso()}/pay`, {
      method: "DELETE",
    });
    const undone = await call("/financial/allowance?timezone=America/Bogota");
    check(
      "y deshacerlo deja el disponible donde estaba",
      Number(undone.available),
      steady,
    );

    await call(`/financial/bills/${billId}`, { method: "DELETE" });
    billId = null;
    allowance = await call("/financial/allowance?timezone=America/Bogota");

    // ------------------------------------------ the screen shows its parts
    await page.goto(`${WEB}/`, { waitUntil: "networkidle" });
    await page.getByText("De qué está hecho").click();
    month = await monthState(call);
    check(
      "la pantalla enseña la resta, no solo el resultado",
      [
        await page.getByText("Esperas que entren").isVisible(),
        await page.getByText("Ya gastaste").isVisible(),
      ],
      [true, true],
    );

    // ----------------------------------------------------------- remove, UI
    await page.getByRole("button", { name: "Quitar" }).click();
    await page
      .getByText("¿Cuánto puedes gastar este mes?")
      .waitFor({ timeout: 10_000 });
    check(
      "quitar el plan devuelve la invitación, no un cero",
      await page.getByText("¿Cuánto puedes gastar este mes?").isVisible(),
      true,
    );
    check(
      "y el servidor vuelve a no tener nada declarado",
      (await fetch(`${API}/financial/allowance`, { headers: { Authorization: token } }))
        .status,
      404,
    );

    check("la pantalla no registró errores", problems, []);
  } catch (error) {
    failures += 1;
    steps.push(` FALLA ${error.message}`);
  } finally {
    // Whatever happened, the seeded database goes back as it was.
    if (call) {
      try {
        await call("/financial/plan", { method: "DELETE" });

        if (billId) {
          await call(`/financial/bills/${billId}`, { method: "DELETE" });
        }

        if (spentId) {
          await call(`/financial/transactions/${spentId}`, { method: "DELETE" });
        }

        // Sweeps anything an earlier run leaked, by the name only this suite
        // ever uses.
        const bills = await call("/financial/bills?timezone=America/Bogota");
        for (const each of bills.bills ?? []) {
          if (each.name === BILL_NAME) {
            await call(`/financial/bills/${each.id}`, { method: "DELETE" });
          }
        }

        const left = await call("/financial/transactions?search=E2E%20Disponible");
        for (const movement of left.transactions ?? []) {
          await call(`/financial/transactions/${movement.id}`, { method: "DELETE" });
        }
      } catch {
        note("no pude limpiar del todo — revisa el plan y los movimientos de prueba");
      }
    }

    await browser.close();
  }

  console.log(steps.join("\n"));
  console.log(
    failures === 0
      ? "\ne2e disponible: todo bien. El número es la resta, y una factura pagada se cuenta una vez."
      : `\ne2e disponible: ${failures} problema(s).`,
  );
  process.exitCode = failures === 0 ? 0 : 1;
}

await main();
