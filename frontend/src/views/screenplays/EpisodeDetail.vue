<template>
  <div class="page-shell">
    <LoadingContainer :loading="loading">
      <div
        v-if="!loading && !episode"
        class="empty-state"
      >
        <div class="empty-hero">
          分集不存在
        </div>
        <button
          class="secondary-action"
          @click="goBack"
        >
          返回剧本
        </button>
      </div>

      <template v-else>
        <div class="page-header">
          <div class="page-header-left">
            <button
              class="back-btn"
              @click="goBack"
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
              返回剧本
            </button>
            <div class="page-header-main">
              <h1 class="page-title">
                <span class="episode-badge">第{{ episode.episode_number }}集</span>
                {{ episode.episode_title || '未命名' }}
              </h1>
              <p class="page-subtitle">
                更新于 {{ formatDate(episode.updated_at) }}
              </p>
            </div>
          </div>
          <div class="header-actions">
            <button
              v-if="!isEditing"
              class="primary-action"
              @click="startEdit"
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
                  d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0115.75 21H5.25A2.25 2.25 0 013 18.75V8.25A2.25 2.25 0 015.25 6H10"
                />
              </svg>
              <span>编辑</span>
            </button>
            <button
              v-if="isEditing"
              class="btn-cancel"
              @click="cancelEdit"
            >
              取消
            </button>
            <button
              v-if="isEditing"
              class="primary-action primary-action--save"
              :disabled="submitting"
              @click="handleSave"
            >
              {{ submitting ? '保存中...' : '保存' }}
            </button>
          </div>
        </div>

        <div class="episode-meta">
          <div class="meta-item">
            <span class="meta-label">分集序号</span>
            <span class="meta-value">{{ episode.episode_number }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">字数</span>
            <span class="meta-value">{{ episode.content_word_count || 0 }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">排序</span>
            <span class="meta-value">{{ episode.sort_order || 0 }}</span>
          </div>
        </div>

        <div class="content-section">
          <div
            v-if="isEditing"
            class="edit-form"
          >
            <div class="form-row">
              <div class="form-group form-group--third">
                <label class="form-label">分集序号</label>
                <input
                  v-model.number="editForm.episode_number"
                  type="number"
                  class="form-input"
                  min="1"
                  required
                >
              </div>
              <div class="form-group form-group--third">
                <label class="form-label">分集标题</label>
                <input
                  v-model="editForm.episode_title"
                  type="text"
                  class="form-input"
                  placeholder="请输入分集标题（可选）"
                >
              </div>
              <div class="form-group form-group--third">
                <label class="form-label">排序值</label>
                <input
                  v-model.number="editForm.sort_order"
                  type="number"
                  class="form-input"
                >
              </div>
            </div>
            <div class="form-group">
              <label class="form-label">文案内容</label>
              <textarea
                v-model="editForm.content"
                class="form-textarea form-textarea--large"
                placeholder="请输入原始文案内容"
              />
            </div>
          </div>

          <div
            v-else
            class="content-display"
          >
            <div class="content-header">
              <h2 class="content-title">
                文案内容
              </h2>
            </div>
            <div class="content-body">
              <pre v-if="episode.content">{{ episode.content }}</pre>
              <p
                v-else
                class="content-empty"
              >
                暂无内容
              </p>
            </div>
          </div>
        </div>
      </template>
    </LoadingContainer>
  </div>
</template>

<script>
import { mapActions } from 'vuex';
import LoadingContainer from '@/components/common/LoadingContainer.vue';
import { formatDate } from '@/utils/helpers';
import screenplaysApi from '@/api/screenplays';

export default {
  name: 'EpisodeDetail',
  components: { LoadingContainer },
  data() {
    return {
      loading: false,
      submitting: false,
      isEditing: false,
      episode: null,
      editForm: {
        episode_number: 1,
        episode_title: '',
        content: '',
        sort_order: 0,
      },
    };
  },
  computed: {
    screenplayId() {
      return this.$route.params.screenplayId;
    },
    episodeId() {
      return this.$route.params.episodeId;
    },
  },
  created() {
    this.fetchData();
  },
  methods: {
    ...mapActions('screenplays', ['updateEpisode']),
    formatDate,
    async fetchData() {
      this.loading = true;
      try {
        const response = await screenplaysApi.getEpisodeDetail(this.episodeId);
        this.episode = response;
      } catch (error) {
        console.error('Failed to fetch episode detail:', error);
        this.episode = null;
      } finally {
        this.loading = false;
      }
    },
    goBack() {
      this.$router.push({
        name: 'ScreenplayDetail',
        params: { id: this.screenplayId },
      });
    },
    startEdit() {
      this.editForm = {
        episode_number: this.episode.episode_number,
        episode_title: this.episode.episode_title || '',
        content: this.episode.content || '',
        sort_order: this.episode.sort_order || 0,
      };
      this.isEditing = true;
    },
    cancelEdit() {
      this.isEditing = false;
      this.editForm = {
        episode_number: 1,
        episode_title: '',
        content: '',
        sort_order: 0,
      };
    },
    async handleSave() {
      this.submitting = true;
      try {
        const response = await this.updateEpisode({
          id: this.episodeId,
          data: this.editForm,
        });
        this.episode = response;
        this.isEditing = false;
        this.$message.success('分集已更新');
      } catch (error) {
        console.error('Failed to save episode:', error);
        this.$message.error('保存分集失败');
      } finally {
        this.submitting = false;
      }
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
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.layout-shell.theme-dark .page-title {
  color: #e2e8f0;
}

.episode-badge {
  padding: 0.25rem 0.75rem;
  border-radius: 8px;
  font-size: 0.9rem;
  background: rgba(20, 184, 166, 0.16);
  color: #0f172a;
}

.layout-shell.theme-dark .episode-badge {
  background: rgba(94, 234, 212, 0.2);
  color: #e2e8f0;
}

.page-subtitle {
  font-size: 0.95rem;
  color: #64748b;
  margin: 0;
}

.header-actions {
  display: flex;
  gap: 0.75rem;
  align-items: center;
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

.primary-action--save {
  background: linear-gradient(135deg, #14b8a6, #0ea5e9);
  color: #ffffff;
  border: none;
}

.primary-action--save:hover {
  transform: translateY(-1px);
  box-shadow: 0 12px 24px rgba(20, 184, 166, 0.25);
}

.primary-action--save:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  transform: none;
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

.episode-meta {
  display: flex;
  gap: 2rem;
  margin-bottom: 2rem;
  padding: 1rem 1.5rem;
  background: rgba(148, 163, 184, 0.1);
  border-radius: 14px;
}

.layout-shell.theme-dark .episode-meta {
  background: rgba(30, 41, 59, 0.6);
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.meta-label {
  font-size: 0.75rem;
  color: #94a3b8;
}

.meta-value {
  font-size: 1rem;
  color: #0f172a;
  font-weight: 600;
}

.layout-shell.theme-dark .meta-value {
  color: #e2e8f0;
}

.content-section {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 18px;
  overflow: hidden;
}

.layout-shell.theme-dark .content-section {
  background: rgba(15, 23, 42, 0.92);
}

.content-header {
  padding: 1.25rem 1.5rem;
  border-bottom: 1px solid rgba(148, 163, 184, 0.15);
}

.content-title {
  font-size: 1rem;
  font-weight: 600;
  color: #0f172a;
  margin: 0;
}

.layout-shell.theme-dark .content-title {
  color: #e2e8f0;
}

.content-body {
  padding: 1.5rem;
}

.content-body pre {
  margin: 0;
  font-family: inherit;
  font-size: 0.95rem;
  line-height: 1.7;
  color: #334155;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.layout-shell.theme-dark .content-body pre {
  color: #cbd5e1;
}

.content-empty {
  color: #94a3b8;
  margin: 0;
  font-style: italic;
}

.edit-form {
  padding: 1.5rem;
}

.form-row {
  display: flex;
  gap: 1rem;
  margin-bottom: 0;
}

.form-group {
  margin-bottom: 1.25rem;
}

.form-group--third {
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
.form-textarea {
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
.layout-shell.theme-dark .form-textarea {
  background: rgba(15, 23, 42, 0.9);
  border-color: rgba(148, 163, 184, 0.25);
  color: #e2e8f0;
}

.form-input:focus,
.form-textarea:focus {
  border-color: rgba(20, 184, 166, 0.6);
  box-shadow: 0 0 0 3px rgba(20, 184, 166, 0.18);
}

.form-textarea {
  resize: vertical;
  min-height: 80px;
}

.form-textarea--large {
  min-height: 400px;
  font-family: inherit;
  line-height: 1.6;
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

.secondary-action {
  margin-top: 1.5rem;
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

.w-5 {
  width: 1.25rem;
}

.h-5 {
  height: 1.25rem;
}

@media (max-width: 768px) {
  .page-shell {
    padding: 2rem 1.5rem;
  }

  .page-header {
    flex-direction: column;
    align-items: stretch;
  }

  .header-actions {
    width: 100%;
    justify-content: flex-end;
  }

  .form-row {
    flex-direction: column;
  }

  .episode-meta {
    flex-direction: column;
    gap: 1rem;
  }
}
</style>
