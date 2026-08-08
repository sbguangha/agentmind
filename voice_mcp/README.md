# voice-mcp · voice_speak 🎙️

一个标准的 **MCP Server**，暴露 `voice_speak` 工具：用 **edge-tts** 将文本合成为语音，并在本机 **流式播放**（边生成边播，降低首字延迟）。

- **技术栈**：Python · 官方 `mcp` SDK（FastMCP）· `edge-tts`（免费、无需 API Key、异步流）· `pygame`（本地播放）
- **传输**：stdio，由 MCP 客户端（如 Nanobot）拉起
- **零密钥**：edge-tts 免费，开箱即用

## 使用

```bash
cd voice_mcp
uv sync --extra dev          # 安装依赖

uv run pytest -q             # 测试（mock 流+播放，零网络零硬件）

uv run python demo_client.py "你好，测试语音"   # 端到端验证：拉起 stdio 服务器并真实调用
```

> ⚠️ stdio MCP Server **不能直接在交互式终端里运行**（`uv run voice-mcp` 会卡住等待客户端协议，回车会被当成非法 JSON 报错）。它是被客户端拉起的；想快速验证就用上面的 `demo_client.py`，或者用 MCP Inspector：`npx @modelcontextprotocol/inspector uv run voice-mcp`。

## 常见问题：连不上微软语音端点

edge-tts 直连 `wss://speech.platform.bing.com/`（免费、无需 key），但该端点**在国内网络经常不可达/被限流**（报 `ConnectionTimeoutError`）。**不需要配置大模型**——LLM 是另一条链路。解决办法：

1. **走本地代理**（有 Clash/V2Ray 时最有效）：
   ```powershell
   $env:EDGE_TTS_PROXY = "http://127.0.0.1:7890"   # 换成你的代理端口
   uv run python demo_client.py "你好，测试语音"
   ```
2. **自动重试已内置**：网络抖动时工具会自动重试一次（`_MAX_ATTEMPTS=2`）。
3. **换用国内可达的 TTS**：如果代理也连不上，可把 `communicate_factory` 替换为国内 TTS（如火山/讯飞，需 key），工具接口不变。

## 接入 AgentMind（推荐）

AgentMind 自带 MCP 客户端（`agentmind/tools/mcp_client.py`）。在 `data/config.json` 添加：

```json
{
  "mcp_servers": {
    "voice": {
      "command": "uv",
      "args": ["run", "--project", "D:/你的路径/voice_mcp", "voice-mcp"],
      "env": { "PYTHONIOENCODING": "utf-8" },
      "tool_timeout": 60
    }
  }
}
```

启动后工具以 `mcp_voice_voice_speak` 出现在 AgentMind 里，对 Agent 说"用语音回答我"即可。

## 接入 Nanobot

（如果你在用 nanobot）在 `~/.nanobot/config.json` 添加，工具以 `mcp_voice_voice_speak` 暴露：

```json
{
  "tools": {
    "mcpServers": {
      "voice": {
        "type": "stdio",
        "command": "uv",
        "args": ["run", "--project", "D:/你的路径/voice_mcp", "voice-mcp"],
        "env": { "PYTHONIOENCODING": "utf-8" }
      }
    }
  }
}
```

## 工具定义

```json
{
  "name": "voice_speak",
  "description": "将文本合成为语音并在本机播放（edge-tts 流式合成 + pygame 边生成边播放）",
  "parameters": {
    "type": "object",
    "properties": {
      "text":  { "type": "string", "description": "需要朗读的文本（最长 1000 字符）" },
      "voice": { "type": "string", "description": "音色，默认 zh-CN-XiaoxiaoNeural" },
      "play":  { "type": "boolean", "description": "是否在本机播放（默认 true；false 时仅返回音频给客户端）" }
    },
    "required": ["text"]
  }
}
```

返回内容：首行是播报状态（如 `语音已播报（zh-CN-XiaoxiaoNeural）：你好…`），末尾附一行 `AUDIO:audio/mpeg:<base64>` 携带完整音频。AgentMind 的 MCP 客户端会自动把这段音频转成 WebUI 里的**微信式语音条**（点击播放），而不会把 base64 塞进模型上下文。

## 许可证提示

`edge-tts` 是 **GPL-3.0** 开源（[rany2/edge-tts](https://github.com/rany2/edge-tts)，2023 年发布，11.7k⭐）。个人/面试演示无碍；若商用分发，请留意 GPL 传染性要求。`pygame` 为 LGPL。

## 核心设计

### 流式播放（`TTSPlayer`）

```
edge-tts Communicate.stream()  ──异步增量──▶  写入临时 .mp3 文件
                                                 │ 达到 ~4KB 阈值
                                                 ▼
                                   pygame.mixer.music.load + play（首字 ~300ms）
                                                 │ 播放器惰性读文件，提前 EOF 时
                                                 ▼
                                   watchdog：get_busy()==False → 重载续播
```

- **首字延迟低**：不等待全部生成完，缓冲区一到阈值即开播。
- **EOF 看门狗**：pygame 边写边读会提前遇到文件末尾而停止，看门狗检测到后重载续播（代价是罕见情况下末尾重叠一两个词）。
- **抢占**：新调用先 `stop()` 当前播放，再播新的（asyncio.Lock 串行化）。
- **清理**：临时文件 `finally` 删除，不残留。

### 错误处理

| 场景 | 处理 |
|---|---|
| 文本为空 | 返回友好错误 |
| 文本超长（>1000） | 截断并标注 |
| 非法音色 | 校验白名单 + 别名映射，返回可选项 |
| 网络异常（edge-tts 需联网） | 包装为 `TTSFailure`，工具返回"合成失败"而非崩溃 |
| 本机无音频设备 | `AudioDeviceError`，返回"本机音频不可用" |
| MCP 调用被取消 | 停止播放 + 清理临时文件 |

### 可测试性

通过依赖注入隔离外部世界：
- `communicate_factory` → 注入 mock edge-tts 流
- `playback: Playback` 协议 → 注入 mock 播放器

单元测试**不依赖网络和音频硬件**。

## 已知取舍（面试话题）

1. **块级流式 vs 逐句流式**：edge-tts 返回整段合成流，本实现是"接近实时的块级流式"。真正的逐句即说即播需要专用流式 TTS 服务（如 Azure Speech），README 如实标注。
2. **为什么用 MCP 而非原生插件**：展示 MCP 能力 + 解耦——任何支持 MCP 的客户端都能复用此工具；代价是 stdio 进程开销。
3. **pygame 续播有轻微重叠**：这是"边写边播 + 惰性读文件"的固有代价，阈值越小首字越快、但慢网络下重叠概率越高。

MIT License
