# 备份与恢复指南

> 目标：能恢复配置、工作流和运行状态，同时避免把密钥、个人数据和巨型模型缓存无差别复制。本文中的命令是模板；执行覆盖、删除或远程复制前必须确认目标和权限。

## 1. 备份范围

| 数据 | 默认策略 | 说明 |
| --- | --- | --- |
| Django 数据库 | 必备 | 包含 Provider/节点/项目配置/用量；API key 与 Agent access token 有明文风险 |
| `config` | 脱敏后必备 | 不包含真实 `.env`、令牌或私钥 |
| `workflows` | 必备 | 保存版本、哈希、custom node 清单 |
| Runtime Agent SQLite | 必备 | Agent job、幂等键、reload generation 和产物元数据；可能同时包含 Mock 与真实 adapter 任务 |
| Django 推理领域表 | 必备 | 路由、预算预留、工作项和产物事实 |
| 模型文件 | 通常用清单重建 | 记录来源、版本、许可证和 SHA-256；关键离线模型可单独冷备 |
| 用户输入/生成输出 | 按项目保留策略 | 不默认无限期备份 |
| 日志 | 脱敏、限期 | 不含密钥和完整用户素材 |
| 临时目录 | 不备份 | 恢复时重建 |

Runtime Agent journal、预算和统一工作项已实现数据结构；在迁移/服务未实际启用的环境，对应项标记“未部署”，不能假装已有可恢复数据。

## 2. 恢复目标

RPO 与 RTO 必须由业务负责人填写并演练后确认：

| 范围 | RPO | RTO | 当前证据 |
| --- | --- | --- | --- |
| Provider/项目配置 | 待定 | 待定 | 未演练 |
| 工作流登记 | 待定 | 待定 | 未演练 |
| 工作项/预算状态 | 待定 | 待定 | 结构已实现，未演练 |
| 用户产物 | 按项目约定 | 按项目约定 | 未演练 |

不要填造假的“0 数据丢失”或恢复分钟数。

## 3. 备份布局

建议备份落到与运行盘不同的受控卷：

```text
<backup-root>\ai-story\<UTC timestamp>\
  manifest.json
  hashes.txt
  database\
  config-redacted\
  workflows\
  runtime-state\
  logs-redacted\
```

备份清单记录 Git 提交、应用版本、数据库版本、模型/工作流哈希、文件数量、创建者和加密方式。

## 4. PowerShell 备份模板

先只读预览：

```powershell
$RuntimeRoot = 'E:\AI\ai-story-runtime'
$Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$BackupRoot = "F:\Backups\ai-story\$Stamp"
Get-ChildItem "$RuntimeRoot\workflows" -File -Recurse |
  Select-Object FullName,Length,LastWriteTime
```

经批准后创建新目录并复制不可变快照：

```powershell
New-Item -ItemType Directory -Path $BackupRoot -ErrorAction Stop | Out-Null
Copy-Item "$RuntimeRoot\workflows" "$BackupRoot\workflows" -Recurse -ErrorAction Stop
Get-ChildItem "$BackupRoot\workflows" -File -Recurse |
  Get-FileHash -Algorithm SHA256 |
  Export-Csv "$BackupRoot\workflow-hashes.csv" -NoTypeInformation -Encoding UTF8
```

不要直接复制含真实密钥的配置。数据库备份使用与当前数据库引擎匹配的官方工具，并在成功后检查退出码和还原能力。

## 5. 模型可重建清单

每个模型记录：

- 上游仓库/下载源。
- 精确版本或 commit、量化和文件名。
- SHA-256、大小、许可证和获取日期。
- Ollama Modelfile 或 ComfyUI 所需目录。
- 依赖的 custom nodes 及 commit。
- 验证它的基准报告。

只有“模型名称”不足以重建环境。

## 6. 恢复流程

1. 新建隔离恢复目录，不覆盖 `E:\AI\ai-story-runtime`。
2. 验证备份清单、签名/哈希、加密和访问权限。
3. 恢复数据库到隔离实例；确认迁移版本。
4. 恢复脱敏配置和工作流，重新注入受控密钥。
5. 按清单恢复模型/custom nodes 并验证哈希。
6. 启动 Ollama、ComfyUI、Agent（若存在）和应用。
7. 运行健康、代表性任务、预算为 `0`、取消和重试测试。
8. 确认不会重复提交恢复前的外部请求。
9. 经负责人批准后切换流量。

## 7. 预算与工作项恢复

当前恢复流程必须区分：

- 已预留、尚未发送外部请求：可安全释放。
- 外部请求已接受、本地结果未知：转 `ambiguous` / `manual_review`，冻结预留并向 Provider 对账。
- 已完成但本地未提交成本：按外部请求 ID 补偿提交。
- lease 过期的本地任务：由单一恢复者认领，使用版本检查防止双执行。

详见 [../adr/0003-budget-reservation.md](../adr/0003-budget-reservation.md) 和 [../adr/0004-work-item-state-machine.md](../adr/0004-work-item-state-machine.md)。

## 8. 恢复演练

至少每季度或重大变更后演练：

- 工作流文件损坏并从备份恢复。
- 数据库恢复到隔离实例。
- 模型文件缺失后按清单重建。
- 恢复期间付费预算保持 `0`。
- 旧工作项不重复执行、旧预留不重复扣费。
- 备份中无真实密钥和超期用户素材。

演练结果记录真实 RPO/RTO、失败点和修复项；未执行时明确写“未演练”。

## 9. 灾难后安全检查

恢复不等于安全事件结束。若涉及主机入侵或密钥泄露，必须使用干净主机、轮换所有凭据、审查数据库/备份访问、重新获取可信模型与依赖，并确认生成工作流没有被篡改。
