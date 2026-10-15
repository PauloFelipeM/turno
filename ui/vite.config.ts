import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, type Plugin } from "vite";

// Packages whose files reach the page without appearing as JS modules (the font files, the CSS that Tailwind
// builds from tailwindcss and tw-animate-css), so the notices must name them too.
const ALSO = ["@fontsource-variable/inter", "tailwindcss", "tw-animate-css"];

/** The nearest package.json above a file inside node_modules: the package that file belongs to. */
function packageRoot(file: string): string | null {
  const at = file.lastIndexOf("node_modules/");
  if (at < 0) return null;
  const rest = file.slice(at + "node_modules/".length).split("/");
  const name = rest[0].startsWith("@") ? `${rest[0]}/${rest[1]}` : rest[0];
  return file.slice(0, at) + "node_modules/" + name;
}

/** Writes THIRD_PARTY_LICENSES.txt next to index.html: every package that ends up in the bundle, with its license text. */
function thirdPartyLicenses(): Plugin {
  return {
    name: "turno-third-party-licenses",
    generateBundle(_options, bundle) {
      const roots = new Set<string>();
      for (const item of Object.values(bundle)) {
        if (item.type !== "chunk") continue;
        for (const id of item.moduleIds) {
          const root = packageRoot(id.split("?")[0]);
          if (root) roots.add(root);
        }
      }
      const nodeModules = fileURLToPath(new URL("./node_modules", import.meta.url));
      for (const name of ALSO) roots.add(join(nodeModules, name));
      const parts: string[] = [
        "Third-party software in the turno panel (turno/web). Each package below is bundled or served with the panel;\n" +
          "the license text is the one shipped in the package.\n",
      ];
      for (const root of [...roots].sort()) {
        const pkgFile = join(root, "package.json");
        if (!existsSync(pkgFile)) continue;
        const pkg = JSON.parse(readFileSync(pkgFile, "utf-8")) as { name: string; version: string; license?: string; homepage?: string };
        const file = readdirSync(root).find((f) => /^(licen[sc]e|copying)(\.|$)/i.test(f));
        const text = file ? readFileSync(join(root, file), "utf-8").trim() : "(no license file in the package; license field: " + (pkg.license ?? "unknown") + ")";
        parts.push(`${"=".repeat(78)}\n${pkg.name} ${pkg.version}  (${pkg.license ?? "unknown"})${pkg.homepage ? `\n${pkg.homepage}` : ""}\n${"-".repeat(78)}\n${text}\n`);
      }
      this.emitFile({ type: "asset", fileName: "THIRD_PARTY_LICENSES.txt", source: parts.join("\n") });
    },
  };
}

// The built panel goes into the Python package (turno/web), served by turno/server.py under /static/.
export default defineConfig({
  base: "/static/",
  plugins: [react(), tailwindcss(), thirdPartyLicenses()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir: fileURLToPath(new URL("../turno/web", import.meta.url)),
    emptyOutDir: true,
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 1500,
  },
});
