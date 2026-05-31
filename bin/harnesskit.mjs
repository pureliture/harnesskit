#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PUBLIC_COMMANDS = {
  plan: "scripts/install/plan.py",
  apply: "scripts/install/apply.py",
  verify: "scripts/install/verify.py",
  build: "scripts/adapters/build.py",
  "publish-manifest": "scripts/projection/manifest.py",
  harness: "scripts/authoring/main.py"
};

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function loadPrivateCommands() {
  try {
    const mod = await import("./private-commands.mjs");
    return mod.PRIVATE_COMMANDS ?? {};
  } catch {
    return {}; // absent in the published artifact — public surface only
  }
}

const PRIVATE_COMMANDS = await loadPrivateCommands();
const COMMANDS = { ...PUBLIC_COMMANDS, ...PRIVATE_COMMANDS };
const hasPrivate = Object.keys(PRIVATE_COMMANDS).length > 0;

const [command, ...args] = process.argv.slice(2);

function printHelp() {
  console.log(`Harnesskit local CLI

Usage:
  harnesskit <command> [args...]

Commands:
  plan    Generate an install plan
  apply   Apply an install plan
  verify  Verify an applied install plan
  build   Build/check adapter outputs
  publish-manifest  Generate a public projection manifest
  harness Manage local private authoring work items

Examples:
  harnesskit plan --profile harness-maintenance --mode apply --format json
  harnesskit apply /tmp/harnesskit-plan.json --target-root . --allow-runtime-hooks
  harnesskit verify /tmp/harnesskit-plan.json --target-root .
  harnesskit publish-manifest --policy publish/harnesskit.yml --repo-root .
  harnesskit harness add skill --slug release-summary --idea /tmp/idea.md --targets codex,claude --target-root /tmp/target --json
  harnesskit harness preview skill-release-summary --target-root /tmp/target --json
`);
  if (hasPrivate) {
    console.log("Private commands (source checkout only):");
    for (const name of Object.keys(PRIVATE_COMMANDS)) {
      console.log(`  ${name}`);
    }
  }
}

if (!command || command === "-h" || command === "--help") {
  printHelp();
  process.exit(0);
}

const script = COMMANDS[command];
if (!script) {
  console.error(`Unknown command: ${command}`);
  printHelp();
  process.exit(2);
}

const result = spawnSync(
  "uv",
  ["run", "--quiet", "--project", root, "python", script, ...args],
  {
    cwd: root,
    stdio: "inherit",
    env: { ...process.env, PYTHONPATH: root }
  }
);

if (result.error) {
  if (result.error.code === "ENOENT") {
    console.error(
      "Harnesskit requires `uv` for local npm/tarball execution. " +
        "Install uv, then retry this command."
    );
  } else {
    console.error(result.error.message);
  }
  process.exit(127);
}

process.exit(result.status ?? 1);
