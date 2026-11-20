# 无人机本体工作台：通过 Git 迁移

交付分为两部分：Git 仓库存放代码和本体契约；数据压缩包保存现有档案、来源快照、候选、审核记录及运行历史。代码仓库为 <https://github.com/hmilyxtl2018/drone-platform>。第一次使用 `git clone`，以后更新代码使用 `git pull`。单独拉取代码不会获得现有业务数据，也不会自动安装依赖或复制 API key。

以下命令默认目标机器为 Windows。仓库可以放在任意目录，无须保留原来的 `D:\Ontology_Agents` 路径。

## 1. 在原机器准备数据包

```powershell
cd D:\Ontology_Agents
py -3 scripts\export-uav-data.py
```

命令生成 `.local/transfers/uav-data-时间戳.zip` 和解压目录。它使用 SQLite backup API 导出已提交的数据，逐库检查完整性，生成含 SHA-256 和表行数的 `transfer-manifest.json`。不会覆盖或重建原机器的数据库。

最终切换前先让采集、提取等写入任务完成，再停止会写入这些数据库的工作台实例，重新导出最终数据包。每个数据库的备份独立一致，在线导出不等于多个数据库的同一时刻快照。可先用在线导出的副本验证新机器。

导出的数据库包括：

- `coast-raid-k-intelligence.sqlite`：实体、版本、来源、属性/关系候选、制造信息、审核及相关任务。
- `coast-raid-k-osint.sqlite`：采集任务和原文材料。
- `coast-raid-k-runtime.sqlite`：运行事件和人工处理记录。
- `ontology-agent-admin.sqlite`：如果存在，保留旧版 Agent 管理数据。
- `coast-raid-k.sqlite`：从本体 JSON 重建的静态知识库视图。

这些数据库由多个工作区共享；导出保留共享记录和关联，不按无人机过滤，避免断开来源与审核链。原数据库配置了 `CRK_INTELLIGENCE_SQLITE_PATH`、`CRK_OSINT_SQLITE_PATH` 或 `CRK_RUNTIME_SQLITE_PATH` 时，请在导出终端设置相同变量。

导出不包含 PostgreSQL、API 密钥或本地虚拟环境。静态知识库来自当前 JSON 契约，不包含只存在于 PostgreSQL 的修改。如需要迁移独立 Agent Engine（8090 端口）的数据库和管理配置，或 PostgreSQL 独有数据，须另外执行 PostgreSQL 备份/恢复。本说明首先交付 8765 端口的本体工作台。

## 2. 把代码上传到 Git 仓库

以下命令用于原始代码目录第一次发布到空仓库。已经 clone 的代码目录直接提交更新，无须重复初始化或添加 origin：

```powershell
cd D:\Ontology_Agents
git init -b main
git add README.md .gitignore platform ontology scripts/export-uav-data.py docs/uav-git-migration.md
git status --short
git diff --cached --stat
git commit -m "Add UAV ontology workbench and migration guide"
git remote add origin https://github.com/hmilyxtl2018/drone-platform.git
git push -u origin main
```

提交前核对文件清单。`.gitignore` 排除了业务数据库、`.local`、本机密钥、虚拟环境、日志和临时输出。不要把数据 ZIP、`db.local.json`、`.local/agent-secrets.dpapi` 或 API key 加进 Git。根目录的 Firecrawl CLI / `node_modules` 不是本体后端的启动依赖，后端通过 HTTP 调用 Firecrawl 和 DeepSeek。

## 3. 对方首次安装并导入数据

安装 Git 和 Python 3.12（64 位，匹配 `platform/requirements.txt` 中的固定依赖版本）。在 PowerShell 中执行：

```powershell
git clone https://github.com/hmilyxtl2018/drone-platform.git Ontology_Agents
cd Ontology_Agents
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r platform\requirements.txt
```

通过约定的文件传输方式拿到数据 ZIP。首次启动前解压到项目根目录，解压后应得到 `ontology/coast-raid-k/` 下的上述数据库，以及根目录的 `transfer-manifest.json`。例如：

