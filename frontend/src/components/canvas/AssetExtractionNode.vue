<template>
  <div
    class="asset-extraction-node"
    :class="`status-${effectiveStatus}`"
    :style="nodeStyle"
    @dblclick="handleNodeDoubleClick"
  >
    <div class="node-top">
      <div class="card-top">
        <div class="card-title-wrap">
          <span class="card-title">资产抽取</span>
          <span class="card-subtitle">{{ sourceTypeText }}</span>
        </div>
        <button
          class="pill-action"
          :disabled="isExecuting || status === 'processing'"
          @click.stop="handleExecute"
        >
          <span
            v-if="isExecuting"
            class="loading loading-spinner loading-xs"
          />
          <span v-else>执行抽取</span>
        </button>
      </div>

      <div class="card-meta">
        <span class="meta-chip">{{ itemCount }} 项待处理</span>
        <span
          v-if="selectedItem"
          class="meta-chip accent"
        >当前查看 {{ selectedItem.label || selectedItem.key }}</span>
        <span
          v-if="isPaidImageProvider"
          class="meta-chip paid"
        >API 生图需逐次确认费用</span>
      </div>
    </div>

    <div
      class="card-body prevent-canvas-wheel"
      @wheel.stop
      @mousedown.stop
    >
      <div
        v-if="data && data.summary"
        class="summary-box"
      >
        {{ data.summary }}
      </div>

      <div
        v-if="localItems.length"
        class="workspace-shell"
      >
        <div class="asset-grid-panel">
          <div class="asset-grid">
            <article
              v-for="item in localItems"
              :key="item.temp_id"
              class="asset-grid-card"
              :class="{
                active: selectedTempId === item.temp_id,
                matched: item.match_status === 'matched',
              }"
              role="button"
              tabindex="0"
              @click.stop="selectItem(item.temp_id)"
              @keyup.enter="selectItem(item.temp_id)"
            >
              <div class="card-top">
                <div>
                  <div class="asset-card-title">
                    {{ item.label || item.key }}
                  </div>
                  <div class="asset-card-key">
                    {{ item.key }}
                  </div>
                </div>
                <span class="item-confidence">{{ confidenceText(item.confidence) }}</span>
              </div>

              <div class="card-meta compact">
                <span class="item-group">{{ item.group || '未分组' }}</span>
                <span class="type-chip">图片资产</span>
              </div>

              <div class="asset-card-value">
                {{ shortValue(item.value) }}
              </div>

              <div class="card-footer compact">
                <span class="status-chip">{{ item.selected_asset_id ? '已关联' : '待处理' }}</span>
                <span class="ghost-link">点击查看</span>
              </div>
            </article>
          </div>
        </div>

        <div
          v-if="selectedItem"
          class="detail-panel"
        >
          <div class="detail-card preview-stage-card">
            <div class="card-top">
              <div>
                <div class="detail-title">
                  {{ selectedItem.label || '未命名资产' }}
                </div>
                <div class="detail-subtitle">
                  右侧详情默认收起，优先展示图片预览与快捷动作
                </div>
              </div>
              <button
                class="ghost-link-btn"
                type="button"
                @click.stop="toggleDetailExpanded"
              >
                {{ detailExpanded ? '收起详情' : '展开详情' }}
              </button>
            </div>

            <div class="preview-stage-shell">
              <img
                v-if="previewImageUrl"
                :src="previewImageUrl"
                alt="预览图"
                class="preview-image"
              >
              <div
                v-else
                class="preview-placeholder"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  class="preview-placeholder-icon"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="1.8"
                    d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
                  />
                </svg>
                <div class="preview-placeholder-title">
                  暂无预览图
                </div>
                <div class="preview-placeholder-desc">
                  点击“生图”后会使用详情中的资产值生成图片预览
                </div>
              </div>
            </div>

            <div class="detail-actions-row">
              <button
                class="pill-action"
                :disabled="isGeneratingImage"
                @click.stop="handleGenerateImage"
              >
                <span
                  v-if="isGeneratingImage"
                  class="loading loading-spinner loading-xs"
                />
                <span v-else>生图</span>
              </button>
              <button
                class="pill-action ghost"
                :disabled="isBindingAsset"
                @click.stop="toggleAssetBrowser"
              >
                <span
                  v-if="isBindingAsset"
                  class="loading loading-spinner loading-xs"
                />
                <span v-else>{{ showAssetBrowser ? '收起关联资产' : '关联资产' }}</span>
              </button>
              <button
                v-if="previewImageUrl"
                class="pill-action accent-action"
                :disabled="isConfirmingImage"
                @click.stop="handleConfirmImage"
              >
                <span
                  v-if="isConfirmingImage"
                  class="loading loading-spinner loading-xs"
                />
                <span v-else>满意后保存</span>
              </button>
            </div>

            <div class="preview-caption">
              当前生图使用“资产值”作为提示文本；满意后再创建图片资产并绑定。
            </div>
          </div>

          <div
            v-if="showAssetBrowser"
            class="detail-card"
          >
            <div class="card-top">
              <div>
                <div class="detail-title">
                  关联资产
                </div>
                <div class="detail-subtitle">
                  直接点击卡片完成关联，比下拉框更直观
                </div>
              </div>
              <button
                v-if="selectedItem.selected_asset_id"
                class="ghost-link-btn"
                type="button"
                @click.stop="clearAssetSelection"
              >
                清除选择
              </button>
            </div>

            <label class="field-block search-block">
              <span class="field-label">搜索资产</span>
              <input
                v-model="assetSearchKeyword"
                class="input input-bordered input-sm"
                type="text"
                placeholder="搜索资产键、分组或描述"
              >
            </label>

            <div
              v-if="selectedAssetSummary"
              class="selected-asset-banner"
            >
              <span class="meta-chip accent">当前已关联</span>
              <span class="selected-asset-title">{{ selectedAssetSummary.key }}</span>
              <span
                v-if="selectedAssetSummary.group"
                class="selected-asset-group"
              >{{ selectedAssetSummary.group }}</span>
            </div>

            <div
              v-if="filteredAssetOptions.length"
              class="asset-option-grid"
            >
              <button
                v-for="asset in filteredAssetOptions"
                :key="assetIdentity(asset)"
                class="asset-option-card"
                :class="{ active: isAssetSelected(asset) }"
                type="button"
                @click.stop="handleBindAsset(asset)"
              >
                <div class="asset-option-media">
                  <img
                    v-if="asset.image_url"
                    :src="asset.image_url"
                    :alt="asset.key || 'asset'"
                    class="asset-option-thumb"
                  >
                  <div
                    v-else
                    class="asset-option-fallback"
                  >
                    {{ (asset.key || '资产').slice(0, 2).toUpperCase() }}
                  </div>
                </div>

                <div class="asset-option-main">
                  <div class="asset-option-title-row">
                    <span class="asset-option-title">{{ asset.key || '未命名资产' }}</span>
                    <span class="asset-option-status">{{ isAssetSelected(asset) ? '已关联' : '点击关联' }}</span>
                  </div>
                  <div class="asset-option-meta">
                    {{ asset.group || '未分组' }} · {{ asset.variable_type_display || typeText(asset.variable_type) }}
                  </div>
                  <div class="asset-option-desc">
                    {{ asset.description || shortValue(asset.value || asset.typed_value) || '暂无说明' }}
                  </div>
                </div>
              </button>
            </div>
            <div
              v-else
              class="empty-inline"
            >
              没有匹配的资产，可尝试调整搜索关键词
            </div>
          </div>

          <div class="detail-card detail-editor-card">
            <button
              class="detail-toggle"
              type="button"
              @click.stop="toggleDetailExpanded"
            >
              <span>资产详情</span>
              <span class="detail-toggle-text">{{ detailExpanded ? '收起' : '展开' }}</span>
            </button>

            <div
              v-if="detailExpanded"
              class="detail-form"
            >
              <div class="card-meta compact helper-meta">
                <span class="meta-chip">图片资产</span>
                <span class="field-hint">资产值字段会直接作为文生图提示文本使用</span>
              </div>

              <label class="field-block">
                <span class="field-label">资产键</span>
                <input
                  v-model="selectedItem.key"
                  class="input input-bordered input-sm"
                  type="text"
                >
              </label>

              <label class="field-block">
                <span class="field-label">显示名称</span>
                <input
                  v-model="selectedItem.label"
                  class="input input-bordered input-sm"
                  type="text"
                >
              </label>

              <div class="field-row">
                <label class="field-block">
                  <span class="field-label">分组</span>
                  <input
                    v-model="selectedItem.group"
                    class="input input-bordered input-sm"
                    type="text"
                  >
                </label>
                <div class="field-block type-block">
                  <span class="field-label">资产类型</span>
                  <div class="type-summary-row">
                    <span class="type-pill">图片资产</span>
                    <button
                      class="ghost-link-btn"
                      type="button"
                      @click.stop="toggleAdvancedType"
                    >
                      {{ showAdvancedType ? '收起高级设置' : '高级设置' }}
                    </button>
                  </div>
                  <span class="field-hint">默认按图片资产处理，只有特殊情况才切换其他类型</span>
                </div>
              </div>

              <label
                v-if="showAdvancedType"
                class="field-block"
              >
                <span class="field-label">高级类型切换</span>
                <select
                  v-model="selectedItem.variable_type"
                  class="select select-bordered select-sm"
                >
                  <option value="image">图片</option>
                  <option value="string">字符串</option>
                  <option value="number">数字</option>
                  <option value="boolean">布尔值</option>
                  <option value="json">JSON</option>
                </select>
              </label>

              <label class="field-block">
                <span class="field-label">资产值</span>
                <textarea
                  v-model="editableValue"
                  class="textarea textarea-bordered value-textarea"
                  placeholder="请输入用于描述图片内容、风格、构图的文本"
                  @change="handleValueChange"
                />
              </label>

              <label class="field-block">
                <span class="field-label">说明</span>
                <textarea
                  v-model="selectedItem.description"
                  class="textarea textarea-bordered textarea-sm detail-textarea"
                />
              </label>

              <button
                class="pill-action secondary"
                :disabled="isSavingItem"
                @click.stop="handleSaveSelected"
              >
                <span
                  v-if="isSavingItem"
                  class="loading loading-spinner loading-xs"
                />
                <span v-else>保存当前项</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <div
        v-else
        class="empty-state"
      >
        <div class="empty-title">
          暂未抽取到资产
        </div>
        <div class="empty-desc">
          执行后会解析文案并给出可关联、可编辑、可文生图的资产项
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import projectsAPI from '@/api/projects';
import {
  confirmPaidGeneration,
  getPaidConfirmationDemand,
  isPaidConfirmationRequired,
} from '@/services/paidGenerationGuard';

