# 项目管理功能实现文档

## 概述

本文档描述了AI Story生成系统的项目管理功能的完整实现，包括前后端架构、API接口、使用指南和测试方法。

---

## 功能特性

### ✅ 核心功能

1. **项目CRUD操作**
   - 创建项目（自动初始化5个工作流阶段）
   - 查看项目列表（支持分页、筛选、搜索）
   - 查看项目详情（完整信息 + 工作流进度）
   - 编辑项目基本信息
   - 删除项目

2. **工作流管理**
   - 5个阶段：文案改写 → 分镜生成 → 文生图 → 运镜生成 → 图生视频
   - 阶段状态追踪：pending, processing, completed, failed
   - 阶段执行控制（手动触发）
   - 阶段重试机制（最多3次，指数退避）
   - 阶段回滚功能

3. **项目状态管理**
   - 项目状态：draft, processing, completed, failed, paused
   - 暂停/恢复项目
   - 自动状态转换（基于阶段状态）

4. **模型配置**
   - 每个阶段可配置多个AI模型
   - 负载均衡策略：轮询、随机、权重、最少负载

5. **高级功能**
   - 项目导出（视频合成 + 字幕）
   - 保存为模板（可复用配置）
   - 项目统计信息

---

## 技术架构

### 后端架构

```
┌─────────────────────────────────────────────────┐
│                 API Layer (DRF)                  │
│  ProjectViewSet / StageViewSet / ConfigViewSet   │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│              Serializers Layer                   │
│  ProjectSerializer / StageSerializer / etc.      │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│             Service Layer (业务逻辑)              │
│         ProjectWorkflowService                   │
│  - 阶段状态管理                                    │
│  - 工作流编排                                      │
│  - 重试/回滚逻辑                                   │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│              Domain Models                       │
│  Project / ProjectStage / ProjectModelConfig     │
└──────────────────────────────────────────────────┘
```

### 前端架构

```
┌─────────────────────────────────────────────────┐
│           Vue Components (daisyUI)               │
│  ProjectList / ProjectCreate / ProjectDetail     │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│            Vuex Store (状态管理)                  │
│  state: projects, currentProject, stages         │
│  actions: CRUD + workflow operations             │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│             API Service Layer                    │
│         projectApi (Axios封装)                    │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│          Backend REST API                        │
│     /api/v1/projects/**                          │
└──────────────────────────────────────────────────┘
```

---

## 文件清单

### 后端文件 (Django)

| 文件路径 | 说明 | 行数 |
|---------|------|------|
| `backend/apps/projects/serializers.py` | 序列化器（数据验证和转换） | 284 |
| `backend/apps/projects/views.py` | ViewSets（API端点） | 336 |
| `backend/apps/projects/services.py` | 业务逻辑服务层 | 327 |
| `backend/apps/projects/urls.py` | URL路由配置 | 14 |
| `backend/apps/projects/models.py` | 领域模型（已存在） | 203 |

### 前端文件 (Vue.js)

| 文件路径 | 说明 | 行数 |
|---------|------|------|
| `frontend/src/api/projects.js` | API客户端封装 | 170 |
| `frontend/src/store/modules/projects.js` | Vuex状态管理 | 333 |
| `frontend/src/views/projects/ProjectCreate.vue` | 项目创建页面（daisyUI） | 302 |
| `frontend/src/views/projects/ProjectDetail.vue` | 项目详情页面 | - |
| `frontend/src/views/projects/ProjectList.vue` | 项目列表页面（已存在） | - |

---

## API接口文档

### 基础URL
```
http://localhost:8000/api/v1/projects/
```

### 接口列表

#### 1. 项目CRUD

**获取项目列表**
```http
GET /projects/projects/
Query参数:
  - page: 页码
  - page_size: 每页数量
  - status: 状态筛选 (draft/processing/completed/failed/paused)
  - search: 搜索关键词
  - ordering: 排序字段 (created_at/-created_at)

响应:
{
  "count": 10,
  "next": "...",
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "name": "项目名称",
      "status": "draft",
      "status_display": "草稿",
      "stages_count": 5,
      "completed_stages_count": 0,
      "created_at": "2025-01-15T10:00:00Z"
    }
  ]
}
```

**获取项目详情**
```http
GET /projects/projects/{id}/

响应:
{
  "id": "uuid",
  "name": "项目名称",
  "description": "项目描述",
  "original_topic": "原始主题内容...",
  "status": "draft",
  "stages": [ /* 5个阶段详情 */ ],
  "model_config": { /* 模型配置 */ },
  "total_stages": 5,
  "completed_stages": 0,
  "progress_percentage": 0
}
```

