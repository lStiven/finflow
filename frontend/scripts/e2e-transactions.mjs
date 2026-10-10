/**
 * Drive a movement's detail in a real browser, and check what is behind it.
 *
 *   just up                  # emulator, seeded data, API and workers
 *   just web                 # the frontend, in another terminal
 *   just e2e-transacciones   # this
 *
 * What UX-07 changed is what a movement with no account and a movement in the
 * wrong category let somebody do from where they noticed it:
 *
 * 1. **«Cómo funciona» teaches once.** Open on the first visit, closed by
 *    «Entendido», still closed after a reload, back with one tap.
 * 2. **«Sin asignar» says the remedy.** The list explains it when filtered on
 *    it; the detail offers exactly the accounts the API would accept — open,
 *    same currency — and nothing else.
 * 3. **Assigning moves that account's balance, and only by that amount**,
 *    read from the API before and after rather than from the screen.
 * 4. **The category changes the merchant**, as the screen says it will: the
 *    merchant itself is re-read from `/merchants/{id}`.
 * 5. **The redundant «Estado» row is gone**, the page does not scroll
 *    sideways on a phone, and nothing is logged as an error.
 *
 * It cleans up after itself, including when an assertion fails: the movement
 * is erased (which gives the account its money back, and that is checked) and
 * the merchant goes back to its first category. The merchant itself stays —
 * there is no endpoint to remove one — under a name only this suite writes,
 * so every run reuses the same one.
 */

import process from "node:process";
import { chromium } from "playwright";

const WEB = process.env.FINFLOW_WEB_URL ?? "http://localhost:5173";
const API = process.env.FINFLOW_API_URL ?? "http://localhost:8000";
const DEMO_EMAIL = process.env.FINFLOW_DEMO_EMAIL ?? "demo@finflow.local";
const DEMO_PASSWORD = process.env.FINFLOW_DEMO_PASSWORD ?? "una frase larga de verdad";

/** Never a real merchant's name; the sweep at the end finds it by this. */
const COUNTERPARTY = "E2E Movimiento Sin Cuenta";
const AMOUNT = 12345;
const FIRST_CATEGORY = "education";
const NEXT_CATEGORY = "restaurants";
const NEXT_LABEL = "Restaurantes";

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
  await page.evaluate(() => {
    const raw = window.localStorage.getItem("finflow.session");
    if (raw === null) return;

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
  });
}

async function noSidewaysScroll(page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth + 1,
  );
}

