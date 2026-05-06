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
      // 잔존 console.log 금지 (CLAUDE.md E-1)
      "no-console": ["warn", { allow: ["warn", "error"] }],
      // any 타입 금지 (CLAUDE.md D-1)
      "@typescript-eslint/no-explicit-any": "error",
      // unknown 수신 후 타입가드 없이 사용 금지
      "@typescript-eslint/no-unsafe-assignment": "off",
    },
  },
];

export default eslintConfig;
