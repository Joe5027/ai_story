<template>
  <div>
    <section class="local-ai-summary-grid">
      <article
        v-for="item in summaryItems"
        :key="item.key"
        class="local-ai-summary-item"
      >
        <span>{{ item.label }}</span>
        <strong>{{ formatCny(item.used) }} / {{ formatCny(item.limit) }}</strong>
        <small>已预留 {{ formatCny(item.reserved) }} · 剩余 {{ formatCny(item.remaining) }}</small>
      </article>
    </section>

    <section
      class="local-ai-panel"
      style="margin-bottom: 1rem"
    >
      <div class="local-ai-section-heading">
        <div>
          <h2>全局预算上限</h2>
          <p>每日、每月和工具预算独立限制；保持 0 即可确保不会自动产生付费调用。</p>
        </div>
        <button
          class="local-ai-button secondary"
          type="button"
          @click="openPolicyForm"
        >
          {{ policyFormVisible ? '重新载入' : '维护上限' }}
        </button>
      </div>
      <form
        v-if="policyFormVisible"
        class="local-ai-form-panel"
        @submit.prevent="savePolicy"
      >
        <h3>全局付费预算策略</h3>
        <p>提交后只影响后续预算预留，不会追溯修改已结算账本。</p>
        <div class="local-ai-form-grid">
          <label class="local-ai-field wide">
            <span>策略名称</span>
            <input
              v-model.trim="policyForm.name"
              class="local-ai-input"
              required
              maxlength="120"
            >
          </label>
          <label class="local-ai-field">
            <span>每日上限（CNY）</span>
            <input
              v-model.number="policyForm.daily_limit_cny"
              class="local-ai-input"
              type="number"
              min="0"
              step="0.01"
              required
            >
          </label>
          <label class="local-ai-field">
            <span>每月上限（CNY）</span>
            <input
              v-model.number="policyForm.monthly_limit_cny"
              class="local-ai-input"
              type="number"
              min="0"
              step="0.01"
              required
            >
          </label>
          <label class="local-ai-field">
            <span>连接测试 / 工具上限（CNY）</span>
            <input
              v-model.number="policyForm.tooling_limit_cny"
              class="local-ai-input"
              type="number"
              min="0"
              step="0.01"
              required
            >
          </label>
          <label class="local-ai-checkbox">
            <input
              v-model="policyForm.is_active"
              type="checkbox"
            >
            启用策略
          </label>
        </div>
        <div class="local-ai-form-actions">
          <button
            class="local-ai-button primary"
            type="submit"
            :disabled="policySaving"
          >
            {{ policySaving ? '保存中' : '保存全局上限' }}
          </button>
          <button
            class="local-ai-button secondary"
            type="button"
            @click="policyFormVisible = false"
          >
            取消
          </button>
        </div>
      </form>
      <div
        v-else
        class="local-ai-toolbar"
      >
        <div class="local-ai-note">
          预算、云端授权和有效价目表必须同时通过；任一项缺失都会阻断自动付费回退。
        </div>
      </div>
    </section>

    <section class="local-ai-panel">
      <div class="local-ai-section-heading">
        <div>
          <h2>项目 AI 设置与执行前估算</h2>
          <p>预算默认值为 0；授权数据出站不会自动放宽预算或补齐价目表。</p>
        </div>
      </div>
      <div class="local-ai-toolbar">
        <div class="local-ai-filter-grid">
          <label class="local-ai-field wide">
            <span>项目 ID</span>
            <input
              v-model.trim="projectId"
              class="local-ai-input"
              placeholder="输入项目 UUID 后读取设置"
              @keyup.enter="loadProjectControl"
            >
          </label>
          <div class="local-ai-form-actions">
            <button
              class="local-ai-button secondary"
              type="button"
              :disabled="projectLoading || !projectId"
              @click="loadProjectControl"
            >
              {{ projectLoading ? '读取中' : '读取项目' }}
            </button>
          </div>
        </div>
      </div>
      <form
        v-if="projectSettings"
        class="local-ai-form-panel"
        @submit.prevent="saveProjectSettings"
      >
        <h3>项目路由安全门</h3>
        <p>开启云端授权前会再次确认。审美不满意不会自动付费，应由用户在具体工作项上手动选择 API 重生成。</p>
        <div class="local-ai-form-grid">
          <label class="local-ai-field">
            <span>默认质量档</span>
            <select
              v-model="projectSettings.default_profile_code"
              class="local-ai-select"
            >
              <option value="draft">草稿</option>
              <option value="balanced">均衡</option>
              <option value="final">成片</option>
            </select>
          </label>
          <label class="local-ai-field">
            <span>项目 API 预算（CNY）</span>
            <input
              v-model.number="projectSettings.project_budget_cny"
              class="local-ai-input"
              type="number"
              min="0"
              step="0.01"
            >
          </label>
          <label class="local-ai-field">
            <span>本次估算能力</span>
            <select
              v-model="estimateRequest.capability"
              class="local-ai-select"
            >
              <option value="llm">文本生成</option>
              <option value="text2image">文生图</option>
              <option value="image_edit">图片编辑</option>
              <option value="image2video">图生视频</option>
              <option value="motion_render">静态运镜</option>
            </select>
          </label>
          <label class="local-ai-field">
            <span>估算工作项数量</span>
            <input
              v-model.number="estimateRequest.task_count"
              class="local-ai-input"
              type="number"
              min="1"
              max="10000"
            >
          </label>
          <label class="local-ai-field">
            <span>估算阶段</span>
            <input
              v-model.trim="estimateRequest.stage_type"
              class="local-ai-input"
              placeholder="可选阶段标识"
            >
          </label>
          <label class="local-ai-checkbox">
            <input
              v-model="projectSettings.prefer_local"
              type="checkbox"
            >
            本地优先
          </label>
          <label class="local-ai-checkbox">
            <input
              v-model="projectSettings.allow_cloud_data_transfer"
              type="checkbox"
            >
            允许提示词和媒体出站
          </label>
          <label class="local-ai-checkbox">
            <input
              v-model="projectSettings.allow_paid_fallback"
              type="checkbox"
            >
            允许技术失败后付费回退
          </label>
        </div>
        <div class="local-ai-form-actions">
          <button
            class="local-ai-button primary"
            type="submit"
            :disabled="projectSaving"
          >
            {{ projectSaving ? '保存中' : '保存项目设置' }}
          </button>
          <button
            class="local-ai-button secondary"
            type="button"
            :disabled="estimateLoading"
            @click="runEstimate"
          >
            {{ estimateLoading ? '估算中' : '执行前估算' }}
          </button>
          <button
            class="local-ai-button secondary"
            type="button"
            :disabled="retrying || !failedWorkItemCount"
            @click="retryFailed"
          >
            {{ retrying ? '提交中' : `重试失败项（${failedWorkItemCount}）` }}
          </button>
        </div>
        <div
          v-if="estimate"
          class="local-ai-note"
        >
          预计 {{ estimate.effective_work_item_count ?? estimate.task_count ?? estimate.work_item_count ?? '--' }} 个工作项，最坏 API 成本
          {{ formatCny(estimate.worst_api_cost_cny ?? estimate.worst_case_cost_cny ?? estimate.estimated_cost_cny ?? estimate.total_cost_cny) }}。
          <span v-if="estimate.missing_configuration?.length">
            缺失配置：{{ estimate.missing_configuration.join('、') }}。
          </span>
          <span v-else-if="estimate.missing?.length">
            缺失配置：{{ estimate.missing.join('、') }}。
          </span>
        </div>
        <div
          v-if="manualRegenerationItems.length"
          class="manual-regeneration-section"
        >
          <div class="manual-regeneration-heading">
            <div>
              <h4>主观质量不满意：使用 API 重生成</h4>
              <p>只复制所选终态工作项，不伪装成技术失败；提交前必须确认云授权和最大费用。</p>
            </div>
          </div>
          <div class="local-ai-table-wrap">
            <table class="local-ai-table manual-regeneration-table">
              <thead>
                <tr>
                  <th>源工作项</th>
                  <th>原执行方式</th>
                  <th>API Provider</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in manualRegenerationItems"
                  :key="item.id"
                >
                  <td>
                    <strong>{{ workItemLabel(item) }}</strong>
                    <small>{{ item.id }}</small>
                  </td>
                  <td>
                    <span :class="['local-ai-status', item.runtime_node ? 'local' : 'unknown']">
                      {{ item.provider_name || item.runtime_node_name || '自动 / 本地' }}
                    </span>
                    <small>{{ statusLabel(item.status) }}</small>
                  </td>
                  <td>
                    <select
                      v-model="manualProviderByItem[item.id]"
                      class="local-ai-select manual-provider-select"
                    >
                      <option value="">
                        请选择
                      </option>
                      <option
                        v-for="provider in paidProvidersFor(item)"
                        :key="provider.id"
                        :value="provider.id"
                      >
                        {{ provider.name }} · {{ provider.model_name }}
                      </option>
                    </select>
                  </td>
                  <td>
                    <button
                      class="local-ai-button primary"
                      type="button"
                      :disabled="manualRegeneratingItemId === item.id || !manualProviderByItem[item.id]"
                      @click="regenerateWorkItemWithApi(item)"
                    >
                      {{ manualRegeneratingItemId === item.id ? '提交中' : '查看费用并重生成' }}
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </form>
      <div
        v-if="projectError"
        class="local-ai-error"
      >
        <div>
          <h3>项目控制面读取失败</h3>
          <p>{{ projectError }}</p>
        </div>
      </div>
    </section>

    <section
      class="local-ai-panel"
      style="margin-top: 1rem"
    >
      <div class="local-ai-section-heading">
        <div>
          <h2>预算与调用账本</h2>
          <p>账本只返回摘要和媒体哈希，不包含完整 API Key、提示词或媒体内容。</p>
        </div>
        <button
          class="local-ai-button secondary"
          type="button"
          :disabled="exporting"
          @click="exportLedger"
        >
          {{ exporting ? '导出中' : '导出安全 CSV' }}
        </button>
      </div>

      <div class="local-ai-toolbar">
        <div class="local-ai-filter-grid">
          <label class="local-ai-field">
            <span>项目 ID</span>
            <input
              v-model.trim="filters.project_id"
              class="local-ai-input"
              placeholder="全部项目"
            >
          </label>
          <label class="local-ai-field">
            <span>阶段</span>
            <input
              v-model.trim="filters.stage_type"
              class="local-ai-input"
              placeholder="全部阶段"
            >
          </label>
          <label class="local-ai-field">
            <span>执行方式</span>
            <select
              v-model="filters.deployment_mode"
              class="local-ai-select"
            >
              <option value="">全部</option>
              <option value="local">本地</option>
              <option value="api">API</option>
              <option value="mock">Mock</option>
            </select>
          </label>
          <label class="local-ai-field">
            <span>状态</span>
            <select
              v-model="filters.status"
              class="local-ai-select"
            >
              <option value="">全部</option>
              <option value="success">成功</option>
              <option value="failed">失败</option>
              <option value="in_progress">执行中</option>
              <option value="ambiguous">待人工核对</option>
            </select>
          </label>
        </div>
        <div class="local-ai-row-actions">
          <button
            class="local-ai-button primary"
            type="button"
            @click="applyFilters"
          >
            查询
          </button>
          <button
            class="local-ai-button secondary"
            type="button"
            @click="resetFilters"
          >
            重置
          </button>
        </div>
      </div>

      <div
        v-if="loading"
        class="local-ai-loading"
      >
        <span class="local-ai-spinner" />
        <span>正在汇总预算与账本</span>
      </div>
      <div
        v-else-if="errorMessage"
        class="local-ai-error"
      >
        <div>
          <h3>预算账本加载失败</h3>
          <p>{{ errorMessage }}</p>
          <button
            class="local-ai-button secondary"
            type="button"
            @click="loadLedger"
          >
            重试
          </button>
        </div>
      </div>
      <div
        v-else-if="!logs.length"
        class="local-ai-empty"
      >
        <div>
          <h3>当前筛选条件下没有调用记录</h3>
          <p>本地、API 和 Mock 调用成功或失败后，都会在这里留下可审计记录。</p>
          <button
            class="local-ai-button secondary"
            type="button"
            @click="resetFilters"
          >
            查看全部记录
          </button>
        </div>
      </div>
      <div
        v-else
        class="local-ai-table-wrap"
      >
        <table class="local-ai-table">
          <thead>
            <tr>
              <th>时间 / 项目</th>
              <th>阶段 / Provider</th>
              <th>执行方式</th>
              <th>状态 / 延迟</th>
              <th>用量</th>
              <th>预估 / 结算</th>
              <th>回退 / 错误</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="log in logs"
              :key="log.id"
            >
              <td>
                <strong>{{ formatDateTime(log.started_at || log.created_at) }}</strong>
                <small>{{ log.project_name || log.project_id || log.project || '未关联项目' }}</small>
              </td>
              <td>
                <strong>{{ log.stage_type || '--' }}</strong>
                <small>{{ log.model_provider_name || log.provider_name || log.model_provider || '--' }}</small>
              </td>
              <td>
                <span :class="['local-ai-status', log.deployment_mode || 'unknown']">
                  {{ deploymentLabel(log.deployment_mode) }}
                </span>
                <small v-if="log.runtime_node_name">{{ log.runtime_node_name }}</small>
              </td>
              <td>
                <span :class="['local-ai-status', statusClass(log.status)]">
                  {{ statusLabel(log.status) }}
                </span>
                <small>{{ Number(log.latency_ms || 0).toLocaleString() }} ms</small>
              </td>
              <td>
                <strong>{{ usageSummary(log) }}</strong>
                <small>尝试 {{ log.attempt_number || 1 }}</small>
              </td>
              <td>
                <strong>{{ formatCny(log.estimated_cost) }}</strong>
                <small>结算 {{ formatCny(log.settled_cost ?? log.cost) }}</small>
              </td>
              <td>
                <strong>{{ log.fallback_reason || log.error_code || '--' }}</strong>
                <small>{{ fallbackSummary(log) }}</small>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div
        v-if="total > pageSize"
        class="local-ai-pagination"
      >
        <button
          class="local-ai-button secondary"
          type="button"
          :disabled="page <= 1 || loading"
          @click="changePage(page - 1)"
        >
          上一页
        </button>
        <span>第 {{ page }} 页，共 {{ Math.ceil(total / pageSize) }} 页</span>
        <button
          class="local-ai-button secondary"
          type="button"
          :disabled="page >= Math.ceil(total / pageSize) || loading"
          @click="changePage(page + 1)"
        >
          下一页
        </button>
      </div>
    </section>
  </div>
