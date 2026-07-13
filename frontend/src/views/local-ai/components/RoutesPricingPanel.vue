<template>
  <div class="local-ai-split">
    <section class="local-ai-panel">
      <div class="local-ai-section-heading">
        <div>
          <h2>生成路由</h2>
          <p>按阶段与质量档排列本地目标和付费回退目标。</p>
        </div>
        <button
          class="local-ai-button primary"
          type="button"
          @click="openRouteForm()"
        >
          新建路由
        </button>
      </div>

      <form
        v-if="routeFormVisible"
        class="local-ai-form-panel"
        @submit.prevent="saveRoute"
      >
        <h3>{{ routeEditingId ? '编辑路由' : '新建路由' }}</h3>
        <p>项目路由优先于全局路由；付费目标仍不能绕过隐私、价格和预算门。</p>
        <div class="local-ai-form-grid">
          <label class="local-ai-field wide">
            <span>路由名称</span>
            <input
              v-model.trim="routeForm.name"
              class="local-ai-input"
              required
              maxlength="120"
              placeholder="balanced-image-local-first"
            >
          </label>
          <label class="local-ai-field">
            <span>能力</span>
            <select
              v-model="routeForm.capability"
              class="local-ai-select"
            >
              <option
                v-for="item in capabilityOptions"
                :key="item.value"
                :value="item.value"
              >
                {{ item.label }}
              </option>
            </select>
          </label>
          <label class="local-ai-field">
            <span>作用域</span>
            <select
              v-model="routeForm.scope"
              class="local-ai-select"
            >
              <option value="global">全局</option>
              <option value="project">指定项目</option>
            </select>
          </label>
          <label
            v-if="routeForm.scope === 'project'"
            class="local-ai-field"
          >
            <span>项目 ID</span>
            <input
              v-model.trim="routeForm.project"
              class="local-ai-input"
              required
              placeholder="项目 UUID"
            >
          </label>
          <label class="local-ai-field">
            <span>阶段</span>
            <input
              v-model.trim="routeForm.stage_type"
              class="local-ai-input"
              placeholder="留空匹配所有阶段"
            >
          </label>
          <label class="local-ai-field">
            <span>质量档</span>
            <select
              v-model="routeForm.profile_code"
              class="local-ai-select"
            >
              <option value="draft">草稿</option>
              <option value="balanced">均衡</option>
              <option value="final">成片</option>
            </select>
          </label>
          <label class="local-ai-field">
            <span>优先级</span>
            <input
              v-model.number="routeForm.priority"
              class="local-ai-input"
              type="number"
            >
          </label>
          <label class="local-ai-checkbox">
            <input
              v-model="routeForm.local_first"
              type="checkbox"
            >
            本地优先
          </label>
          <label class="local-ai-checkbox">
            <input
              v-model="routeForm.is_active"
              type="checkbox"
            >
            启用路由
          </label>
        </div>
        <div class="local-ai-form-actions">
          <button
            class="local-ai-button primary"
            type="submit"
            :disabled="routeSaving"
          >
            {{ routeSaving ? '保存中' : '保存路由' }}
          </button>
          <button
            class="local-ai-button secondary"
            type="button"
            @click="closeRouteForm"
          >
            取消
          </button>
        </div>
      </form>

      <form
        v-if="targetRoute"
        class="local-ai-form-panel"
        @submit.prevent="saveTarget"
      >
        <h3>为“{{ targetRoute.name }}”添加目标</h3>
        <p>本地目标可绑定 Runtime 节点；付费回退必须绑定 API Provider，并维护有效价目表。</p>
        <div class="local-ai-form-grid">
          <label class="local-ai-field wide">
            <span>目标名称</span>
            <input
              v-model.trim="targetForm.name"
              class="local-ai-input"
              required
              placeholder="local-flux-primary"
            >
          </label>
          <label class="local-ai-field">
            <span>角色</span>
            <select
              v-model="targetForm.role"
              class="local-ai-select"
            >
              <option value="local_primary">本地主目标</option>
              <option value="local_secondary">本地备目标</option>
              <option value="paid_fallback">付费回退</option>
            </select>
          </label>
          <label class="local-ai-field">
            <span>Provider</span>
            <select
              v-model="targetForm.provider"
              class="local-ai-select"
            >
              <option value="">不绑定</option>
              <option
                v-for="provider in providers"
                :key="provider.id"
                :value="provider.id"
              >
                {{ provider.name }}（{{ deploymentLabel(provider.deployment_mode) }}）
              </option>
            </select>
          </label>
          <label class="local-ai-field">
            <span>Runtime 节点</span>
            <select
              v-model="targetForm.runtime_node"
              class="local-ai-select"
            >
              <option value="">不绑定</option>
              <option
                v-for="node in nodes"
                :key="node.id"
                :value="node.id"
              >
                {{ node.name }}
              </option>
            </select>
          </label>
          <label class="local-ai-field">
            <span>顺序</span>
            <input
              v-model.number="targetForm.position"
              class="local-ai-input"
              type="number"
              min="0"
            >
          </label>
          <label class="local-ai-field">
            <span>超时（秒）</span>
            <input
              v-model.number="targetForm.timeout_seconds"
              class="local-ai-input"
              type="number"
              min="1"
            >
          </label>
          <label class="local-ai-field">
            <span>最大尝试次数</span>
            <input
              v-model.number="targetForm.max_attempts"
              class="local-ai-input"
              type="number"
              min="1"
              max="2"
            >
          </label>
        </div>
        <div class="local-ai-form-actions">
          <button
            class="local-ai-button primary"
            type="submit"
            :disabled="targetSaving"
          >
            {{ targetSaving ? '保存中' : '添加目标' }}
          </button>
          <button
            class="local-ai-button secondary"
            type="button"
            @click="targetRoute = null"
          >
            取消
          </button>
        </div>
      </form>

      <div
        v-if="routeLoading"
        class="local-ai-loading"
      >
        <span class="local-ai-spinner" />
        <span>正在读取路由</span>
      </div>
      <div
        v-else-if="routeError"
        class="local-ai-error"
      >
        <div>
          <h3>路由加载失败</h3>
          <p>{{ routeError }}</p>
          <button
            class="local-ai-button secondary"
            type="button"
            @click="loadRoutes"
          >
            重试
          </button>
        </div>
      </div>
      <div
        v-else-if="!routes.length"
        class="local-ai-empty"
      >
        <div>
          <h3>尚未配置生成路由</h3>
          <p>先创建全局本地优先路由，再按项目添加少量覆盖，便于解释和回滚。</p>
          <button
            class="local-ai-button primary"
            type="button"
            @click="openRouteForm()"
          >
            新建路由
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
              <th>路由</th>
              <th>匹配范围</th>
              <th>目标顺序</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="route in routes"
              :key="route.id"
            >
              <td>
                <strong>{{ route.name }}</strong>
                <small>{{ capabilityLabel(route.capability) }} · {{ profileLabel(route.profile_code) }}</small>
              </td>
              <td>
                <strong>{{ route.scope === 'project' ? '项目' : '全局' }}</strong>
                <small>{{ route.stage_type || '全部阶段' }}</small>
              </td>
              <td>
                <strong>{{ targetSummary(route) }}</strong>
                <small>{{ (route.targets || []).length }} 个目标</small>
              </td>
              <td>
                <span :class="['local-ai-status', route.is_active ? 'active' : 'unknown']">
                  {{ route.is_active ? '已启用' : '已停用' }}
                </span>
              </td>
              <td>
                <div class="local-ai-row-actions">
                  <button
                    class="local-ai-button secondary"
                    type="button"
                    @click="openTargetForm(route)"
                  >
                    加目标
                  </button>
                  <button
                    class="local-ai-button secondary"
                    type="button"
                    @click="openRouteForm(route)"
                  >
                    编辑
                  </button>
                  <button
                    class="local-ai-button danger"
                    type="button"
                    @click="removeRoute(route)"
                  >
                    删除
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="local-ai-panel">
      <div class="local-ai-section-heading">
        <div>
          <h2>Provider 价目表</h2>
          <p>手工维护、版本化并设置生效时间；缺少匹配价格时禁止自动付费。</p>
        </div>
        <button
          class="local-ai-button primary"
          type="button"
          @click="openPriceForm()"
        >
          新增价格
        </button>
      </div>

      <form
        v-if="priceFormVisible"
        class="local-ai-form-panel"
        @submit.prevent="savePrice"
      >
        <h3>{{ priceEditingId ? '编辑价格' : '新增价格' }}</h3>
        <p>汇率由用户手工维护；系统不会自动抓取厂商价格或外汇行情。</p>
        <div class="local-ai-form-grid">
          <label class="local-ai-field wide">
            <span>API Provider</span>
            <select
              v-model="priceForm.provider"
              class="local-ai-select"
              required
            >
              <option value="">请选择</option>
              <option
                v-for="provider in apiProviders"
                :key="provider.id"
                :value="provider.id"
              >
                {{ provider.name }} · {{ provider.model_name }}
              </option>
            </select>
          </label>
          <label class="local-ai-field">
            <span>能力</span>
            <select
              v-model="priceForm.capability"
              class="local-ai-select"
            >
              <option
                v-for="item in capabilityOptions"
                :key="item.value"
                :value="item.value"
              >
                {{ item.label }}
              </option>
            </select>
          </label>
          <label class="local-ai-field wide">
            <span>模型匹配</span>
            <input
              v-model.trim="priceForm.model_pattern"
              class="local-ai-input"
              required
              placeholder="* 或具体模型名称"
            >
          </label>
          <label class="local-ai-field">
            <span>计费单位</span>
            <select
              v-model="priceForm.billing_unit"
              class="local-ai-select"
            >
              <option
                v-for="item in billingUnitOptions"
                :key="item.value"
                :value="item.value"
              >
                {{ item.label }}
              </option>
            </select>
          </label>
          <label class="local-ai-field">
            <span>单位基数</span>
            <input
              v-model.number="priceForm.unit_size"
              class="local-ai-input"
              type="number"
              min="0.000001"
              step="0.000001"
              required
            >
          </label>
          <label class="local-ai-field">
            <span>单位价格</span>
            <input
              v-model.number="priceForm.unit_price"
              class="local-ai-input"
              type="number"
              min="0"
              step="0.00000001"
              required
            >
          </label>
          <label class="local-ai-field">
            <span>原币种</span>
            <input
              v-model.trim="priceForm.currency"
              class="local-ai-input"
              maxlength="3"
              required
            >
          </label>
          <label class="local-ai-field">
            <span>折算 CNY 汇率</span>
            <input
              v-model.number="priceForm.exchange_rate_to_cny"
              class="local-ai-input"
              type="number"
              min="0.00000001"
              step="0.00000001"
              required
            >
          </label>
          <label class="local-ai-field">
            <span>价格版本</span>
            <input
              v-model.trim="priceForm.version"
              class="local-ai-input"
              required
              placeholder="2026-07"
            >
          </label>
          <label class="local-ai-field">
            <span>生效时间</span>
            <input
              v-model="priceForm.effective_from"
              class="local-ai-input"
              type="datetime-local"
              required
            >
          </label>
          <label class="local-ai-field">
            <span>失效时间</span>
            <input
              v-model="priceForm.effective_to"
              class="local-ai-input"
              type="datetime-local"
            >
          </label>
          <label class="local-ai-field wide">
            <span>来源说明</span>
            <input
              v-model.trim="priceForm.source_note"
              class="local-ai-input"
              required
              placeholder="厂商控制台截图或人工核对说明"
            >
          </label>
          <label class="local-ai-checkbox">
            <input
              v-model="priceForm.is_active"
              type="checkbox"
            >
            启用价格
          </label>
        </div>
        <div class="local-ai-form-actions">
          <button
            class="local-ai-button primary"
            type="submit"
            :disabled="priceSaving"
          >
            {{ priceSaving ? '保存中' : '保存价格' }}
          </button>
          <button
            class="local-ai-button secondary"
            type="button"
            @click="closePriceForm"
          >
            取消
          </button>
        </div>
      </form>

      <div
        v-if="priceLoading"
        class="local-ai-loading"
      >
        <span class="local-ai-spinner" />
        <span>正在读取价目表</span>
      </div>
      <div
        v-else-if="priceError"
        class="local-ai-error"
      >
        <div>
          <h3>价目表加载失败</h3>
          <p>{{ priceError }}</p>
          <button
            class="local-ai-button secondary"
            type="button"
            @click="loadPrices"
          >
            重试
          </button>
        </div>
      </div>
      <div
        v-else-if="!prices.length"
        class="local-ai-empty"
      >
        <div>
          <h3>尚未维护 API 价格</h3>
          <p>这是安全默认值：没有价格时，自动付费回退会被拒绝。</p>
          <button
            class="local-ai-button primary"
            type="button"
            @click="openPriceForm()"
          >
            新增第一条价格
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
              <th>Provider / 模型</th>
              <th>单位价格</th>
              <th>CNY 折算</th>
              <th>版本 / 有效期</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="price in prices"
              :key="price.id"
            >
              <td>
                <strong>{{ price.provider_name || providerName(price.provider) }}</strong>
                <small>{{ price.model_pattern }} · {{ capabilityLabel(price.capability) }}</small>
              </td>
              <td>
                <strong>{{ formatMoney(price.unit_price, price.currency) }}</strong>
                <small>每 {{ price.unit_size }} {{ billingUnitLabel(price.billing_unit) }}</small>
              </td>
              <td>
                <strong>{{ formatCnyPrice(price) }}</strong>
                <small>汇率 {{ price.exchange_rate_to_cny }}</small>
              </td>
              <td>
                <strong>{{ price.version || 'v1' }}</strong>
                <small>{{ formatDate(price.effective_from) }} 至 {{ formatDate(price.effective_to) }}</small>
              </td>
              <td>
                <div class="local-ai-row-actions">
                  <button
                    class="local-ai-button secondary"
                    type="button"
                    @click="openPriceForm(price)"
                  >
                    编辑
                  </button>
                  <button
                    class="local-ai-button danger"
                    type="button"
                    @click="removePrice(price)"
                  >
                    删除
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script>
import {
  generationRouteApi,
  generationRouteTargetApi,
  providerPriceRateApi,
  runtimeNodeApi
} from '@/api/inference'
import { modelProviderApi } from '@/api/models'

