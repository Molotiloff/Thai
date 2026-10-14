import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const standalone = join(root, ".next", "standalone");

if (!existsSync(standalone)) {
  throw new Error("Standalone bundle not found. Check output: 'standalone' in next.config.ts.");
}

const copyDirectory = (source, destination, required = true) => {
  if (!existsSync(source)) {
    if (required) {
      throw new Error(`Required build directory not found: ${source}`);
    }
    return;
  }

  rmSync(destination, { recursive: true, force: true });
  mkdirSync(destination, { recursive: true });
  cpSync(source, destination, { recursive: true });
};

copyDirectory(join(root, ".next", "static"), join(standalone, ".next", "static"));
copyDirectory(join(root, "public"), join(standalone, "public"), false);
