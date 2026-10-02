/**
 * Open every screen of the app, on a phone and on a desktop, and refuse to
 * pass if any of them is broken.
 *
 *   just up               # emulator, seeded data, API and workers
 *   just web              # the frontend, in another terminal
 *   just e2e-views        # this
 *
 * The screens are read from `src/routes` rather than listed here, so a branch
 * with a screen more or a screen less is checked for exactly what it has, and
 * a new screen is covered the day it lands. Dynamic segments are filled with
 * real ids from the seeded API — a movement, a merchant, an account.
 *
 * For each screen, at 390 and 1280 px, it fails on:
 *
 * - a JavaScript error, or an error the page logs to the console;
 * - an API answer of 400 or more, except the 404s this app asks for on
 *   purpose (no monthly plan declared is a 404 by design);
 * - the page scrolling sideways;
 * - not exactly one `h1`;
 * - being sent somewhere else — a redirect to /login is a screen that
 *   stopped working, not a screen.
 *
 * And, signed in, that what cannot be drawn is drawn as the app's own error
 * screen — never the router's bare «Something went wrong!»: an address that
 * matches nothing, a movement that does not exist, and the API unreachable.
 */

import { readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const WEB = process.env.FINFLOW_WEB_URL ?? "http://localhost:5173";
const API = process.env.FINFLOW_API_URL ?? "http://localhost:8000";
const DEMO_EMAIL = process.env.FINFLOW_DEMO_EMAIL ?? "demo@finflow.local";
const DEMO_PASSWORD = process.env.FINFLOW_DEMO_PASSWORD ?? "una frase larga de verdad";

const ROUTES_DIR = fileURLToPath(new URL("../src/routes", import.meta.url));
/** The screens somebody reaches without signing in. */
const SIGNED_OUT = new Set(["/login", "/recuperar", "/restablecer"]);
/** The 404s the app asks for on purpose, by API path. */
const EXPECTED_404 = new Set(["/financial/plan", "/financial/allowance"]);
const VIEWPORTS = [
  ["teléfono", { width: 390, height: 844 }],
  ["escritorio", { width: 1280, height: 800 }],
];

async function reachable(url) {
  try {
    await fetch(url, { signal: AbortSignal.timeout(2000) });
    return true;
  } catch {
    return false;
  }
}

function routeFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return routeFiles(path);
    return name.endsWith(".tsx") && !name.startsWith("__") ? [path] : [];
  });
}

/** `cuentas/$accountId.financiacion.tsx` → `/cuentas/$accountId/financiacion`. */
function toPattern(file) {
  const parts = relative(ROUTES_DIR, file)
    .replace(/\.tsx$/, "")
    .split(/[/.]/)
    .filter((part) => part !== "index");

  return `/${parts.join("/")}`;
}

async function signIn(page) {
  await page.goto(`${WEB}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("Correo").fill(DEMO_EMAIL);
  await page.getByLabel("Contraseña").fill(DEMO_PASSWORD);
  await page.locator('form button[type="submit"]').click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
    timeout: 15_000,
  });
  await page.evaluate(() => {
    const { userId } = JSON.parse(window.localStorage.getItem("finflow.session"));
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

async function ids() {
  const login = await (
    await fetch(`${API}/identity/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email: DEMO_EMAIL, password: DEMO_PASSWORD }),
    })
  ).json();
  const get = async (path) =>
    (
      await fetch(`${API}${path}`, {
        headers: { Authorization: `Bearer ${login.access_token}` },
      })
    ).json();

  const [movements, merchants, accounts] = await Promise.all([
    get("/financial/transactions?limit=1"),
    get("/merchants?limit=1"),
    get("/financial/accounts"),
  ]);
  const financed =
    accounts.accounts.find((account) =>
      ["loan", "credit_card"].includes(account.kind),
    ) ?? accounts.accounts[0];

  return {
    $transactionId: movements.transactions[0]?.id,
    $merchantId: merchants.merchants?.[0]?.id,
    $accountId: financed?.id,
  };
}

function fill(pattern, known) {
  let missing = null;
  const path = pattern.replace(/\$[A-Za-z]+/g, (segment) => {
    if (!known[segment]) missing = segment;
    return known[segment] ?? segment;
  });

  return { path, missing };
}

/** The title the app's error screen shows for each case. */
const ERROR_TITLES = {
  missing: "Esto ya no está aquí",
  offline: "Parece que no hay conexión",
};

/**
 * Each case on its own: what the screen must say, and that it still has one
 * `h1`, does not scroll sideways, and offers a way out.
 */
