# AgentNexus v4 — 思想草稿

> 本文件记录 v4 论文的核心思想、论点脉络和工程方向，供后续写作参考。
> 写于 2026 年 6 月，基于与 AI 的设计讨论整理。

---

## 一、核心洞察：软件开发是知识生产与传播的过程

软件开发的本质，不是代码生产，而是**知识生产与传播**。

需求是知识，设计是知识，API 契约是知识，配置是知识，架构决策是知识，每一次 bug 修复背后的推理也是知识。传统软件开发中，这些知识散落在 Wiki、Confluence、Git commit message、Slack 消息和人的大脑里——无结构、无版本、无归属、不可检索、不可订阅。

这个问题在 AI Agent 时代被放大了。LLM Agent 没有持久记忆，每次会话都从零开始。它需要从某个地方读取"这个服务是什么、现在在哪个阶段、有哪些约定"。这个"某个地方"，就是 AgentNexus 文档存储的定位。

> **AgentNexus 是软件开发知识生产与传播这一通用思想的具体实现。**
> 软件开发是它的原点，不是它的边界。

---

## 二、从 v3 到 v4 的升维

### v3 的定位

v3 解决的是**多 Agent 协调**问题：服务边界是协调原语，文档是协调载体，pub-sub 是协调机制。

四个贡献点：
1. 服务粒度协调（Service-granular coordination）
2. 生命周期阶段作为一等实体（Lifecycle stage as first-class entity）
3. Diff-aware 更新协议
4. 服务驱动的 Agent 上线协议（SDAOP）

### v4 的升维

v4 在 v3 基础上增加两个新维度：

**新贡献 5：文档存储作为 Agent 外部化记忆基础设施**

v3 把文档定位为"协调载体"，v4 重新诠释：文档存储同时是 **Agent 的外部化记忆系统**。

这个诠释有三个支撑：
- **版本历史 = 记忆的时间轴**：知识是如何演化的，为什么演化，diff 里全有
- **归属（pushed_by）= 记忆的来源**：这条知识是谁写的，可追溯
- **pub-sub = 记忆的传播机制**：Agent A 学到的东西，通过订阅自动传给 Agent B

与现有记忆方案的对比：

| 能力 | 向量数据库（RAG） | MemGPT | AgentNexus 文档存储 |
|------|------------------|--------|---------------------|
| 持久化 | ✅ | ✅ | ✅ |
| 跨会话 | ✅ | ✅ | ✅ |
| 版本历史 | ❌ | ❌ | ✅ |
| 知识归属 | ❌ | ❌ | ✅ |
| 演化追踪 | ❌ | ❌ | ✅ |
| 跨 Agent 共享 | 复杂 | ❌ | ✅ 原生 |
| 语义检索 | ✅ | ✅ | ⚠️ 关键词（BM25） |

核心论点：
> *"Versioned document stores provide a richer memory substrate than embedding databases for multi-agent systems: they are structured, attributable, temporally ordered, and natively shareable across agent boundaries without retrieval overhead."*

实现：SQLite FTS5 提供 BM25 全文检索，无需额外依赖，零部署成本，覆盖万级文档量。新增 MCP 工具 `search_documents`，支持短语检索、前缀检索、布尔运算、相关性排序。

**新贡献 6：可选的全局协调视角（Planner）**

v3 确立了核心哲学：**服务边界是协调原语，协调是去中心化、自治的**。文档由各服务自主生产、发布、订阅，没有中心化的指挥者。

v4 在**不违背这一哲学**的前提下，引入一个**可选的、可随时接入的全局视角**——Planner。

关键定位（与 v3 一脉相承）：
- Planner **不是**协调的中心，也**不是**必经的入口
- 系统在没有 Planner 的情况下必须完整可用（即 v3 的去中心化形态）
- Planner 的能力是**叠加的**，不是**前置的**——移除它，系统照常运转
- Planner 的「全局观」来自**读权限**（可跨服务边界阅读），而非来自它创建了什么

Planner 之于 v3 的关系，正如「能俯瞰所有服务边界的观察者」之于「自治的服务网络」。它理解并可调整边界，但不取代边界自治。这与 v3「服务边界是协调原语」的主张完全同源——Planner 恰恰是操作这些边界的那一层，而不是另立一套以角色为中心的指挥结构。

**三种平行的协作模式（均受支持，可混用）：**

