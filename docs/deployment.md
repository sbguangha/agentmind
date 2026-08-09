# 部署到公网：让面试官打开链接就能用

目标：把 AgentMind 部署到**国内可访问**的服务器/平台，得到一个可直接打开的 URL（建议再做短链），放进简历。

## 一、方案选哪个？

| 方案 | 国内访问 | 成本 | 运维 | 推荐度 |
|---|---|---|---|---|
| **腾讯云/阿里云轻量服务器 + Docker** | ✅ 快 | ~40 元/月起（学生更便宜） | 需自己装环境 | ⭐⭐⭐⭐⭐ 最可控 |
| **Zeabur / Sealos（国内可访问 PaaS）** | ✅ 快 | 有免费额度/低价 | 免运维，直接给子域名 | ⭐⭐⭐⭐ 最快上线 |
| Render.com | ❌ 慢/不稳 | 免费 | 免运维 | ⭐ 不推荐（国外） |

> 核心：**别用国外平台**。面试官在国内，Render 等访问很慢甚至打不开。

## 二、最省事路径（Zeabur 示例）

1. 注册 [zeabur.com](https://zeabur.com)（国内可访问，支持微信/GitHub 登录）
2. 新建项目 → 选择 **Git** → 导入你的 `agentmind` 仓库
3. 平台自动识别 Dockerfile 构建，绑定端口 8765
4. 在"变量"里配置：
   - `AGENTMIND_API_KEY` = 你的 DeepSeek key
   - `AGENTMIND_MODEL` = `deepseek-v4-flash`
   - `AGENTMIND_API_BASE` = `https://api.deepseek.com/v1`
   - `AGENTMIND_WEB_TOKEN` = 一段随机字符串（防滥用，见"安全"）
5. 部署完成后得到一个 `https://xxx.zeabur.app` 链接，直接可用

## 三、最可控路径（腾讯云轻量 + Docker）

```bash
# 1. 买一台轻量服务器（Ubuntu 22.04，约 40 元/月起），控制台放行 8765 端口
# 2. SSH 登录后装 Docker
curl -fsSL https://get.docker.com | sh

# 3. 拉取你的代码（或直接传 Dockerfile 上去）
git clone https://github.com/sbguangha/agentmind.git
cd agentmind

# 4. 写环境变量文件
cat > .env <<'EOF'
AGENTMIND_API_KEY=sk-你的key
AGENTMIND_MODEL=deepseek-v4-flash
AGENTMIND_API_BASE=https://api.deepseek.com/v1
AGENTMIND_WEB_TOKEN=一段随机字符串
EOF

# 5. 构建并启动
docker compose up -d --build

# 6. 浏览器访问 http://服务器IP:8765/?token=你的token
```

Docker 镜像内置了 `uv` 和全部依赖，构建一次即可；`agentmind_data` 卷会持久化会话/记忆/语音。

### 换用短域名 + HTTPS（推荐，简历更好看）

1. 买一个短域名（如 `sbguangha.cn`，几十元/年）
2. 解析一条 A 记录到服务器 IP
3. 用宝塔面板或 Caddy/Nginx 反代 `8765` 端口并自动签发 HTTPS 证书
   - 反代后 `https://agent.sbguangha.cn` 即可访问；JS 会自动用 `wss://` 连接 WebSocket
4. **简历就写**：`https://agent.sbguangha.cn`

## 四、短链（简历上的链接）

优先级从高到低：

1. **直接写平台子域名或自定义短域名**——`https://agent.sbguangha.cn` 或 `https://xxx.zeabur.app` 本身已经够短、可点击。**推荐这个**。
2. 想要更短：用国内短链服务（如百度短链）把长 URL 缩短。注意：公开短链指向未备案域名在国内有被拦风险，且多一跳；自购短域名更稳。
3. 不要在简历写 `http://IP:8765` 这种——不专业且像演示地址。

## 五、安全（公开链接必须注意）

1. **设置 `AGENTMIND_WEB_TOKEN`**：不设 token 的话，任何拿到链接的人都能用你的 Agent、烧你的 API 余额。设了之后：
   - 简历写 `https://agent.sbguangha.cn/?token=你的token`（token 会随简历传播，可接受短期演示；若在意，面试时再给 token，简历只写域名）
   - WebUI 的 `/api`、`/ws` 全部强制校验 token
2. **`AGENTMIND_WORKSPACE_ACCESS=restricted`**：保持默认，模型只能碰 `/app/workspace`
3. **`allow_shell` 默认关闭**、`enable_web` 可按需关——减少被恶意利用面
4. 关注 DeepSeek 账户余额，可设充值上限/用量告警
5. 面试结束后直接 `docker compose down` 或删项目，避免长期暴露

## 六、已知限制（远程 vs 本机）

| 功能 | 本机 | 远程服务器 |
|---|---|---|
| 聊天/工具/记忆/电商售后/审批 | ✅ | ✅ 全部可用 |
| **语音条（浏览器点击播放）** | ✅ | ⚠️ 需服务器能连上微软 edge-tts 端点（国内常被墙）。失败时优雅报错，不影响其他功能 |
| 本地扬声器播报 | ✅ | ❌ 服务器无声卡（但我们已改为浏览器播放语音条，绕开了它） |

想让远程也出语音条：在服务器配代理后设 `EDGE_TTS_PROXY`（见 `voice_mcp/README.md`），或换国内 TTS。
