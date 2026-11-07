--!strict
--[[
	RobloxMCP.lua — single-file Roblox Studio plugin.

	Bridges Studio to the local `roblox-mcp` server (default 127.0.0.1:44755) by
	long-polling /poll and posting results back to /result. Every mutating command
	is wrapped in a ChangeHistoryService recording, so Ctrl+Z undoes it.

	Install: save this file into the Studio plugins folder
	  Windows : %LOCALAPPDATA%\Roblox\Plugins\RobloxMCP.lua
	  macOS   : ~/Documents/Roblox/Plugins/RobloxMCP.lua
	Then enable "Allow HTTP Requests" in Game Settings → Security and restart Studio.
]]

local HttpService = game:GetService("HttpService")
local InsertService = game:GetService("InsertService")
local ChangeHistoryService = game:GetService("ChangeHistoryService")
local Selection = game:GetService("Selection")
local ServerStorage = game:GetService("ServerStorage")
local ScriptEditorService = game:GetService("ScriptEditorService")

local SERVER_URL = "http://127.0.0.1:44755"
local POLL_WAIT = 20

--------------------------------------------------------------------- UI
local toolbar = plugin:CreateToolbar("Roblox MCP")
local button = toolbar:CreateButton("MCP", "连接 / 断开 Roblox MCP", "rbxassetid://4458901886")
button.ClickableWhenViewportHidden = true

local widgetInfo = DockWidgetPluginGuiInfo.new(Enum.InitialDockState.Right, false, false, 340, 260)
local widget = plugin:CreateDockWidgetPluginGui("RobloxMCPPanel", widgetInfo)
widget.Title = "Roblox MCP"

local root = Instance.new("Frame")
root.Size = UDim2.fromScale(1, 1)
root.BackgroundColor3 = Color3.fromRGB(30, 30, 32)
root.BorderSizePixel = 0
root.Parent = widget

local statusLabel = Instance.new("TextLabel")
statusLabel.Size = UDim2.new(1, -16, 0, 24)
statusLabel.Position = UDim2.fromOffset(8, 6)
statusLabel.BackgroundTransparency = 1
statusLabel.Font = Enum.Font.Code
statusLabel.TextSize = 14
statusLabel.TextXAlignment = Enum.TextXAlignment.Left
statusLabel.TextColor3 = Color3.fromRGB(235, 235, 235)
statusLabel.Text = "○ 未连接"
statusLabel.Parent = root

local hintLabel = Instance.new("TextLabel")
hintLabel.Size = UDim2.new(1, -16, 0, 20)
hintLabel.Position = UDim2.fromOffset(8, 28)
hintLabel.BackgroundTransparency = 1
hintLabel.Font = Enum.Font.Code
hintLabel.TextSize = 12
hintLabel.TextXAlignment = Enum.TextXAlignment.Left
hintLabel.TextColor3 = Color3.fromRGB(150, 150, 150)
hintLabel.Text = SERVER_URL
hintLabel.Parent = root

local logBox = Instance.new("TextBox")
logBox.Size = UDim2.new(1, -16, 1, -56)
logBox.Position = UDim2.fromOffset(8, 50)
logBox.BackgroundColor3 = Color3.fromRGB(22, 22, 24)
logBox.BorderSizePixel = 0
logBox.Font = Enum.Font.Code
logBox.TextSize = 12
logBox.TextXAlignment = Enum.TextXAlignment.Left
logBox.TextYAlignment = Enum.TextYAlignment.Top
logBox.TextWrapped = true
logBox.MultiLine = true
logBox.ClearTextOnFocus = false
logBox.TextEditable = false
logBox.TextColor3 = Color3.fromRGB(190, 190, 190)
logBox.Text = ""
logBox.Parent = root

local logLines: { string } = {}
local MAX_LOG = 120

local function log(message: string)
	local stamp = os.date("%H:%M:%S")
	table.insert(logLines, string.format("[%s] %s", stamp, message))
	if #logLines > MAX_LOG then
		table.remove(logLines, 1)
	end
	logBox.Text = table.concat(logLines, "\n")
	print("[RobloxMCP] " .. message)
end

--------------------------------------------------------------------- HTTP
local connected = false

