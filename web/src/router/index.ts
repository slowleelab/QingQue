import { createRouter, createWebHistory } from "vue-router"
import AgentWorkbench from "@/views/AgentWorkbench.vue"
import BotChat from "@/views/BotChat.vue"

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "workbench", component: AgentWorkbench },
    { path: "/chat", name: "chat", component: BotChat },
  ],
})

export default router
