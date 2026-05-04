import { defineConfig } from "vite"
import vue from "@vitejs/plugin-vue"
import { resolve } from "path"
import AutoImport from "unplugin-auto-import/vite"
import Components from "unplugin-vue-components/vite"
import { ElementPlusResolver } from "unplugin-vue-components/resolvers"

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      resolvers: [ElementPlusResolver()],
    }),
    Components({
      resolvers: [ElementPlusResolver()],
    }),
  ],
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api/bot": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/bot/, "/api"),
      },
      "/api/assist": {
        target: "http://localhost:8001",
        changeOrigin: true,
        ws: true,
        rewrite: (path) => path.replace(/^\/api\/assist/, "/api"),
      },
      "/customer": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
    },
  },
})