local function setConnected(value: boolean, detail: string?)
	connected = value
	if value then
		statusLabel.Text = "● 已连接" .. (detail and ("  " .. detail) or "")
		statusLabel.TextColor3 = Color3.fromRGB(120, 220, 140)
	else
		statusLabel.Text = "○ 未连接" .. (detail and ("  " .. detail) or "")
		statusLabel.TextColor3 = Color3.fromRGB(230, 160, 120)
	end
end

local function request(path: string, method: string, payload: any?): (boolean, any)
	local options: { [string]: any } = {
		Url = SERVER_URL .. path,
		Method = method,
		Headers = {
			["Content-Type"] = "application/json",
			["Accept"] = "application/json",
			["X-Roblox-MCP-Plugin"] = "1",
		},
	}
	if payload ~= nil then
		options.Body = HttpService:JSONEncode(payload)
	end
	local ok, response = pcall(HttpService.RequestAsync, HttpService, options)
	if not ok then
		return false, tostring(response)
	end
	return true, response
end

--------------------------------------------------------------------- values
local function pathOf(instance: Instance): string
	local parts = {}
	local node: Instance? = instance
	while node and node ~= game do
		table.insert(parts, 1, node.Name)
		node = node.Parent
	end
	return table.concat(parts, "/")
end

local function encodeValue(value: any): any
	if value == nil then
		return nil
	end
	local kind = typeof(value)
	if kind == "Vector3" then
		return { __type = "Vector3", x = value.X, y = value.Y, z = value.Z }
	elseif kind == "Vector2" then
		return { __type = "Vector2", x = value.X, y = value.Y }
	elseif kind == "CFrame" then
		return {
			__type = "CFrame",
			position = { value.Position.X, value.Position.Y, value.Position.Z },
			look = { value.LookVector.X, value.LookVector.Y, value.LookVector.Z },
		}
	elseif kind == "Color3" then
		return { __type = "Color3", r = value.R, g = value.G, b = value.B }
	elseif kind == "EnumItem" then
		return { __type = "Enum", enum = value.EnumType.Name, name = value.Name }
	elseif kind == "Instance" then
		return { __type = "Instance", path = pathOf(value) }
	elseif kind == "UDim2" then
		return { __type = "UDim2", x = { value.X.Scale, value.X.Offset }, y = { value.Y.Scale, value.Y.Offset } }
	elseif kind == "UDim" then
		return { __type = "UDim", scale = value.Scale, offset = value.Offset }
	elseif kind == "BrickColor" then
		return { __type = "BrickColor", name = value.Name }
	elseif kind == "NumberRange" then
		return { __type = "NumberRange", min = value.Min, max = value.Max }
	elseif kind == "number" then
		if value ~= value or value == math.huge or value == -math.huge then
			return tostring(value)
		end
		return value
	elseif kind == "table" then
		local out = {}
		for key, item in pairs(value) do
			out[key] = encodeValue(item)
		end
		return out
	elseif kind == "boolean" or kind == "string" then
		return value
	end
	return tostring(value)
end

local function decodeValue(value: any): any
	if type(value) ~= "table" then
		return value
	end
	local kind = value.__type
	if kind == "Vector3" then
		return Vector3.new(value.x or 0, value.y or 0, value.z or 0)
	elseif kind == "Vector2" then
		return Vector2.new(value.x or 0, value.y or 0)
	elseif kind == "CFrame" then
		local position = value.position or { 0, 0, 0 }
		local at = Vector3.new(position[1] or 0, position[2] or 0, position[3] or 0)
		local look = value.look
		if look then
			local target = Vector3.new(look[1] or 0, look[2] or 0, look[3] or 0)
			if (target - at).Magnitude < 1e-4 then
				return CFrame.new(at)
			end
			return CFrame.lookAt(at, target)
		end
		return CFrame.new(at)
	elseif kind == "Color3" then
		return Color3.new(value.r or 0, value.g or 0, value.b or 0)
	elseif kind == "UDim2" then
		local x = value.x or { 0, 0 }
		local y = value.y or { 0, 0 }
		return UDim2.new(x[1] or 0, x[2] or 0, y[1] or 0, y[2] or 0)
	elseif kind == "UDim" then
		return UDim.new(value.scale or 0, value.offset or 0)
	elseif kind == "Enum" then
		local enumType = Enum[value.enum]
		if not enumType then
			error("未知枚举类型: " .. tostring(value.enum))
		end
		local item = enumType[value.name]
		if not item then
			error("未知枚举值: Enum." .. tostring(value.enum) .. "." .. tostring(value.name))
		end
		return item
	elseif kind == "Instance" then
		return resolvePath(value.path)
	elseif kind == "BrickColor" then
		return BrickColor.new(value.name)
	elseif kind == "NumberRange" then
		return NumberRange.new(value.min or 0, value.max or 1)
	end
	return value