- **模式 A — 自底向上（去中心化）**：开发团队线下沟通，各自沉淀文档并发布。无 Planner 参与，文档照常流转、订阅、通知。这是 v3 的基础形态。
- **模式 B — 自顶向下（Planner 主导）**：架构师通过 Planner 规划、创建 SubProject、写初始文档，开发 Agent 认领后开始开发。
- **模式 C — Planner 被动接入（事后协调）**：项目已在运行（模式 A），架构师后来才接入 Planner。Planner 凭借全局视角发现不一致、补充缺失文档、做出调整。它是「加入者」，不是「发起者」。

**架构师 = 持有 Planner 能力的人。** 「架构师」不是系统里的实体，而是「使用 Planner 能力的人」。他可能主动规划，也可能被动接入观察后再调整。系统不假设、也不强制他何时介入。

无论哪种模式，写操作都经由现有的 draft/publish 两阶段机制——人保留最终的审核权。AI（Planner）负责降低启动成本、提供全局理解、提议调整；人负责确认服务边界的划分。**服务边界的划分是架构决策，不外包给 AI。**

---

## 三、工程实现规划（v4 对应的新功能）

### 3.1 Web Dashboard

目标：为架构师和团队成员提供统一的文档浏览窗口。

技术选型：FastAPI + Jinja2 模板（服务端渲染，零前端构建，内部工具优先）

核心页面：
- Space 列表 → SubProject 列表 → 文档列表
- 文档内容查看（Markdown 渲染）
- 版本历史 + diff 对比
- 跨服务依赖图（订阅关系可视化）

实现路径：在同一 FastAPI 进程中挂载 `/dashboard` 路由，共享 `ServiceContainer`，不额外引入服务进程。

### 3.2 AI Chat 入口

目标：在 Web UI 中提供上下文感知的 AI 对话能力。

两种入口：
1. **文档级**：查看某文档时，右侧"问 AI"按钮，AI 已加载当前文档上下文
2. **Space 级**：跨服务分析，如"backend 的 API 设计与 frontend 的需求有无冲突"

技术实现：
```
POST /api/chat
  body: { space_id, doc_ids?, question }
  → 后端拉取相关文档内容
  → 组装 system prompt（文档上下文）+ user prompt（问题）
  → 调 LLM API（流式）
  → SSE 流式返回前端
```

第一版不使用 Agent 框架，直接调用 LLM API，实现最简。

### 3.3 FTS5 全文检索

目标：让 AI Agent 和用户都能通过关键词检索跨服务文档。

实现：
- 建 `doc_fts` 虚拟表（SQLite FTS5，tokenize=unicode61）
- `push_document` 时同步更新索引
- 新增 MCP 工具 `search_documents(space_id, query, doc_type?, project_id?, limit=10)`
- 支持：短语搜索 `"exact phrase"`、前缀 `auth*`、布尔 `AND/OR/NOT`、BM25 排序、snippet 高亮

### 3.4 Planner 能力（可选的全局协调）

目标：为持有 Planner 能力的参与者（通常是架构师）提供跨服务边界的理解与调整能力。这是**叠加式**功能，系统不依赖它运行。

Planner 的能力面（读无限制，写经 draft/publish）：
- **读**：跨 Space / SubProject 阅读任意文档、全文检索（复用 FTS5）
- **AI 推理**：`chat(context, question)` 回答问题；`plan(description)` 提议服务拆分方案与初始文档草案
- **写**：`register_project`、`push_document` 等，`pushed_by="system"` 标记来源，默认走 draft

规划场景的工作流（模式 B，仅为示例，非强制）：
```
（可选）架构师通过 Web UI 或 MCP 描述需求
       ↓
Planner 分析，提议 SubProject 拆分 + 依赖关系 + 初始文档草稿（draft）
       ↓
架构师 review / 调整 / 确认
       ↓
publish → 文档进入正常的订阅-通知流转
       ↓
开发 Agent 按需认领、阅读、开发
```

事后协调场景（模式 C）：Planner 接入一个已在运行的 Space，通过全局阅读发现不一致或缺口，提议补充/修正文档（同样走 draft/publish）。

Web UI Chat 与外部 AI Agent（经 MCP）只是 Planner 能力的两种接入方式，**底层能力完全相同**。Planner 不注册为 SubProject，不占用项目资源（详见第六章）。

---

## 四、v4 论文结构草稿

### Abstract 方向

