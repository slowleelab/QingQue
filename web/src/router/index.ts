import { createRouter, createWebHistory } from "vue-router"
import CustomerChat from "@/views/CustomerChat.vue"
import AgentWorkbench from "@/views/AgentWorkbench.vue"
import AdminDashboard from "@/views/admin/AdminDashboard.vue"
import DocumentList from "@/views/admin/DocumentList.vue"
import FaqManager from "@/views/admin/FaqManager.vue"

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "customer", component: CustomerChat },
    { path: "/agent", name: "agent", component: AgentWorkbench },
    {
      path: "/admin",
      component: AdminDashboard,
      children: [
        { path: "", redirect: "/admin/documents" },
        { path: "documents", name: "admin-documents", component: DocumentList },
        { path: "faq", name: "admin-faq", component: FaqManager },
      ],
    },
  ],
})

export default router