export default {
  name: 'AssetExtractionNode',
  props: {
    status: {
      type: String,
      default: 'pending'
    },
    position: {
      type: Object,
      default: () => ({ x: 0, y: 0 })
    },
    data: {
      type: Object,
      default: null
    },
    projectId: {
      type: [String, Number],
      required: true
    },
    availableAssets: {
      type: Array,
      default: () => []
    },
    imageProvider: {
      type: Object,
      default: null
    }
  },
  data() {
    return {
      isExecuting: false,
      isSavingItem: false,
      isGeneratingImage: false,
      isConfirmingImage: false,
      isBindingAsset: false,
      localItems: [],
      selectedTempId: '',
      editableValue: '',
      showAdvancedType: false,
      detailExpanded: false,
      showAssetBrowser: false,
      assetSearchKeyword: '',
    };
  },
  computed: {
    nodeStyle() {
      return {
        position: 'absolute',
        left: `${this.position.x}px`,
        top: `${this.position.y}px`,
      };
    },
    effectiveStatus() {
      if (this.isExecuting || this.isSavingItem || this.isGeneratingImage || this.isConfirmingImage || this.isBindingAsset) {
        return 'processing';
      }
      return this.status;
    },
    sourceTypeText() {
      const map = {
        rewritten_text: '来源：精修剧本',
        original_text: '来源：原始文案',
        original_topic: '来源：原始文案',
      };
      return map[(this.data && this.data.source_type)] || '来源：原始文案';
    },
    itemCount() {
      return this.localItems.length;
    },
    selectedItem() {
      return this.localItems.find(item => item.temp_id === this.selectedTempId) || null;
    },
    previewImageUrl() {
      return (this.selectedItem && this.selectedItem.generated_image_preview && this.selectedItem.generated_image_preview.url) || '';
    },
    isPaidImageProvider() {
      return this.imageProvider?.deployment_mode === 'api';
    },
    filteredAssetOptions() {
      const item = this.selectedItem;
      if (!item) {
        return [];
      }
      const keyword = (this.assetSearchKeyword || '').trim().toLowerCase();
      return this.resolveAssetOptions(item).filter((asset) => {
        if (!keyword) {
          return true;
        }
        const text = [asset.key, asset.group, asset.description, asset.value]
          .filter(Boolean)
          .join(' ')
          .toLowerCase();
        return text.includes(keyword);
      });
    },
    selectedAssetSummary() {
      const item = this.selectedItem;
      if (!item || !item.selected_asset_id) {
        return null;
      }
      return this.resolveAssetOptions(item).find(asset => this.assetIdentity(asset) === item.selected_asset_id) || null;
    },
  },
  watch: {
    'data.items': {
      immediate: true,
      deep: true,
      handler(value) {
        this.localItems = (value || []).map(item => this.normalizeItem(item));
        if (!this.localItems.length) {
          this.selectedTempId = '';
          this.editableValue = '';
          return;
        }
        if (!this.localItems.some(item => item.temp_id === this.selectedTempId)) {
          this.selectedTempId = this.localItems[0].temp_id;
        }
        this.syncEditableValue();
      }
    },
    selectedTempId() {
      this.showAdvancedType = false;
      this.detailExpanded = false;
      this.showAssetBrowser = false;
      this.assetSearchKeyword = '';
      this.syncEditableValue();
    },
    selectedItem: {
      deep: true,
      handler() {
        this.syncEditableValue();
      }
    }
  },
  methods: {
    handleNodeDoubleClick(event) {
      if (!(event.target instanceof Element)) {
        this.$emit('node-dblclick');
        return;
      }

      if (event.target.closest('button, input, textarea, select, option, video, [contenteditable="true"], .prevent-canvas-wheel')) {
        return;
      }

      this.$emit('node-dblclick');
    },
    confidenceText(value) {
      const number = Number(value || 0);
      return `置信度 ${Math.round(number * 100)}%`;
    },
    typeText(value) {
      const map = {
        string: '字符串',
        number: '数字',
        boolean: '布尔值',
        json: 'JSON',
        image: '图片',
      };
      return map[value] || '未定义';
    },
    formatValue(value) {
      if (value == null) {
        return '暂无值';
      }
      if (typeof value === 'string') {
        return value;
      }
      try {
        return JSON.stringify(value, null, 2);
      } catch (error) {
        return String(value);
      }
    },
    shortValue(value) {
      const text = this.formatValue(value);
      return text.length > 88 ? `${text.slice(0, 88)}...` : text;
    },
    normalizeItem(item) {
      return {
        ...item,
        variable_type: item && item.variable_type ? item.variable_type : 'image',
      };
    },
    toggleAdvancedType() {
      this.showAdvancedType = !this.showAdvancedType;
    },
    toggleDetailExpanded() {
      this.detailExpanded = !this.detailExpanded;
    },
    toggleAssetBrowser() {
      this.showAssetBrowser = !this.showAssetBrowser;
    },
    parseEditableValue(raw, variableType) {
      if (variableType === 'json') {
        return raw ? JSON.parse(raw) : {};
      }
      if (variableType === 'number') {
        return raw === '' ? '' : Number(raw);
      }
      if (variableType === 'boolean') {
        return ['true', '1', 'yes', 'on'].includes(String(raw).toLowerCase());
      }
      return raw;
    },
    syncEditableValue() {
      if (!this.selectedItem) {
        this.editableValue = '';
        return;
      }
      const nextValue = this.formatValue(this.selectedItem.value);
      if (nextValue !== this.editableValue) {
        this.editableValue = nextValue;
      }
    },
    handleValueChange() {
      if (!this.selectedItem) {
        return;
      }
      try {
        this.selectedItem.value = this.parseEditableValue(this.editableValue, this.selectedItem.variable_type);
      } catch (error) {
        this.$message?.error('资产值格式不正确，请检查后再保存');
      }
    },
    selectItem(tempId) {
      this.selectedTempId = tempId;
    },
    assetIdentity(asset) {
      return asset.id || asset.asset_id || '';
    },
    isAssetSelected(asset) {
      return this.selectedItem && this.selectedItem.selected_asset_id === this.assetIdentity(asset);
    },
    clearAssetSelection() {
      if (!this.selectedItem) {
        return;
      }
      this.selectedItem.selected_asset_id = '';
      this.$message?.info('已清除当前选择，若需生效请重新关联其他资产');
    },
    resolveAssetOptions(item) {
      const candidateMap = new Map();
      (item.candidates || []).forEach((candidate) => {
        candidateMap.set(candidate.asset_id, {
          id: candidate.asset_id,
          key: candidate.key,
          group: candidate.group,
          description: candidate.description,
          variable_type: candidate.variable_type,
          variable_type_display: this.typeText(candidate.variable_type),
        });
      });
      this.availableAssets.forEach((asset) => {
        candidateMap.set(asset.id, asset);
      });
      return Array.from(candidateMap.values());
    },
    handleExecute() {
      this.isExecuting = true;
      try {
        this.$emit('execute', { stageType: 'asset_extraction', inputData: {} });
      } finally {
        this.isExecuting = false;
      }
    },
    applyStagePayload(stage) {
      const items = (stage && stage.domain_data && stage.domain_data.items) || (stage && stage.output_data && stage.output_data.items) || [];
      this.localItems = items.map(item => this.normalizeItem(item));
      if (this.localItems.length && !this.localItems.some(item => item.temp_id === this.selectedTempId)) {
        this.selectedTempId = this.localItems[0].temp_id;
      }
      this.syncEditableValue();
    },
    async handleSaveSelected() {
      if (!this.selectedItem) {
        return;
      }
      this.handleValueChange();
      this.isSavingItem = true;
      try {
        const response = await projectsAPI.updateAssetExtractionItem(this.projectId, {
          temp_id: this.selectedItem.temp_id,
          key: this.selectedItem.key,
          label: this.selectedItem.label,
          group: this.selectedItem.group,
          variable_type: this.selectedItem.variable_type || 'image',
          value: this.selectedItem.value,
          description: this.selectedItem.description,
          generation_prompt: this.selectedItem.value,
          selected_asset_id: this.selectedItem.selected_asset_id,
        });
        this.applyStagePayload(response.stage);
        this.$message?.success('当前抽取项已保存');
      } catch (error) {
        console.error('[AssetExtractionNode] 保存抽取项失败:', error);
        this.$message?.error(error.response?.data?.error || error.message || '保存失败');
      } finally {
        this.isSavingItem = false;
      }
    },
    async handleGenerateImage() {
      if (!this.selectedItem) {
        return;
      }
      this.handleValueChange();
      const prompt = String(this.selectedItem.value || '').trim();
      if (!prompt) {
        this.$message?.warning('请先填写详情中的资产值');
        return;
      }
      this.isGeneratingImage = true;
      try {
        const requestPayload = {
          temp_id: this.selectedItem.temp_id,
          prompt,
        };
        let response;
        try {
          response = await projectsAPI.generateAssetExtractionImage(
            this.projectId,
            requestPayload,
            { suppressGlobalError: true }
          );
        } catch (error) {
          if (!isPaidConfirmationRequired(error)) {
            throw error;
          }
          const paidDemand = getPaidConfirmationDemand(error);
          const paidConfirmation = await confirmPaidGeneration({
            projectId: this.projectId,
            capability: paidDemand.capability || 'text2image',
            stageType: paidDemand.stage_type || 'asset_extraction_preview',
            taskCount: 1,
            usagePerItem: {
              request_count: 1,
              image_count: 1,
              width: 1024,
              height: 1024,
              ...(paidDemand.usage_per_item || {}),
            },
            providerId: paidDemand.provider_id || this.imageProvider?.id || null,
            operationLabel: `为“${this.selectedItem.label || this.selectedItem.key}”使用 API 生成预览图`,
            confirm: this.$confirm,
            alert: this.$alert,
          });
          if (!paidConfirmation) {
            this.$message?.info('已取消，本次图片预览未发送到外部 Provider');
            return;
          }
          response = await projectsAPI.generateAssetExtractionImage(
            this.projectId,
            {
              ...requestPayload,
              confirm_paid: true,
              confirmed_max_cost_cny: paidConfirmation.maxCostCny,
            }
          );
        }
        this.applyStagePayload(response.stage);
        this.$message?.success('图片预览已生成');
      } catch (error) {
        console.error('[AssetExtractionNode] 生成图片预览失败:', error);
        this.$message?.error(error.response?.data?.error || error.message || '生成预览失败');
      } finally {
        this.isGeneratingImage = false;
      }
    },
    async handleConfirmImage() {
      if (!(this.selectedItem && this.selectedItem.generated_image_preview && this.selectedItem.generated_image_preview.url)) {
        this.$message?.warning('请先生成预览图');
        return;
      }
      this.handleValueChange();
      this.isConfirmingImage = true;
      try {
        const response = await projectsAPI.confirmAssetExtractionImage(this.projectId, {
          temp_id: this.selectedItem.temp_id,
          key: this.selectedItem.key,
          group: this.selectedItem.group,
          description: this.selectedItem.description,
          value: this.selectedItem.value || '',
        });
        this.applyStagePayload(response.stage);
        this.$message?.success('图片资产已创建并绑定');
        this.$emit('asset-bindings-updated');
      } catch (error) {
        console.error('[AssetExtractionNode] 确认图片资产失败:', error);
        this.$message?.error(error.response?.data?.error || error.message || '创建图片资产失败');
      } finally {
        this.isConfirmingImage = false;
      }
    },
    async handleBindAsset(asset) {
      if (!this.selectedItem) {
        return;
      }
      const assetId = this.assetIdentity(asset);
      if (!assetId) {
        return;
      }
      this.isBindingAsset = true;
      try {
        const response = await projectsAPI.applyAssetExtraction(this.projectId, [{
          temp_id: this.selectedItem.temp_id,
          action: 'bind_existing',
          asset_id: assetId,
        }]);
        this.applyStagePayload(response.stage);
        this.$message?.success('资产关联成功');
        this.$emit('asset-bindings-updated');
      } catch (error) {
        console.error('[AssetExtractionNode] 关联已有资产失败:', error);
        this.$message?.error(error.response?.data?.error || error.message || '关联资产失败');
      } finally {
        this.isBindingAsset = false;
      }
    },
  }
};
</script>