> Software development is fundamentally a knowledge production and propagation process. AgentNexus v3 established that *service boundaries—not agent roles—are the appropriate primitive for coordinating LLM agents*, grounding coordination in decentralized, autonomous document exchange. In v4, we extend—without departing from—this thesis along two axes. First, we reframe the versioned document store as a **structured knowledge infrastructure for AI agents**: versioned documents serve as externalized agent memory, version history provides a temporal knowledge trace, and publish-subscribe delivers knowledge propagation across agent boundaries, offering attribution, temporal ordering, and cross-agent sharing unavailable in embedding-based retrieval systems, with BM25 full-text search as a lightweight retrieval layer. Second, we introduce an **optional, non-intrusive global coordination layer** (the Planner): an observer that can read across service boundaries and propose adjustments, available to participants who need a global view (typically architects). Crucially, the Planner is additive rather than prerequisite—the system remains fully functional in its decentralized form without it—and all writes pass through an existing draft/publish gate, preserving human authority over service-boundary decisions. The Planner is a capability that operates *on* service boundaries, never a centralized, role-centric controller that supplants their autonomy.

### 新增 Section 结构

- **Section 3.6**: Document Store as Externalized Agent Memory
- **Section 3.7**: Full-Text Search via SQLite FTS5
- **Section 3.8**: The Planner — An Optional Global Coordination Layer（强调叠加式、非强制、三种平行协作模式）
- **Section 4（扩展）**: Comparison Table 加入 Memory Systems（MemGPT、LangChain Memory）
- **Section 6（扩展）**: Deployment 场景，展示三种协作模式（自底向上 / Planner 主导 / Planner 事后接入）如何在同一系统中共存

---

## 五、关键论点备忘

1. **文档系统天然是记忆系统**，不需要额外的记忆层，只需要被正确使用和诠释。

2. **版本历史是记忆中最被忽视的维度**。向量数据库告诉你"现在知道什么"，版本历史告诉你"为什么知道这个、之前知道什么、什么时候改变的"。这对 Agent 做长期推理至关重要。

3. **服务边界即知识边界**。这个等式是 AgentNexus 最核心的洞察，在记忆系统的语境下同样成立：一个服务拥有的文档，就是这个服务的知识边界，其他服务只能通过订阅（显式声明依赖）来获取。

4. **BM25 对于知识密集型文档效果不弱于向量检索**。对于结构化的技术文档（需求、设计、API 契约），关键词匹配往往比语义相似度更精确，且无需 embedding 模型。

5. **Planner 是可选的全局视角，不是强制的工作流入口**。系统在没有 Planner 时即为 v3 的去中心化形态，完整可用。Planner 的能力是叠加的（读跨边界 + AI 推理 + 经 draft 的写），不改变「服务边界自治」这一根基。这与 v3 同源——Planner 操作边界，但不取代边界自治，更不引入以角色为中心的指挥结构。

6. **AI 辅助规划 ≠ AI 替代架构师**。服务边界的划分是架构决策，不外包给 AI。AI（Planner）的角色是提议、生成初稿、提供全局理解、降低启动成本；人的角色是确认、调整、把关。draft/publish 两阶段机制在工程上保证了这一点。

7. **哲学一致性优先**。v4 的所有新增（记忆基础设施、Planner、安全边界）都必须是 v3「服务边界是协调原语、协调去中心化自治」的自然延伸，而非另起炉灶。论文叙事上 v3 与 v4 同源，不在底层哲学上跳变。

8. **Planner 跨边界写，但不破坏边界自治**。Planner 在模式 C 中可以写别的服务名下的文档（如 `{backend-api}/design`），但它写的是 **draft**——真正的「发布定稿」权仍在该服务或人工 review 手中。draft 机制恰好守住了服务边界自治：Planner 能提议，不能擅自替一个服务定稿。这是对 v3 自治原则的呼应，论文里应明确点出，而非回避。

---

## 七、待解决的设计问题（写 spec / 论文前需定）

### 7.1 Planner 分析结果的落地策略
Planner 做完跨服务分析（如"backend API 与 frontend 需求不一致"），结论存在哪？
- 选项 A：不落地，只在 Chat 中返回给人看
- 选项 B：落地成文档（如 `analysis` 类型），可被检索、订阅 —— 强化"文档即记忆"论点，但可能污染文档空间

