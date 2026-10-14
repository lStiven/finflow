/**
 * Drive the alerts guide in a real browser, and check what is behind it.
 *
 *   just up                  # emulator, seeded data, API and workers
 *   just web                 # the frontend, in another terminal
 *   just e2e-guia-avisos     # this
 *
 * UX-14 made the alerts guide something you do rather than read: the real
 * state of the channel at the top, with the button that connects it.
 *
 * 1. **Not connected, it says where you are** — step one of three — and the
 *    button is right there.
 * 2. **Pressing it is the real thing**: the API holds a pending channel, the
 *    guide moves to step two and waits, and the bot link is Telegram's.
 * 3. **Perfil is the same flow**: a link from an earlier visit is offered
 *    again rather than shown as if it were still usable.
 * 4. **Connected, it says so** with the chat's name. Telegram cannot be made
 *    to press «Empezar» from here — that would call the real Bot API with
 *    this stack's token — so this state is drawn from a stubbed answer.
 * 5. Nothing scrolls sideways at 390 or 320 px, and nothing logs an error.
 *
 * Every request to t.me is blocked: the suite never leaves the machine. It
 * registers its own person, who stays behind in the emulator.
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
  return async (path) => {
    const response = await fetch(`${API}${path}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) throw new Error(`GET ${path} → ${response.status}`);
    return response.json();
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

  const email = `e2e-avisos-${RUN}@finflow.local`;
  const password = "una frase larga de prueba";
  const call = client(person(email, password));

  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    locale: "es-CO",
    timezoneId: "America/Bogota",
    reducedMotion: "reduce",
  });
  // The bot link opens in a new tab; it must never reach Telegram.
  await context.route(/^https:\/\/t\.me\//, (route) => route.abort());
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(`error: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    if (message.text().includes("404 (Not Found)")) return;
    problems.push(`console: ${message.text()}`);
  });

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
    });

    // ------------------------------------------------- not connected yet
    await page.goto(`${WEB}/guias/avisos`, { waitUntil: "networkidle" });
    check(
      "sin canal, la guía dice dónde estás: paso uno de tres",
      [
        await page.getByRole("heading", { name: "Conéctalo en un toque" }).isVisible(),
        await page.getByText("(ahora)").count(),
        await page.getByText("(hecho)").count(),
      ],
      [true, 1, 0],
    );

    // ------------------------------------------------ pressing it is real
    const popup = context.waitForEvent("page", { timeout: 10_000 }).catch(() => null);
    await page.getByRole("button", { name: "Conectar Telegram" }).click();
    await page.getByText("Esperando a que pulses Empezar en Telegram…").waitFor({
      timeout: 10_000,
    });
    const opened = await popup;
    if (opened) await opened.close();

    const channels = await call("/alerts/channels");
    check(
      "conectar deja un canal pendiente en la API",
      channels.channels.map((channel) => channel.status),
      ["pending"],
    );
    check(
      "y la guía pasa al paso dos, esperando",
      await page.getByText("(hecho)").count(),
      1,
    );
    check(
      "el enlace es el bot de Telegram",
      (
        await page.getByRole("link", { name: "abre el bot aquí" }).getAttribute("href")
      )?.startsWith("https://t.me/"),
      true,
    );

    // ---------------------------------------- Perfil, the same flow
    await page.goto(`${WEB}/perfil`, { waitUntil: "networkidle" });
    check(
      "en Perfil, un enlace de antes se ofrece de nuevo, no como si siguiera vivo",
      [
        await page.getByRole("button", { name: "Conectar otra vez" }).isVisible(),
        await page
          .getByText("Hay un enlace sin usar de antes", { exact: false })
          .isVisible(),
      ],
      [true, true],
    );

    // ----------------------------------------------- connected, stubbed
    await page.route(`${API}/alerts/channels`, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              channels: [
                {
                  channel_id: "e2e",
                  kind: "telegram",
                  status: "verified",
                  chat_hint: "…6789",
                  label: "Ana",
                  created_at: Math.floor(Date.now() / 1000),
                  verified_at: Math.floor(Date.now() / 1000),
                  preferences: [],
                },
              ],
            }),
          })
        : route.continue(),
    );
    await page.goto(`${WEB}/guias/avisos`, { waitUntil: "networkidle" });
    check(
      "conectado, la guía lo dice con el nombre del chat",
      [
        await page
          .getByRole("heading", { name: "Tus avisos están conectados" })
          .isVisible(),
        await page.getByText("Te escribimos a Ana · …6789").isVisible(),
      ],
      [true, true],
    );

    check("a 390 px la guía no se va de lado", await sideways(page), false);
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
      ? "\ne2e guía de avisos: todo bien. Se conecta desde la guía, y dice dónde vas."
      : `\ne2e guía de avisos: ${failures} problema(s).`,
  );
  process.exitCode = failures === 0 ? 0 : 1;
}

await main();
