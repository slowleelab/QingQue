import { createRouter, createWebHistory } from "vue-router"
import CustomerChat from "@/views/CustomerChat.vue"
import AgentWorkbench from "@/views/AgentWorkbench.vue"
import LoginPage from "@/views/LoginPage.vue"
import AdminDashboard from "@/views/admin/AdminDashboard.vue"
import DocumentList from "@/views/admin/DocumentList.vue"
import FaqManager from "@/views/admin/FaqManager.vue"
import IngestionMonitor from "@/views/admin/IngestionMonitor.vue"

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "customer", component: CustomerChat },
    { path: "/agent", name: "agent", component: AgentWorkbench },
    { path: "/login", name: "login", component: LoginPage },
    {
      path: "/admin",
      component: AdminDashboard,
      children: [
        { path: "", redirect: "/admin/documents" },
        { path: "documents", name: "admin-documents", component: DocumentList },
        { path: "faq", name: "admin-faq", component: FaqManager },
        { path: "monitor", name: "admin-monitor", component: IngestionMonitor },
      ],
    },
  ],
})

export default router
