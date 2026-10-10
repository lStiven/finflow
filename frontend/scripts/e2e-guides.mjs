/**
 * Drive the guides and the empty screens in a real browser, against the API.
 *
 *   just up            # emulator, seeded data, API and workers
 *   just web           # the frontend, in another terminal
 *   just e2e-guias     # this
 *
 * F5 of the UX plan, both halves, as one newly registered person walked
 * through the states a connection can be in:
 *
 * 1. **An empty list says why** (UX-05). With no bank chosen, before Gmail
 *    confirms, confirmed with nothing arrived, and with mail thrown away for
 *    its sender, Transacciones names that reason and links to the step that
 *    fixes it — and what cannot be seen (a Gmail filter) is said as such.
 * 2. **Guías is a list of tasks** (UX-13). What is left is answered by the
 *    server and counted; declaring an account ticks its row as «Comprobado».
 * 3. **Every task and every problem leads somewhere real**: each link opens a
 *    screen with its title and no error.
 * 4. Nothing scrolls sideways at 390 or 320 px, and nothing logs an error.
 *
 * The person stays behind in the emulator, like the connect suite's.
 */

import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const WEB = process.env.FINFLOW_WEB_URL ?? "http://localhost:5173";
const API = process.env.FINFLOW_API_URL ?? "http://localhost:8000";
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const RUN = Date.now().toString(36);

const BANK_DOMAIN = "an.notificacionesbancolombia.com";
const STRANGER = "alertas@otrobanco.com";
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