/** Erase what an earlier run may have left, by the name only this suite uses. */
async function sweep(call) {
  const left = await call(
    `/financial/transactions?search=${encodeURIComponent(COUNTERPARTY)}`,
  );

  for (const movement of left.transactions ?? []) {
    await call(`/financial/transactions/${movement.id}`, { method: "DELETE" });
  }
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
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    // The dashboard asks for two 404s on purpose («no hay plan declarado»).
    if (message.text().includes("404 (Not Found)")) return;

    problems.push(`console: ${message.text()}`);
  });

  let call;
  let movementId = null;
  let merchantId = null;

  try {
    const token = await signIn(page);
    call = client(token);
    await dismissOnboarding(page);
    await sweep(call);

    // ------------------------------------------------ «Cómo funciona», once
    await page.goto(`${WEB}/transacciones`, { waitUntil: "networkidle" });
    const help = page.getByRole("heading", { name: "Cómo funciona" });
    check("la primera visita abre «Cómo funciona»", await help.isVisible(), true);

    await page.getByRole("button", { name: "Entendido" }).click();
    check("«Entendido» lo cierra", await help.isVisible(), false);

    await page.reload({ waitUntil: "networkidle" });
    check("y no vuelve a abrirse solo", await help.isVisible(), false);

    await page.getByRole("button", { name: "Cómo funciona" }).click();
    check("el botón de la cabecera lo vuelve a abrir", await help.isVisible(), true);

    // ---------------------------------------------- a movement with no account
    const created = await call("/financial/transactions", {
      method: "POST",
      body: JSON.stringify({
        direction: "outgoing",
        amount: String(AMOUNT),
        occurred_at: Math.floor(Date.now() / 1000) - 60,
        counterparty: COUNTERPARTY,
        currency: "COP",
        account_id: null,
        bank: "",
        note: null,
        category: FIRST_CATEGORY,
      }),
    });
    movementId = created.id;
    const fresh = await call(`/financial/transactions/${movementId}`);
    merchantId = fresh.merchant?.id ?? null;
    check("el movimiento nace sin cuenta", fresh.account_id, null);
    check("y con su comercio", merchantId !== null, true);
    if (merchantId) {
      // A run that died half-way may have left the merchant elsewhere.
      await call(`/merchants/${merchantId}`, {
        method: "PATCH",
        body: JSON.stringify({ category: FIRST_CATEGORY }),
      });
    }

    await page.goto(`${WEB}/transacciones?unassigned=true`, {
      waitUntil: "networkidle",
    });
    check(
      "filtrando «Sin asignar», la lista explica cómo se resuelve",
      await page.getByText("Llegaron sin saber de qué cuenta son").isVisible(),
      true,
    );

    // -------------------------------------------------------------- the detail
    await page.goto(`${WEB}/transacciones/${movementId}`, { waitUntil: "networkidle" });
    check(
      "la fila «Estado», que repetía «Cuenta», ya no está",
      await page.getByText("Estado", { exact: true }).count(),
      0,
    );
    check(
      "el aviso de «sin asignar» está",
      await page.getByText("No está en ninguna cuenta").isVisible(),
      true,
    );

    const accounts = await call("/financial/accounts?scope=all");
    const acceptable = accounts.accounts.filter(
      (account) => account.closed_at === null && account.currency === "COP",
    );
    await page.getByRole("combobox", { name: "Asignar a", exact: true }).click();
    const assignList = page.getByRole("listbox", { name: "Asignar a", exact: true });
    const offered = (await assignList.getByRole("option").allTextContents()).filter(
      (label) => label !== "Elige una cuenta",
    );
    check(
      "ofrece exactamente las cuentas abiertas en su moneda",
      [...offered].sort(),
      acceptable.map((account) => account.name).sort(),
    );

    const target = acceptable.find((account) => account.category === "asset");
    if (!target) throw new Error("El seed no tiene una cuenta de activos en COP");
    const before = Number(target.balance);

    await assignList.getByRole("option", { name: target.name, exact: true }).click();
    await page.getByRole("button", { name: "Asignar", exact: true }).click();
    await page.getByText(`Quedó en ${target.name}`).waitFor({ timeout: 10_000 });

    const assigned = await call(`/financial/transactions/${movementId}`);
    check("la API lo tiene en esa cuenta", assigned.account_id, target.id);
    const after = await call(`/financial/accounts/${target.id}`);
    check(
      "y el saldo bajó exactamente lo del movimiento",
      Number(after.balance),
      before - AMOUNT,
    );
    check(
      "el aviso se fue, porque ya no aplica",
      await page.getByText("No está en ninguna cuenta").count(),
      0,
    );

    // ------------------------------------------------------- the category
    await page.getByRole("button", { name: /Educación/ }).click();
    await choose(page, page, "Categoría", { value: NEXT_CATEGORY });
    await page.getByRole("button", { name: "Guardar", exact: true }).click();
    await page
      .getByText(`${COUNTERPARTY} ahora es ${NEXT_LABEL}`, { exact: false })
      .waitFor({ timeout: 10_000 });

    const merchant = await call(`/merchants/${merchantId}`);
    check("la categoría quedó en el comercio", merchant.category, NEXT_CATEGORY);
    check(
      "y la fila la muestra",
      await page.getByRole("button", { name: new RegExp(NEXT_LABEL) }).isVisible(),
      true,
    );

    check(
      "la pantalla no se va de lado en un teléfono",
      await noSidewaysScroll(page),
      true,
    );

    // -------------------------------------------------- and back as it was
    await call(`/financial/transactions/${movementId}`, { method: "DELETE" });
    movementId = null;
    const restored = await call(`/financial/accounts/${target.id}`);
    check(
      "borrado el movimiento, la cuenta recupera su saldo",
      Number(restored.balance),
      before,
    );

    check("la pantalla no registró errores", problems, []);
  } catch (error) {
    failures += 1;
    steps.push(` FALLA ${error.message}`);
  } finally {
    if (call) {
      try {
        if (movementId) {
          await call(`/financial/transactions/${movementId}`, { method: "DELETE" });
        }
        await sweep(call);
        if (merchantId) {
          await call(`/merchants/${merchantId}`, {
            method: "PATCH",
            body: JSON.stringify({ category: FIRST_CATEGORY }),
          });
        }
      } catch {
        note("no pude limpiar del todo — revisa el movimiento y el comercio de prueba");
      }
    }

    await browser.close();
  }

  console.log(steps.join("\n"));
  console.log(
    failures === 0
      ? "\ne2e transacciones: todo bien. «Sin asignar» se resuelve donde se ve, y la categoría cambia en su comercio."
      : `\ne2e transacciones: ${failures} problema(s).`,
  );
  process.exitCode = failures === 0 ? 0 : 1;
}

await main();