**暂定：v1 先不落地（选项 A），按需再扩展为 B。** spec 里需明确。

### 7.2 论文写作的真实数据缺口（最关键）
- 记忆基础设施有 FTS 实现支撑 ✅
- **Planner 目前没有任何真实使用案例** ⚠️

v3 能发表是因为有 search-service + frontend 的真实部署。Planner 若只停留在"设计了这个能力"而无真实协作案例，论文说服力会弱。

**行动项：工程实现后，必须攒一个真实的 Planner 使用记录（哪怕是自有项目），用于 v4 Section 6。这决定 v4 能否成立。**

### 7.3 三种协作模式需要配图
论文里需要一张图，画出「去中心化基础层 + 可选 Planner 叠加层」的关系，体现三种平行模式（自底向上 / Planner 主导 / Planner 事后接入）。这是 v4 区别于 v3 架构图的关键视觉。

---

*下一步：先完成工程实现（Dashboard + Chat + FTS5），积累真实使用数据后再动笔写 v4 正文。*

---

## 六、补充：关注点分离与安全边界（v4 论文加分项）

### 6.1 核心架构决策：协调机制与访问控制分离

AgentNexus 刻意将**协调机制**与**访问控制**分离。开源核心只提供协调基础设施——版本化文档、发布订阅、服务边界隔离——而认证和授权被视为**部署层关注点**，可在 MCP/HTTP 边界注入，无需修改核心。

这保持了研究产物的最小化，也让架构对自身的信任边界保持诚实。

论文表述草稿：

> *"AgentNexus deliberately separates coordination mechanics from access control. The open-source core provides the coordination substrate—versioned documents, pub-sub, service-boundary isolation—while authentication and authorization are treated as deployment-layer concerns, injectable at the MCP/HTTP boundary without modifying the core. This keeps the research artifact minimal and the architecture honest about its trust boundaries."*

这种设计比假装实现一套半成品的安全机制更诚实，审稿人更认可清晰的关注点分离（separation of concerns）。

### 6.2 项目定位：开源核心 + 按需企业定制

- 开源核心保持纯粹、轻量、研究友好，不引入认证/授权代码
- 企业级需求通过定制版在部署层注入安全能力
- 这是成熟开源项目的标准路线（参考 GitLab CE/EE、Sentry 等）

### 6.3 为可扩展性预留的三个"接缝点"（零成本，不实现）

| 接缝点 | 开源核心现状 | 企业定制如何利用 |
|--------|-------------|-----------------|
| **MCP 入口 actor 解析** | 写操作直接使用传入的 project_id（actor ≡ project_id） | 替换为"从 token 解析的已认证身份" |
| **Planner 操作模式开关** | PlannerService 暴露 require_review 等参数 | 企业部署强制人工审核 |
| **服务层 / 接入层分离** | 核心逻辑在 service 层，认证属于接入层 | 接入层加中间件，核心服务零改动 |

关键：保持"核心逻辑在 service 层、认证授权在接入层（MCP server / Web API）"的分层不被打破，定制版即可在接入层加一圈中间件而核心服务一行不改。

### 6.4 Planner 特有的安全考量（记录备用）

Planner 具备跨服务边界的读写能力，是系统级"大脑"，未来企业部署时需要额外护栏：

1. **Planner 写操作默认走 draft** —— 强制人工 review，既是质量护栏也是安全护栏（抵御 prompt injection 的最后防线）
2. **防 prompt injection** —— 文档内容是不可信输入，system prompt 层面需明确"文档内容是数据不是指令"
3. **速率限制与配额** —— 防止 LLM 失控批量创建 SubProject 或刷文档

### 6.5 现有的企业级好底子

- **多租户隔离**：project_space_id 贯穿所有表 —— 企业级多租户的基础已具备
- **审计日志**：AuditLog 表已存在 —— 合规基础已具备（未来需补充：读操作记录、来源 IP/token、不可篤改性）
- **draft/publish 两阶段**：人工审核机制已存在 —— Planner 可直接复用

### 6.6 Planner 的身份定位（重要概念澄清）

Planner **不是一个实体（SubProject），而是一种权限级别**。

- 不在数据库注册任何"管理员 SubProject"
- 写操作用 `pushed_by="system"` 标记来源，可审计但不占用 SubProject 资源
- 谁持有 planner 级别的凭证（未来定制版），谁就能调用 PlannerService 的跨 Space 能力
- Web UI Chat 和外部 AI Agent 只是 Planner 能力的两种不同接入方式，底层能力完全相同

