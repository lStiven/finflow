/**
 * Export the movements from the Transacciones screen in a real browser, and
 * check the file against the API.
 *
 *   just up               # emulator, seeded data, API and workers
 *   just web              # the frontend, in another terminal
 *   just e2e-export       # this
 *
 * What only a real stack can say:
 *
 * 1. **What the dialog chooses is what the file holds, every page of it.**
 *    «Todo» + «Gastos» carries exactly the movements `/financial/transactions`
 *    pages through for the same filter — same ids, same amounts — and not the
 *    25 the screen drew; the count the dialog announced is that number.
 * 2. **A period and an account narrow it the way the API does**: «Mes pasado»
 *    on one account is that month, half-open in Bogotá, on that account.
 * 3. **A range that ends before it starts cannot be downloaded**, and says so.
 * 4. **Excel gets a real workbook**, named for today and opening as a zip.
 * 5. **Text from a bank cannot become a formula**, and the list's search is
 *    carried into the dialog.
 * 6. **Exporting moves nothing.** Balances, net worth and ledger count, before
 *    and after.
 *
 * Exits non-zero on the first mismatch, and removes the movement it wrote,
 * including when an assertion fails.
 */

import { readFile } from "node:fs/promises";
import process from "node:process";
import { chromium } from "playwright";

const WEB = process.env.FINFLOW_WEB_URL ?? "http://localhost:5173";
const API = process.env.FINFLOW_API_URL ?? "http://localhost:8000";
const DEMO_EMAIL = process.env.FINFLOW_DEMO_EMAIL ?? "demo@finflow.local";
const DEMO_PASSWORD = process.env.FINFLOW_DEMO_PASSWORD ?? "una frase larga de verdad";

/** Unmistakable in a seeded database, and a formula if a spreadsheet ran it. */
const HOSTILE = "=1+1 E2E Export";
const HOSTILE_SEARCH = "E2E Export";

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

  return accessToken.startsWith("Bearer ") ? accessToken : `Bearer ${accessToken}`;
}

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

/** Every page of a filter, the way a client that only had the list would. */
async function everyPage(call, query) {
  const found = [];

  for (let offset = 0; ; offset += 200) {
    const page = await call(
      `/financial/transactions?${query}&limit=200&offset=${offset}`,
    );
    found.push(...page.transactions);
    if (found.length >= page.total || page.transactions.length === 0) return found;
  }
}

/** RFC 4180, enough of it for a file this app wrote. */
function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];

    if (quoted) {
      if (char === '"' && text[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        cell += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(cell);
      cell = "";
    } else if (char === "\n") {
      row.push(cell.replace(/\r$/, ""));
      rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += char;
    }
  }

  if (cell !== "" || row.length > 0) rows.push([...row, cell]);

  const [header, ...body] = rows;
  return body.map((values) =>
    Object.fromEntries(header.map((key, i) => [key, values[i]])),
  );
}

/**
 * Open the dialog, choose, download.
 *
 * `choices` are the labels of the segmented buttons to press, in order —
 * «Todo», «Gastos», «CSV» — and `account` the name to pick in the account
 * select, if any.
 */
async function exportFrom(page, { choices = [], account } = {}) {
  await page.getByRole("button", { name: "Exportar" }).click();
  const dialog = page.getByRole("dialog", { name: "Exportar movimientos" });
  await dialog.waitFor();

  for (const label of choices) {
    await dialog.getByRole("button", { name: label, exact: true }).click();
  }
  if (account)
    await dialog.getByLabel("Cuenta", { exact: true }).selectOption({ label: account });

  const button = dialog.getByRole("button", { name: /^Descargar/ });
  await button.waitFor();
  await page.waitForFunction(
    () =>
      !document
        .querySelector('[role="dialog"] footer button')
        ?.hasAttribute("disabled"),
    undefined,
    { timeout: 10_000 },
  );
  const announced = await dialog.locator("footer p").first().innerText();

  const [file] = await Promise.all([
    page.waitForEvent("download", { timeout: 15_000 }),
    button.click(),
  ]);
  await dialog.waitFor({ state: "detached" });

  return {
    announced,
    name: file.suggestedFilename(),
    bytes: await readFile(await file.path()),
  };
}

function rowsOf(bytes) {
  return parseCsv(bytes.toString("utf8").replace(/^\ufeff/, ""));
}

