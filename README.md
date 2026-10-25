# mlp-finder

按任务名的任意片段搜索火山引擎 MLP 训练任务。MLP 自带的搜索只能匹配任务名开头，本项目把任务同步到本地 SQLite，再做不区分大小写的子串搜索。

需求和设计见 [mlp-finder-prd.md](mlp-finder-prd.md)。

## 配置

在项目根目录新建 `.env`，每行一个 `KEY=VALUE`，等号两边不加空格和引号：

```
VOLC_ACCESS_KEY=
VOLC_SECRET_KEY=
VOLC_REGION=cn-beijing
SYNC_INTERVAL_SEC=300
TASK_URL_TEMPLATE=https://console.volcengine.com/ml-platform/region:ml-platform+{region}/customTask/detail?Id={id}
```

| 变量 | 必填 | 说明 |
|---|---|---|
| `VOLC_ACCESS_KEY` | 是 | 火山引擎 AK |
| `VOLC_SECRET_KEY` | 是 | 火山引擎 SK |
| `VOLC_REGION` | 是 | MLP 所在地域，如 `cn-beijing`、`cn-shanghai` |
| `SYNC_INTERVAL_SEC` | 否 | 增量同步间隔（秒），默认 `300` |
| `TASK_URL_TEMPLATE` | 否 | 任务详情页 URL 模板。在 MLP 控制台打开任意一个任务的详情页，复制地址栏 URL，把任务 ID 换成 `{id}`，地域换成 `{region}`（取 `VOLC_REGION` 的值）。不填则搜索结果不带链接 |
| `DB_PATH` | 否 | SQLite 文件路径，默认 `data/mlp-finder.db` |

## 本地运行

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

启动后会立即同步一次（首次是全量，5000 多条任务约需 1 分钟），之后每 `SYNC_INTERVAL_SEC` 秒增量同步一次。浏览器打开 `http://localhost:8000` 即可搜索。接口：

- `GET /api/tasks?q=3d task&state=&creator=&offset=0`：搜索
- `GET /api/filters`：库里出现过的状态和创建人，用作过滤下拉框的选项
- `POST /api/sync`：立即增量同步
- `GET /api/sync/status`：上次同步的结果和任务数

## 测试与排查

```
python3 -m unittest tests.test_db -v                   # 数据库和搜索，不需要密钥
python3 -m scripts.probe_list_jobs --contains 0627     # 用真实密钥试调 ListJobs，排查鉴权和字段问题
```

## 密钥安全

- AK/SK 请使用 IAM 子用户的密钥，只授予 MLP 只读权限。本项目不对任务做任何写操作。
- `.env` 已在 `.gitignore` 中，不要提交到 git，也不要打进 Docker 镜像。部署时用 `docker run --env-file .env` 传入。
- 每台机器各自创建 `.env`，不要通过 git、聊天工具或邮件明文传递密钥。
- `.claude/settings.json` 禁止 Claude Code 读取 `.env` 文件。
- 如果怀疑密钥泄露，立即在火山引擎 IAM 控制台禁用该密钥，再换一个新的。
