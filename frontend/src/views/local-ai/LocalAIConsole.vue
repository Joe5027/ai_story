<template>
  <div class="local-ai-console">
    <header class="local-ai-page-header">
      <div>
        <p class="local-ai-eyebrow">
          混合推理控制面
        </p>
        <h1>本地 AI 与付费 API</h1>
        <p>
          管理运行节点、路由顺序、手工价目表和预算账本。自动付费仍受项目授权、有效价格和可用预算共同约束。
        </p>
      </div>
      <router-link
        to="/models"
        class="local-ai-button secondary"
      >
        模型管理
      </router-link>
    </header>

    <nav
      class="local-ai-tabs"
      aria-label="本地 AI 控制台导航"
    >
      <router-link
        v-for="tab in tabs"
        :key="tab.name"
        :to="{ name: tab.name }"
        class="local-ai-tab"
      >
        <span>{{ tab.label }}</span>
        <small>{{ tab.description }}</small>
      </router-link>
    </nav>

    <RuntimeNodesPanel v-if="$route.name === 'LocalAINodes'" />
    <RoutesPricingPanel v-else-if="$route.name === 'LocalAIRoutes'" />
    <BudgetLedgerPanel v-else />
  </div>
</template>

<script>
import RuntimeNodesPanel from './components/RuntimeNodesPanel.vue'
import RoutesPricingPanel from './components/RoutesPricingPanel.vue'
import BudgetLedgerPanel from './components/BudgetLedgerPanel.vue'

export default {
  name: 'LocalAIConsole',
  components: {
    RuntimeNodesPanel,
    RoutesPricingPanel,
    BudgetLedgerPanel
  },
  data() {
    return {
      tabs: [
        { name: 'LocalAINodes', label: '运行节点', description: '硬件、模型与槽位' },
        { name: 'LocalAIRoutes', label: '路由与价格', description: '目标顺序与计费依据' },
        { name: 'LocalAIBudget', label: '预算与账本', description: '额度、回退与调用明细' }
      ]
    }
  }
}
</script>

<style>
@import './local-ai.css';
</style>
