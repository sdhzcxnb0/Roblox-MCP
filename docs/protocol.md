# 桥接协议

MCP 服务器和 Studio 插件之间的私有 HTTP 协议。协议很薄，方便自己改写插件或替换服务器。

- 地址：`http://127.0.0.1:44755`（`--host` / `--port` 可改）
- 编码：UTF-8 JSON，`Content-Type: application/json`
- 鉴权：可选，配置 `--token` 后所有请求需带 `X-Roblox-MCP-Token: <token>`

## 端点

### `GET /health`

返回服务器状态，插件用来探测服务器是否在线。

```json
{
  "connected": true,
  "plugin": {"name": "RobloxMCP", "studio": "Studio", "place": "MyGame"},
  "queued": 0,
  "port": 44755,
  "lastPollAgo": 0.4
}
```

`connected` 表示最近 35 秒内收到过插件的轮询。

### `POST /hello`

插件启动时上报身份，服务器记下 place 名称等。

```json
{"plugin": "RobloxMCP", "studio": "Studio", "place": "MyGame"}
```

响应：`{"server": "roblox-mcp", "pollWait": 20}`

### `GET /poll?wait=20`

长轮询。有命令时立即返回，否则最多挂起 `wait` 秒（上限 50）。

```json
{"id": "9f2c…", "command": "get_tree", "args": {"root": "game", "maxDepth": 2}}
```

无命令时返回 `204 No Content`。服务器只在插件 35 秒内轮询过时才认它在线。

### `POST /result`

插件回传执行结果，`id` 必须与命令一致。

```json
{"id": "9f2c…", "ok": true, "data": {"nodes": 11, "tree": {}}}
{"id": "9f2c…", "ok": false, "error": "找不到 Workspace 的子节点 \"Bassplate\""}
```

`ok: false` 时 MCP 工具调用以错误文本返回给模型。

### `POST /log`

插件日志（可选），写入服务器内存里最近 200 行。

```json
{"level": "info", "message": "开始轮询 http://127.0.0.1:44755"}
```

### `POST /bye`

插件卸载 / 暂停时通知服务器，清空在线状态。

## 命令

| command | args | data |
| --- | --- | --- |
| `get_tree` | `root`, `maxDepth`, `maxNodes` | `tree`（嵌套节点）、`nodes`、`truncated` |
| `get_instance` | `path`, `properties`（可选数组） | `path`、`className`、`properties`、`attributes`、`children` |
| `search_instances` | `query`、`className`、`root`、`maxResults` | `matches[]`、`total`、`truncated` |
| `create_instance` | `className`、`parent`、`name`、`properties` | `path`、`className` |
| `set_property` | `path`、`property`、`value` | `path`、`value` |
| `delete_instance` | `path` | `path`、`className` |
| `insert_model` | `assetId`、`parent`、`position` | `path`、`className`、`count` |
| `execute_luau` | `code` | `value`、`durationMs`、`note` |
| `list_scripts` | `root`、`maxResults` | `scripts[]`、`total`、`truncated` |
| `read_script` | `path` | `source`、`lines` |
| `write_script` | `path`、`source` | `lines` |
| `get_selection` | — | `selection[]` |

## 值编码

Roblox 的数据类型用带 `__type` 的 JSON 表示，Python 和 Luau 两边对称编解码：

| 类型 | JSON |
| --- | --- |
| Vector3 | `{"__type":"Vector3","x":1,"y":2,"z":3}` |
| Vector2 | `{"__type":"Vector2","x":1,"y":2}` |
| CFrame | `{"__type":"CFrame","position":[0,5,0],"look":[0,0,-1]}`（`look` 可选） |
| Color3 | `{"__type":"Color3","r":1,"g":0.5,"b":0}` |
| UDim | `{"__type":"UDim","scale":1,"offset":0}` |
| UDim2 | `{"__type":"UDim2","x":[1,0],"y":[0,24]}` |
| EnumItem | `{"__type":"Enum","enum":"Material","name":"Plastic"}` |
| Instance | `{"__type":"Instance","path":"Workspace/Part"}` |
| BrickColor | `{"__type":"BrickColor","name":"Bright red"}` |
| NumberRange | `{"__type":"NumberRange","min":0,"max":1}` |

其余可序列化的 Lua 值（number/string/boolean/table）原样传输；
函数、userdata 等会退化成字符串（`tostring`）。

## 时序

```
插件                         服务器                        MCP 客户端
 │  POST /hello ──────────────▶ │
 │  GET /poll?wait=20 ────────▶ │（挂起）
 │                              │ ◀── tools/call get_tree ──│
 │ ◀── 200 {id, command,args} ──│
 │  执行 get_tree（Studio 线程） │
 │  POST /result ──────────────▶ │
 │                              │ ── 结果文本 ─────────────▶ │
 │  GET /poll?wait=20 ────────▶ │
```

一条命令同一时刻只发给一个插件；插件离线时 `tools/call` 会等 3 秒后返回
「插件未连接」的提示，而不是无限挂起。