const newRouteForm = () => ({
  name: '',
  capability: 'llm',
  scope: 'global',
  project: '',
  stage_type: '',
  profile_code: 'balanced',
  priority: 0,
  local_first: true,
  fallback_error_classes: [
    'NODE_UNAVAILABLE',
    'RUNTIME_NOT_READY',
    'EXECUTION_TIMEOUT',
    'GPU_OOM',
    'RUNTIME_CRASH',
    'EMPTY_OUTPUT',
    'OUTPUT_SCHEMA_INVALID',
    'ARTIFACT_DOWNLOAD_FAILED',
    'CHECKSUM_MISMATCH'
  ],
  is_active: true
})

const newTargetForm = () => ({
  name: '',
  role: 'local_primary',
  provider: '',
  runtime_node: '',
  position: 0,
  timeout_seconds: 300,
  max_attempts: 1,
  is_active: true
})

const newPriceForm = () => ({
  provider: '',
  capability: 'llm',
  model_pattern: '*',
  billing_unit: 'input_token',
  unit_size: 1000,
  unit_price: 0,
  minimum_charge: 0,
  currency: 'CNY',
  exchange_rate_to_cny: 1,
  version: 'v1',
  effective_from: '',
  effective_to: '',
  source_note: '',
  conditions: {},
  is_active: true
})

