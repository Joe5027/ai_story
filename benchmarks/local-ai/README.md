# AI Story 本地模型基准输入

此目录只保存可版本化、可复核、无密钥的输入定义，不保存生成结果，也不表示任何模型已经安装或通过验收。当前三类样本的 `status` 均为 `not_run`。

## 样本集

| 类别 | 文件 | 目的 |
| --- | --- | --- |
| 中文文本 | `text/chinese-storyboard.json` | 长文本改写、资产抽取、分镜 JSON 和运镜结构契约 |
| 图片编辑 | `image-edit/character-consistency.json` | 固定 seed 建立角色参考，再做服饰/场景编辑并人工盲审一致性 |
| 视频运镜 | `video-motion/camera-motions.json` | 缓慢推进、水平平移、交叉融合三种 720p/24fps/8–10 秒策略 |

`manifest.json` 是唯一入口。样本中只有虚构内容，不含用户项目数据、API Key、内部 URL、真实人物肖像或受保护媒体。

## 重复执行规则

1. 建立 `storage/benchmark-runs/<UTC时间>/`，不要把结果写回此目录。
2. 复制原始 case JSON 到运行目录，并记录其 SHA-256。
3. 在运行记录中填写 Git commit、操作系统、GPU/显存、内存、驱动、模型 ID 与 digest、量化、workflow 版本与 SHA-256、Runtime Agent 版本。
4. 严格使用 case 中的 profile、seed、尺寸、FPS、时长和 schema；如因硬件限制改变参数，必须作为新 case，而不是覆盖原 case。
5. 保存任务状态、排队/执行耗时、峰值显存/内存、标准错误码和产物 SHA-256。结构化输出使用 JSON Schema 校验，媒体使用 `ffprobe`/解码与人工盲审。
6. 只有真实执行后才能把证据汇总到 `docs/local-ai/BENCHMARK_REPORT.md`。失败和未执行必须原样记录，不能用厂商数据或估算值代替实测。

图片编辑样本采用两步链：先用固定 prompt/seed 生成虚构角色参考图，再将该产物作为编辑输入。这样仓库不需要携带人物照片；重复性来自固定输入、模型 digest 和 workflow hash，而不是假设不同模型版本会产生逐像素相同输出。

视频运镜样本引用图片样本的最终参考产物。若只验证 FFmpeg 草稿路径，可以对同一静态图执行三个 motion plan；若验证 LightX2V，则必须同时记录每个不超过 5 秒 segment、8 帧 overlap、末帧接续和最终合成产物。

## 明确不是结果

- `expected_contract` 是机器可验证的输出约束，不是质量得分。
- `review_rubric` 是盲审维度，不是已给出的评价。
- `candidate_models` 是待测路线，不代表许可证已复核、模型已下载或本机可用。
- 所有样本保持 `status: not_run`，直到真实结果另存并人工签字。
