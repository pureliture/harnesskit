import { build } from "esbuild";
import { createHash } from "node:crypto";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const sourceRoot = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(sourceRoot, "..");
const distRoot = resolve(
  process.env.HARNESS_FRONTEND_DIST || resolve(repositoryRoot, "src-tauri/target/frontend-dist"),
);
const entrypoint = "appearance/bootstrap.js";
const outputPath = "assets/app.bundle.js";
const lockfilePath = resolve(sourceRoot, "package-lock.json");
const disabledNgraphEngine = resolve(sourceRoot, "vendor/ngraph-disabled.js");
const toolIdentityCatalogPath = resolve(sourceRoot, "tool-identities.catalog.js");

async function sha256(path) {
  return createHash("sha256").update(await readFile(path)).digest("hex");
}

async function copy(relative) {
  const source = resolve(sourceRoot, relative);
  const target = resolve(distRoot, relative);
  await mkdir(dirname(target), { recursive: true });
  await cp(source, target, { dereference: false, recursive: false });
}

function invalidLock(reason) {
  throw new Error(`frontend_lock_invalid:${reason}`);
}

function invalidToolIdentityCatalog(reason) {
  throw new Error(`tool_identity_catalog_invalid:${reason}`);
}

async function qualifiedToolIdentityOutputs() {
  let source;
  try {
    source = await readFile(toolIdentityCatalogPath, "utf8");
  } catch {
    invalidToolIdentityCatalog("unreadable");
  }
  const prefix = "export const TOOL_IDENTITY_CATALOG = Object.freeze(";
  const start = source.indexOf(prefix);
  if (start < 0 || !source.trimEnd().endsWith(");")) {
    invalidToolIdentityCatalog("module_shape");
  }
  let catalog;
  try {
    catalog = JSON.parse(source.slice(start + prefix.length, source.lastIndexOf(");")));
  } catch {
    invalidToolIdentityCatalog("json");
  }
  if (!Array.isArray(catalog) || !catalog.length) {
    invalidToolIdentityCatalog("entries");
  }
  const outputs = [];
  for (const entry of catalog) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      invalidToolIdentityCatalog("entry");
    }
    if (entry.mode === "text") {
      if (entry.src !== null) invalidToolIdentityCatalog("text_source");
      continue;
    }
    if (
      entry.mode !== "qualified"
      || typeof entry.src !== "string"
      || !entry.src.startsWith("./assets/tool-identities/")
    ) {
      invalidToolIdentityCatalog("qualified_source");
    }
    const relative = entry.src.slice(2);
    if (
      relative.includes("\\")
      || relative.includes(":")
      || relative.split("/").some((part) => !part || part === "." || part === "..")
    ) {
      invalidToolIdentityCatalog("qualified_path");
    }
    outputs.push(relative);
  }
  if (new Set(outputs).size !== outputs.length) {
    invalidToolIdentityCatalog("duplicate_output");
  }
  return outputs.sort(compareText);
}

function packageNameFromLockPath(packagePath) {
  if (
    typeof packagePath !== "string"
    || !packagePath
    || packagePath.includes("\\")
    || packagePath.includes(":")
  ) {
    invalidLock("package_path");
  }
  const parts = packagePath.split("/");
  if (
    parts[0] !== "node_modules"
    || parts.some((part) => !part || part === "." || part === "..")
  ) {
    invalidLock("package_path");
  }
  const lastNodeModules = parts.lastIndexOf("node_modules");
  const leaf = parts.slice(lastNodeModules + 1);
  if (leaf.length === 1 && !leaf[0].startsWith("@")) return leaf[0];
  if (leaf.length === 2 && leaf[0].startsWith("@") && leaf[0].length > 1) {
    return leaf.join("/");
  }
  invalidLock("package_name");
}

