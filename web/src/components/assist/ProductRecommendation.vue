<template>
  <el-card shadow="hover" class="product-card" data-testid="marketing-card">
    <div class="product-header">
      <span class="product-name">{{ recommendation.product_name }}</span>
      <el-tag :type="recommendation.eligibility_match ? 'success' : 'danger'" size="small">
        {{ recommendation.eligibility_match ? "符合资格" : "不符合资格" }}
      </el-tag>
    </div>
    <p class="reason">{{ recommendation.reason }}</p>
    <div class="script-suggestion">
      <strong>话术建议：</strong>{{ recommendation.script_suggestion }}
    </div>
    <el-tag v-if="recommendation.risk_tip" type="danger" size="small" effect="dark">
      ⚠ {{ recommendation.risk_tip }}
    </el-tag>
  </el-card>
</template>

<script setup lang="ts">
import type { ProductRecommendation as ProductRecommendationType } from "@/api/types"

defineProps<{ recommendation: ProductRecommendationType }>()
</script>

<style scoped>
.product-card {
  margin-bottom: 10px;
}

.product-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-2);
}

.product-name {
  font-size: var(--fs-md);
  font-weight: 600;
  color: var(--color-text-primary);
}

.reason {
  font-size: var(--fs-base);
  color: var(--color-text-regular);
  margin-bottom: var(--space-2);
}

.script-suggestion {
  font-size: var(--fs-base);
  color: var(--color-primary);
  margin-bottom: var(--space-2);
  padding: var(--space-2);
  background: var(--color-bg-hover);
  border-radius: var(--radius-md);
}
</style>