/** Midnight in Bogotá of a `YYYY-MM-DD` day, as epoch seconds. */
function bogotaMidnight(day) {
  return Date.parse(`${day}T00:00:00-05:00`) / 1000;
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
    acceptDownloads: true,
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
  let hostileId = null;

  try {
    call = client(await signIn(page));
    const before = await untouchable(call);

    // 1 · «Todo» + «Gastos» is every outgoing movement, not the page.
    await page.goto(`${WEB}/transacciones`, { waitUntil: "networkidle" });
    const csv = await exportFrom(page, { choices: ["Todo", "Gastos", "CSV"] });
    const text = csv.bytes.toString("utf8");
    check(
      "el CSV empieza con BOM para que Excel lea UTF-8",
      text.startsWith("\ufeff"),
      true,
    );

    const rows = rowsOf(csv.bytes);
    const listed = await everyPage(call, "direction=outgoing");
    check(
      "el diálogo anuncia cuántos van",
      csv.announced.includes(String(listed.length)),
      true,
    );
    check(
      "el CSV trae todos los gastos, no solo la página",
      rows.length,
      listed.length,
    );
    check(
      "mismos movimientos, en el mismo orden",
      rows.map((row) => row.ID),
      listed.map((movement) => movement.id),
    );
    check(
      "mismos montos que la API",
      rows.map((row) => row.Monto),
      listed.map((movement) => movement.amount),
    );
    check(
      "ninguno se llama ingreso",
      rows.filter((row) => row.Tipo === "Ingreso").length,
      0,
    );
    check(
      "el nombre del archivo es de hoy",
      /^finflow-movimientos-\d{4}-\d{2}-\d{2}\.csv$/.test(csv.name),
      true,
    );

    // 2 · «Mes pasado» on one account is exactly that month on that account.
    // The account is one that *had* movements last month: on the 1st, an
    // account busy only this month exports nothing, and the dialog rightly
    // refuses to download an empty file.
    const accounts = (await call("/financial/accounts")).accounts;
    const today = new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/Bogota",
    }).format(new Date());
    const [year, month] = today.split("-").map(Number);
    const lastMonth =
      month === 1 ? `${year - 1}-12` : `${year}-${String(month - 1).padStart(2, "0")}`;
    const from = bogotaMidnight(`${lastMonth}-01`);
    const to = bogotaMidnight(`${today.slice(0, 7)}-01`);

    let account = null;
    let expected = [];
    for (const candidate of accounts) {
      const found = await everyPage(
        call,
        `account_id=${candidate.id}&from=${from}&to=${to}`,
      );
      if (found.length > 0) {
        account = candidate;
        expected = found;
        break;
      }
    }

    if (account) {
      const scoped = await exportFrom(page, {
        choices: ["Mes pasado", "Todos", "CSV"],
        account: account.name,
      });
      check(
        `«Mes pasado» en ${account.name}: los mismos movimientos que la API`,
        rowsOf(scoped.bytes).map((row) => row.ID),
        expected.map((movement) => movement.id),
      );
      check(
        "todos en esa cuenta",
        [...new Set(rowsOf(scoped.bytes).map((row) => row.Cuenta))].filter(Boolean),
        [account.name],
      );
    } else if (accounts[0]) {
      await page.getByRole("button", { name: "Exportar" }).click();
      const dialog = page.getByRole("dialog", { name: "Exportar movimientos" });
      for (const label of ["Mes pasado", "Todos", "CSV"]) {
        await dialog.getByRole("button", { name: label, exact: true }).click();
      }
      await dialog
        .getByLabel("Cuenta", { exact: true })
        .selectOption({ label: accounts[0].name });
      await page.waitForLoadState("networkidle");
      check(
        "sin movimientos el mes pasado, no se descarga un archivo vacío",
        await dialog.getByRole("button", { name: /^Descargar/ }).isDisabled(),
        true,
      );
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "detached" });
    }

    // 3 · A range that ends before it starts cannot be downloaded.
    await page.getByRole("button", { name: "Exportar" }).click();
    const dialog = page.getByRole("dialog", { name: "Exportar movimientos" });
    await dialog.getByRole("button", { name: "Elegir fechas", exact: true }).click();
    await dialog.getByLabel("Desde").fill("2026-09-10");
    await dialog.getByLabel("Hasta").fill("2026-09-01");
    check(
      "un rango al revés no se puede descargar",
      await dialog.getByRole("button", { name: /^Descargar/ }).isDisabled(),
      true,
    );
    check(
      "y dice por qué",
      (await dialog.locator("footer p").first().innerText()).includes("después"),
      true,
    );
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "detached" });

    // 4 · Excel gets a workbook.
    const xlsx = await exportFrom(page, { choices: ["Todo", "Excel (.xlsx)"] });
    check("el Excel es un .xlsx", xlsx.name.endsWith(".xlsx"), true);
    check("y abre como zip (PK)", xlsx.bytes.subarray(0, 2).toString("latin1"), "PK");

    // 5 · A counterparty that is a formula is displayed, never run — and the
    // list's search is carried into the dialog.
    const written = await call("/financial/transactions", {
      method: "POST",
      body: JSON.stringify({
        direction: "outgoing",
        amount: "1234.50",
        currency: "COP",
        occurred_at: Math.floor(Date.now() / 1000) - 60,
        counterparty: HOSTILE,
      }),
    });
    hostileId = written.id;
    await page.goto(
      `${WEB}/transacciones?search=${encodeURIComponent(HOSTILE_SEARCH)}`,
      {
        waitUntil: "networkidle",
      },
    );
    const [hostile, ...others] = rowsOf(
      (await exportFrom(page, { choices: ["Este mes", "CSV"] })).bytes,
    );
    check("la búsqueda de la lista viaja al archivo", others.length, 0);
    check("la fórmula queda como texto", hostile?.["Texto del banco"], `'${HOSTILE}`);
    check("y su monto es un número exacto", hostile?.Monto, "1234.50");

    await call(`/financial/transactions/${hostileId}`, { method: "DELETE" });
    hostileId = null;

    // 6 · Nothing moved.
    check("exportar no movió ningún saldo", await untouchable(call), before);
    check("la pantalla no registró errores", problems, []);
  } catch (error) {
    failures += 1;
    steps.push(` FALLA ${error.message}`);
  } finally {
    if (call && hostileId) {
      try {
        await call(`/financial/transactions/${hostileId}`, { method: "DELETE" });
      } catch {
        steps.push("  ··   no pude borrar el movimiento de prueba");
      }
    }

    await browser.close();
  }

  console.log(steps.join("\n"));
  console.log(
    failures === 0
      ? "\ne2e exportar: todo bien. El archivo es la lista entera, y no mueve un peso."
      : `\ne2e exportar: ${failures} problema(s).`,
  );
  process.exitCode = failures === 0 ? 0 : 1;
}

await main();
