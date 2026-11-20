# 初始迁移数据

`uav-data-20261120.zip` 是 2026-11-20 导出的初始快照，约 15 MB，包含 5 个 SQLite 数据库和 `transfer-manifest.json`。其中有 121 个本体及关联来源、候选、审核和运行记录；共享数据库内其他工作区的关联记录也一并保留。API key 和 PostgreSQL 数据库不包含在内。

首次部署在仓库根目录执行：

```powershell
Get-Content data\uav-data-20261120.zip.sha256
Get-FileHash data\uav-data-20261120.zip -Algorithm SHA256
Expand-Archive -LiteralPath data\uav-data-20261120.zip -DestinationPath .
```

确认两个 SHA-256 一致后解压，再按[迁移说明](../docs/uav-git-migration.md)校验数据库文件并启动。

这是部署用的初始快照，后续 `git pull` 不会更新正在使用的数据库。已有业务数据时先停止服务并备份，不要重复解压覆盖。