async function errorScreens(page, label) {
  const cases = [
    ["una dirección que no existe", "/esto-no-existe", ERROR_TITLES.missing, null],
    [
      "un movimiento que ya no existe",
      `/transacciones/${crypto.randomUUID()}`,
      ERROR_TITLES.missing,
      null,
    ],
    // Every API call refused, the way a phone without signal sees it. The
    // app retries twice before giving up, so this one waits longer.
    ["sin conexión con la API", "/cuentas", ERROR_TITLES.offline, `${API}/**`],
  ];
  const lines = [];

  for (const [what, path, title, blocked] of cases) {
    if (blocked)
      await page.route(blocked, (route) => route.abort("internetdisconnected"));

    try {
      await page.goto(`${WEB}${path}`, { waitUntil: "domcontentloaded" });
      await page
        .getByRole("heading", { level: 1, name: title })
        .waitFor({ timeout: blocked ? 20_000 : 10_000 });
      const seen = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > window.innerWidth,
        headings: document.querySelectorAll("h1").length,
        generic: document.body.innerText.includes("Something went wrong"),
      }));
      const wayOut = await page.getByRole("link", { name: "Ir al resumen" }).count();
      const wrong = [
        seen.generic ? "salió «Something went wrong»" : null,
        seen.overflow ? "se va de lado" : null,
        seen.headings === 1 ? null : `${seen.headings} h1`,
        wayOut === 1 ? null : "sin enlace al resumen",
      ].filter(Boolean);

      lines.push(
        wrong.length === 0
          ? `  ok   ${label.padEnd(10)} error: ${what}`
          : ` FALLA ${label.padEnd(10)} error: ${what}: ${wrong.join(" · ")}`,
      );
    } catch (error) {
      lines.push(` FALLA ${label.padEnd(10)} error: ${what}: ${error.message}`);
    } finally {
      if (blocked) await page.unroute(blocked);
    }
  }

  return lines;
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

  const known = await ids();
  const screens = routeFiles(ROUTES_DIR)
    .map(toPattern)
    .sort()
    .map((pattern) => ({ pattern, ...fill(pattern, known) }));

  const steps = [];
  let failures = 0;
  const browser = await chromium.launch();

  try {
    for (const [label, viewport] of VIEWPORTS) {
      for (const signedIn of [false, true]) {
        const context = await browser.newContext({
          viewport,
          locale: "es-CO",
          timezoneId: "America/Bogota",
          reducedMotion: "reduce",
        });
        const page = await context.newPage();
        let problems = [];
        page.on("pageerror", (error) => problems.push(`error: ${error.message}`));
        page.on("console", (message) => {
          if (message.type() !== "error") return;
          // The browser logs every failed request without its URL; the
          // responses themselves are judged below, where the URL is known.
          if (/Failed to load resource/.test(message.text())) return;
          problems.push(`consola: ${message.text()}`);
        });
        page.on("response", (response) => {
          const path = new URL(response.url()).pathname;
          if (!response.url().startsWith(API) || response.status() < 400) return;
          if (response.status() === 404 && EXPECTED_404.has(path)) return;
          problems.push(`${response.status()} ${path}`);
        });

        if (signedIn) await signIn(page);

        for (const screen of screens) {
          if (SIGNED_OUT.has(screen.pattern) === signedIn) continue;

          if (screen.missing) {
            steps.push(`  ··   ${screen.pattern}: el seed no trae ${screen.missing}`);
            continue;
          }

          problems = [];
          await page.goto(`${WEB}${screen.path}`, { waitUntil: "networkidle" });
          await page.waitForTimeout(300);
          const seen = await page.evaluate(() => ({
            overflow: document.documentElement.scrollWidth > window.innerWidth,
            headings: document.querySelectorAll("h1").length,
            path: window.location.pathname,
          }));

          const wrong = [
            ...problems,
            seen.overflow ? "se va de lado" : null,
            seen.headings === 1 ? null : `${seen.headings} h1`,
            seen.path === screen.path ? null : `terminó en ${seen.path}`,
          ].filter(Boolean);

          if (wrong.length === 0) {
            steps.push(`  ok   ${label.padEnd(10)} ${screen.pattern}`);
          } else {
            failures += 1;
            steps.push(
              ` FALLA ${label.padEnd(10)} ${screen.pattern}: ${wrong.join(" · ")}`,
            );
          }
        }

        if (signedIn) {
          for (const line of await errorScreens(page, label)) {
            if (line.startsWith(" FALLA")) failures += 1;
            steps.push(line);
          }
        }

        await context.close();
      }
    }
  } catch (error) {
    failures += 1;
    steps.push(` FALLA ${error.message}`);
  } finally {
    await browser.close();
  }

  console.log(steps.join("\n"));
  console.log(
    failures === 0
      ? `\ne2e pantallas: todo bien. ${screens.length} pantallas, en teléfono y escritorio, sin errores.`
      : `\ne2e pantallas: ${failures} problema(s).`,
  );
  process.exitCode = failures === 0 ? 0 : 1;
}

await main();