end

--------------------------------------------------------------------- paths
function resolvePath(path: string?): Instance
	if type(path) ~= "string" or path == "" then
		error("路径为空")
	end
	local parts = {}
	for part in string.gmatch(path, "[^/]+") do
		table.insert(parts, part)
	end
	if parts[1] == "game" or parts[1] == "Game" then
		table.remove(parts, 1)
	end
	local node: Instance = game
	for _, name in ipairs(parts) do
		local child = node:FindFirstChild(name)
		if not child then
			error(string.format("找不到 %s 的子节点 %q（完整路径：%s）", node:GetFullName(), name, path))
		end
		node = child
	end
	return node
end

--------------------------------------------------------------------- undo
local function withUndo(label: string, fn: () -> ())
	local recording: any = nil
	local okBegin, beginResult = pcall(function()
		return ChangeHistoryService:TryBeginRecording(label)
	end)
	if okBegin then
		recording = beginResult
	end
	local ok, err = pcall(fn)
	if recording ~= nil then
		local operation = ok and Enum.FinishRecordingOperation.Commit or Enum.FinishRecordingOperation.Cancel
		pcall(function()
			ChangeHistoryService:FinishRecording(recording, operation)
		end)
	end
	if not ok then
		error(err, 0)
	end
end

--------------------------------------------------------------------- handlers
local handlers = {}

local COMMON_SERVICES = {
	Workspace = true,
	Players = true,
	Lighting = true,
	ServerScriptService = true,
	ServerStorage = true,
	ReplicatedStorage = true,
	ReplicatedFirst = true,
	StarterGui = true,
	StarterPack = true,
	StarterPlayer = true,
	SoundService = true,
	Teams = true,
	TextChatService = true,
}

local function buildTree(instance: Instance, depth: number, maxDepth: number, counter: { count: number, max: number })
	if counter.count >= counter.max then
		return nil
	end
	counter.count += 1
	local node: { [string]: any } = {
		name = instance.Name,
		class = instance.ClassName,
		path = pathOf(instance),
	}
	local children = instance:GetChildren()
	if depth < maxDepth then
		local built = {}
		for _, child in ipairs(children) do
			if instance == game and depth == 0 and not COMMON_SERVICES[child.Name] and #child:GetChildren() == 0 then
				continue
			end
			local builtChild = buildTree(child, depth + 1, maxDepth, counter)
			if builtChild then
				table.insert(built, builtChild)
			else
				node.truncated = true
				break
			end
		end
		if #built > 0 then
			node.children = built
		end
	elseif #children > 0 then
		node.leaf = #children
	end
	return node
end

handlers.ping = function(_args)
	return { pong = true }
end

handlers.get_tree = function(args)
	local rootPath = args.root or "game"
	local maxDepth = math.clamp(tonumber(args.maxDepth) or 2, 0, 12)
	local maxNodes = math.clamp(tonumber(args.maxNodes) or 300, 10, 5000)
	local rootInstance = (rootPath == "game" or rootPath == "") and game or resolvePath(rootPath)
	local counter = { count = 0, max = maxNodes }
	local tree = buildTree(rootInstance, 0, maxDepth, counter)
	return {
		root = rootPath,
		tree = tree,
		nodes = counter.count,
		truncated = counter.count >= maxNodes,
	}
end

local BASE_PROPS = { "Name", "ClassName", "Archivable" }
local PART_PROPS = {
	"Position",
	"Size",
	"CFrame",
	"Orientation",
	"Color",
	"Material",
	"Transparency",
	"Anchored",
	"CanCollide",
	"CanTouch",
	"CastShadow",
	"Shape",
	"Locked",
	"Massless",
}
local GUI_PROPS = {
	"Size",
	"Position",
	"AnchorPoint",
	"Visible",
	"BackgroundColor3",
	"BackgroundTransparency",
	"Text",
	"TextColor3",
	"TextSize",
	"Font",
	"ZIndex",
	"LayoutOrder",
	"AutomaticSize",
}
local SCRIPT_PROPS = { "Enabled", "RunContext" }
local MODEL_PROPS = { "PrimaryPart" }

