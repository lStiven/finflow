/**
 * Screenshot the running app in a headless browser.
 *
 *   node scripts/shot.mjs                       every default route, phone-sized
 *   node scripts/shot.mjs /reportes /cuentas    just these
 *   node scripts/shot.mjs /reportes --desktop --full
 *
 * There is no browser in this DevContainer's editor, so a screen that has
 * only been type-checked has never actually been *seen*. This is how it gets
 * seen: it drives the same bundle a phone would load, against the same local
 * API `just up` seeds, and writes PNGs somebody can open.
 *
 * It screenshots what is already running rather than starting anything.
 * Booting the stack from here would mean a second copy of `run_stack.py` and
 * a process this script could leave behind if it died mid-shot; instead it
 * says plainly what to start. Phone-sized by default because that is what
 * Finflow is: a web app reached from a phone's browser.
 */

import { mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import process from "node:process";
import { chromium } from "playwright";

/** The screens worth a look after a change, in the order somebody walks them. */
const DEFAULT_ROUTES = [
  "/",
  "/transacciones",
  "/transacciones/nueva",
  "/cuentas",
  "/comercios",
  "/reportes",
  "/perfil",
];

/** What `just seed` leaves behind. Overridable for a different local user. */
const DEMO_EMAIL = process.env.FINFLOW_DEMO_EMAIL ?? "demo@finflow.local";
const DEMO_PASSWORD = process.env.FINFLOW_DEMO_PASSWORD ?? "una frase larga de verdad";

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };

/** The value after a flag, or a clear error instead of a silent `undefined`. */
function takeValue(rest, flag) {
  const value = rest.shift();

  if (value === undefined) throw new Error(`${flag} necesita un valor`);

  return value;
}

function parseArgs(argv) {
  const routes = [];
  const options = {
    base: process.env.FINFLOW_WEB_URL ?? "http://localhost:5173",
    out: ".screenshots",
    viewport: PHONE,
    fullPage: false,
    login: true,
    // The guide's modals sit over every screen on a fresh profile. Off unless
    // asked for, because they are not what a change is usually being checked
    // against.
    onboarding: false,
  };

  const rest = [...argv];

  while (rest.length > 0) {
    const argument = rest.shift();

    if (argument === "--desktop") options.viewport = DESKTOP;
    else if (argument === "--full") options.fullPage = true;
    else if (argument === "--no-login") options.login = false;
    else if (argument === "--onboarding") options.onboarding = true;
    else if (argument === "--base") options.base = takeValue(rest, argument);
    else if (argument === "--out") options.out = takeValue(rest, argument);
    else if (argument.startsWith("--")) throw new Error(`No conozco ${argument}`);
    else routes.push(argument.startsWith("/") ? argument : `/${argument}`);
  }

  return { routes: routes.length > 0 ? routes : DEFAULT_ROUTES, options };
}

/** `/transacciones/nueva` → `transacciones-nueva`, and `/` → `index`. */
function slug(route) {
  const cleaned = route.replace(/^\/+|\/+$/g, "").replace(/[^\w-]+/g, "-");

  return cleaned === "" ? "index" : cleaned;
}

async function reachable(base) {
  try {
    await fetch(base, { signal: AbortSignal.timeout(2000) });

    return true;
  } catch {
    return false;
  }
}

/**
 * Sign in through the form rather than by writing the session into storage.
 *
 * Slower, and on purpose: it goes through the real screen, so a login that
 * broke fails here instead of producing six screenshots of a login page.
 */
async function signIn(page, base) {
  await page.goto(`${base}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("Correo").fill(DEMO_EMAIL);
  await page.getByLabel("Contraseña").fill(DEMO_PASSWORD);
  // The submit button, not the mode switch beside it: the door offers two
  // buttons reading "Entrar" — one picks the login tab and one signs in.
  await page.locator('form button[type="submit"]').click();

  // The door redirects to the dashboard on success. Waiting for the URL to
  // stop being /login is what tells the two apart.
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
    timeout: 15_000,
  });
}

/**
 * Mark the onboarding guide as already read, so the screens underneath it are
 * what gets photographed.
 *
 * A fresh browser profile has acknowledged nothing, and the welcome and the
 * "ya quedó conectado" celebration are modal — without this every shot is a
 * picture of the same card. These acks live in `localStorage` on purpose
 * (see `onboarding/progress.ts`), which is why they can be set from here at
 * all, and `--onboarding` leaves them alone for whoever is photographing the
 * guide itself.
 */
async function dismissOnboarding(page, base) {
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

  // Read at mount, not reactively, so the page has to be loaded again for the
  // modals to stay shut.
  if (wrote) await page.goto(base, { waitUntil: "networkidle" });

  return wrote;
}

async function main() {
  const { routes, options } = parseArgs(process.argv.slice(2));

  if (!(await reachable(options.base))) {
    console.error(
      `No hay nada escuchando en ${options.base}.\n` +
        "  just up     # emulador, datos de prueba y la API\n" +
        "  just web    # el frontend, en otra terminal\n" +
        "y vuelve a intentarlo (o pasa --base <url>).",
    );
    process.exitCode = 1;

    return;
  }

  const outDir = resolve(options.out);
  await mkdir(outDir, { recursive: true });

  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: options.viewport,
    deviceScaleFactor: 2,
    locale: "es-CO",
    timezoneId: "America/Bogota",
    // The app animates on entry; a screenshot taken mid-fade is a screenshot
    // of nothing. It also honours the preference, so this is what a reader
    // with it set would see anyway.
    reducedMotion: "reduce",
  });
  const page = await context.newPage();

  // Anything the page logs is a real signal here — nobody is watching a
  // console — so failures are surfaced rather than swallowed.
  const problems = [];
  page.on("pageerror", (error) => problems.push(`error: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(`console: ${message.text()}`);
  });

  try {
    if (options.login) {
      await signIn(page, options.base);
      if (!options.onboarding) await dismissOnboarding(page, options.base);
    }

    for (const route of routes) {
      const before = problems.length;
      await page.goto(`${options.base}${route}`, { waitUntil: "networkidle" });
      const file = join(outDir, `${slug(route)}.png`);
      await page.screenshot({ path: file, fullPage: options.fullPage });

      const said = problems.slice(before);
      console.log(`${route.padEnd(24)} ${file}${said.length > 0 ? "  ⚠" : ""}`);
      for (const line of said) console.log(`  ${line}`);
    }
  } finally {
    await context.close();
    await browser.close();
  }

  if (problems.length > 0) {
    console.log(`\n${problems.length} mensaje(s) de la página, arriba.`);
  }
}

await main();