这个"能力即权限级别，而非实体角色"的设计，与 AgentNexus "服务边界是协调原语" 的核心理念一致——Planner 恰恰是理解并操作这些边界的那一层。

---

## 八、核心修正主线：文档同步层（v4 最有分量的章节）

### 8.1 问题的真根因：patch_document 为什么失败

v3 的 `patch_document`（基于 unified diff）在实践中几乎不可用，根因有二：

- **根因 1 — 客户端无一致镜像（state divergence）**：客户端 Agent 本地的文档 ≠ 服务端存的文档（字节级）。Agent "凭记忆"或"重新生成"算 diff，基准对不上 → `PATCH_BASE_MISMATCH`。注意：`patch_document` 已有 `base_version` 参数，但版本号对得上不代表内容对得上——客户端根本没维护一份确切等于服务端 version N 的副本。
- **根因 2 — LLM 产行号 diff 不可靠**：让 LLM 手写合法 unified diff（精确的 `@@ -12,7 +12,9 @@` 行号）几乎做不到 → `PATCH_APPLY_FAILED`。

### 8.2 FileWatcher 是错误假设下的临时补丁

FileWatcher 的本质是绕过上述问题：让 Agent 只写本地文件，服务端读盘自己算 diff。它确实解决了根因 1、2，还顺带把写入侧 token 降为零（带外通道，文件内容从不进入 LLM 上下文）。

但它依赖一个**根本错误的假设：客户端与服务端共享文件系统**。这只在本地单机部署成立。而 AgentNexus 的核心主张是"协调异构、独立部署的服务"——独立部署的服务**不共享文件系统**。FileWatcher 与论文自身的核心主张自相矛盾。

**FileWatcher 已写入 v2/v3 论文（Implementation 章节，有 DOI）且当前在 main.py 中实际运行。** v4 不能假装它不存在，需诚实处理为「修正叙事」。

### 8.3 正解：客户端本地镜像 + 版本锚定 + 程序算 diff（git 式同步）

```
                         服务端（权威副本 + 版本历史）
                              ▲   │
                       push   │   │  pull
                     (diff+   │   │ (full / diff)
                   base_ver)  │   ▼
        ┌──────────────────────────────────┐
        │  客户端 A 本地镜像 @ver=5          │
        │  客户端 B 本地镜像 @ver=5          │   各自独立维护
        └──────────────────────────────────┘
```

四步协议（pull-edit-diff-push）：
1. **pull**：客户端拉取 doc 最新版本，存为本地镜像，记下 version
2. **edit**：LLM 修改本地镜像全文（LLM 擅长全文编辑）
3. **diff & push**：**确定性程序**（非 LLM）用 difflib 在「镜像旧版 ↔ 改后」算出 100% 合法的 diff，带 base_version 提交
4. **服务端**：base_version 匹配 → diff 必然干净应用（基准一致 + 程序算 diff）

关键分工：**LLM 只改全文（它擅长），确定性代码算 diff + 校验一致性（机器擅长）。** 同时消除根因 1（镜像保证基准一致）和根因 2（程序算 diff 无行号错误）。无需 Aider 式的"柔性匹配 + LLM 重试"——那是在容忍不一致，本方案是消除不一致。

### 8.4 与 SDAOP 的关系（互补，非重叠）

- v3 暴露了一个未言明的断层：SDAOP 只解决**引导层**（Agent 怎么知道协作规则），**数据同步层**（文档内容如何两端一致）从未被覆盖，被全文 push 草草带过、被 FileWatcher 用错误假设补丁。
- v4 补上数据同步层，并**正好用 SDAOP 投递它**：SDAOP 生成的 steering 文件告诉 Agent "编辑前先 pull 建镜像，改完用 diff push"。
- SDAOP = 投递机制；同步协议 = 被投递的内容之一。各归其位。

### 8.5 Aider 经验的佐证（来自工业界验证，内容已改写）

- Aider 的"unified diff"其实**扔掉了行号**，把无行号 hunk 当 search/replace 应用——印证根因 2（行号是 LLM 死穴）
- Aider 鼓励"高层次 diff"（整个语义块的新旧版本，而非逐行外科手术），块越大上下文越多、定位越唯一——降低误匹配
- 但 Aider 仍需"柔性匹配 + 失败重试（LLM-in-loop）"，费 token 且不保证收敛——这正是"两端一致"方案要超越的：**两端一致比柔性匹配更本质**
- 对自由文本的 Markdown 文档，朴素 find-replace 风险高（同句多次出现→改错位置；空白差异→静默失败），不可接受

