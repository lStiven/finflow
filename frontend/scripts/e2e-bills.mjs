/**
 * Drive the bills screen in a real browser, and check the data behind it.
 *
 *   just up                 # emulator, seeded data, API and workers
 *   just web                # the frontend, in another terminal
 *   just e2e-bills          # this
 *
 * Two things are being asked, and they are not the same question:
 *
 * 1. **Does the screen work?** Every step goes through the rendered page —
 *    typing into the form, clicking the buttons — rather than through the
 *    API, so a screen that type-checks and renders nothing fails here.
 * 2. **Does the data behind it say the same thing?** After each step the
 *    server is asked directly, with the same token the page holds, and the
 *    two answers are compared. A screen that shows what it just sent while
 *    the server stored something else is the failure this half exists for,
 *    and no amount of unit testing on either side can see it.
 *
 * And two invariants run through all of it, which are two halves of one rule:
 *
 * * **Declaring a bill must not move money.** Balances, net worth and the
 *   movement list are read before and after declaring, amending, pausing,
 *   skipping and deleting, and they have to be identical.
 * * **Confirming a charge must move it exactly once.** The same figures are
 *   read after paying, and the chosen account has to have moved by exactly
 *   what was confirmed — then a second confirmation is sent straight at the
 *   API, and nothing may move again. That is at-least-once delivery played
 *   out for real: the row's key comes from the bill and the period, so the
 *   table refuses the second write. A count in the use case would pass every
 *   unit test and fail here.
 *
 * And one more, added with the detector: **a suggestion is not a bill until
 * somebody says so.** The section at the foot of the screen proposes what the
 * seeded history repeats, and accepting one has to produce an ordinary
 * declared bill — through the same endpoint the form uses — without moving a
 * peso, and has to come back marked so it is never offered twice.
 *
 * Neither can be checked against a double, which is why this script exists.
 *
 * Exits non-zero on the first mismatch, with what it expected and what it
 * got. It cleans up after itself: the bill it declares is deleted at the end,
 * including when an assertion fails.
 */

import process from "node:process";
import { chromium } from "playwright";

const WEB = process.env.FINFLOW_WEB_URL ?? "http://localhost:5173";
const API = process.env.FINFLOW_API_URL ?? "http://localhost:8000";
const DEMO_EMAIL = process.env.FINFLOW_DEMO_EMAIL ?? "demo@finflow.local";
const DEMO_PASSWORD = process.env.FINFLOW_DEMO_PASSWORD ?? "una frase larga de verdad";

/** Unmistakable in a seeded database, and never a real merchant's name. */
const PREFIX = "E2E Gimnasio";
const NAME = `${PREFIX} ${Date.now()}`;
/** The bill used for the reconciliation half, kept apart so the cleanup can
 * find both by their shared prefix. */
const MATCHED_NAME = `${PREFIX} conciliado ${Date.now()}`;
const AMOUNT = "120.000";
/** What `just seed` leaves four months of, and declares no bill for. */
const DETECTED = "SPOTIFY COL";
const AMENDED = "135.000";

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

/**
 * Shut the onboarding modals, the way `shot.mjs` does and for the same reason.
 *
 * A fresh browser profile has acknowledged nothing, and the welcome and the
 * "ya quedó conectado" celebration are modal — they sit over every screen and
 * swallow every click. The acks live in `localStorage` by design, which is
 * why they can be written from out here.
 */
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

  // Read at mount, so the page has to load again for them to stay shut.
  if (wrote) await page.goto(`${WEB}/`, { waitUntil: "networkidle" });
}

