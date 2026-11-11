// Writes a gzip copy (file.gz) next to every compressible file in dist/, so nginx with `gzip_static on`
// serves it as-is instead of compressing the same bundle again for every visitor. Node built-ins only.
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { constants, gzipSync } from "node:zlib";

const dist = fileURLToPath(new URL("../dist/", import.meta.url));
const COMPRESSIBLE = new Set([".js", ".css", ".html", ".svg", ".json", ".txt", ".map", ".webmanifest"]);
const MIN_BYTES = 1024; // below this, gzip rarely wins (matches nginx gzip_min_length)

let files = 0, before = 0, after = 0;
function walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const st = statSync(path);
    if (st.isDirectory()) walk(path);
    else if (COMPRESSIBLE.has(extname(name)) && st.size >= MIN_BYTES) {
      const gz = gzipSync(readFileSync(path), { level: constants.Z_BEST_COMPRESSION });
      if (gz.length >= st.size) continue;
      writeFileSync(`${path}.gz`, gz);
      files++; before += st.size; after += gz.length;
    }
  }
}
walk(dist);
const kb = (n) => `${(n / 1024).toFixed(0)} KB`;
console.log(`precompress: ${files} file(s), ${kb(before)} -> ${kb(after)} gzip`);