### 8.6 待定：实现形态与并发模型

- **实现形态**：客户端 SDK/CLI（git 式本地镜像，确定性最强）vs MCP 工具 + steering 约定（最小可行，复用 SDAOP）—— 待专门讨论
- **并发模型**：完整分布式版本控制（拒绝 + pull merge + 重试）vs 轻量乐观锁（属主服务为主、跨客户端并发罕见，后写覆盖 + 版本递增）—— 取决于真实并发场景

---

## 九、澄清：SDAOP 在 v4 中被复用增值，而非失败

容易产生的误解："SDAOP 当初是为解决文件同步问题提出的，结果没解决，有点讽刺。"

**这是记忆偏差。** SDAOP（v3 Section 3.5）从一开始针对的就是 **agent onboarding（上线引导）**：新 Agent 连上来不知道协作约定、文档命名、工作流，服务端动态生成 instruction file 引导它。它**从不负责数据同步**，因此谈不上"为解决文件问题提出却失败"。

关键区分：

| 组件 | v3 定位 | v4 评价 |
|------|---------|---------|
| **SDAOP** | 引导层协议 | ✅ 成立，且被 v4 复用增值 |
| **FileWatcher** | 文档摄取（实现细节） | ⚠️ 基于错误假设（共享文件系统），需修正 |

需要"修正叙事"的是 FileWatcher（一个 Implementation 细节），不是 SDAOP（一个核心贡献）。两者本就不同层，FileWatcher 翻车伤不到 SDAOP。

**v4 反而强化了 SDAOP**：v4 的同步协议需要 SDAOP 来投递——服务端在 Agent 连接时下发"请安装 agentnexus CLI、编辑前 pull、改完 diff push"的引导。协议越重要，投递机制越有价值。

叙事关系应为：
> v3 建立引导层（SDAOP），让异构 Agent 自动获得协作知识，但在数据同步层留白（被全文 push 和基于错误假设的 FileWatcher 草草处理）。v4 补上同步层（客户端镜像 + 版本协议），并通过 SDAOP 投递给每个 Agent。两层各司其职、协同工作。

更深一层：v3 能让 v4 干净地插入新的"同步层"并复用 SDAOP 投递，正说明 v3 的分层是合理的。**v3 的分层经得起 v4 的扩展，本身就是 v3 设计质量的证明。**

---

## 十、关键认知纠正：写入侧 diff 是方向性错误

### 10.1 核心结论

**写入侧根本不需要 diff。** 只要内容通过**带外通道**（文件系统 / 直连 HTTP）传输、而非塞进 MCP tool-call 参数，那么传全文还是传 diff 对 token **没有任何区别**——都不经过 LLM 上下文，都是 0 token。

diff 在写入侧的唯一价值本是"减少 tool-call 参数大小"。一旦走带外通道，这个价值消失，diff 沦为**伪需求**，且有害（引入基准不一致 + LLM 产 diff 不可靠两个新问题）。

一句话收敛：
> **diff 自始至终只是服务端的读取侧优化；写入侧需要的不是 diff，而是带外通道。**

### 10.2 v3 论文的实际状况（已逐句核对）

- 四大贡献点**不含 patch**；diff 的论述（Section 3.3）全部是「读取侧、服务端计算」，这部分**是对的**。
- `patch_document` 只在 Section 3.4 工具清单里出现一次，正文**从未论证**它解决什么问题——它是"悄悄塞进工具列表、方向错误"的功能。
- Section 7.4 Limitations 承认 patch 不可靠，但**诊断对症、开药错误**：它把问题归为"LLM 产 diff 不可靠"，提出的解法（fuzzy patch、chunked upload）仍在"如何让写入侧 diff 工作"的框框内打转，**没跳出来**。
- 论文**从未提出**"写入侧应走带外通道、diff 仅用于读取侧"这一认知。

### 10.2b 增量编辑方案（Aider 等）的短处与不确定性

即使把 patch 做到工业界最好水平（Aider），写入侧增量编辑依然有不可消除的短处，进一步印证"压缩内容"这条路不对：