export default {
  name: 'RoutesPricingPanel',
  data() {
    return {
      capabilityOptions: [
        { value: 'llm', label: '文本生成' },
        { value: 'text2image', label: '文生图' },
        { value: 'image_edit', label: '图片编辑' },
        { value: 'image2video', label: '图生视频' },
        { value: 'motion_render', label: '免费静态运镜' }
      ],
      billingUnitOptions: [
        { value: 'input_token', label: '输入 Token' },
        { value: 'output_token', label: '输出 Token' },
        { value: 'image', label: '图片张数' },
        { value: 'video_second', label: '视频秒数' },
        { value: 'video_task', label: '视频任务' },
        { value: 'request', label: '固定请求' }
      ],
      routes: [],
      prices: [],
      providers: [],
      nodes: [],
      routeLoading: false,
      priceLoading: false,
      routeSaving: false,
      targetSaving: false,
      priceSaving: false,
      routeError: '',
      priceError: '',
      routeFormVisible: false,
      priceFormVisible: false,
      routeEditingId: null,
      priceEditingId: null,
      targetRoute: null,
      routeForm: newRouteForm(),
      targetForm: newTargetForm(),
      priceForm: newPriceForm()
    }
  },
  computed: {
    apiProviders() {
      return this.providers.filter((provider) => (provider.deployment_mode || 'api') === 'api')
    }
  },
  created() {
    this.loadReferenceData()
    this.loadRoutes()
    this.loadPrices()
  },
  methods: {
    normalizeList(response) {
      if (Array.isArray(response)) {
        return response
      }
      return Array.isArray(response?.results) ? response.results : []
    },
    async loadReferenceData() {
      try {
        const [providers, nodes] = await Promise.all([
          modelProviderApi.getProviders({ page_size: 500 }),
          runtimeNodeApi.list({ page_size: 500 })
        ])
        this.providers = this.normalizeList(providers)
        this.nodes = this.normalizeList(nodes)
      } catch (error) {
        console.error('加载路由参考数据失败:', error)
      }
    },
    async loadRoutes() {
      this.routeLoading = true
      this.routeError = ''
      try {
        this.routes = this.normalizeList(await generationRouteApi.list({ page_size: 500 }))
      } catch (error) {
        this.routeError = this.errorMessage(error, '无法读取生成路由。')
      } finally {
        this.routeLoading = false
      }
    },
    async loadPrices() {
      this.priceLoading = true
      this.priceError = ''
      try {
        this.prices = this.normalizeList(await providerPriceRateApi.list({ page_size: 500 }))
      } catch (error) {
        this.priceError = this.errorMessage(error, '无法读取 Provider 价目表。')
      } finally {
        this.priceLoading = false
      }
    },
    openRouteForm(route = null) {
      this.routeEditingId = route?.id || null
      this.routeForm = route ? { ...newRouteForm(), ...route, project: route.project || '' } : newRouteForm()
      this.routeFormVisible = true
      this.targetRoute = null
    },
    closeRouteForm() {
      this.routeFormVisible = false
      this.routeEditingId = null
      this.routeForm = newRouteForm()
    },
    async saveRoute() {
      this.routeSaving = true
      try {
        const payload = {
          name: this.routeForm.name,
          capability: this.routeForm.capability,
          scope: this.routeForm.scope,
          project: this.routeForm.scope === 'project' ? this.routeForm.project : null,
          stage_type: this.routeForm.stage_type,
          profile_code: this.routeForm.profile_code,
          priority: Number(this.routeForm.priority || 0),
          local_first: Boolean(this.routeForm.local_first),
          fallback_error_classes: this.routeForm.fallback_error_classes,
          is_active: Boolean(this.routeForm.is_active)
        }
        if (this.routeEditingId) {
          await generationRouteApi.patch(this.routeEditingId, payload)
        } else {
          await generationRouteApi.create(payload)
        }
        this.closeRouteForm()
        await this.loadRoutes()
      } catch (error) {
        await this.$alert(this.errorMessage(error, '路由保存失败'), '保存失败', { tone: 'error' })
      } finally {
        this.routeSaving = false
      }
    },
    openTargetForm(route) {
      this.routeFormVisible = false
      this.targetRoute = route
      this.targetForm = {
        ...newTargetForm(),
        position: Array.isArray(route.targets) ? route.targets.length : 0
      }
    },
    async saveTarget() {
      if (!this.targetForm.provider && !this.targetForm.runtime_node) {
        await this.$alert('Provider 与 Runtime 节点至少选择一个。', '缺少目标', { tone: 'warning' })
        return
      }
      this.targetSaving = true
      try {
        await generationRouteTargetApi.create({
          ...this.targetForm,
          route: this.targetRoute.id,
          provider: this.targetForm.provider || null,
          runtime_node: this.targetForm.runtime_node || null,
          position: Number(this.targetForm.position || 0),
          timeout_seconds: Number(this.targetForm.timeout_seconds || 300),
          max_attempts: Number(this.targetForm.max_attempts || 1)
        })
        this.targetRoute = null
        await this.loadRoutes()
      } catch (error) {
        await this.$alert(this.errorMessage(error, '路由目标保存失败'), '保存失败', { tone: 'error' })
      } finally {
        this.targetSaving = false
      }
    },
    async removeRoute(route) {
      const confirmed = await this.$confirm(`确定删除路由“${route.name}”吗？`, '删除路由', {
        tone: 'danger',
        confirmText: '删除'
      })
      if (!confirmed) {
        return
      }
      try {
        await generationRouteApi.remove(route.id)
        await this.loadRoutes()
      } catch (error) {
        await this.$alert(this.errorMessage(error, '路由删除失败'), '删除失败', { tone: 'error' })
      }
    },
    openPriceForm(price = null) {
      this.priceEditingId = price?.id || null
      this.priceForm = price
        ? {
            ...newPriceForm(),
            ...price,
            effective_from: this.toLocalDateTime(price.effective_from),
            effective_to: this.toLocalDateTime(price.effective_to)
          }
        : { ...newPriceForm(), effective_from: this.toLocalDateTime(new Date().toISOString()) }
      this.priceFormVisible = true
    },
    closePriceForm() {
      this.priceFormVisible = false
      this.priceEditingId = null
      this.priceForm = newPriceForm()
    },
    async savePrice() {
      this.priceSaving = true
      try {
        const payload = {
          ...this.priceForm,
          provider: this.priceForm.provider || null,
          unit_size: Number(this.priceForm.unit_size),
          unit_price: Number(this.priceForm.unit_price),
          exchange_rate_to_cny: Number(this.priceForm.exchange_rate_to_cny),
          effective_from: this.toIso(this.priceForm.effective_from),
          effective_to: this.toIso(this.priceForm.effective_to)
        }
        if (this.priceEditingId) {
          await providerPriceRateApi.patch(this.priceEditingId, payload)
        } else {
          await providerPriceRateApi.create(payload)
        }
        this.closePriceForm()
        await this.loadPrices()
      } catch (error) {
        await this.$alert(this.errorMessage(error, '价格保存失败'), '保存失败', { tone: 'error' })
      } finally {
        this.priceSaving = false
      }
    },
    async removePrice(price) {
      const confirmed = await this.$confirm('确定删除这条价格吗？删除后对应 API 目标将无法自动回退。', '删除价格', {
        tone: 'danger',
        confirmText: '删除'
      })
      if (!confirmed) {
        return
      }
      try {
        await providerPriceRateApi.remove(price.id)
        await this.loadPrices()
      } catch (error) {
        await this.$alert(this.errorMessage(error, '价格删除失败'), '删除失败', { tone: 'error' })
      }
    },
    capabilityLabel(value) {
      return this.capabilityOptions.find((item) => item.value === value)?.label || value || '--'
    },
    profileLabel(value) {
      return { draft: '草稿', balanced: '均衡', final: '成片' }[value] || value || '--'
    },
    billingUnitLabel(value) {
      return this.billingUnitOptions.find((item) => item.value === value)?.label || value || '--'
    },
    deploymentLabel(value) {
      return { local: '本地', api: 'API', mock: 'Mock' }[value] || 'API'
    },
    targetSummary(route) {
      const targets = Array.isArray(route.targets) ? route.targets : []
      if (!targets.length) {
        return '尚未添加目标'
      }
      return [...targets]
        .sort((a, b) => Number(a.position || 0) - Number(b.position || 0))
        .map((target) => target.name || target.provider_name || target.runtime_node_name)
        .filter(Boolean)
        .slice(0, 3)
        .join(' → ')
    },
    providerName(id) {
      return this.providers.find((provider) => provider.id === id)?.name || '未知 Provider'
    },
    formatMoney(value, currency = 'CNY') {
      const amount = Number(value || 0)
      return `${currency || 'CNY'} ${Number.isFinite(amount) ? amount.toFixed(6) : '0.000000'}`
    },
    formatCnyPrice(price) {
      const amount = Number(price.unit_price || 0) * Number(price.exchange_rate_to_cny || 1)
      return `CNY ${amount.toFixed(6)}`
    },
    formatDate(value) {
      if (!value) {
        return '长期'
      }
      const date = new Date(value)
      return Number.isNaN(date.getTime()) ? '--' : date.toLocaleDateString('zh-CN')
    },
    toLocalDateTime(value) {
      if (!value) {
        return ''
      }
      const date = new Date(value)
      if (Number.isNaN(date.getTime())) {
        return ''
      }
      const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000)
      return local.toISOString().slice(0, 16)
    },
    toIso(value) {
      if (!value) {
        return null
      }
      const date = new Date(value)
      return Number.isNaN(date.getTime()) ? null : date.toISOString()
    },
    errorMessage(error, fallback) {
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