local function propsFor(instance: Instance): { string }
	local props = { "Parent" }
	for _, name in ipairs(BASE_PROPS) do
		table.insert(props, name)
	end
	if instance:IsA("BasePart") then
		for _, name in ipairs(PART_PROPS) do
			table.insert(props, name)
		end
	elseif instance:IsA("GuiObject") then
		for _, name in ipairs(GUI_PROPS) do
			table.insert(props, name)
		end
	elseif instance:IsA("LuaSourceContainer") then
		for _, name in ipairs(SCRIPT_PROPS) do
			table.insert(props, name)
		end
	elseif instance:IsA("Model") then
		for _, name in ipairs(MODEL_PROPS) do
			table.insert(props, name)
		end
	elseif instance:IsA("Light") then
		for _, name in ipairs({ "Brightness", "Color", "Range", "Enabled", "Shadows" }) do
			table.insert(props, name)
		end
	end
	return props
end

handlers.get_instance = function(args)
	local instance = resolvePath(args.path)
	local requested = args.properties
	if type(requested) ~= "table" or #requested == 0 then
		requested = propsFor(instance)
	end
	local properties = {}
	for _, name in ipairs(requested) do
		local ok, value = pcall(function()
			return instance[name]
		end)
		if ok and value ~= nil then
			properties[name] = encodeValue(value)
		end
	end
	local attributes = {}
	local okAttributes, rawAttributes = pcall(function()
		return instance:GetAttributes()
	end)
	if okAttributes and type(rawAttributes) == "table" then
		for key, value in pairs(rawAttributes) do
			attributes[key] = encodeValue(value)
		end
	end
	local children = {}
	for _, child in ipairs(instance:GetChildren()) do
		table.insert(children, { name = child.Name, className = child.ClassName })
	end
	return {
		path = pathOf(instance),
		className = instance.ClassName,
		properties = properties,
		attributes = attributes,
		children = children,
	}
end