<style scoped>
.asset-extraction-node {
  width: 980px;
  height: 620px;
  max-height: 620px;
  border-radius: 20px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  background: rgba(255, 255, 255, 0.88);
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.08);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: transform 0.25s ease, box-shadow 0.25s ease, border-color 0.25s ease;
}

.asset-extraction-node:hover {
  transform: translateY(-4px);
  box-shadow: 0 24px 52px rgba(15, 23, 42, 0.14);
  border-color: rgba(14, 165, 233, 0.18);
}

.layout-shell.theme-dark .asset-extraction-node {
  background: rgba(15, 23, 42, 0.94);
  border-color: rgba(148, 163, 184, 0.2);
  box-shadow: 0 18px 40px rgba(2, 6, 23, 0.4);
}

.layout-shell.theme-dark .asset-extraction-node:hover {
  border-color: rgba(56, 189, 248, 0.22);
}

.node-top {
  padding: 16px 18px;
}

.card-top,
.card-meta,
.card-footer,
.detail-actions-row,
.type-summary-row,
.detail-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.card-title-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.card-title,
.empty-title,
.detail-title,
.asset-card-title {
  font-weight: 700;
  color: #0f172a;
}

.card-subtitle,
.empty-desc,
.item-confidence,
.summary-box,
.detail-subtitle,
.asset-card-key,
.asset-card-value,
.ghost-link,
.empty-inline,
.preview-caption,
.preview-placeholder-desc,
.asset-option-meta,
.asset-option-desc,
.field-hint,
.detail-toggle-text {
  color: #64748b;
}