**创建项目**
```http
POST /projects/projects/
Content-Type: application/json

{
  "name": "我的第一个项目",
  "description": "项目描述（可选）",
  "original_topic": "讲述一个关于AI的故事...",
  "prompt_template_set": 1  // 可选，null表示使用默认
}

响应: 201 Created
{
  "id": "新项目UUID",
  "name": "我的第一个项目",
  "status": "draft",
  ...
}

注意: 创建项目时会自动初始化5个阶段，状态均为pending
```

**更新项目**
```http
PATCH /projects/projects/{id}/
Content-Type: application/json

{
  "name": "更新后的名称",
  "description": "更新后的描述"
}
```

**删除项目**
```http
DELETE /projects/projects/{id}/

响应: 204 No Content
```

#### 2. 工作流控制

**执行阶段**
```http
POST /projects/projects/{id}/execute_stage/
Content-Type: application/json

{
  "stage_name": "rewrite",  // rewrite/storyboard/image_generation/camera_movement/video_generation
  "input_data": {}  // 可选的输入数据
}

响应:
{
  "message": "阶段 文案改写 开始执行",
  "project": { /* 更新后的项目信息 */ },
  "stage": { /* 更新后的阶段信息 */ }
}
```

**重试阶段**
```http
POST /projects/projects/{id}/retry_stage/
Content-Type: application/json

{
  "stage_name": "rewrite"
}

响应:
{
  "message": "阶段 文案改写 开始重试 (第1次)",
  "stage": { /* 阶段信息 */ }
}
```

**回滚阶段**
```http
POST /projects/projects/{id}/rollback_stage/
Content-Type: application/json

{
  "stage_name": "storyboard"
}

响应:
{
  "message": "已回滚到阶段 分镜生成",
  "project": { /* 更新后的项目信息 */ }
}

注意: 会重置当前及后续所有阶段
```

#### 3. 项目控制

**暂停项目**
```http
POST /projects/projects/{id}/pause/

响应:
{
  "message": "项目已暂停",
  "project": { /* 项目信息 */ }
}
```

**恢复项目**
```http
POST /projects/projects/{id}/resume/

响应:
{
  "message": "项目已恢复",
  "project": { /* 项目信息 */ }
}
```

#### 4. 模型配置

**获取模型配置**
```http
GET /projects/projects/{id}/model_config/

响应:
{
  "id": "uuid",
  "load_balance_strategy": "weighted",
  "rewrite_providers": [1, 2],
  "storyboard_providers": [1],
  ...
}
```

**更新模型配置**
```http
PATCH /projects/projects/{id}/update_model_config/
Content-Type: application/json

{
  "load_balance_strategy": "round_robin",
  "rewrite_providers": [1, 2, 3]
}
```

#### 5. 高级功能

**保存为模板**
```http
POST /projects/projects/{id}/save_as_template/
Content-Type: application/json

{
  "template_name": "科幻风格模板",
  "include_model_config": true
}
```

**导出项目**
```http
POST /projects/projects/{id}/export/
Content-Type: application/json

{
  "include_subtitles": true,
  "video_format": "mp4"
}

响应:
{
  "message": "导出任务已创建",
  "export_id": "...",
  "status": "processing"
}
```

**获取统计信息**
```http
GET /projects/projects/statistics/

响应:
{
  "total_projects": 10,
  "draft_projects": 3,
  "processing_projects": 2,
  "completed_projects": 4,
  "failed_projects": 1,
  "paused_projects": 0
}
```

---

## 使用指南

### 1. 启动服务

**后端**
```bash
cd backend
python manage.py runserver
# 访问: http://localhost:8000
```

**前端**
```bash
cd frontend
npm run dev
# 访问: http://localhost:3000
```

### 2. 创建项目流程

1. 访问 http://localhost:3000/projects/create
2. 填写项目信息：
   - 项目名称（必填）
   - 项目描述（可选）
   - 原始主题/文案（必填，至少10字符）
   - 提示词集（可选）
3. 点击"创建项目"
4. 自动跳转到项目详情页

### 3. 执行工作流

在项目详情页：

1. **查看进度**：顶部显示总体进度条
2. **执行阶段**：点击"执行"按钮启动pending状态的阶段
3. **重试失败**：如果阶段失败，点击"重试"按钮
4. **回滚阶段**：点击"回滚"可以重置该阶段及后续阶段
5. **项目控制**：
   - 暂停：暂停处理中的项目
   - 恢复：恢复暂停的项目
   - 导出：完成后可导出视频
   - 保存为模板：保存配置供复用

### 4. 阶段执行顺序

必须按顺序执行（系统会自动检查前置条件）：

```
1. 文案改写 (rewrite)
   ↓
2. 分镜生成 (storyboard)
   ↓
3. 文生图 (image_generation)
   ↓
4. 运镜生成 (camera_movement)
   ↓
5. 图生视频 (video_generation)
```

---

## 测试方法