handlers.search_instances = function(args)
	local query = string.lower(args.query or "")
	local targetClass = string.lower(args.className or "")
	local maxResults = math.clamp(tonumber(args.maxResults) or 40, 1, 500)
	local rootInstance = (not args.root or args.root == "game") and game or resolvePath(args.root)
	local matches = {}
	local total = 0
	local visited = 0

	local function visit(instance: Instance)
		if visited > 60000 then
			return
		end
		visited += 1
		local name = string.lower(instance.Name)
		local className = instance.ClassName
		local nameOk = query == "" or string.find(name, query, 1, true) ~= nil
		local classOk = targetClass == "" or string.find(string.lower(className), targetClass, 1, true) ~= nil
		if nameOk and classOk then
			total += 1
			if #matches < maxResults then
				table.insert(matches, { path = pathOf(instance), className = className, name = instance.Name })
			end
		end
		for _, child in ipairs(instance:GetChildren()) do
			visit(child)
		end
	end

	visit(rootInstance)
	return { matches = matches, total = total, truncated = total > #matches }
end

handlers.create_instance = function(args)
	local className = args.className
	if type(className) ~= "string" or className == "" then
		error("className 必填")
	end
	local okNew, instance = pcall(Instance.new, className)
	if not okNew then
		error("无法创建类 " .. tostring(className) .. ": " .. tostring(instance))
	end
	local parentPath = args.parent
	local parent = (not parentPath or parentPath == "") and workspace or resolvePath(parentPath)
	if args.name and args.name ~= "" then
		instance.Name = args.name
	end
	for key, value in pairs(args.properties or {}) do
		local okSet, err = pcall(function()
			instance[key] = decodeValue(value)
		end)
		if not okSet then
			instance:Destroy()
			error(string.format("设置属性 %s 失败: %s", tostring(key), tostring(err)), 0)
		end
	end
	local path = pathOf(instance)
	withUndo("MCP: 创建 " .. instance.Name, function()
		instance.Parent = parent
	end)
	path = pathOf(instance)
	return { path = path, className = instance.ClassName }
end

handlers.set_property = function(args)
	local instance = resolvePath(args.path)
	local property = args.property
	if type(property) ~= "string" or property == "" then
		error("property 必填")
	end
	local value = decodeValue(args.value)
	withUndo("MCP: 修改 " .. instance.Name .. "." .. property, function()
		instance[property] = value
	end)
	return { path = pathOf(instance), value = encodeValue(instance[property]) }
end

handlers.delete_instance = function(args)
	local instance = resolvePath(args.path)
	if instance == game then
		error("不能删除 game")
	end
	if instance.Parent == game then
		error("拒绝删除服务 " .. instance.Name .. "，请指定具体子节点")
	end
	local path = pathOf(instance)
	local className = instance.ClassName
	withUndo("MCP: 删除 " .. instance.Name, function()
		instance:Destroy()
	end)
	return { path = path, className = className }
end

handlers.insert_model = function(args)
	local assetId = tonumber(args.assetId)
	if not assetId then
		error("assetId 必填且必须是数字")
	end
	local parentPath = args.parent
	local parent = (not parentPath or parentPath == "") and workspace or resolvePath(parentPath)
	local okLoad, container = pcall(function()
		return InsertService:LoadAsset(assetId)
	end)
	if not okLoad then
		error(
			string.format(
				"加载 asset %d 失败: %s（确认该模型允许 get，或换个 asset id）",
				assetId,
				tostring(container)
			),
			0
		)
	end
	local inserted = {}
	withUndo("MCP: 插入模型 " .. tostring(assetId), function()
		for _, item in ipairs(container:GetChildren()) do
			item.Parent = parent
			table.insert(inserted, item)
		end
		container:Destroy()
	end)
	local count = 0
	for _, item in ipairs(inserted) do
		count += 1 + #item:GetDescendants()
	end
	local position = args.position
	if position ~= nil then
		local target = decodeValue(position)
		local point = target.Position or target
		for _, item in ipairs(inserted) do
			pcall(function()
				if item:IsA("Model") then
					item:PivotTo(CFrame.new(point))
				elseif item:IsA("BasePart") then
					item.Position = point
				end
			end)
		end
	end
	local first = inserted[1]
	return {
		path = first and pathOf(first) or pathOf(parent),
		className = first and first.ClassName or "Model",
		count = count,
	}
end

handlers.execute_luau = function(args)
	local code = args.code
	if type(code) ~= "string" or code == "" then
		error("code 必填")
	end
	local started = os.clock()
	local module = Instance.new("ModuleScript")
	module.Name = "RobloxMCP_Exec"
	module.Source = string.format("return (function()\n%s\nend)()\n", code)
	module.Parent = ServerStorage
	local ok, result = pcall(function()
		return require(module)
	end)
	pcall(function()
		module:Destroy()
	end)
	if not ok then
		error("Luau 执行出错: " .. tostring(result), 0)
	end
	return {
		value = encodeValue(result),
		durationMs = math.floor((os.clock() - started) * 1000),
		note = "print 输出在 Studio 的 Output 面板中。",
	}
end

handlers.list_scripts = function(args)
	local rootInstance = (not args.root or args.root == "game") and game or resolvePath(args.root)
	local maxResults = math.clamp(tonumber(args.maxResults) or 200, 1, 2000)
	local scripts = {}
	local total = 0
	local function visit(instance: Instance)
		if instance:IsA("LuaSourceContainer") then
			total += 1
			if #scripts < maxResults then
				local okSource, source = pcall(function()
					return instance.Source
				end)
				local lines = 0
				if okSource and type(source) == "string" then
					lines = 1
					for _ in string.gmatch(source, "\n") do
						lines += 1
					end
				end
				table.insert(scripts, { path = pathOf(instance), className = instance.ClassName, lines = lines })
			end
		end
		for _, child in ipairs(instance:GetChildren()) do
			visit(child)
		end
	end
	visit(rootInstance)
	return { scripts = scripts, total = total, truncated = total > #scripts }
end

local function getDocument(instance: Instance)
	local ok, document = pcall(function()
		return ScriptEditorService:FindScriptDocument(instance)
	end)
	if ok then
		return document
	end
	return nil
end

handlers.read_script = function(args)
	local instance = resolvePath(args.path)
	if not instance:IsA("LuaSourceContainer") then
		error(tostring(instance:GetFullName()) .. " 不是脚本")
	end
	local document = getDocument(instance)
	if document then
		local ok, text = pcall(function()
			return document:GetText()
		end)
		if ok and type(text) == "string" then
			return { path = pathOf(instance), source = text, lines = select(2, string.gsub(text, "\n", "")) + 1 }
		end
	end
	local okSource, source = pcall(function()
		return instance.Source
	end)
	if not okSource then
		error("无法读取脚本源码: " .. tostring(source))
	end
	return { path = pathOf(instance), source = source, lines = select(2, string.gsub(source, "\n", "")) + 1 }
end

handlers.write_script = function(args)
	local instance = resolvePath(args.path)
	if not instance:IsA("LuaSourceContainer") then
		error(tostring(instance:GetFullName()) .. " 不是脚本")
	end
	local source = args.source
	if type(source) ~= "string" then
		error("source 必填")
	end
	withUndo("MCP: 修改脚本 " .. instance.Name, function()
		local document = getDocument(instance)
		local edited = false
		if document then
			edited = pcall(function()
				local old = document:GetText()
				if old == source then
					return
				end
				local lineCount = select(2, string.gsub(old, "\n", "")) + 1
				document:EditTextAsync(source, 0, 0, lineCount, 0)
			end)
		end
		if not edited then
			instance.Source = source
			pcall(function()
				plugin:SaveScriptChanges(instance)
			end)
		end
	end)
	return { path = pathOf(instance), lines = select(2, string.gsub(source, "\n", "")) + 1 }
end

handlers.get_selection = function()
	local items = {}
	for _, instance in ipairs(Selection:Get()) do
		table.insert(items, { path = pathOf(instance), className = instance.ClassName })
	end
	return { selection = items }
end

--------------------------------------------------------------------- loop
local running = false
local loopThread: thread? = nil

local function handleCommand(body: string)
	local okDecode, message = pcall(function()
		return HttpService:JSONDecode(body)
	end)
	if not okDecode or type(message) ~= "table" then
		log("收到无法解析的命令: " .. tostring(message))
		return
	end
	local id = message.id
	local command = message.command
	local handler = handlers[command]
	local payload: { [string]: any }
	if not handler then
		payload = { id = id, ok = false, error = "未知命令: " .. tostring(command) }
	else
		local ok, result = pcall(handler, message.args or {})
		if ok then
			payload = { id = id, ok = true, data = result }
			setConnected(true)
		else
			payload = { id = id, ok = false, error = tostring(result) }
			log("命令 " .. tostring(command) .. " 失败: " .. tostring(result))
		end
	end
	local okPost = request("/result", "POST", payload)
	if not okPost then
		log("回传结果失败")
	end
end

local function pollLoop()
	while running do
		local ok, response = request("/poll?wait=" .. POLL_WAIT, "GET")
		if not running then
			break
		end
		if not ok then
			setConnected(false, "等待服务器")
			task.wait(2)
		elseif response.StatusCode == 204 then
			setConnected(true)
		elseif response.StatusCode == 200 and response.Body ~= "" then
			handleCommand(response.Body)
		else
			task.wait(0.2)
		end
	end
end

local function startLoop()
	if running then
		return
	end
	running = true
	loopThread = task.spawn(function()
		local placeName = game.Name
		local info = { plugin = "RobloxMCP", studio = "Studio", place = placeName }
		request("/hello", "POST", info)
		setConnected(true)
		log("开始轮询 " .. SERVER_URL)
		local ok, err = pcall(pollLoop)
		if not ok then
			log("轮询中断: " .. tostring(err))
			setConnected(false, "已停止")
		end
		running = false
	end)
end

local function stopLoop()
	running = false
	if loopThread then
		pcall(task.cancel, loopThread)
		loopThread = nil
	end
	setConnected(false, "已暂停")
end

button.Click:Connect(function()
	if running then
		stopLoop()
		log("已暂停")
	else
		startLoop()
	end
end)

widget.Enabled = true

plugin.Unloading:Connect(function()
	running = false
	if loopThread then
		pcall(task.cancel, loopThread)
	end
	pcall(function()
		request("/bye", "POST", { plugin = "RobloxMCP" })
	end)
end)

log("插件加载完成，服务器 " .. SERVER_URL)
task.defer(startLoop)