.layout-shell.theme-dark .card-title,
.layout-shell.theme-dark .empty-title,
.layout-shell.theme-dark .detail-title,
.layout-shell.theme-dark .asset-card-title,
.layout-shell.theme-dark .preview-placeholder-title,
.layout-shell.theme-dark .asset-option-title,
.layout-shell.theme-dark .selected-asset-title {
  color: #e2e8f0;
}

.layout-shell.theme-dark .card-subtitle,
.layout-shell.theme-dark .empty-desc,
.layout-shell.theme-dark .item-confidence,
.layout-shell.theme-dark .summary-box,
.layout-shell.theme-dark .detail-subtitle,
.layout-shell.theme-dark .asset-card-key,
.layout-shell.theme-dark .asset-card-value,
.layout-shell.theme-dark .ghost-link,
.layout-shell.theme-dark .empty-inline,
.layout-shell.theme-dark .preview-caption,
.layout-shell.theme-dark .preview-placeholder-desc,
.layout-shell.theme-dark .asset-option-meta,
.layout-shell.theme-dark .asset-option-desc,
.layout-shell.theme-dark .field-hint,
.layout-shell.theme-dark .detail-toggle-text,
.layout-shell.theme-dark .ghost-link-btn,
.layout-shell.theme-dark .selected-asset-group {
  color: #94a3b8;
}

