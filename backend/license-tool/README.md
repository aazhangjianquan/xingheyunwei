# License 生成工具

此目录中的 `LicenseTool-1.0-SNAPSHOT-jar-with-dependencies.jar` 来自本机下载目录中用户指定的工具。API 使用 Java 17 或更高版本执行：

```sh
java -jar LicenseTool-1.0-SNAPSHOT-jar-with-dependencies.jar \
  -tenantId 1 \
  -expireTime 1893427199999 \
  -accountNumber -1 \
  -agentNumber -1
```

四个参数读取自申请记录；到期时间为 Unix 毫秒时间戳。网页输入和展示时间均使用北京时间。`-1` 表示数量不限。

工具输出完整 `{"license":{"body":"…","key":"…"}}` JSON；API 将完整结果加密保存，详情页展示和复制完整 JSON。后续查看读取数据库中的同一结果，不重新执行工具。通过末节点审批生成；仅申请者和实际最终审批人可读取。重复审批不会重复生成。

Docker 镜像自动安装无界面的 Java 运行环境并包含此工具。本地开发需安装 Java 17+，也可通过 `JAVA_BIN` 指定 Java 可执行文件，通过 `LICENSE_TOOL_JAR` 指定 JAR 文件的绝对路径。缺少工具、执行失败或超时会回滚最后一次审批，可以修复环境后重试。

Docker 使用 `python:3.12-slim-bookworm`，通过阿里云 HTTPS Debian 镜像安装 OpenJDK 17，主仓库与安全更新仓库均已换源，下载失败自动重试 3 次。换源兼容新版 `.sources` 与旧版 `sources.list` 配置。

如需改用其他镜像，可在构建时指定两个源，例如清华镜像：

```sh
docker compose build \
  --build-arg DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian \
  --build-arg DEBIAN_SECURITY_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian-security \
  api
docker compose up -d api
```
