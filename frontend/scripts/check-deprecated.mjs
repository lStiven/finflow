/**
 * Fail when this project imports a symbol its own dependencies mark
 * `@deprecated`.
 *
 *     npm run check:deprecated        (or `just web-audit`)
 *
 * The check is import-aware, and that is the whole design. Two cheaper
 * approaches both produce noise instead of findings:
 *
 *  - *File-level matching.* A `.d.ts` containing `@deprecated` says nothing
 *    about what we call. `@tanstack/react-router`'s `fileRoute.d.ts` carries
 *    the tag on the `FileRoute` class while this project uses
 *    `createFileRoute` from the same file, and `tailwind-merge`'s tags sit on
 *    Tailwind *class names*, not on `twMerge`.
 *
 *  - *Bare symbol matching.* Common names collide across packages —
 *    `@types/node` deprecates something called `format`, `parse` and
 *    `register`, none of which are the `format`, `parse` or `register` this
 *    code uses. Only a symbol imported *from the package that deprecates it*
 *    is a finding.
 *
 * Types usually live in a separate package from the runtime, so `react` is
 * resolved through `@types/react` as well — which is where the deprecation
 * that prompted this script was hiding.
 */

import { readFileSync } from "node:fs";
import { readdir } from "node:fs/promises";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");
const SRC = path.join(ROOT, "src");
const MODULES = path.join(ROOT, "node_modules");

/** Generated files: we do not control what they import. */
const GENERATED = new Set(["schema.d.ts", "routeTree.gen.ts"]);

async function walk(dir, test) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await walk(full, test)));
    else if (test(entry.name)) out.push(full);
  }
  return out;
}

/** Every named import in our source, grouped by the module it came from. */
async function collectImports() {
  const files = await walk(SRC, (name) => /\.tsx?$/.test(name));
  const imports = new Map();

  for (const file of files) {
    if (GENERATED.has(path.basename(file))) continue;
    const text = readFileSync(file, "utf8");
    // A default import may sit before the braces — `import createClient, {
    // type Middleware } from "openapi-fetch"` — and dropping those modules
    // silently is worse than not checking them, because the report would say
    // it checked everything.
    const pattern =
      /import\s+(?:type\s+)?(?:(?<dflt>[A-Za-z_$][\w$]*)\s*,\s*)?(?:\{(?<named>[^}]*)\}|(?<bare>[A-Za-z_$][\w$]*))\s+from\s+["'](?<specifier>[^"']+)["']/g;

    for (const match of text.matchAll(pattern)) {
      const { dflt, named, bare, specifier } = match.groups;
      if (specifier.startsWith(".") || specifier.startsWith("@/")) continue;
      const set = imports.get(specifier) ?? new Set();
      for (const raw of (named ?? "").split(",")) {
        const name = raw
          .trim()
          .replace(/^type\s+/, "")
          .split(/\s+as\s+/)[0];
        if (name) set.add(name.trim());
      }
      // A default export is deprecated under its declared name, not the
      // local alias, so both are worth matching.
      for (const name of [dflt, bare]) if (name) set.add(name);
      imports.set(specifier, set);
    }
  }
  return imports;
}

/** The package directories that can declare types for a module specifier. */
function typeDirs(specifier) {
  const base = specifier.startsWith("@")
    ? specifier.split("/").slice(0, 2).join("/")
    : specifier.split("/")[0];
  const scoped = `@types/${base.replace("@", "").replace("/", "__")}`;
  return [path.join(MODULES, base), path.join(MODULES, scoped)];
}

const DECLARATION =
  /@deprecated(?<note>[\s\S]*?)\*\/\s*(?:export\s+)?(?:declare\s+)?(?:abstract\s+)?(?:interface|type|function|class|const|var|enum)\s+(?<name>[A-Za-z_][A-Za-z0-9_]*)/g;

async function main() {
  const imports = await collectImports();
  const findings = [];

  for (const [specifier, names] of imports) {
    for (const dir of typeDirs(specifier)) {
      let declarations;
      try {
        declarations = await walk(dir, (name) => name.endsWith(".d.ts"));
      } catch {
        continue; // Not installed, or ships no types of its own.
      }

      for (const file of declarations) {
        for (const match of readFileSync(file, "utf8").matchAll(DECLARATION)) {
          const { name, note } = match.groups;
          if (!names.has(name)) continue;
          findings.push({
            name,
            specifier,
            evidence: path.relative(ROOT, file),
            note: note
              .replace(/\s*\*\s*/g, " ")
              .trim()
              .slice(0, 200),
          });
        }
      }
    }
  }

  const unique = [
    ...new Map(findings.map((f) => [`${f.specifier}:${f.name}`, f])).values(),
  ];

  if (unique.length === 0) {
    const checked = [...imports.keys()].sort().join(", ");
    console.log(`No deprecated imports. Checked ${imports.size} modules: ${checked}`);
    return;
  }

  for (const f of unique) {
    console.error(`\ndeprecated: ${f.name}  (imported from "${f.specifier}")`);
    console.error(`  declared:  ${f.evidence}`);
    console.error(`  says:      ${f.note}`);
  }
  console.error(`\n${unique.length} deprecated import(s).`);
  process.exitCode = 1;
}

await main();