.pill-action {
  border: 1px solid rgba(148, 163, 184, 0.3);
  background: rgba(255, 255, 255, 0.96);
  color: #0f172a;
  border-radius: 999px;
  padding: 8px 16px;
  font-size: 13px;
  font-weight: 600;
  transition: all 0.25s ease;
}

.pill-action:hover:not(:disabled) {
  border-color: rgba(34, 211, 238, 0.5);
  box-shadow: 0 10px 24px rgba(56, 189, 248, 0.18);
  transform: translateY(-1px);
}

.pill-action.secondary {
  width: 100%;
}

.pill-action.ghost {
  background: rgba(248, 250, 252, 0.88);
}

.pill-action.accent-action {
  background: rgba(14, 165, 233, 0.12);
  color: #0369a1;
  border-color: rgba(56, 189, 248, 0.34);
}

.pill-action:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.layout-shell.theme-dark .pill-action {
  background: rgba(30, 41, 59, 0.92);
  color: #e2e8f0;
}

.layout-shell.theme-dark .pill-action.accent-action {
  background: rgba(8, 145, 178, 0.18);
  color: #67e8f9;
}

.ghost-link-btn {
  border: none;
  background: transparent;
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  padding: 0;
}

.ghost-link-btn:hover {
  color: #0891b2;
}

