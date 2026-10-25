# PRD：MLP 训练任务搜索（mlp-finder）

## 1. 背景

火山引擎 MLP 的训练任务列表只能按任务名开头搜索，词中间的片段搜不到。比如：

- `ThisIsASampleTask`：搜 `Sample` 没有结果，搜 `ThisIs` 有结果
- `this-is-3d-task`：搜 `3d` 没有结果，搜 `this-is-3d` 有结果

MLP 是闭源产品，我们没法推动官方修复，所以自己做一个轻量的 Web 应用来搜索。

## 2. 目标 / 非目标

**目标**

- 输入任务名中的任意片段（不区分大小写），能搜到对应任务，点击后跳转到 MLP 的任务详情页。

**非目标**

- 不对任务做任何写操作（创建、停止、删除）。
- 不复刻 MLP 的详情、日志、监控等页面。
- v1 不做多用户和权限管理。

## 3. 功能需求

| 编号 | 功能 | 说明 |
|---|---|---|
| F1 | 同步 | **首次回填**：分页拉取全部历史任务，只跑一次。**增量**：每 5 分钟按创建时间倒序翻页，翻到一整页都早于"库中最早一个未结束任务的创建时间"就停下。这样既能拿到新任务，也能刷新运行中任务的状态。**每日全量**：凌晨全量拉一次，补上遗漏，并标记 MLP 上已删除的任务。 |
| F2 | 手动同步 | 页面上放一个"立即同步"按钮，并显示上次同步的时间和结果。 |
| F3 | 搜索 | 只有一个输入框，做子串匹配，不区分大小写。多个词用空格分隔，按"与"组合。按创建时间倒序，每页 50 条。 |
| F4 | 过滤（P1） | 可按状态、创建人、创建时间范围过滤。 |
| F5 | 跳转 | 结果列表显示：任务名（高亮命中部分）、状态、创建人、创建时间。点击任务名，在新标签页打开 MLP 详情页。 |

## 4. 技术方案

- **后端**：用 FastAPI 做单个服务，同步任务直接放在进程内定时执行。
- **数据库**：SQLite。
- **前端**：一个静态 HTML 页面，由后端直接提供。
- **部署**：一个 Docker 容器，部署在内网。

### 4.1 选型依据

目前约有 1.72 万个任务（约一年的量，平均每天约 50 个），增量同步通常 1～2 页就够。

本地实测：用 SQLite 模拟任务名（5 段随机词、用 `-` 连接），执行两个关键词的 `LIKE '%kw%'` 加排序加 `LIMIT 50`：

| 行数 | 主表带 3KB 原始 JSON | 原始 JSON 拆到另一张表 |
|---|---|---|
| 1.72 万 | 13ms | 1.6ms |
| 20 万（约 10 年） | 159ms | 21ms |

瓶颈是每行的数据量，不是行数，所以原始 JSON 单独存一张表。

什么情况下该换 PostgreSQL：要部署多个实例、有多个进程同时写，或者团队已经有现成的 PG。这些都和数据量无关。换了以后，`pg_trgm` 的 GIN 索引可以让 `ILIKE '%kw%'` 走索引。

### 4.2 数据表

```sql
CREATE TABLE tasks (
  id         TEXT PRIMARY KEY,      -- MLP 任务 ID
  name       TEXT NOT NULL,
  state      TEXT,
  creator    TEXT,
  created_at TIMESTAMP,
  deleted    BOOLEAN DEFAULT FALSE,
  synced_at  TIMESTAMP
);
CREATE INDEX idx_tasks_created ON tasks(created_at DESC);

CREATE TABLE task_raw (
  id  TEXT PRIMARY KEY REFERENCES tasks(id),
  raw JSON                          -- 原始返回，以后加字段不用改同步逻辑
);
```

### 4.3 搜索 SQL

关键词里的 `%`、`_` 需先转义。

```sql
SELECT id, name, state, creator, created_at
FROM tasks
WHERE NOT deleted
  AND name LIKE '%' || :kw1 || '%' ESCAPE '\'
  AND name LIKE '%' || :kw2 || '%' ESCAPE '\'
ORDER BY created_at DESC
LIMIT 50 OFFSET :offset;
```