async function until(read, done, timeout = ARRIVAL_MS) {
  const deadline = Date.now() + timeout;
  for (;;) {
    const value = await read();
    if (done(value) || Date.now() > deadline) return value;
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
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
          "  just up     # emulador, datos de prueba, la API y los procesos\n" +
          "  just web    # el frontend, en otra terminal",
      );
      process.exitCode = 1;

      return;
    }
  }

  const email = `e2e-guias-${RUN}@finflow.local`;
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

  /** The reason the empty movements list gives, read off the screen. */
  async function reason(path = "/transacciones") {
    await page.goto(`${WEB}${path}`, { waitUntil: "networkidle" });
    // By its words: the shell's own nudge also links to /conectar.
    const link = page
      .getByRole("link", {
        name: /^(Conectar mi banco|Seguir conectando|Revisar la conexión|Ver el estado de la conexión|Ver qué llegó)$/,
      })
      .first();
    await link.waitFor({ timeout: 10_000 }).catch(() => {});
    return {
      title: (await page.locator("p.font-medium").allTextContents()).find((text) =>
        /banco|Gmail|correo|remitente|alertas/.test(text),
      ),
      href: (await link.count()) > 0 ? await link.getAttribute("href") : null,
    };
  }

  try {
    await page.goto(`${WEB}/login`, { waitUntil: "domcontentloaded" });
    await page.getByLabel("Correo").fill(email);
    await page.getByLabel("Contraseña").fill(password);
    await page.locator('form button[type="submit"]').click();
    await page.waitForURL((url) => !url.pathname.startsWith("/login"));
    await page.evaluate(() => {
      const { userId } = JSON.parse(window.localStorage.getItem("finflow.session"));
      window.localStorage.setItem(
        `finflow.onboarding.${userId}`,
        JSON.stringify({ welcomeSeen: true, introSeen: true, readyCelebrated: true }),
      );
      // The help panels are UX-01's, checked elsewhere.
      window.localStorage.setItem(
        `finflow.help.${userId}`,
        JSON.stringify(["transacciones", "resumen"]),
      );
    });

    // ------------------------------------- UX-05: why the list is empty
    let said = await reason();
    check("sin banco elegido, Transacciones vacía dice que falta conectarlo", said, {
      title: "Tu banco todavía no está conectado",
      href: "/conectar?paso=1",
    });
    check(
      "y ofrece agregar a mano",
      await page.getByRole("link", { name: "Agregar a mano" }).first().isVisible(),
      true,
    );
    said = await reason("/");
    check(
      "Resumen vacío dice lo mismo",
      said.title,
      "Tu banco todavía no está conectado",
    );

    await call("/identity/inbox", {
      method: "PATCH",
      body: JSON.stringify({ allowed_domains: [BANK_DOMAIN], allowed_addresses: [] }),
    });
    said = await reason();
    check("con banco pero sin confirmar, dice que falta Gmail", said, {
      title: "Falta que Gmail confirme tu dirección",
      href: "/conectar?paso=2",
    });

    const { address } = await call("/ingestion/setup");
    fixture("confirm", address);
    said = await reason();
    check(
      "confirmada y sin correos, lo dice",
      said.title,
      "Todavía no llega ningún correo de tu banco",
    );
    check(
      "y dice que el filtro de Gmail no se puede ver desde aquí",
      await page.getByText("no se puede ver desde aquí", { exact: false }).isVisible(),
      true,
    );

    const delivered = await fetch(`${API}/ingestion/bank-notifications`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        recipient: address,
        message_id: `<e2e-guias-${RUN}@finflow.local>`,
        sender: STRANGER,
        subject: "Alerta",
        raw_content: "Compraste $12.000 en ALGO",
      }),
    });
    check("el correo de un desconocido llega al buzón", delivered.ok, true);
    await until(
      () => call("/ingestion/notifications?limit=5"),
      (view) => view.notifications.some((each) => each.status === "ignored"),
    );
    said = await reason();
    check(
      "con correos descartados, nombra al remitente",
      [
        said.title,
        await page.getByText(STRANGER, { exact: false }).first().isVisible(),
      ],
      ["Llegaron correos de un remitente que no aprobaste", true],
    );

    // ---------------------------------- UX-13: what is left, from the server
    await page.goto(`${WEB}/guias`, { waitUntil: "networkidle" });
    await page.getByText("listos").first().waitFor({ timeout: 10_000 });
    check(
      "Guías cuenta lo que falta: nada listo todavía",
      await page.getByText("0 de 4 listos").isVisible(),
      true,
    );
    check(
      "y conectar el banco lleva al asistente",
      await page.getByRole("link", { name: /^Conectar tu banco/ }).getAttribute("href"),
      "/conectar",
    );

    await call("/financial/accounts", {
      method: "POST",
      body: JSON.stringify({ name: "E2E Guías", kind: "cash", currency: "COP" }),
    });
    await page.reload({ waitUntil: "networkidle" });
    await page
      .getByText("1 de 4 listos")
      .waitFor({ timeout: 10_000 })
      .catch(() => {});
    check(
      "declarar una cuenta la marca como comprobada",
      [
        await page.getByText("1 de 4 listos").isVisible(),
        await page.getByText("Comprobado").count(),
      ],
      [true, 1],
    );

    check("a 390 px Guías no se va de lado", await sideways(page), false);
    await page.setViewportSize({ width: 320, height: 640 });
    check("ni a 320 px", await sideways(page), false);
    await page.setViewportSize({ width: 390, height: 844 });

    // -------------------------------------- every link leads somewhere real
    const hrefs = [
      ...new Set(
        await page
          .locator("a.group[href^='/']")
          .evaluateAll((anchors) =>
            anchors.map((anchor) => anchor.getAttribute("href")),
          ),
      ),
    ].filter((href) => href !== "/guias");
    note(`${hrefs.length} destinos distintos en Guías`);
    const broken = [];
    for (const href of hrefs) {
      await page.goto(`${WEB}${href}`, { waitUntil: "networkidle" });
      const titles = await page.locator("h1").count();
      const failed = await page
        .getByRole("button", { name: "Intentar de nuevo" })
        .count();
      if (titles !== 1 || failed > 0) broken.push(href);
    }
    check("cada tarea y cada problema abre una pantalla real", broken, []);

    // ------------------------------------ the flows, seen rather than read
    // Every story walks end to end with «Siguiente» and the arrow keys, and
    // the one chosen lives in the address.
    await page.goto(`${WEB}/guias/flujos`, { waitUntil: "networkidle" });
    const tabs = await page.getByRole("tab").allTextContents();
    check("los cuatro recorridos están", tabs.length, 4);
    const walked = [];
    for (const name of tabs) {
      await page.getByRole("tab", { name }).click();
      const counter = page.getByText(/^1 de \d+$/);
      await counter.waitFor({ timeout: 5_000 });
      const total = Number((await counter.innerText()).split(" de ")[1]);
      for (let at = 2; at <= total; at++) {
        if (at % 2 === 0) {
          await page.getByRole("button", { name: "Siguiente" }).click();
        } else {
          // As somebody on a keyboard would: focus inside, then the arrow.
          await page.getByRole("button", { name: "Siguiente" }).focus();
          await page.keyboard.press("ArrowRight");
        }
      }
      walked.push(
        (await page.getByText(`${total} de ${total}`, { exact: true }).isVisible()) &&
          (await page.getByRole("button", { name: "Siguiente" }).isDisabled()),
      );
    }
    check("cada recorrido llega a su último paso", walked, [true, true, true, true]);
    await page.getByRole("tab", { name: "De qué está hecha una cuota" }).click();
    check(
      "el recorrido elegido queda en la dirección",
      new URL(page.url()).searchParams.get("ver"),
      "cuota",
    );
    await page.setViewportSize({ width: 320, height: 640 });
    check("los recorridos no se van de lado a 320 px", await sideways(page), false);
    await page.setViewportSize({ width: 390, height: 844 });

    await page.goto(`${WEB}/transacciones`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Cómo funciona" }).click();
    await page.getByRole("button", { name: "Verlo paso a paso" }).click();
    check(
      "en Transacciones, «Verlo paso a paso» abre cómo llega un movimiento",
      await page
        .getByRole("region", { name: "Cómo llega un movimiento" })
        .getByText("1 de 6", { exact: true })
        .isVisible(),
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
      ? "\ne2e guías: todo bien. Una lista vacía dice por qué, y cada tarea lleva a su sitio."
      : `\ne2e guías: ${failures} problema(s).`,
  );
  process.exitCode = failures === 0 ? 0 : 1;
}

await main();