.card-meta {
  justify-content: flex-start;
  padding-top: 0;
  flex-wrap: wrap;
}

.card-meta.compact,
.card-footer.compact {
  padding: 0;
}

.meta-chip,
.item-group,
.type-chip,
.status-chip,
.type-pill {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 4px 10px;
  background: rgba(148, 163, 184, 0.12);
  font-size: 12px;
}

.meta-chip.accent,
.asset-grid-card.active .status-chip,
.selected-asset-banner .meta-chip,
.type-pill {
  background: rgba(34, 211, 238, 0.14);
  color: #0891b2;
}

.meta-chip.paid {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.layout-shell.theme-dark .meta-chip.paid {
  background: rgba(245, 158, 11, 0.18);
  color: #fbbf24;
}

.card-body {
  flex: 1;
  min-height: 0;
  padding: 0 18px 18px;
  overflow: auto;
}

.summary-box,
.empty-state,
.detail-card,
.asset-grid-card,
.preview-stage-shell,
.asset-option-card,
.selected-asset-banner {
  border-radius: 18px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(248, 250, 252, 0.76);
}

.layout-shell.theme-dark .summary-box,
.layout-shell.theme-dark .empty-state,
.layout-shell.theme-dark .detail-card,
.layout-shell.theme-dark .asset-grid-card,
.layout-shell.theme-dark .preview-stage-shell,
.layout-shell.theme-dark .asset-option-card,
.layout-shell.theme-dark .selected-asset-banner {
  background: rgba(15, 23, 42, 0.72);
  border-color: rgba(148, 163, 184, 0.18);
}

.summary-box,
.empty-state {
  padding: 14px 16px;
  margin-bottom: 14px;
}

.workspace-shell {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.92fr);
  gap: 16px;
  min-height: 0;
}

