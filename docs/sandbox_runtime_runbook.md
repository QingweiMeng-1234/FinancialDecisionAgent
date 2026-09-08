# Financial Agent Sandbox Runtime 操作手册

Financial Agent 的 MCP 服务可以通过 Anthropic 的
[`sandbox-runtime`](https://github.com/anthropic-experimental/sandbox-runtime)
运行。仓库固定使用 `@anthropic-ai/sandbox-runtime` `0.0.73`；该项目目前仍是
Beta Research Preview，Windows 后端为 alpha。

## 这层沙箱限制什么

- 进程树在专用本地账户 `srt-sandbox` 下运行。
- Windows Filtering Platform（WFP）阻止该账户直接出网；HTTP、HTTPS 和其他 TCP
  连接必须经过 Sandbox Runtime 的本地代理。
- 网络采用严格白名单。未知新闻发布站点会失败关闭，不会临时放行。
- `.env`、`.git` 和常见用户凭据目录不可读；项目源码只读。
- 只允许写 MCP 日志、运行时数据库、当前语料/索引目录、文章内容和报告目录。
- API key 在沙箱进程内显示为临时占位符，只在 TLS 代理确认目标主机匹配后注入。

“只读操作”本身不等于沙箱。只读是某个工具或工作流的权限策略；沙箱是在操作系统边界
强制约束整个进程树。只读工具放进这个沙箱后，即使工具实现有漏洞或被提示注入诱导，
进程仍受到文件写入和网络白名单限制。

## 首次安装

要求 Node.js `20.11.0` 或更高版本。在仓库根目录执行：

```powershell
npm install
.\node_modules\.bin\srt.cmd windows-install
```

第二条命令会弹出一次 UAC，并进行机器级变更：

- 创建 `srt-sandbox` 本地账户；
- 创建 `sandbox-runtime-users` 本地组；
- 安装仅针对该沙箱账户 SID 的 WFP 过滤规则；
- 使用默认代理端口范围 `60080-60089`。

安装是幂等的。启动脚本不会代替你执行这一步。

## 启动与停止

沙箱启动方式：

```powershell
.\scripts\start-financial-agent-mcp-sandboxed.ps1
```

需要 Sandbox Runtime 调试日志时：

```powershell
.\scripts\start-financial-agent-mcp-sandboxed.ps1 -DebugSandbox
```

强制重启已存在的 Financial Agent MCP：

```powershell
.\scripts\start-financial-agent-mcp-sandboxed.ps1 -ForceRestart
```

停止方式与原服务相同：

```powershell
.\scripts\stop-financial-agent-mcp.ps1
```

日志位于 `logs/financial-agent-mcp/`。原有非沙箱启动脚本仍保留，方便故障排查；生产或
Agent 可调用的场景应优先使用沙箱启动脚本。

## 网络白名单维护

策略文件是 `config/sandbox/financial-agent-mcp.srt.json`。当前允许基础 API、Yahoo
Finance、Wikidata 和 Cloudflare DoH。由于原文抓取会访问任意发布站点，未知域名默认
失败。确认业务确实需要某个来源后，把精确域名加入 `network.allowedDomains`，重启服务；
不要添加覆盖面过大的通配符。

API key 必须同时满足两个条件才会被注入：

1. 目标域名位于 `network.allowedDomains`；
2. 目标域名位于该 key 的 `credentials.envVars[].injectHosts`。

如果增加新的外部供应商，还要把其 key 配成 `mode: "mask"` 并限定 `injectHosts`。
掩码注入目前只覆盖 HTTPS 请求头和请求体，不覆盖 URL 查询参数；必须使用查询参数传 key
的供应商应先改造调用方式，否则应设为 `mode: "deny"`。当前 Alpha Vantage key 即按此
规则禁用，未配置时沿用应用已有的离线 fallback。
若新增了语料或索引代际目录，也要把精确目录加入 `filesystem.allowWrite`。Windows 的
文件授权在沙箱初始化时应用，因此修改配置后必须重启。

## 验证与卸载

查看本地固定版本：

```powershell
npm run sandbox:check
```

卸载会再次弹出 UAC，并删除 WFP 规则、沙箱账户、本地组和相关注册表状态：

```powershell
.\node_modules\.bin\srt.cmd windows-uninstall
```

上游说明卸载后仍会保留 `%ProgramData%\sandbox-runtime` 和当前用户
`%LOCALAPPDATA%\sandbox-runtime` 中的部分状态；需要完全清理时，应先核对这两个精确
目录，再手动处理。
