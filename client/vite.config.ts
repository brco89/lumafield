import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

type ProxyRequest = { setHeader(name: string, value: string): void };
type ProxyEvents = { on(event: "proxyReq", listener: (request: ProxyRequest) => void): void };

// The dev server proxies the API so the browser never needs CORS or a public
// origin during local development. It also adds the demo bootstrap credential
// server-side, so the console opens directly without exposing it to JavaScript.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "..", "LUMAFIELD_");
  const demoAccessToken = env.LUMAFIELD_DEMO_ACCESS_TOKEN;
  if (!demoAccessToken) throw new Error("LUMAFIELD_DEMO_ACCESS_TOKEN is not configured on the console server.");

  return {
    plugins: [react()],
    server: {
      // The mobile demo is exposed through an ephemeral Cloudflare Quick
      // Tunnel. A leading dot permits only this domain and its subdomains,
      // while the dev server itself remains bound to loopback.
      allowedHosts: [".trycloudflare.com"],
      proxy: {
        "/v1": {
          target: "http://127.0.0.1:8000",
          configure(proxy) {
            (proxy as unknown as ProxyEvents).on("proxyReq", (request) => {
              request.setHeader("X-Demo-Access", demoAccessToken);
            });
          },
        },
      },
    },
  };
});