function compareText(left, right) {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

function verifiedPackageIntegrity(resolution) {
  const integrity = resolution?.integrity;
  if (typeof integrity === "string" && integrity.startsWith("sha512-")) {
    const encodedDigest = integrity.slice("sha512-".length);
    if (!/^[A-Za-z0-9+/]+={0,2}$/.test(encodedDigest)) {
      invalidLock("integrity");
    }
    const digest = Buffer.from(encodedDigest, "base64");
    if (digest.length !== 64 || digest.toString("base64") !== encodedDigest) {
      invalidLock("integrity");
    }
    return integrity;
  }

  const resolved = resolution?.resolved;
  const match = typeof resolved === "string"
    ? resolved.match(
      /^git\+https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\.git#([0-9a-f]{40})$/i,
    )
    : null;
  if (!match) invalidLock("identity");
  return `git-commit:${match[1].toLowerCase()}`;
}

async function lockLicenseInventory() {
  let lock;
  try {
    lock = JSON.parse(await readFile(lockfilePath, "utf8"));
  } catch {
    invalidLock("json");
  }
  if (
    lock?.lockfileVersion !== 3
    || !lock.packages
    || typeof lock.packages !== "object"
    || Array.isArray(lock.packages)
  ) {
    invalidLock("schema");
  }
  const inventory = [];
  for (const [packagePath, resolution] of Object.entries(lock.packages)) {
    if (!packagePath) continue;
    if (!resolution || typeof resolution !== "object" || Array.isArray(resolution)) {
      invalidLock("resolution");
    }
    const name = packageNameFromLockPath(packagePath);
    const { license, version } = resolution;
    if (
      typeof version !== "string"
      || !version
      || typeof license !== "string"
      || !license
    ) {
      invalidLock("identity");
    }
    const integrity = verifiedPackageIntegrity(resolution);
    inventory.push({
      integrity,
      license,
      name,
      path: packagePath,
      usage: resolution.dev === true ? "build" : "runtime",
      version,
    });
  }
  if (!inventory.length) invalidLock("empty");
  return inventory.sort(
    (left, right) => compareText(left.name, right.name)
      || compareText(left.version, right.version)
      || compareText(left.path, right.path),
  );
}

await rm(distRoot, { force: true, recursive: true });
await mkdir(resolve(distRoot, "assets"), { recursive: true });

await build({
  entryPoints: [resolve(sourceRoot, entrypoint)],
  outfile: resolve(distRoot, outputPath),
  bundle: true,
  format: "esm",
  platform: "browser",
  target: ["safari16"],
  sourcemap: false,
  minify: true,
  legalComments: "none",
  treeShaking: true,
  logLevel: "warning",
  alias: {
    "ngraph.forcelayout": disabledNgraphEngine,
    "ngraph.graph": disabledNgraphEngine,
  },
});

const staticOutputs = [
  "index.html",
  "styles.css",
  "assets/branding/harness-desktop-64.png",
  "legal/third-party-notices.html",
  ...(await qualifiedToolIdentityOutputs()),
];

for (const relative of staticOutputs) {
  await copy(relative);
}

const licenses = await lockLicenseInventory();
const licenseInventoryPath = resolve(distRoot, "license-inventory.json");
await writeFile(
  licenseInventoryPath,
  `${JSON.stringify({ schema_version: 1, packages: licenses }, null, 2)}\n`,
  "utf8",
);

const runtimeOutputs = [outputPath, ...staticOutputs, "license-inventory.json"].sort();
const files = await Promise.all(
  runtimeOutputs.map(async (path) => ({
    path,
    sha256: await sha256(resolve(distRoot, path)),
  })),
);

const bundleManifest = {
  schema_version: 1,
  entrypoint,
  lockfile: {
    path: "package-lock.json",
    sha256: await sha256(lockfilePath),
  },
  output: {
    path: outputPath,
    sha256: await sha256(resolve(distRoot, outputPath)),
  },
  license_inventory: {
    path: "license-inventory.json",
    sha256: await sha256(licenseInventoryPath),
  },
  licenses,
  files,
  source_map: false,
};
await writeFile(
  resolve(distRoot, "bundle-manifest.json"),
  `${JSON.stringify(bundleManifest, null, 2)}\n`,
  "utf8",
);