.asset-grid-panel,
.detail-panel {
  min-height: 0;
}

.asset-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 14px;
}

.asset-grid-card {
  padding: 14px;
  cursor: pointer;
  transition: transform 0.22s ease, box-shadow 0.22s ease, border-color 0.22s ease;
}

.asset-grid-card:hover,
.asset-grid-card.active {
  transform: translateY(-4px);
  box-shadow: 0 18px 32px rgba(15, 23, 42, 0.12);
  border-color: rgba(34, 211, 238, 0.32);
}

.asset-grid-card.matched,
.detail-card {
  position: relative;
}

.asset-grid-card.matched::before,
.detail-card::before {
  content: '';
  display: block;
  height: 3px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(34, 211, 238, 0.88), rgba(59, 130, 246, 0.82));
  margin-bottom: 12px;
}

.asset-card-title {
  font-size: 15px;
  line-height: 1.3;
}

.asset-card-key {
  margin-top: 4px;
  font-size: 12px;
}

.asset-card-value {
  margin: 12px 0;
  min-height: 54px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.detail-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.detail-card {
  padding: 14px 16px 16px;
}

.preview-stage-shell {
  min-height: 244px;
  overflow: hidden;
}

.preview-image {
  width: 100%;
  height: 244px;
  object-fit: contain;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.9);
}