### 1. 使用Django Admin测试

访问 http://localhost:8000/admin

1. 创建测试用户
2. 在"项目"模块中手动创建项目
3. 查看自动生成的阶段
4. 测试状态转换

### 2. 使用API测试工具

**使用curl**
```bash
# 创建项目
curl -X POST http://localhost:8000/api/v1/projects/projects/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "name": "测试项目",
    "original_topic": "这是一个测试主题，用于验证API功能是否正常工作。",
    "description": "API测试"
  }'

# 获取项目列表
curl http://localhost:8000/api/v1/projects/projects/ \
  -H "Authorization: Bearer YOUR_TOKEN"

# 执行阶段
curl -X POST http://localhost:8000/api/v1/projects/projects/{PROJECT_ID}/execute_stage/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"stage_name": "rewrite"}'
```

**使用Postman**
1. 导入API集合
2. 设置认证token
3. 按顺序测试各个接口

### 3. 前端集成测试

1. 访问项目列表页：http://localhost:3000/projects
2. 点击"创建项目"，填写表单并提交
3. 在列表中找到新创建的项目
4. 点击项目进入详情页
5. 测试各个操作按钮
6. 验证状态更新是否实时

---

## 故障排查

### 常见问题

**1. 前端无法连接后端**
```
错误: Network Error
解决: 检查 frontend/src/services/apiClient.js 中的 baseURL
确保指向正确的后端地址（http://localhost:8000）
```

**2. 401 Unauthorized**
```
错误: Authentication credentials were not provided
解决: 需要先实现用户认证系统，或在开发环境中临时禁用认证
在 views.py 中将 permission_classes = [IsAuthenticated]
改为 permission_classes = [AllowAny]  # 仅开发环境
```

**3. 阶段执行失败**
```
错误: 前置阶段未完成
解决: 必须按顺序执行阶段，先完成 rewrite 才能执行 storyboard
```

**4. CORS错误**
```
错误: Access-Control-Allow-Origin
解决: 检查 backend/config/settings/base.py
确保 CORS_ALLOWED_ORIGINS 包含前端地址
```

---

## 后续开发建议

### 优先级高

1. **用户认证系统**
   - 实现JWT认证
   - 前端登录/注册页面
   - Token自动刷新

2. **Celery异步任务**
   - 实现 `execute_project_stage` 任务
   - 集成 Pipeline 工作流引擎
   - 实时进度推送（WebSocket）

3. **全局通知组件**
   - 替换 `alert()` 为 Toast 组件
   - 错误提示优化

### 优先级中

4. **项目编辑页面**
   - 基本信息编辑
   - 模型配置编辑

5. **视频导出功能**
   - FFmpeg视频合成
   - 字幕生成和嵌入
   - 下载链接生成

6. **项目模板系统**
   - 模板保存逻辑
   - 从模板创建项目
   - 模板市场

### 优先级低

7. **批量操作**
   - 批量删除
   - 批量导出

8. **数据可视化**
   - 项目统计图表
   - 成本分析

9. **协作功能**
   - 项目分享
   - 多人协作

---

## 代码质量检查

### 后端代码规范
- ✅ 遵循PEP 8
- ✅ 使用类型注解
- ✅ 完整的Docstring
- ✅ 单一职责原则（SRP）
- ✅ 依赖倒置原则（DIP）

### 前端代码规范
- ✅ Vue 2组件化
- ✅ Vuex状态管理
- ✅ daisyUI统一风格
- ✅ 响应式设计
- ✅ 错误处理

---

## 性能优化建议

1. **数据库查询优化**
   - 已使用 `select_related` 和 `prefetch_related`
   - 添加适当的索引

2. **前端性能**
   - 列表分页（已实现）
   - 懒加载组件
   - 缓存API响应

3. **API性能**
   - 使用Redis缓存热点数据
   - 实现API限流

---

## 总结

### 已完成 ✅
- ✅ 完整的后端API（Serializers + ViewSets + Service）
- ✅ 前端API服务层封装
- ✅ Vuex状态管理
- ✅ 项目创建页面（daisyUI）
- ✅ 项目详情页面（daisyUI）
- ✅ 工作流状态追踪
- ✅ 阶段重试/回滚机制

### 部分完成 ⏳
- ⏳ 导出功能（接口已定义，实现待完成）
- ⏳ 模板功能（接口已定义，实现待完成）
- ⏳ 项目编辑页面（可复用创建页面）

### 待实现 ⭕
- ⭕ 用户认证集成
- ⭕ Celery异步任务
- ⭕ WebSocket实时更新
- ⭕ 视频合成和导出

---

## 联系与支持

如有问题或建议，请参考：
- 项目文档: `/docs/`
- 架构设计: `ARCHITECTURE.md`
- README: `README.md`