### 4.4 接口

- `GET /api/tasks?q=3d task&state=&creator=&offset=0`：返回 `{items: [{id, name, state, creator, creator_name, created_at, url}], total}`。`creator` 是 MLP 返回的完整标识（如 `trn:iam::<账号ID>:user/<用户名>`），`creator_name` 是其中的用户名
- `GET /api/filters`：返回库里出现过的状态和创建人，用作过滤下拉框的选项
- `POST /api/sync`：触发一次同步
- `GET /api/sync/status`：返回上次同步的时间、结果、任务数

### 4.5 配置（环境变量）

- `VOLC_ACCESS_KEY` / `VOLC_SECRET_KEY` / `VOLC_REGION`
- `SYNC_INTERVAL_SEC=300`
- `TASK_URL_TEMPLATE`：打开任意一个任务的详情页，复制地址栏 URL，把任务 ID 换成 `{id}`

## 5. 非功能需求

- 新建的任务最迟一个同步周期（5 分钟）内能搜到。
- 数据在 20 万条以内（约 10 年的量）时，搜索响应不超过 100ms。
- AK/SK 只保存在服务端，前端页面和网络请求里都不能出现。
- 全量同步每天最多一次。假设每页 100 条，全量一次约 172 个请求，如果每 5 分钟跑一次就是每天约 5 万次，大概率会触发限流。

## 6. 验收标准

- [ ] 搜 `Sample`、`sample`、`ASample` 都能命中 `ThisIsASampleTask`
- [ ] 搜 `3d`、`3D-task` 都能命中 `this-is-3d-task`
- [ ] 搜 `3d task`（两个词）能命中 `this-is-3d-task`
- [ ] 点击结果能跳转到正确的 MLP 任务详情页
- [ ] 新建任务在 5 分钟内能搜到
- [ ] 浏览器开发者工具里看不到 AK/SK

## 7. 里程碑

1. **M0（0.5 天）**：确认 MLP 训练任务列表接口：
   - 接口名称（推测是 `ml_platform` 服务下的 `ListCustomTasks`）、返回字段
   - 分页方式、每页最多返回多少条、限流阈值
   - 是否支持按创建时间排序或过滤（如果不支持，增量同步改成每次拉取最近 N 天创建的任务）
   - 详情页的 URL 格式

   **结果（2026-10-25）**：
   - 接口：`ml_platform` 服务的 `ListJobs`，版本 `2024-07-01`，POST JSON，HMAC-SHA256 签名。
   - 分页：`PageNumber` + `PageSize`，`PageSize` 只接受 10～100。支持 `SortBy=CreateTime`、`SortOrder=Descend`，以及 `CreateTimeAfter` 过滤，增量同步直接用它。
   - 字段：`Id`、`Name`、`CreatedBy`、`CreateTime`、`UpdateTime`、`Status.State`、`ProjectName`、`ResourceConfig` 等。
   - 当前 AK 可见的任务约 5500 条，少于第 4.1 节估计的 1.72 万。
   - 接口自带 `NameContains`，但它是分词匹配，不是子串匹配：任务名在 `-`、`_` 和字母数字交界处切分，关键词必须是某个词的开头。例如 `testnnsht2layer-0627-...` 能搜到 `0627`、`layer`，搜不到 `2layer`。所以仍需同步到本地自己搜。
   - 详情页 URL 格式：`https://console.volcengine.com/ml-platform/region:ml-platform+cn-beijing/customTask/detail?Id={id}`。地域写在路径里，模板中用 `{region}` 占位，取 `VOLC_REGION` 的值。
2. **M1（1 天）**：完成同步、数据库和搜索接口。
3. **M2（0.5 天）**：完成前端页面和 Docker 部署。

## 8. 待定

- 同步范围是只同步自己的任务，还是整个账号或项目下的全部任务？这取决于 AK 的权限。
- v2 是否把推理服务、开发机等其他资源也纳入搜索？