/** Money as the ledger holds it, so a comparison cannot be fooled by format. */
async function moneyState(call) {
  const [accounts, movements] = await Promise.all([
    call("/financial/accounts?scope=all"),
    call("/financial/transactions?limit=200"),
  ]);

  return {
    balances: accounts.accounts.map((a) => `${a.id}:${a.balance}`).sort(),
    netWorth: accounts.net_worth.map((n) => `${n.currency}:${n.amount}`).sort(),
    movements: movements.transactions.length,
  };
}

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
  // True only while this suite makes the server fail on purpose.
  let forcing = false;
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    if (forcing && message.text().includes("500")) return;

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
  let billId = null;

  try {
    const token = await signIn(page);
    call = client(token);
    await dismissOnboarding(page);

    // ---------------------------------------------------------------- before
    const before = await moneyState(call);
    note(`saldos y movimientos antes: ${before.movements} movimientos`);

    // --------------------------------------------------------- declare, UI
    await page.goto(`${WEB}/facturas`, { waitUntil: "networkidle" });
    check(
      "la pantalla abre",
      await page.getByRole("heading", { name: "Facturas" }).isVisible(),
      true,
    );

    await page.getByRole("button", { name: "Declarar una factura" }).click();
    await page.getByLabel("Nombre").fill(NAME);
    await page.getByLabel("Monto").fill(AMOUNT);
    await page.getByLabel("Primer cobro").fill(firstOfThisMonth());
    // Index 0 is "Ninguna cuenta". Picking a real one is what lets the
    // confirmation below be checked against a balance rather than against a
    // row count — a balance that moved by the wrong amount is the failure
    // worth catching, and an unassigned charge cannot show it.
    await choose(page, page, "Sale de", { index: 1 });
    // `exact`, and not by accident: the suggestions section at the foot of
    // this screen has buttons called «Declarar SPOTIFY COL como factura», and
    // Playwright matches an accessible name by substring unless told not to.
    await page.getByRole("button", { name: "Declarar", exact: true }).click();

    // It shows twice on purpose — once as a declared bill and once as a
    // charge of this month — so the locator has to say which.
    const card = page.getByText(NAME, { exact: true }).first();
    await card.waitFor({ timeout: 10_000 });
    check("la factura aparece en la pantalla", await card.isVisible(), true);
    check(
      "y también entre los cobros del mes",
      await page.getByText(NAME, { exact: true }).count(),
      2,
    );

    // ------------------------------------------------- integrity: it is there
    let stored = await find(call, NAME);
    billId = stored?.id ?? null;
    check("el servidor la guardó", stored !== undefined, true);
    check("con el monto que se tecleó", stored?.amount, "120000");
    check("con la cadencia por defecto", stored?.cadence, "monthly");
    check("activa y sin congelar", [stored?.status, stored?.frozen], ["active", false]);
    check("y contra la cuenta que se eligió", stored?.account_id !== null, true);

    // ------------------------------------- integrity: no money moved at all
    check("declarar no movió ningún saldo", await moneyState(call), before);

    // ------------------------------------------------ the two totals agree
    const view = await call("/financial/bills");
    const total = view.totals.find((t) => t.currency === "COP");
    check("el mes cuenta esta factura", Number(total?.expected) >= 120000, true);
    check(
      "«falta por pagar» nunca es mayor que «este mes»",
      Number(total.outstanding) <= Number(total.expected),
      true,
    );
    check(
      "la pantalla enseña las dos cifras, no una",
      [
        await page.getByRole("figure", { name: "Este mes" }).isVisible(),
        await page.getByRole("figure", { name: "Falta por pagar" }).isVisible(),
      ],
      [true, true],
    );

    // ------------------------------------------------------------ amend, UI
    // A tile's actions sit behind one labelled «Opciones», each a word at 44 px.
    await page.getByRole("button", { name: `Opciones de ${NAME}` }).click();
    const sizes = [];
    for (const label of ["Editar", "Cobrar sola", "Pausar", "Borrar"]) {
      const box = await page
        .getByRole("button", { name: `${label} ${NAME}` })
        .boundingBox();
      sizes.push(box ? Math.round(box.height) >= 44 : false);
    }
    check("las cuatro acciones de la tarjeta son palabras de 44 px", sizes, [
      true,
      true,
      true,
      true,
    ]);
    await page.getByRole("button", { name: `Editar ${NAME}` }).click();
    await page.getByLabel("Monto").fill(AMENDED);
    await page.getByRole("button", { name: "Guardar" }).click();

    stored = await until(call, "corregir el monto", (b) => b?.amount === "135000");
    check("corregir el monto llega al servidor", stored?.amount, "135000");
    check("y sigue sin mover saldos", await moneyState(call), before);

    // ------------------------------------------------------- confirm, UI
    // The one control on this screen that moves money. It lives on the
    // timeline, not on the card, because what gets paid is one charge of one
    // month rather than the bill.
    await page.getByRole("button", { name: `Pagar ${NAME}` }).click();
    await page.getByRole("button", { name: "Confirmar" }).click();

    const paid = await untilCharge(
      call,
      "confirmar el cobro",
      (charge) => charge?.state === "paid",
    );
    check("el cobro queda pagado en el servidor", paid?.state, "paid");
    check("y nombra el movimiento que lo respalda", paid?.movement_id !== null, true);
    // Other bills' paid charges share the timeline; this one is found by the
    // movement the API says paid it.
    const toMovement = page.locator(`a[href="/transacciones/${paid?.movement_id}"]`);
    await toMovement.waitFor({ timeout: 10_000 }).catch(() => {});
    check(
      "el cobro pagado lleva a ese mismo movimiento",
      (await toMovement.count()) > 0
        ? (await toMovement.first().innerText()).trim()
        : null,
      "Ver el movimiento",
    );
    check("con lo que de verdad salió", paid?.settled_amount, "135000");

    const afterPay = await moneyState(call);
    check(
      "confirmar escribió exactamente un movimiento",
      afterPay.movements,
      before.movements + 1,
    );
    check(
      "y movió la cuenta de la factura por exactamente lo confirmado",
      moved(before, afterPay),
      [`${stored.account_id}:-135000`],
    );

    const view2 = await call("/financial/bills");
    const total2 = view2.totals.find((t) => t.currency === "COP");
    check(
      "lo pagado sale de «falta por pagar» y se queda en «este mes»",
      Number(total2.outstanding) < Number(total.expected),
      true,
    );

    // ------------------------------------ integrity: at-least-once, for real
    // Straight at the API, because the screen no longer offers the button —
    // which is the point: this is the retry, the double submit, the second
    // tab. The row's key comes from the bill and the period, so the table
    // refuses it. A count in the use case would pass every unit test and
    // take the money twice here.
    const again = await call(
      `/financial/bills/${billId}/occurrences/${paid.due_on}/pay`,
      { method: "POST", body: "{}" },
    );
    check(
      "confirmar dos veces devuelve el mismo movimiento",
      again.occurrence.movement_id,
      paid.movement_id,
    );
    check("y no mueve nada la segunda vez", await moneyState(call), afterPay);

    // ---------------------------------------------------------- undo, UI
    await page.getByRole("button", { name: `Deshacer el pago ${NAME}` }).click();

    await untilCharge(call, "deshacer el pago", (charge) => charge?.state !== "paid");
    check("deshacer devuelve el saldo tal como estaba", await moneyState(call), before);

    // ---------------------------------------------------------- skip, UI
    await page.getByRole("button", { name: `Saltar ${NAME}` }).click();

    const skipped = await untilCharge(
      call,
      "saltar el cobro",
      (charge) => charge?.state === "skipped",
    );
    check("saltar queda guardado", skipped?.state, "skipped");
    check("y no escribe nada en el ledger", await moneyState(call), before);

    await page.getByRole("button", { name: `Ya no saltarlo ${NAME}` }).click();
    await untilCharge(
      call,
      "deshacer el salto",
      (charge) => charge?.state !== "skipped",
    );

    // ------------------------------------------------- a failure is said, UI
    // Pausing used to fail in silence: the tile simply stayed as it was.
    forcing = true;
    await page.route(`${API}/financial/bills/${billId}/pause`, (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "No se pudo pausar" }),
      }),
    );
    await page.getByRole("button", { name: `Opciones de ${NAME}` }).click();
    await page.getByRole("button", { name: `Pausar ${NAME}` }).click();
    const said = page.getByRole("alert").filter({ hasText: /pausar|servidor/i });
    await said.first().waitFor({ timeout: 10_000 });
    check("si pausar falla, la tarjeta lo dice", await said.first().isVisible(), true);
    await page.unroute(`${API}/financial/bills/${billId}/pause`);
    forcing = false;

    // ------------------------------------------------------------ pause, UI
    await page.getByRole("button", { name: `Pausar ${NAME}` }).click();

    stored = await until(call, "pausar", (b) => b?.status === "paused");
    check("pausar llega al servidor", stored?.status, "paused");
    check("una pausada no predice nada", stored?.next_occurrence, null);

    const paused = await call("/financial/bills");
    check(
      "y sale del listado de cobros del mes",
      paused.occurrences.some((o) => o.bill_id === billId),
      false,
    );

    // ----------------------------------------------------------- resume, UI
    await page.getByRole("button", { name: `Opciones de ${NAME}` }).click();
    await page.getByRole("button", { name: `Reanudar ${NAME}` }).click();

    stored = await until(call, "reanudar", (b) => b?.status === "active");
    check("reanudar la devuelve", stored?.status, "active");
    check("y vuelve a predecir", stored?.next_occurrence !== null, true);

    // ----------------------------------------------------------- delete, UI
    // Deleting asks first, in place — a tap that removes something should
    // have to be meant.
    await page.getByRole("button", { name: `Opciones de ${NAME}` }).click();
    await page.getByRole("button", { name: `Borrar ${NAME}` }).click();
    const confirm = page.getByRole("button", { name: `Sí, borrar ${NAME}` });
    check("borrar pregunta antes", await confirm.isVisible(), true);
    await confirm.click();

    await until(call, "borrar", (b) => b === undefined);
    check("borrar la quita del servidor", (await find(call, NAME)) === undefined, true);
    billId = null;
    check("borrar tampoco movió saldos", await moneyState(call), before);

    // ------------------------------------------------ reconciliation, UI
    // The safety net under the automatic charge, and the one property no
    // unit test can prove: when a movement that *is* the charge is already
    // in the ledger, opening the screen has to answer the charge **with that
    // movement** and write nothing. A second row here is the double count
    // this whole feature exists to remove, produced by the feature itself.
    const period = firstOfThisMonth();
    const matched = await call("/financial/bills", {
      method: "POST",
      body: JSON.stringify({
        name: MATCHED_NAME,
        amount: "120000",
        currency: "COP",
        cadence: "monthly",
        starts_on: period,
      }),
    });
    billId = matched.id;
    const alreadyPaid = await call("/financial/transactions", {
      method: "POST",
      body: JSON.stringify({
        direction: "outgoing",
        amount: "120000",
        currency: "COP",
        // Noon UTC on the charge's own day: the same instant the confirmed
        // charge would carry, and the same calendar day in every zone this
        // is read in.
        occurred_at: Math.floor(Date.parse(`${period}T12:00:00Z`) / 1000),
        counterparty: MATCHED_NAME,
      }),
    });

    // Taken *after* the movement exists: what must not change from here is
    // the number of rows, which is the whole question.
    const withMovement = await moneyState(call);

    await page.goto(`${WEB}/facturas`, { waitUntil: "networkidle" });
    const settled = await untilCharge(
      call,
      "conciliar el cobro",
      (charge) => charge?.state === "paid",
      15_000,
      MATCHED_NAME,
    );
    check(
      "el cobro queda pagado por el movimiento que ya estaba",
      settled?.state,
      "paid",
    );
    check("y la pantalla dice que no escribió nada", settled?.settled_by, "matched");
    check(
      "conciliar no escribió ningún movimiento",
      await moneyState(call),
      withMovement,
    );
    check(
      "y la pantalla lo cuenta",
      await page
        .getByText("Ya estaba pagada por un movimiento tuyo", { exact: false })
        .first()
        .isVisible()
        .catch(() => false),
      true,
    );

    // And the way back, which is not the same undo: forgetting the link must
    // leave the bank's own movement exactly where it is.
    await page
      .getByRole("button", { name: `No es este ${MATCHED_NAME}` })
      .first()
      .click();
    const unlinked = await untilCharge(
      call,
      "desenlazar el cobro",
      (charge) => charge?.state !== "paid",
      10_000,
      MATCHED_NAME,
    );
    check("desenlazar deja el cobro sin pagar", unlinked?.state !== "paid", true);
    check("y no borra el movimiento", await moneyState(call), withMovement);

    // ------------------------------------------------------- autopay, UI
    await page.getByRole("button", { name: `Opciones de ${MATCHED_NAME}` }).click();
    await page.getByRole("button", { name: `Cobrar sola ${MATCHED_NAME}` }).click();
    check(
      "armar el cobro automático avisa antes de que escriba plata",
      await page
        .getByText("escribirá un movimiento", { exact: false })
        .first()
        .isVisible(),
      true,
    );
    await page.getByRole("button", { name: `Sí, cobrar sola ${MATCHED_NAME}` }).click();
    const armed = await until(
      call,
      "armar el cobro automático",
      (bill) => bill?.autopay === true,
      10_000,
      MATCHED_NAME,
    );
    check("queda armada en el servidor", armed?.autopay, true);
    check("y guarda desde cuándo", typeof armed?.autopay_from, "string");
    check("armarla no cobró nada", await moneyState(call), withMovement);

    await call(`/financial/bills/${matched.id}`, { method: "DELETE" });
    await call(`/financial/transactions/${alreadyPaid.id}`, {
      method: "DELETE",
    });
    billId = null;

    // ------------------------------------------------- suggestions, UI
    // The detector's half. `just seed` leaves four months of SPOTIFY COL in
    // the ledger and declares no bill for it, so the section has to be
    // proposing it — and accepting has to produce an ordinary declared bill
    // without moving a peso, because a guess may not be what moves money.
    await page.goto(`${WEB}/facturas`, { waitUntil: "networkidle" });
    const suggestion = page.getByRole("button", {
      name: `Declarar ${DETECTED} como factura`,
    });
    // Waited for rather than asked about: the suggestions are a separate,
    // slower read that is deliberately not blocking the screen, so a bare
    // `isVisible` races it and reports "no había sugerencia" for a section
    // that was still on its way.
    const proposed = await suggestion
      .waitFor({ timeout: 15_000 })
      .then(() => true)
      .catch(() => false);

    if (proposed) {
      await suggestion.click();
      const accepted = await until(
        call,
        "aceptar la sugerencia",
        (bill) => bill !== undefined,
        10_000,
        DETECTED,
      );
      check("aceptar una sugerencia declara la factura", accepted?.name, DETECTED);
      check("con la cadencia que el detector leyó", accepted?.cadence, "monthly");
      check("y sin mover un peso", await moneyState(call), before);

      // Guarded: `until` answers `undefined` when it gives up, and reaching
      // into that turns a clear FALLA into a TypeError that swallows every
      // check below it.
      if (accepted !== undefined) {
        const marked = await call("/financial/recurring");
        const back = marked.series.find((each) => each.name === DETECTED);
        check(
          "y la sugerencia vuelve marcada como ya declarada",
          back?.bill_id,
          accepted.id,
        );

        await call(`/financial/bills/${accepted.id}`, { method: "DELETE" });
      }
    } else {
      note(`no había sugerencia para ${DETECTED} — ¿corriste 'just seed'?`);
    }

    check("la pantalla no registró errores", problems, []);
  } catch (error) {
    failures += 1;
    steps.push(` FALLA ${error.message}`);
  } finally {
    // Whatever happened, the seeded database goes back as it was — looked up
    // by name rather than by the id captured along the way, because a failure
    // before that point used to leave the bill behind for the next run to
    // trip over. It also sweeps anything an earlier run leaked.
    if (call) {
      try {
        const view = await call("/financial/bills");
        const mine = view.bills.filter(
          (bill) => bill.name.startsWith(PREFIX) || bill.name === DETECTED,
        );

        for (const bill of mine) {
          await call(`/financial/bills/${bill.id}`, { method: "DELETE" });
        }

        if (mine.length > 0) note(`${mine.length} factura(s) de prueba borradas`);
      } catch {
        note("no pude limpiar las facturas de prueba — revísalas a mano");
      }
    }

    await browser.close();
  }

  console.log(steps.join("\n"));
  console.log(
    failures === 0
      ? "\ne2e facturas: todo bien. Declarar no movió nada, confirmar movió una vez."
      : `\ne2e facturas: ${failures} problema(s).`,
  );
  process.exitCode = failures === 0 ? 0 : 1;
}