```powershell
Expand-Archive -LiteralPath C:\Transfer\uav-data-时间戳.zip -DestinationPath .
```

这是新机器首次导入步骤；已有数据库的机器不要直接覆盖，先停止写入并备份原数据。

核对导入文件哈希：

```powershell
$manifest = Get-Content transfer-manifest.json -Raw | ConvertFrom-Json
foreach ($entry in $manifest.files.PSObject.Properties) {
    $actual = (Get-FileHash -LiteralPath $entry.Name -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $entry.Value.sha256) { throw "数据校验失败：$($entry.Name)" }
}
```

## 4. 启动并配置搜索 / 提取

先运行已有数据浏览。此模式不需要 PostgreSQL，也不要求先配置 API key：

```powershell
$env:CRK_KB_STORE = "sqlite"
.\.venv\Scripts\python.exe platform\serve.py --port 8765
```

打开 `http://127.0.0.1:8765/ontology-workbench.html`。

需要继续 Firecrawl 定向采集和 DeepSeek 提取时，先停止上面的服务，在**启动服务的同一个 PowerShell 窗口**中设置：

```powershell
$firecrawlKeyInput = Read-Host 'Firecrawl API key' -AsSecureString
$deepseekKeyInput = Read-Host 'DeepSeek API key' -AsSecureString
$env:FIRECRAWL_API_KEY = [System.Net.NetworkCredential]::new('', $firecrawlKeyInput).Password
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', $deepseekKeyInput).Password
$env:CRK_SEARCH_PROVIDER = "firecrawl"
$env:CRK_LLM_PROVIDER = "deepseek"
$env:DEEPSEEK_MODEL = "deepseek-chat" # 按目标账号可用模型调整
$env:CRK_KB_STORE = "sqlite"
.\.venv\Scripts\python.exe platform\serve.py --port 8765
```

以上密钥保存在当前进程环境中，关闭终端后需重新输入。原机器的 DPAPI 密钥文件绑定 Windows 用户，复制到另一机器不能代替重新配置。服务端不自动读取 `.env` 文件。`configured: true` 只表示已配置；实际搜索/模型连通性在首次使用时确认。

服务当前只监听 `127.0.0.1`，供新机器本地浏览器访问。如需其他电脑通过局域网访问，需要另外配置监听地址、身份认证及网络入口。

## 5. 验收与后续更新

另开一个 PowerShell 窗口：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/api/kb/health
$entities = Invoke-RestMethod 'http://127.0.0.1:8765/api/ontology/entities?limit=1000'
$entities.entities.Count
```

实体数量应与数据包 manifest 中 Intelligence 库的 `ontology_entity` 行数相同。再打开一个已存在型号，检查属性、组成与构型、数据 3D 建模、制造信息、生产与交付、成本与价格及待审核候选。候选与正式事实应保留原来的审核状态。

后续更新先备份目标机器数据并停止服务，然后执行：

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r platform\requirements.txt
$env:CRK_KB_STORE = "sqlite"
.\.venv\Scripts\python.exe platform\serve.py --port 8765
```

`git pull` 更新代码与契约，不会同步两台机器上的业务操作。接收方产生的新候选和审核记录保存在接收方数据库；今后如需多人共用同一份数据，应统一连接一套服务。

## 已执行的迁移验证

2026-11-20：在本机独立目录中创建全新 Python 3.12 虚拟环境，按固定版本依赖安装；复制代码、导入数据 ZIP，在 8876 测试端口独立启动。启动前，5 个数据库的 SHA-256、完整性检查和全部表行数均与 manifest 一致；启动后读取到 121 个本体，样本型号的制造数据及审核状态与原服务完全相同。静态知识库使用 SQLite，工作台页面、3D 资源与契约均返回 HTTP 200。验证没有复制凭据，没有依赖原机 PostgreSQL 或 Node.js，也没有调用付费搜索或模型接口。目标机器仍需按上述步骤安装并做最终验收。
