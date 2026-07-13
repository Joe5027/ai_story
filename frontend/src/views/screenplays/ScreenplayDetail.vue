<template>
  <div class="page-shell">
    <LoadingContainer :loading="loading">
      <div
        v-if="!loading && !currentScreenplay"
        class="empty-state"
      >
        <div class="empty-hero">
          剧本不存在
        </div>
        <button
          class="secondary-action"
          @click="$router.push({ name: 'ScreenplayList' })"
        >
          返回列表
        </button>
      </div>

      <template v-else>
        <div class="page-header">
          <div class="page-header-left">
            <button
              class="back-btn"
              @click="$router.push({ name: 'ScreenplayList' })"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                stroke-width="1.5"
                stroke="currentColor"
                class="w-5 h-5"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  d="M15.75 19.5L8.25 12l7.5-7.5"
                />
              </svg>
              返回列表
            </button>
            <div class="page-header-main">
              <h1 class="page-title">
                {{ currentScreenplay.title }}
              </h1>
              <p class="page-subtitle">
                {{ currentScreenplay.description || '暂无描述' }}
              </p>
            </div>
          </div>
          <button
            class="primary-action"
            @click="showEpisodeDialog = true"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              stroke-width="1.5"
              stroke="currentColor"
              class="w-5 h-5"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                d="M12 4.5v15m7.5-7.5h-15"
              />
            </svg>
            <span>添加分集</span>
          </button>
        </div>

        <div class="screenplay-meta">
          <span class="meta-badge">{{ currentScreenplay.status_display }}</span>
          <span class="meta-text">{{ episodes.length }} 个分集</span>
          <span class="meta-text">创建于 {{ formatDate(currentScreenplay.created_at) }}</span>
        </div>

        <div
          v-if="episodes.length === 0"
          class="empty-state"
        >
          <div class="empty-hero">
            暂无分集
          </div>
          <p class="empty-hint">
            添加分集，开始编写文案内容。
          </p>
          <button
            class="secondary-action"
            @click="showEpisodeDialog = true"
          >
            添加分集
          </button>
        </div>

        <div
          v-else
          class="card-grid"
        >
          <article
            v-for="episode in episodes"
            :key="episode.id"
            class="episode-card"
            @click="goToEpisode(episode)"
          >
            <div class="card-top">
              <div>
                <h2 class="card-title">
                  <span class="episode-number">第{{ episode.episode_number }}集</span>
                  {{ episode.episode_title || '未命名' }}
                </h2>
                <p class="card-desc">
                  {{ truncateContent(episode.content) }}
                </p>
              </div>
            </div>

            <div class="card-meta">
              <div class="meta-item">
                <span class="meta-label">字数</span>
                <span class="meta-value">{{ episode.content_word_count || 0 }}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">排序</span>
                <span class="meta-value">{{ episode.sort_order || 0 }}</span>
              </div>
            </div>

            <div class="card-footer">
              <span class="meta-time">更新于 {{ formatDate(episode.updated_at) }}</span>
              <div class="episode-actions">
                <button
                  class="episode-action-btn"
                  @click.stop="goToEpisode(episode)"
                >
                  编辑
                </button>
                <button
                  class="episode-action-btn episode-action-btn--danger"
                  @click.stop="handleDeleteEpisode(episode)"
                >
                  删除
                </button>
              </div>
            </div>
          </article>
        </div>
      </template>
    </LoadingContainer>

    <!-- 添加分集弹窗 -->
    <div
      v-if="showEpisodeDialog"
      class="modal-overlay"
      @click.self="closeEpisodeDialog"
    >
      <div class="modal-card modal-card--wide">
        <h3 class="modal-title">
          添加分集
        </h3>
        <form @submit.prevent="handleEpisodeSubmit">
          <div class="form-row">
            <div class="form-group form-group--half">
              <label class="form-label">分集序号</label>
              <input
                v-model.number="episodeForm.episode_number"
                type="number"
                class="form-input"
                min="1"
                required
              >
            </div>
            <div class="form-group form-group--half">
              <label class="form-label">排序值</label>
              <input
                v-model.number="episodeForm.sort_order"
                type="number"
                class="form-input"
              >
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">分集标题</label>
            <input
              v-model="episodeForm.episode_title"
              type="text"
              class="form-input"
              placeholder="请输入分集标题（可选）"
            >
          </div>
          <div class="form-group">
            <label class="form-label">文案内容</label>
            <textarea
              v-model="episodeForm.content"
              class="form-textarea form-textarea--large"
              placeholder="请输入原始文案内容"
              rows="10"
            />
          </div>
          <div class="modal-actions">
            <button
              type="button"
              class="btn-cancel"
              @click="closeEpisodeDialog"
            >
              取消
            </button>
            <button
              type="submit"
              class="btn-confirm"
              :disabled="submitting"
            >
              {{ submitting ? '保存中...' : '保存' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<script>
import { mapState, mapActions } from 'vuex';
import LoadingContainer from '@/components/common/LoadingContainer.vue';
import { formatDate } from '@/utils/helpers';

export default {
  name: 'ScreenplayDetail',
  components: { LoadingContainer },
  data() {
    return {
      loading: false,
      submitting: false,
      showEpisodeDialog: false,
      episodeForm: {
        episode_number: 1,
        episode_title: '',
        content: '',
        sort_order: 0,
      },
    };
  },
  computed: {
    ...mapState('screenplays', ['currentScreenplay', 'episodes']),
    screenplayId() {
      return this.$route.params.id;
    },
  },
  created() {
    this.fetchData();
  },
  beforeUnmount() {
    this.clearCurrentScreenplay();
  },
  methods: {
    ...mapActions('screenplays', [
      'fetchScreenplayDetail',
      'clearCurrentScreenplay',
      'createEpisode',
      'deleteEpisode',
    ]),
    formatDate,
    async fetchData() {
      this.loading = true;
      try {
        await this.fetchScreenplayDetail(this.screenplayId);
      } finally {
        this.loading = false;
      }
    },
    truncateContent(content) {
      if (!content) return '暂无内容';
      return content.length > 100 ? content.substring(0, 100) + '...' : content;
    },
    goToEpisode(episode) {
      this.$router.push({
        name: 'EpisodeDetail',
        params: {
          screenplayId: this.screenplayId,
          episodeId: episode.id,
        },
      });
    },
    async handleDeleteEpisode(episode) {
      const confirmed = await this.$confirm(
        `确定删除第${episode.episode_number}集「${episode.episode_title || '未命名'}」吗？`,
        '删除分集',
        { tone: 'danger', confirmText: '删除' }
      );
      if (!confirmed) return;

      try {
        await this.deleteEpisode(episode.id);
        this.$message.success('分集已删除');
      } catch (error) {
        console.error('Failed to delete episode:', error);
        this.$message.error('删除分集失败');
      }
    },
    async handleEpisodeSubmit() {
      this.submitting = true;
      try {
        await this.createEpisode({
          ...this.episodeForm,
          screenplay: this.screenplayId,
        });
        this.$message.success('分集已添加');
        this.closeEpisodeDialog();
      } catch (error) {
        console.error('Failed to save episode:', error);
        this.$message.error('添加分集失败');
      } finally {
        this.submitting = false;
      }
    },
    closeEpisodeDialog() {
      this.showEpisodeDialog = false;
      this.episodeForm = {
        episode_number: (this.episodes?.length || 0) + 1,
        episode_title: '',
        content: '',
        sort_order: 0,
      };
    },
  },
};
</script>

<style scoped>
.page-shell {
  min-height: 100%;
  padding: 2.5rem 3.5rem 3rem;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1.5rem;
  margin-bottom: 1.5rem;
}

.page-header-left {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.5rem 1rem;
  border-radius: 999px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: transparent;
  color: #64748b;
  font-size: 0.875rem;
  cursor: pointer;
  transition: all 0.2s ease;
  width: fit-content;
}

.layout-shell.theme-dark .back-btn {
  color: #94a3b8;
  border-color: rgba(148, 163, 184, 0.25);
}

.back-btn:hover {
  border-color: rgba(20, 184, 166, 0.5);
  color: #0f172a;
}

.layout-shell.theme-dark .back-btn:hover {
  color: #e2e8f0;
}

.page-header-main {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.page-title {
  font-size: 2rem;
  font-weight: 600;
  color: #0f172a;
  margin: 0;
}

.layout-shell.theme-dark .page-title {
  color: #e2e8f0;
}

.page-subtitle {
  font-size: 0.95rem;
  color: #64748b;
  margin: 0;
}

.primary-action {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 1.5rem;
  background: #ffffff;
  color: #0f172a;
  border: 1px solid rgba(15, 23, 42, 0.12);
  border-radius: 999px;
  font-size: 0.95rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
}

.layout-shell.theme-dark .primary-action {
  background: rgba(15, 23, 42, 0.9);
  border-color: rgba(148, 163, 184, 0.25);
  color: #e2e8f0;
}

.primary-action:hover {
  border-color: rgba(20, 184, 166, 0.6);
  box-shadow: 0 12px 24px rgba(20, 184, 166, 0.18);
  transform: translateY(-1px);
}

.screenplay-meta {
  display: flex;
  gap: 1rem;
  align-items: center;
  margin-bottom: 2rem;
  flex-wrap: wrap;
}

.meta-badge {
  padding: 0.3rem 0.75rem;
  border-radius: 999px;
  font-size: 0.8rem;
  background: rgba(20, 184, 166, 0.16);
  color: #0f172a;
}

.layout-shell.theme-dark .meta-badge {
  background: rgba(94, 234, 212, 0.2);
  color: #e2e8f0;
}

.meta-text {
  font-size: 0.875rem;
  color: #64748b;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 1.5rem;
}

.episode-card {
  background: linear-gradient(90deg, rgba(20, 184, 166, 0.7) 0%, rgba(14, 165, 233, 0.7) 100%)
      0 0 / 0 3px no-repeat,
    rgba(255, 255, 255, 0.92);
  border-radius: 18px;
  padding: 1.5rem;
  border: 1px solid rgba(148, 163, 184, 0.2);
  box-shadow: 0 16px 32px rgba(15, 23, 42, 0.08);
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  cursor: pointer;
}

.layout-shell.theme-dark .episode-card {
  background: linear-gradient(90deg, rgba(94, 234, 212, 0.5) 0%, rgba(56, 189, 248, 0.5) 100%)
      0 0 / 0 3px no-repeat,
    rgba(15, 23, 42, 0.92);
  border-color: rgba(148, 163, 184, 0.2);
  box-shadow: 0 16px 32px rgba(2, 6, 23, 0.55);
}

.episode-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 18px 36px rgba(15, 23, 42, 0.12);
  background-size: 100% 3px, auto;
}

.card-top {
  flex: 1;
}

.card-title {
  font-size: 1.05rem;
  font-weight: 600;
  color: #0f172a;
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
}

.layout-shell.theme-dark .card-title {
  color: #e2e8f0;
}

.episode-number {
  padding: 0.15rem 0.5rem;
  border-radius: 8px;
  font-size: 0.75rem;
  background: rgba(20, 184, 166, 0.16);
  color: #0f172a;
}

.layout-shell.theme-dark .episode-number {
  background: rgba(94, 234, 212, 0.2);
  color: #e2e8f0;
}

.card-desc {
  margin: 0.75rem 0 0;
  color: #64748b;
  font-size: 0.875rem;
  line-height: 1.5;
}

.layout-shell.theme-dark .card-desc {
  color: #94a3b8;
}

.card-meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
  background: rgba(148, 163, 184, 0.1);
  border-radius: 14px;
  padding: 0.75rem 1rem;
}

.layout-shell.theme-dark .card-meta {
  background: rgba(30, 41, 59, 0.6);
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.meta-label {
  font-size: 0.75rem;
  color: #94a3b8;
}

.meta-value {
  font-size: 0.95rem;
  color: #0f172a;
  font-weight: 600;
}

.layout-shell.theme-dark .meta-value {
  color: #e2e8f0;
}

.card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.meta-time {
  font-size: 0.8rem;
  color: #94a3b8;
}

.episode-actions {
  opacity: 0;
  transition: opacity 0.2s ease;
  display: flex;
  gap: 0.5rem;
}

.episode-card:hover .episode-actions {
  opacity: 1;
}

.episode-action-btn {
  padding: 0.35rem 0.75rem;
  border-radius: 8px;
  font-size: 0.8rem;
  border: 1px solid rgba(148, 163, 184, 0.3);
  background: rgba(255, 255, 255, 0.8);
  color: #334155;
  cursor: pointer;
  transition: all 0.15s ease;
}

.layout-shell.theme-dark .episode-action-btn {
  background: rgba(15, 23, 42, 0.8);
  border-color: rgba(148, 163, 184, 0.25);
  color: #e2e8f0;
}

.episode-action-btn:hover {
  border-color: rgba(20, 184, 166, 0.5);
}

.episode-action-btn--danger:hover {
  border-color: rgba(239, 68, 68, 0.5);
  color: #ef4444;
}

.empty-state {
  text-align: center;
  padding: 4rem 1rem;
}

.empty-hero {
  font-size: 1.3rem;
  font-weight: 600;
  color: #0f172a;
}

.layout-shell.theme-dark .empty-hero {
  color: #e2e8f0;
}

.empty-hint {
  color: #94a3b8;
  margin: 0.6rem 0 1.6rem;
}

.secondary-action {
  padding: 0.75rem 1.75rem;
  border-radius: 999px;
  background: #0f172a;
  color: #ffffff;
  border: none;
  cursor: pointer;
}

.layout-shell.theme-dark .secondary-action {
  background: #e2e8f0;
  color: #0f172a;
}

/* Modal styles */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.modal-card {
  background: #ffffff;
  border-radius: 20px;
  padding: 2rem;
  width: 90%;
  max-width: 480px;
  box-shadow: 0 24px 48px rgba(0, 0, 0, 0.15);
  max-height: 90vh;
  overflow-y: auto;
}

.modal-card--wide {
  max-width: 640px;
}

.layout-shell.theme-dark .modal-card {
  background: #1e293b;
}

.modal-title {
  font-size: 1.25rem;
  font-weight: 600;
  color: #0f172a;
  margin: 0 0 1.5rem;
}

.layout-shell.theme-dark .modal-title {
  color: #e2e8f0;
}

.form-row {
  display: flex;
  gap: 1rem;
}

.form-group {
  margin-bottom: 1.25rem;
}

.form-group--half {
  flex: 1;
}

.form-label {
  display: block;
  font-size: 0.875rem;
  font-weight: 500;
  color: #334155;
  margin-bottom: 0.5rem;
}

.layout-shell.theme-dark .form-label {
  color: #cbd5e1;
}

.form-input,
.form-textarea,
.form-select {
  width: 100%;
  padding: 0.75rem 1rem;
  border: 1px solid rgba(148, 163, 184, 0.35);
  border-radius: 12px;
  font-size: 0.95rem;
  background: rgba(255, 255, 255, 0.9);
  outline: none;
  transition: all 0.2s ease;
  box-sizing: border-box;
}

.layout-shell.theme-dark .form-input,
.layout-shell.theme-dark .form-textarea,
.layout-shell.theme-dark .form-select {
  background: rgba(15, 23, 42, 0.9);
  border-color: rgba(148, 163, 184, 0.25);
  color: #e2e8f0;
}

.form-input:focus,
.form-textarea:focus,
.form-select:focus {
  border-color: rgba(20, 184, 166, 0.6);
  box-shadow: 0 0 0 3px rgba(20, 184, 166, 0.18);
}

.form-textarea {
  resize: vertical;
  min-height: 80px;
}

.form-textarea--large {
  min-height: 200px;
  font-family: inherit;
  line-height: 1.6;
}

.modal-actions {
  display: flex;
  gap: 0.75rem;
  justify-content: flex-end;
  margin-top: 1.5rem;
}

.btn-cancel {
  padding: 0.625rem 1.25rem;
  border-radius: 999px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font-size: 0.9rem;
}

.layout-shell.theme-dark .btn-cancel {
  color: #94a3b8;
  border-color: rgba(148, 163, 184, 0.25);
}

.btn-confirm {
  padding: 0.625rem 1.5rem;
  border-radius: 999px;
  border: none;
  background: linear-gradient(135deg, #14b8a6, #0ea5e9);
  color: #ffffff;
  cursor: pointer;
  font-size: 0.9rem;
  font-weight: 500;
}

.btn-confirm:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

@media (max-width: 768px) {
  .page-shell {
    padding: 2rem 1.5rem;
  }

  .page-header {
    flex-direction: column;
    align-items: stretch;
  }

  .primary-action {
    width: 100%;
    justify-content: center;
  }

  .form-row {
    flex-direction: column;
  }

  .card-footer {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.75rem;
  }

  .episode-actions {
    opacity: 1;
  }
}
</style>
