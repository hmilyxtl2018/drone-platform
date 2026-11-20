# 无人机本体工作台

无人机型号档案、来源证据、属性与关系候选、制造信息和审核记录的本地工作台。

- 按型号查看属性、组成与构型、制造信息、生产与交付、成本与价格。
- 数据 3D 建模页展示参数化示意模型；当前不包含真实 GLB/CAD 资产。
- Firecrawl 定向采集、DeepSeek 提取候选，保留来源快照、逐字引文和审核状态。
- 使用本地 SQLite 保存业务数据；模型输出与待审候选须经过审核和发布。

## 首次部署（Windows）

需要 Git 和 Python 3.12（64 位）。

```powershell
git clone https://github.com/hmilyxtl2018/drone-platform.git
cd drone-platform
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r platform\requirements.txt
```

**启动前导入单独提供的迁移数据包**，解压到仓库根目录。仓库包含代码与本体契约；现有档案、原文、候选和审核数据随迁移数据包交付。解压后数据库位于 `ontology/coast-raid-k/`。完整步骤及哈希校验见[迁移说明](docs/uav-git-migration.md)。

```powershell
$env:CRK_KB_STORE = "sqlite"
.\.venv\Scripts\python.exe platform\serve.py --port 8765
```

打开 <http://127.0.0.1:8765/ontology-workbench.html>。使用迁移包后，核心本体工作台不要求安装 PostgreSQL 或 Node.js。

需要继续搜索和提取时，在新机器配置 Firecrawl / DeepSeek 密钥，详见[服务配置](docs/uav-git-migration.md#4-启动并配置搜索--提取)。本机加密密钥文件绑定 Windows 账户，须在目标机器重新配置。

## 检查和更新

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/api/kb/health
```

后续先备份数据并停止服务，然后更新：

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r platform\requirements.txt
$env:CRK_KB_STORE = "sqlite"
.\.venv\Scripts\python.exe platform\serve.py --port 8765
```

Git 更新代码，不同步两台机器上的业务数据库。服务默认仅供本机浏览器访问；局域网访问及独立 Agent Engine（8090 端口）需另外部署。

## 原机导出数据

```powershell
py -3 scripts\export-uav-data.py
```

输出位于 `.local/transfers/`，包括数据 ZIP 和校验清单。数据、凭据、日志与本机运行环境由 `.gitignore` 排除。