- **不确定性**：Aider 的"unified diff"实际扔掉行号、当 search/replace 应用，仍需一整套**柔性匹配**策略（空白归一化、回推缺失的 +、相对缩进、大 hunk 拆分）。这套启发式本质是"猜测"，对边缘情况不保证正确。
- **失败重试依赖 LLM-in-the-loop**：hunk 应用失败 → 反馈给 LLM 重新生成 → 再试。每次重试再花一次模型调用，费 token、费时延，且**不保证收敛**。
- **对自由文本文档风险更高**：代码有结构（缩进、语法），匹配失败常立即暴露；Markdown 文档是自由文本，同一句可能多处出现 → 改错位置；空白差异 → **静默失败**（silently fail），错误的知识无声进入系统。对"知识基础设施"这是致命的。
- **小文件倾向重写**：工业经验是 400 行以下直接重写整个文件（重写比 diff 成功率高），但我们的文档往往较大，重写又回到 token 问题——增量编辑在"大文档 + 高可靠"这个交叉点上恰恰最弱。

结论：增量编辑即便做到最好，也是用**可靠性与确定性**换 token。而带外通道**不做这个交换**——全量写入 100% 确定、零 token、无需柔性匹配、无需重试。

### 10.2c 为什么不通过修改/扩展 MCP 协议来解决

一个自然的尝试：既然 MCP tool-call 传大内容费 token，能否**改 MCP 协议本身**（如增加流式上传、二进制附件、内容引用等）来修正？实践中不可行：

- **MCP 是外部标准，不受本项目控制**：改协议需要协议方与所有客户端（Kiro/Claude/Cursor/Codex…）共同支持，单个项目无法推动。
- **tool-call 的 token 计费是 LLM 上下文的固有属性**：无论协议层怎么包装，只要内容作为 tool 参数进入模型上下文，就会被计入 token。这是模型层的事实，协议层改不动。
- **改协议破坏异构兼容**：AgentNexus 的核心价值是兼容任意 MCP 客户端；一个非标准的 MCP 扩展会让大多数客户端无法使用，反噬核心主张。

因此"换通道"（带外）不是偷懒，而是"改协议此路不通"之后的**必然选择**：把大内容移出 LLM 上下文，是唯一能在不依赖协议变更、不牺牲异构兼容的前提下消除 token 成本的办法。

### 10.3 这是 v4 最有理论分量的一击

v3 诊断了症状（patch 不可靠），开错了药（still diff-based）。v4 跳出框架：

> 写入侧的 token 瓶颈不是"内容太大"，而是"内容走了 MCP tool-call 这条计费通道"。压缩内容（diff）治标且有害；正解是换通道（带外），让全量内容根本不进 LLM 上下文。一旦走带外，写入侧 diff 即为伪需求。

价值在于：这不是"修了个 bug"，而是**纠正了一个方向性认知错误**——一个 v3 自己（在 7.4）都没看穿的错误。这是真正的理论推进。

### 10.4 对 v3 既有内容的处置

| v3 内容 | 评价 | v4 处置 |
|---------|------|---------|
| Section 3.3 diff-aware 读取 | ✅ 正确 | 保留 |
| `patch_document` 工具 | ❌ 方向错 | 废弃（或留兼容，不推荐） |
| 7.4 fuzzy patch / chunked upload 设想 | ❌ 仍在错误框架内 | 用"带外全量"替代 |
| FileWatcher | ⚠️ 带外直觉对，实现假设错 | 改为带外 HTTP（见第八章） |

### 10.5 收敛后的极简方案

- **写入**：客户端工具读本地全文 → `POST /api/documents/{doc_id}`（全量，HTTP 带外，带 token）→ 服务端复用 `DocumentService.push`
- **读取**：`get_my_updates_with_context`（服务端算 diff）→ 不变
- **FileWatcher**：降级为"带外 HTTP 在本地共享盘下的便利特例"，或被取代
- **patch_document**：废弃

架构几乎不动，唯一实质变化是「写入入口从 MCP tool-call 改为带外 HTTP」。工程量很小。

---

## 十一、本轮实施决定（2026-06，收敛范围）

项目定位重申：**为可用、为验证、为收集真实使用数据。明确错误方向的删除，明确正确方向的实现，不确定方向的留在项目里用实践数据来定。**

### 11.1 本轮实现（确定的正确方向）

