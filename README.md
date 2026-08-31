# zcode-task-cleaner

ZCode 会话数据检测与硬删除工具。

ZCode 客户端界面上"删除"任务是**软删除**——只是打上标记并从界面隐藏，会话消息、命令日志、模型交互记录等数据仍完整保留在磁盘上，长期累积可占用数 GB。本工具直接操作底层数据库和文件，实现真正的硬删除，并附带检测与预览能力。

单文件 Python 脚本，仅使用标准库，无需安装任何依赖。

## 功能

- **检测**：列出所有会话（项目、标题、状态、更新时间、消息数、磁盘占用），支持按状态 / 项目 / 时间过滤
- **预览**：删除前完整展示将要删掉的每一个会话及释放的空间（dry-run）
- **硬删除**：一次联动清理每个会话在 5 处存储中的全部数据
- **安全机制**：默认 dry-run、最近活跃会话自动保护、可选删除前导出 JSON 备份、事务失败自动回滚
- **自定义存储路径自适应**：自动读取 `setting.json` 中的 `dataBaseDir`，支持把数据目录改到任意盘的用户

## 环境要求

- Python 3.8+
- Windows（ZCode 桌面客户端；macOS / Linux 理论兼容，路径规则相同，未实测）

## 快速开始

```bash
# 1. 查看全部会话概览
python zcode-task-cleaner.py list

# 2. 预览要删的东西（永远先跑一次预览！）
python zcode-task-cleaner.py delete --scope deleted

# 3. 确认无误后加 --yes 真正执行
python zcode-task-cleaner.py delete --scope deleted --yes
```

> **铁律**：`delete` 不加 `--yes` 只做预览，不会删除任何数据。

## 背景知识：ZCode 的会话存储结构

工具会自动发现以下位置，无需配置：

| 存储层 | 位置 | 说明 |
|---|---|---|
| 任务索引 | `<dataBaseDir>\.zcode\v2\tasks-index.sqlite` | 任务列表，界面显示的就是它；`dataBaseDir` 读自 `~/.zcode/v2/setting.json`，未自定义时为 `~/.zcode` |
| 会话消息库 | `~/.zcode/cli/db/db.sqlite` | 所有会话的完整聊天记录（12 张关联表） |
| 会话文件 | `~/.zcode/cli/{agents,exec,artifacts}/<sess_id>` | 子智能体记录、命令输出日志、工具产物 |
| 模型日志 | `~/.zcode/cli/rollout/model-io-<sess_id>.jsonl` | 单个会话可达几十 MB |
| 项目检查点 | `<v2>\checkpoints\<hash>\` | 按**项目**组织（读目录内 `state.json` 的 `workspacePath` 判断归属），非按会话 |

任务的三种状态与数据的真实关系：

| 状态 | 界面表现 | 数据是否还在 |
|---|---|---|
| 活跃 | 正常显示 | 在 |
| 归档 | 收进归档区，可能看不到 | 在 |
| 界面已删除 | 永久隐藏 | **仍在**（软删除标记） |

## 命令参考

### 子命令

| 子命令 | 作用 |
|---|---|
| `list` | 列出符合条件的会话（只读，绝不删除） |
| `delete` | 硬删除符合条件的会话（默认 dry-run 预览） |

### 过滤参数（list / delete 通用）

| 参数 | 说明 |
|---|---|
| `--scope active` | 只看活跃任务（**默认**） |
| `--scope archived` | 只看归档任务 |
| `--scope deleted` | 只看界面已删除（软删除）的任务 |
| `--scope all` | 全部 |
| `--project <子串>` | 按项目路径过滤，不区分大小写，支持子串匹配 |
| `--task <ID>...` | 精确指定会话 ID（`sess_xxx`，支持前缀，可多个；指定后忽略 scope） |
| `--older-than <天>` | 只处理 N 天前更新的会话 |
| `--keep-recent <分钟>` | 跳过最近 N 分钟内活跃的会话，防止误删正在使用的（默认 60，`0` 关闭） |

### 仅 delete 专用

| 参数 | 说明 |
|---|---|
| `--export <目录>` | 删除前把每个会话的完整消息（message + part 表）导出为 JSON |
| `--purge-checkpoints` | 某项目的任务被删光时，顺带删除该项目的检查点目录 |
| `--yes` | 真正执行（**不加则永远只预览**） |

## 常用示例

```bash
# 界面上删过的任务彻底清理（最常见用法）
python zcode-task-cleaner.py delete --scope deleted --yes

# 清理某项目的全部会话（含归档和已删），先导出备份
python zcode-task-cleaner.py delete --project myapp --scope all --export ./backup --yes

# 批量清理 30 天前的所有任务
python zcode-task-cleaner.py list --older-than 30 --scope all          # 先看
python zcode-task-cleaner.py delete --older-than 30 --scope all --yes  # 再删

# 只删一个会话（ID 支持前缀，从 list 输出里复制）
python zcode-task-cleaner.py delete --task sess_7e1883d1 --yes

# 清理某项目并连带删它的检查点
python zcode-task-cleaner.py delete --project myapp --scope all --purge-checkpoints --yes
```

## 每个会话删除时会发生什么

对每个选中的会话，按顺序执行：

1. （若指定 `--export`）导出完整消息为 `<sess_id>.json`；
2. 从 `db.sqlite` 删除该会话在 12 张关联表中的全部记录（短事务包裹，失败自动回滚）；
3. 从 `tasks-index.sqlite` 删除任务记录及分组关联；
4. 删除 `agents\`、`exec\`、`artifacts\` 下的同名目录和 `rollout\model-io-<sess_id>.jsonl`；
5. （若指定 `--purge-checkpoints`）项目任务清零时删除对应检查点目录。

**不会动的数据**：项目记忆（`~/.zcode/cli/memories/`）、客户端设置、当前正在使用的会话（最近 60 分钟内活跃的自动跳过）。

## 注意事项（实测经验）

1. **删除后建议重启客户端**。客户端内存中缓存着任务列表，不重启的话界面上可能还显示已删任务；个别场景下（如修改存储路径触发数据迁移）客户端可能把内存中的旧状态整体写回导致已删数据"复活"，重跑一遍删除即可。
2. **客户端开着也能删**。数据库为 WAL 模式，支持并发短事务写入；偶尔报 `database is locked` 属于锁竞争，关闭客户端重跑即可，失败的事务自动回滚，不会损坏数据。
3. **db.sqlite 文件不会立即缩小**。删除记录后文件大小不变，空间在后续整理时回收；需要立即回收可关闭客户端后执行 `sqlite3 db.sqlite "VACUUM;"`。
4. **硬删除不可恢复**（除非使用了 `--export`）。大型清理前建议先导出。
5. **界面上看不到 ≠ 已删除**。任务列表只展示最近活跃的部分，归档和久未更新的任务会被折叠或截断；判断真实状态以 `list` 输出为准。

## 免责声明

本工具直接操作 ZCode 的本地数据库与文件，删除不可恢复。使用前请确认预览输出、善用 `--export` 备份。本工具与 ZCode 官方无关，仅针对实测版本编写，客户端更新后存储结构若变化需相应调整。
