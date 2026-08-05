import { readFile } from "node:fs/promises";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

import { JSDOM, VirtualConsole } from "jsdom";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(scriptDir, "..");
const html = await readFile(path.join(frontendDir, "dist", "index.html"), "utf8");
const scriptMatch = html.match(/<script[^>]+src="([^"]+)"/);
if (!scriptMatch) throw new Error("Production index does not reference a JavaScript bundle.");

const errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on("error", (...args) => errors.push(args.join(" ")));
virtualConsole.on("jsdomError", (error) => errors.push(error.message));

const dom = new JSDOM(html, {
  url: "https://dashboard.example.test/",
  runScripts: "outside-only",
  pretendToBeVisual: true,
  virtualConsole,
});
const bundlePath = path.join(frontendDir, "dist", scriptMatch[1].replace(/^\//, ""));
const source = await readFile(bundlePath, "utf8");

try {
  new vm.Script(source, { filename: bundlePath }).runInContext(dom.getInternalVMContext());
  await new Promise((resolve) => setTimeout(resolve, 50));
  const rootText = dom.window.document.querySelector("#root")?.textContent?.trim() || "";
  if (!rootText) throw new Error("Production bundle rendered an empty root element.");
  if (errors.length) throw new Error("Production bundle logged errors: " + errors.join(" | "));
  console.log("Production bundle rendered successfully.");
} finally {
  dom.window.close();
}
