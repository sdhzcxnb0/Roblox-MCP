# Roblox MCP

把 **Roblox Studio** 接进任意 MCP 客户端（Claude Desktop、Claude Code、Cursor、Codex、DSH……），
让 AI 直接**读场景、改属性、写脚本、跑 Luau**，每一步都能 Ctrl+Z 撤销。

> 只在**本机**工作：MCP 服务器监听 `127.0.0.1:44755`，Studio 插件用长轮询连上来，不经过任何外网。

```
MCP 客户端 ──stdio──▶ roblox-mcp ──HTTP 127.0.0.1:44755──▶ Studio 插件 ──▶ 你的 place
```

## 功能

| 分类 | 工具 | 说明 |
| --- | --- | --- |
| 观察 | `studio_status` | 插件是否连接、当前 place、桥接端口 |
| 观察 | `get_tree` | 场景树（服务 / 实例 / 类名），可限深度和节点数 |
| 观察 | `get_instance` | 单个实例的类、常用属性、Attributes、子节点 |
| 观察 | `search_instances` | 按名字片段 / 类名搜索实例 |
| 观察 | `list_scripts` | 列出所有脚本及行数 |
| 观察 | `read_script` | 读取脚本源码 |
| 观察 | `get_selection` | 用户当前选中的实例 |
| 修改 | `create_instance` | 新建 Part / Script / Model 等，可带初始属性 |
| 修改 | `set_property` | 改属性，支持 `Vector3(4,1,2)`、`Enum.Material.Neon` 等写法 |
| 修改 | `delete_instance` | 删除实例（拒绝删服务） |
| 修改 | `insert_model` | 用 InsertService 从资产 id 插入模型 |
| 修改 | `write_script` | 整段替换脚本源码 |
| 执行 | `execute_luau` | 在 Studio 里跑 Luau 片段并返回结果 |

所有修改都会包在 `ChangeHistoryService` 记录里，Studio 里按一次 Ctrl+Z 即可回滚。

## 安装

### 1. 装 Python 服务器

```bash
git clone https://github.com/sdhzcxnb0/Roblox-MCP.git
cd Roblox-MCP
pip install -e .          # 需要 Python 3.10+，会装官方 MCP SDK
```

也可以不安装、直接从仓库目录用 `python -m roblox_mcp` 启动。

### 2. 装 Studio 插件

把 `plugin/RobloxMCP.lua` 复制到 Studio 的插件目录：

| 系统 | 路径 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\Roblox\Plugins\RobloxMCP.lua` |
| macOS | `~/Documents/Roblox/Plugins/RobloxMCP.lua` |

然后重启 Studio，在工具栏 **Plugins** 里找到 **MCP** 按钮（插件第一次运行会自动开始连接）。

还需要打开 HTTP 请求：Studio → **Game Settings → Security → Allow HTTP Requests**。

### 3. 配 MCP 客户端

**Claude Desktop**（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "roblox": {
      "command": "roblox-mcp"
    }
  }
}
```

**Claude Code / Codex CLI**：

```bash
claude mcp add roblox -- roblox-mcp
```

**DSH（本 Harness）**：在 profile 的 `cordis.patch.yml` 里加一条：

```yaml
- id: mcp-roblox
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: roblox
    transport: stdio
    command: python3
    args: ['-m', 'roblox_mcp']
    cwd: /path/to/Roblox-MCP
```

**Cursor / 其他客户端**：同样填 `command: roblox-mcp`、`transport: stdio`。

### 4. 验证

重启客户端后问一句「Studio 连上了吗？」，模型会调用 `studio_status`；
返回 `connected` 且 place 名称正确就说明通了。

## 用法示例

```
帮我看看 Workspace 里有哪些 Part。
把 Workspace/Baseplate 的 Size 改成 Vector3(100, 1, 100)，颜色改成 Enum.Material.Neon。
在 ServerScriptService 里新建一个 Script 叫 Main，内容是一个每 5 秒 print 的循环。
读一下 ReplicatedStorage/Config，把 speed 改成 32。
在 Workspace 里插入模型 1033714，放在 Vector3(0, 10, 0)。
```

路径写法：从服务开始，`/` 分隔，例如 `Workspace/House/Wall`，根可以写 `game`。

属性值写法：普通 JSON 标量（`true`、`0.5`、`"hi"`）或字面量：
`Vector3(1,2,3)`、`Color3(1,0,0)`、`Color3("#ff8800")`、`Enum.Material.Glass`、
`CFrame(0,5,0)`、`UDim2(0,100,0,50)`、`NumberRange(0,10)`。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 工具报「插件未连接」 | Studio 没开、插件没启用，或 44755 端口被占用（换端口见下） |
| Studio 输出里出现 HTTP 403 | Game Settings 里没开 Allow HTTP Requests |
| `insert_model` 失败 | 该资产不允许 free 拉取；换公开模型 id 或在 Creator Store 授权 |
| `execute_luau` 报错 | 插件需要能 `require` 临时 ModuleScript；被拦截时改用命令栏执行 |
| 端口冲突 | `--port 44756` 启动，同时改 `plugin/RobloxMCP.lua` 顶部的 `SERVER_URL` |
| 想加一层认证 | `--token <随机串>` 启动，插件里给请求头加 `X-Roblox-MCP-Token: <随机串>` |

## 安全说明

- `execute_luau` **等同于让 AI 在 Studio 里执行任意代码**（以插件权限），只在你信得过的客户端/模型下使用。
- 服务器只绑定 `127.0.0.1`，不对外网开放；可选 `--token` 防止本机其他程序冒充插件。
- 插件不发送你的文件、账号或 Cookie，只回传场景结构与脚本内容。

## 开发

```bash
python tests/test_e2e.py     # 无需 Studio：用假插件跑通 13 个工具的端到端测试
```

- `roblox_mcp/` — MCP 服务器（`server.py` 工具、`bridge.py` 长轮询桥、`values.py` 值编解码）
- `plugin/RobloxMCP.lua` — Studio 插件（单文件）
- `tests/fake_plugin.py` — 模拟 Studio 的桥接客户端
- `docs/protocol.md` — 桥接协议说明

## Roadmap

- [ ] `capture_viewport`（截图给模型看效果）
- [ ] 批量 `apply_patch`（一次改多个实例，单条 undo）
- [ ] 资源/属性的事件订阅，长任务进度上报

## License

MIT
