import { readFile, writeFile } from "node:fs/promises";
import openapiTS, { astToString } from "openapi-typescript";

const schemaUrl = new URL("../../docs/openapi/pangi-admin-api.json", import.meta.url);
const outputUrl = new URL("../src/api/generated.ts", import.meta.url);
const checkOnly = process.argv.includes("--check");

const ast = await openapiTS(schemaUrl, { alphabetize: true });
const expected = astToString(ast);

if (checkOnly) {
  const actual = await readFile(outputUrl, "utf8").catch(() => null);
  if (actual !== expected) {
    throw new Error(
      "Generated API types have drifted. Run `npm run api:generate` after exporting OpenAPI.",
    );
  }
} else {
  await writeFile(outputUrl, expected, "utf8");
  console.log(`Wrote ${outputUrl.pathname}`);
}