.preview-placeholder {
  height: 244px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 20px;
}

.preview-placeholder-icon {
  width: 52px;
  height: 52px;
  margin-bottom: 12px;
  color: rgba(100, 116, 139, 0.7);
}

.preview-placeholder-title,
.asset-option-title,
.selected-asset-title {
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
}

.preview-placeholder-desc {
  font-size: 12px;
  line-height: 1.6;
  max-width: 250px;
}

.detail-actions-row {
  margin-top: 14px;
  flex-wrap: wrap;
}

.preview-caption {
  margin-top: 10px;
  font-size: 12px;
  line-height: 1.6;
}

.detail-toggle {
  width: 100%;
  border: none;
  background: transparent;
  padding: 0;
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
  cursor: pointer;
}

.layout-shell.theme-dark .detail-toggle {
  color: #e2e8f0;
}

.detail-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 14px;
}

.field-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.field-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
}

.layout-shell.theme-dark .field-label {
  color: #cbd5e1;
}

.value-textarea,
.detail-textarea {
  width: 100%;
  min-height: 88px;
}

.search-block {
  margin-top: 12px;
}

.selected-asset-banner {
  margin-top: 12px;
  padding: 10px 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.selected-asset-group {
  font-size: 12px;
  color: #64748b;
}

.asset-option-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  margin-top: 12px;
  max-height: 300px;
  overflow: auto;
}

.asset-option-card {
  width: 100%;
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 12px;
  padding: 12px;
  text-align: left;
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}

.asset-option-card:hover,
.asset-option-card.active {
  border-color: rgba(34, 211, 238, 0.36);
  box-shadow: 0 12px 24px rgba(15, 23, 42, 0.1);
  transform: translateY(-2px);
}

.asset-option-media {
  width: 64px;
  height: 64px;
}

.asset-option-thumb,
.asset-option-fallback {
  width: 64px;
  height: 64px;
  border-radius: 14px;
}

.asset-option-thumb {
  object-fit: cover;
  background: rgba(255, 255, 255, 0.9);
}

.asset-option-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(148, 163, 184, 0.12);
  color: #475569;
  font-size: 13px;
  font-weight: 700;
}

.layout-shell.theme-dark .asset-option-fallback {
  color: #cbd5e1;
}

.asset-option-main {
  min-width: 0;
}

.asset-option-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.asset-option-status {
  font-size: 12px;
  color: #0891b2;
  white-space: nowrap;
}

.asset-option-meta,
.asset-option-desc {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-word;
}

.helper-meta {
  align-items: flex-start;
}

.empty-inline {
  border: 1px dashed rgba(148, 163, 184, 0.32);
  border-radius: 14px;
  padding: 18px 14px;
  text-align: center;
  font-size: 13px;
  margin-top: 12px;
}

.empty-state {
  text-align: center;
}

.status-processing {
  border-color: rgba(56, 189, 248, 0.45);
}

.status-completed {
  border-color: rgba(148, 163, 184, 0.22);
}

.status-failed {
  border-color: rgba(248, 113, 113, 0.42);
}

@media (max-width: 1280px) {
  .asset-extraction-node {
    width: 920px;
  }
}
</style>