</template>

<script>
import { budgetApi, projectInferenceApi, usageLedgerApi } from '@/api/inference'
import { modelProviderApi } from '@/api/models'
import { confirmPaidGeneration } from '@/services/paidGenerationGuard'

const blankFilters = () => ({
  project_id: '',
  stage_type: '',
  deployment_mode: '',
  status: ''
})

export default {
  name: 'BudgetLedgerPanel',
  data() {
    return {
      summary: {},
      logs: [],
      loading: false,
      exporting: false,
      errorMessage: '',
      filters: blankFilters(),
      page: 1,
      pageSize: 30,
      total: 0,
      projectId: '',
      projectSettings: null,
      originalCloudAuthorization: false,
      projectWorkItems: [],
      projectLoading: false,
      projectSaving: false,
      estimateLoading: false,
      retrying: false,
      paidProviders: [],
      manualProviderByItem: {},
      manualRegeneratingItemId: '',
      manualRegenerationKeys: {},
      projectError: '',
      estimate: null,
      policyFormVisible: false,
      policySaving: false,
      policyEditingId: null,
      policyForm: {
        name: '全局安全预算',
        daily_limit_cny: 0,
        monthly_limit_cny: 0,
        tooling_limit_cny: 0,
        is_active: true
      },
      estimateRequest: {
        capability: 'llm',
        task_count: 1,
        stage_type: ''
      }
    }
  },
  computed: {
    summaryItems() {
      return [
        this.summaryItem('day', '全局今日', ['daily', 'day']),
        this.summaryItem('month', '全局本月', ['monthly', 'month']),
        this.summaryItem('project', '当前项目', ['project']),
        this.summaryItem('tooling', '连接测试 / 工具', ['tooling', 'tool'])
      ]
    },
    failedWorkItemCount() {
      return this.projectWorkItems.filter((item) => item.status === 'failed').length
    },
    manualRegenerationItems() {
      return this.projectWorkItems
        .filter((item) => ['succeeded', 'failed'].includes(item.status))
        .filter((item) => this.paidProviders.some(
          (provider) => provider.provider_type === item.capability
        ))
        .slice(0, 50)
    }
  },
  created() {
    this.projectId = String(this.$route.query.project_id || '')
    this.loadLedger()
    if (this.projectId) {
      this.loadProjectControl()
    }
  },
  methods: {
    async loadLedger() {
      this.loading = true
      this.errorMessage = ''
      const params = this.queryParams()
      try {
        const [summary, ledger] = await Promise.all([
          budgetApi.summary(this.filters.project_id ? { project_id: this.filters.project_id } : {}),
          usageLedgerApi.list(params)
        ])
        this.summary = summary || {}
        this.logs = Array.isArray(ledger) ? ledger : (ledger?.results || [])
        this.total = Array.isArray(ledger) ? ledger.length : Number(ledger?.count || this.logs.length)
      } catch (error) {
        this.errorMessage = this.getErrorMessage(error, '无法读取预算摘要或调用账本。')
      } finally {
        this.loading = false
      }
    },
    queryParams() {
      const params = {
        page: this.page,
        page_size: this.pageSize,
        ordering: '-created_at'
      }
      Object.entries(this.filters).forEach(([key, value]) => {
        if (value !== '') {
          params[key] = value
        }
      })
      return params
    },
    applyFilters() {
      this.page = 1
      this.loadLedger()
    },
    resetFilters() {
      this.filters = blankFilters()
      this.page = 1
      this.loadLedger()
    },
    changePage(page) {
      this.page = page
      this.loadLedger()
    },
    openPolicyForm() {
      const policy = (this.summary?.policies || []).find((item) => item.is_active) || this.summary?.policies?.[0]
      this.policyEditingId = policy?.id || null
      this.policyForm = {
        name: policy?.name || '全局安全预算',
        daily_limit_cny: Number(policy?.daily_limit_cny || 0),
        monthly_limit_cny: Number(policy?.monthly_limit_cny || 0),
        tooling_limit_cny: Number(policy?.tooling_limit_cny || 0),
        is_active: policy?.is_active ?? true
      }
      this.policyFormVisible = true
    },
    async savePolicy() {
      this.policySaving = true
      try {
        const payload = {
          name: this.policyForm.name,
          currency: 'CNY',
          period_type: 'lifetime',
          hard_limit: 0,
          daily_limit_cny: Number(this.policyForm.daily_limit_cny || 0),
          monthly_limit_cny: Number(this.policyForm.monthly_limit_cny || 0),
          tooling_limit_cny: Number(this.policyForm.tooling_limit_cny || 0),
          exhausted_action: 'block',
          allow_overage: false,
          is_active: Boolean(this.policyForm.is_active)
        }
        if (this.policyEditingId) {
          await budgetApi.updatePolicy(this.policyEditingId, payload)
        } else {
          await budgetApi.createPolicy(payload)
        }
        this.policyFormVisible = false
        await this.loadLedger()
      } catch (error) {
        await this.$alert(this.getErrorMessage(error, '全局预算保存失败'), '保存失败', { tone: 'error' })
      } finally {
        this.policySaving = false
      }
    },
    async loadProjectControl() {
      if (!this.projectId) {
        return
      }
      this.projectLoading = true
      this.projectError = ''
      this.estimate = null
      try {
        const [settings, workItems, providers] = await Promise.all([
          projectInferenceApi.getAISettings(this.projectId),
          projectInferenceApi.getWorkItems(this.projectId, { page_size: 500 }),
          modelProviderApi.getProviders({
            deployment_mode: 'api',
            is_active: true,
            page_size: 500,
          })
        ])
        this.projectSettings = {
          default_profile_code: 'balanced',
          project_budget_cny: 0,
          prefer_local: true,
          allow_cloud_data_transfer: false,
          allow_paid_fallback: false,
          ...settings
        }
        this.originalCloudAuthorization = Boolean(this.projectSettings.allow_cloud_data_transfer)
        this.projectWorkItems = Array.isArray(workItems) ? workItems : (workItems?.results || [])
        this.paidProviders = Array.isArray(providers) ? providers : (providers?.results || [])
        this.manualProviderByItem = Object.fromEntries(
          this.projectWorkItems.map((item) => {
            const matching = this.paidProviders.filter(
              (provider) => provider.provider_type === item.capability
            )
            return [item.id, matching.length === 1 ? matching[0].id : '']
          })
        )
        this.filters.project_id = this.projectId
        this.page = 1
        await this.loadLedger()
      } catch (error) {
        this.projectSettings = null
        this.projectWorkItems = []
        this.paidProviders = []
        this.manualProviderByItem = {}
        this.projectError = this.getErrorMessage(error, '无法读取项目 AI 设置。')
      } finally {
        this.projectLoading = false
      }
    },
    async saveProjectSettings() {
      if (!this.projectSettings) {
        return
      }
      if (this.projectSettings.allow_cloud_data_transfer && !this.originalCloudAuthorization) {
        const confirmed = await this.$confirm(
          '开启后，该项目的提示词和媒体可在技术失败时发送给外部 Provider。预算与价目表仍会独立校验。确定授权吗？',
          '授权数据出站',
          { tone: 'warning', confirmText: '确认授权' }
        )
        if (!confirmed) {
          this.projectSettings.allow_cloud_data_transfer = false
          return
        }
      }
      this.projectSaving = true
      try {
        const payload = {
          default_profile_code: this.projectSettings.default_profile_code,
          project_budget_cny: Number(this.projectSettings.project_budget_cny || 0),
          prefer_local: Boolean(this.projectSettings.prefer_local),
          allow_cloud_data_transfer: Boolean(this.projectSettings.allow_cloud_data_transfer),
          allow_paid_fallback: Boolean(this.projectSettings.allow_paid_fallback),
          confirm_cloud_data_transfer: Boolean(
            this.projectSettings.allow_cloud_data_transfer && !this.originalCloudAuthorization
          )
        }
        const response = await projectInferenceApi.updateAISettings(this.projectId, payload)
        this.projectSettings = { ...this.projectSettings, ...response }
        this.originalCloudAuthorization = Boolean(this.projectSettings.allow_cloud_data_transfer)
        await this.$alert('项目 AI 设置已保存。', '保存成功', { tone: 'success' })
      } catch (error) {
        await this.$alert(this.getErrorMessage(error, '项目设置保存失败'), '保存失败', { tone: 'error' })
      } finally {
        this.projectSaving = false
      }
    },
    async runEstimate() {
      this.estimateLoading = true
      try {
        this.estimate = await projectInferenceApi.getEstimate(this.projectId, {
          profile: this.projectSettings.default_profile_code,
          capability: this.estimateRequest.capability,
          task_count: Number(this.estimateRequest.task_count || 1),
          stage_type: this.estimateRequest.stage_type
        })
      } catch (error) {
        await this.$alert(this.getErrorMessage(error, '执行前估算失败'), '估算失败', { tone: 'error' })
      } finally {
        this.estimateLoading = false
      }
    },
    async retryFailed() {
      const confirmed = await this.$confirm(
        `只重试当前项目的 ${this.failedWorkItemCount} 个失败工作项，不会重跑成功项。继续吗？`,
        '重试失败工作项',
        { tone: 'warning', confirmText: '开始重试' }
      )
      if (!confirmed) {
        return
      }
      this.retrying = true
      try {
        await projectInferenceApi.retryFailedItems(this.projectId, {
          work_item_ids: this.projectWorkItems.filter((item) => item.status === 'failed').map((item) => item.id)
        })
        await this.loadProjectControl()
      } catch (error) {
        await this.$alert(this.getErrorMessage(error, '重试请求提交失败'), '提交失败', { tone: 'error' })
      } finally {
        this.retrying = false
      }
    },
    paidProvidersFor(item) {
      return this.paidProviders.filter(
        (provider) => provider.provider_type === item.capability
      )
    },
    workItemUsageEstimate(item) {
      const usage = item.usage || {}
      if (item.capability === 'llm') {
        return {
          request_count: 1,
          ...usage,
          input_tokens: Math.max(16000, Number(usage.input_tokens || 0)),
          output_tokens: Math.max(4000, Number(usage.output_tokens || 0)),
        }
      }
      if (item.capability === 'image2video') {
        return {
          request_count: 1,
          ...usage,
          video_tasks: Math.max(1, Number(usage.video_tasks || 0)),
          video_seconds: Math.max(10, Number(usage.video_seconds || 0)),
        }
      }
      return {
        request_count: 1,
        ...usage,
        image_count: Math.max(1, Number(usage.image_count || 0)),
      }
    },
    workItemLabel(item) {
      const parts = [item.stage_type || item.capability]
      if (item.storyboard_id) {
        parts.push(`分镜 ${String(item.storyboard_id).slice(0, 8)}`)
      }
      if (item.segment_index != null) {
        parts.push(`片段 ${Number(item.segment_index) + 1}`)
      }
      return parts.join(' · ')
    },
    newManualRegenerationKey(itemId, providerId) {
      const randomPart = globalThis.crypto?.randomUUID?.()
        || `${Date.now()}-${Math.random().toString(36).slice(2)}`
      return `manual-api:${itemId}:${providerId}:${randomPart}`
    },
    async regenerateWorkItemWithApi(item) {
      const providerId = this.manualProviderByItem[item.id]
      const provider = this.paidProviders.find((candidate) => candidate.id === providerId)
      if (!provider) {
        await this.$alert('请先选择与工作项能力匹配的 API Provider。', '请选择 Provider', {
          tone: 'warning'
        })
        return
      }
      const paidConfirmation = await confirmPaidGeneration({
        projectId: this.projectId,
        capability: item.capability,
        stageType: item.stage_type,
        taskCount: 1,
        usagePerItem: this.workItemUsageEstimate(item),
        providerId: provider.id,
        operationLabel: `主观质量不满意，使用 ${provider.name} 重生成 ${this.workItemLabel(item)}`,
        confirm: this.$confirm,
        alert: this.$alert,
      })
      if (!paidConfirmation) {
        return
      }

      const keyId = `${item.id}:${provider.id}`
      const idempotencyKey = this.manualRegenerationKeys[keyId]
        || this.newManualRegenerationKey(item.id, provider.id)
      this.manualRegenerationKeys[keyId] = idempotencyKey
      this.manualRegeneratingItemId = item.id
      try {
        const response = await projectInferenceApi.regenerateWorkItemWithApi(
          this.projectId,
          {
            work_item_id: item.id,
            provider_id: provider.id,
            confirm_paid_generation: true,
            confirmed_max_cost_cny: paidConfirmation.maxCostCny,
          },
          idempotencyKey
        )
        delete this.manualRegenerationKeys[keyId]
        await this.$alert(
          `API 重生成工作项已提交，最大费用上限 ${this.formatCny(response.maximum_estimated_cost_cny)}。`,
          response.idempotent_replay ? '已恢复原请求' : '已提交 API 重生成',
          { tone: 'success' }
        )
        await this.loadProjectControl()
      } catch (error) {
        // 网络结果不明确时保留幂等键；用户重试不会创建第二个付费工作项。
        await this.$alert(
          this.getErrorMessage(error, 'API 重生成提交失败；再次尝试会复用同一幂等键。'),
          '提交失败',
          { tone: 'error' }
        )
      } finally {
        this.manualRegeneratingItemId = ''
      }
    },
    async exportLedger() {
      this.exporting = true
      try {
        const response = await usageLedgerApi.exportCsv(this.queryParams())
        const blob = response instanceof Blob
          ? response
          : new Blob([response], { type: 'text/csv;charset=utf-8' })
        this.saveBlob(blob, `ai-usage-ledger-${new Date().toISOString().slice(0, 10)}.csv`)
      } catch (error) {
        // 后端未启用 CSV action 时使用当前已脱敏列表生成安全导出。
        const csv = this.buildSafeCsv(this.logs)
        this.saveBlob(new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' }), 'ai-usage-ledger-current-page.csv')
      } finally {
        this.exporting = false
      }
    },
    buildSafeCsv(logs) {
      const headers = [
        'id', 'created_at', 'project_id', 'stage_type', 'provider', 'deployment_mode',
        'status', 'latency_ms', 'input_tokens', 'output_tokens', 'image_count',
        'video_seconds', 'estimated_cost_cny', 'settled_cost_cny', 'error_code', 'fallback_reason'
      ]
      const rows = logs.map((log) => [
        log.id,
        log.created_at,
        log.project_id || log.project,
        log.stage_type,
        log.model_provider_name || log.provider_name || log.model_provider,
        log.deployment_mode,
        log.status,
        log.latency_ms,
        log.input_tokens,
        log.output_tokens,
        log.image_count,
        log.video_seconds,
        log.estimated_cost,
        log.settled_cost ?? log.cost,
        log.error_code,
        log.fallback_reason
      ])
      return [headers, ...rows].map((row) => row.map(this.csvCell).join(',')).join('\n')
    },
    csvCell(value) {
      const text = String(value ?? '')
      return `"${text.replace(/"/g, '""')}"`
    },
    saveBlob(blob, filename) {
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
    },
    summaryItem(key, label, candidates) {
      const policy = (this.summary?.policies || []).find((item) => item.is_active) || {}
      if (key === 'day') {
        const limit = Number(policy.daily_limit_cny || 0)
        const used = Number(this.summary?.today_settled || 0)
        const reserved = Number(this.summary?.reserved_total || 0)
        return { key, label, limit, used, reserved, remaining: Math.max(0, limit - used - reserved) }
      }
      if (key === 'month') {
        const limit = Number(policy.monthly_limit_cny || 0)
        const used = Number(this.summary?.month_settled || 0)
        const reserved = Number(this.summary?.reserved_total || 0)
        return { key, label, limit, used, reserved, remaining: Math.max(0, limit - used - reserved) }
      }
      if (key === 'project') {
        const limit = Number(this.projectSettings?.project_budget_cny || 0)
        const used = this.projectId ? Number(this.summary?.settled_total || 0) : 0
        const reserved = this.projectId ? Number(this.summary?.reserved_total || 0) : 0
        return { key, label, limit, used, reserved, remaining: Math.max(0, limit - used - reserved) }
      }
      if (key === 'tooling') {
        const limit = Number(policy.tooling_limit_cny || 0)
        return { key, label, limit, used: 0, reserved: 0, remaining: limit }
      }
      let data = {}
      for (const candidate of candidates) {
        if (this.summary?.[candidate] && typeof this.summary[candidate] === 'object') {
          data = this.summary[candidate]
          break
        }
      }
      const prefix = candidates[0]
      const limit = Number(data.limit_cny ?? data.limit ?? this.summary?.[`${prefix}_limit_cny`] ?? 0)
      const used = Number(data.spent_cny ?? data.spent ?? data.used ?? this.summary?.[`${prefix}_spent_cny`] ?? 0)
      const reserved = Number(data.reserved_cny ?? data.reserved ?? this.summary?.[`${prefix}_reserved_cny`] ?? 0)
      const remaining = Number(data.remaining_cny ?? data.remaining ?? Math.max(0, limit - used - reserved))
      return { key, label, limit, used, reserved, remaining }
    },
    usageSummary(log) {
      const pieces = []
      if (log.input_tokens || log.output_tokens || log.tokens_used) {
        pieces.push(`${Number(log.input_tokens || 0) + Number(log.output_tokens || log.tokens_used || 0)} Token`)
      }
      if (log.image_count) {
        pieces.push(`${log.image_count} 张图`)
      }
      if (log.video_seconds) {
        pieces.push(`${log.video_seconds} 秒视频`)
      }
      return pieces.join('，') || '未上报用量'
    },
    fallbackSummary(log) {
      if (log.fallback_from_name || log.fallback_to_name) {
        return `${log.fallback_from_name || '本地'} → ${log.fallback_to_name || 'API'}`
      }
      if (log.fallback_from || log.fallback_to) {
        return `${log.fallback_from || '本地'} → ${log.fallback_to || 'API'}`
      }
      return log.error_message || '未发生回退'
    },
    deploymentLabel(value) {
      return { local: '本地', api: 'API', mock: 'Mock' }[value] || '未知'
    },
    statusClass(value) {
      return {
        success: 'success',
        succeeded: 'succeeded',
        failed: 'failed',
        ambiguous: 'ambiguous',
        in_progress: 'running'
      }[value] || 'unknown'
    },
    statusLabel(value) {
      return {
        success: '成功',
        succeeded: '成功',
        failed: '失败',
        ambiguous: '待核对',
        in_progress: '执行中',
        pending: '等待中'
      }[value] || value || '未知'
    },
    formatCny(value) {
      const amount = Number(value || 0)
      return `¥${Number.isFinite(amount) ? amount.toFixed(2) : '0.00'}`
    },
    formatDateTime(value) {
      if (!value) {
        return '--'
      }
      const date = new Date(value)
      return Number.isNaN(date.getTime()) ? '--' : date.toLocaleString('zh-CN', { hour12: false })
    },
    getErrorMessage(error, fallback) {
      const data = error?.response?.data
      if (typeof data?.error === 'string') {
        return data.error
      }
      if (typeof data?.error?.message === 'string') {
        return data.error.message
      }
      if (typeof data?.detail === 'string') {
        return data.detail
      }
      if (data && typeof data === 'object') {
        const message = Object.values(data).flat().find((item) => typeof item === 'string')
        if (message) {
          return message
        }
      }
      return fallback
    }
  }
}
</script>

<style scoped>
.manual-regeneration-section {
  margin-top: 1rem;
  border-top: 1px solid var(--local-ai-border);
  padding-top: 1rem;
}

.manual-regeneration-heading {
  margin-bottom: 0.75rem;
}

.manual-regeneration-heading h4 {
  margin: 0;
  color: var(--local-ai-ink);
  font-size: 0.95rem;
}

.manual-regeneration-heading p {
  margin: 0.25rem 0 0;
  color: var(--local-ai-muted);
  font-size: 0.8rem;
}

.manual-regeneration-table {
  min-width: 760px;
  border: 1px solid var(--local-ai-border);
  border-radius: 14px;
}

.manual-provider-select {
  min-width: 220px;
}
</style>