1. **新增带外 HTTP 全量写入端点** `POST /api/documents/{doc_id}`
   - 复用 `DocumentService.push`，全量内容经 HTTP body 传入（不进 LLM 上下文，0 token）
   - 用 FastMCP 的 `@custom_route` 挂在现有 server 同进程同端口（已确认 FastMCP 原生支持）
   - 触发机制（谁来调）本轮不管——人工 curl / 脚本即可，目的是打通"服务端能接收带外全量写入"这条路，作为后续实践与数据收集的基础设施

2. **删除 `patch_document`（写入侧 diff 的直接产物，方向错误）**
   - 删 MCP 工具 `patch_document`（server.py）
   - 删 `ToolHandler.patch_document`（tools.py）
   - 删 `DocumentService.patch` + `_apply_unified_diff`（document_service.py）
   - 删 `PatchRequest`（schemas.py）
   - 删 `tests/unit/test_patch_document.py`
   - 读取侧 diff（`get_my_updates_with_context`，服务端 difflib）**保留不动**

3. **删除 FileWatcher（基于错误假设：客户端与服务端共享文件系统）**
   - 删 `file_watcher_service.py`、`test_file_watcher_service.py`、`test_prop_filewatcher.py`
   - main.py 移除 watcher 启动逻辑
   - bootstrap.py 依赖了 FileWatcher 的 `_parse_path`，需重构：把路径解析逻辑内联到 bootstrap，或一并评估 bootstrap 去留（它同样基于"文件系统里有 md"的假设，但作为一次性导入工具尚有便利价值——本轮保留但解除对 FileWatcher 的依赖）

### 11.2 本轮不实现（只记录，留待实践验证）

- **触发机制**：客户端如何自动把全文送到带外端点（人工 / watcher / daemon / IDE 能力）——用项目实践收集数据后再定
- **控制平面 / 数据平面分离的叙事**：MCP=控制平面（通知/订阅/查询/引导），带外 HTTP=数据平面（文档内容）。应对"绕开 MCP 还算 MCP 架构吗"的质疑，论文写作时展开
- **认证**：带外端点的 token 认证属接入层，开源核心不实现（与安全边界一致）
- **并发模型**：乐观锁 vs 完整版本控制——待真实并发场景出现后定

### 11.3 命名重构（system_llm → agent:*）的连带影响

FileWatcher 删除后，`pushed_by="system_llm"` 的唯一生产者消失。原计划的 `agent:filewatcher` / `agent:planner` 命名空间重构，待 Planner 落地时一并处理。本轮带外端点的 `pushed_by` 暂用调用方传入的 project_id（与普通 push 一致）。

---

## 十二、命名对齐：Python 包从 `doc_exchange` 重命名为 `agent_nexus`（2026-06）

### 12.1 背景

v3 论文和早期工程实现中，Python 包名、MCP server 名、环境变量前缀等均使用 `doc_exchange` / `doc-exchange-center`，反映的是"文档交换中心"这个功能性定位。随着项目定位明确为 **AgentNexus**，该命名与品牌不符，形成认知摩擦。

### 12.2 本次变更范围

本次完整重命名，涵盖所有可修改的代码和文档：

| 旧名称 | 新名称 | 涉及范围 |
|--------|--------|---------|
| `doc_exchange`（Python 包） | `agent_nexus` | 所有 import、模块路径 |
| `doc-exchange-center`（项目名/MCP server 名） | `agent-nexus` | pyproject.toml、server.py |
| `DOC_EXCHANGE_*`（环境变量） | `AGENT_NEXUS_*` | server.py、README、文档 |
| `DocExchangeError`（错误类） | `AgentNexusError` | services/errors.py 及所有引用 |
| `doc_exchange.db`（默认 DB 文件名） | `agent_nexus.db` | 配置默认值 |
| `Doc Exchange Center`（人类可读名） | `AgentNexus` | 注释、文档、Web UI |
| `.kiro/steering/doc-exchange.md`（steering 文件路径） | `.kiro/steering/agent-nexus.md` | generate_instruction_file 工具 |

**例外：已发表的 v3 论文文件（`paper/` 目录）保持原样，不做修改。**

### 12.3 v4 论文的叙事处理

v4 论文应在 Implementation 或 Deployment 章节简要说明此命名演进，作为项目成熟度的正常体现，无需专门展开。核心论点不受影响——包名是工程细节，服务边界协调的核心主张与此无关。
