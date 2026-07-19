import { createRouter, createWebHistory } from "vue-router"
import CustomerChat from "@/views/CustomerChat.vue"
import AgentWorkbench from "@/views/AgentWorkbench.vue"

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "customer", component: CustomerChat },
    { path: "/agent", name: "agent", component: AgentWorkbench },
  ],
})

export default router
