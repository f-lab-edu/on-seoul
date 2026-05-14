import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    ignores: [
      "node_modules/**",
      ".next/**",
      "out/**",
      "build/**",
      "next-env.d.ts",
    ],
  },
  {
    rules: {
      // console.log 잔존 금지 (CLAUDE.md E-1). warn/error는 허용.
      "no-console": ["error", { allow: ["warn", "error"] }],
      // any 타입 금지 (CLAUDE.md D-1)
      "@typescript-eslint/no-explicit-any": "error",
    },
  },
];

export default eslintConfig;