/** The first of the current month, so the charge falls inside the window. */
function firstOfThisMonth() {
  const now = new Date();

  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
}

/**
 * Poll the server until it agrees, or give up loudly.
 *
 * Replaces the fixed sleeps this script started with. A sleep long enough to
 * be safe is a slow suite, and one short enough to be fast is a suite that
 * fails on a cold Vite module — which is exactly what it did. Waiting for the
 * condition is both faster and the only version that means anything.
 */
async function until(call, describe, predicate, timeoutMs = 10_000, name = NAME) {
  const deadline = Date.now() + timeoutMs;
  let last;

  while (Date.now() < deadline) {
    last = await find(call, name);

    if (predicate(last)) return last;

    await new Promise((resolve) => setTimeout(resolve, 150));
  }

  steps.push(` FALLA ${describe} (el servidor no lo reflejó en ${timeoutMs} ms)`);
  failures += 1;

  return last;
}

async function find(call, name) {
  const view = await call("/financial/bills");

  return view.bills.find((bill) => bill.name === name);
}

/** `until`, but watching one charge of the window rather than the bill. */
async function untilCharge(call, describe, predicate, timeoutMs = 10_000, name = NAME) {
  const deadline = Date.now() + timeoutMs;
  let last;

  while (Date.now() < deadline) {
    const view = await call("/financial/bills");
    const bill = view.bills.find((each) => each.name === name);
    last = view.occurrences.find((charge) => charge.bill_id === bill?.id);

    if (predicate(last)) return last;

    await new Promise((resolve) => setTimeout(resolve, 150));
  }

  steps.push(` FALLA ${describe} (el servidor no lo reflejó en ${timeoutMs} ms)`);
  failures += 1;

  return last;
}

/**
 * Which balances changed between two readings, and by how much.
 *
 * A diff rather than a comparison, because "nothing moved" and "exactly this
 * moved" are the two assertions this script is made of, and only the second
 * one catches a charge posted at the wrong figure.
 */
function moved(before, after) {
  const was = new Map(before.balances.map((row) => row.split(/:(?=[^:]*$)/)));

  return after.balances
    .map((row) => {
      const [id, balance] = row.split(/:(?=[^:]*$)/);
      const delta = Number(balance) - Number(was.get(id) ?? 0);

      return delta === 0 ? null : `${id}:${delta}`;
    })
    .filter((row) => row !== null);
}

await main();
