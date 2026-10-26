import { defineConfig, devices } from "@playwright/test";

import { FAKE_API_PATH } from "./tests/browser/fakeBackend";

const PORT = 3100;
const BASE_URL = `http://localhost:${PORT}`;

/**
 * 浏览器测试：前端用生产构建，在单独的端口上运行，不影响 3000 端口上的开发服务器。
 *
 * 后端接口全部由 tests/browser/fakeBackend.ts 在浏览器里拦截并模拟。前端的后端地址指向本站的 /fake-api，
 * 漏了模拟的请求只会得到 404，不会打到真实的后端和数据库。
 */
export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: true,
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run build && npm run start -- --port ${PORT}`,
    url: `${BASE_URL}/login`,
    reuseExistingServer: false,
    timeout: 300_000,
    env: { NEXT_PUBLIC_API_BASE_URL: `${BASE_URL}${FAKE_API_PATH}` },
  },
});
