# 关键机制

## 0. 双 Runtime 边界：世界运行时与演出运行时

问题不是把 WebGAL 改造成 Agent 世界，而是让两个不同运行时通过稳定产物衔接：

```text
World / Agent Runtime
  ActorPerformance -> Director Completion -> validated WorldSegment -> WorldEvent
  WorldEvent -> Broadcast Agent -> BroadcastPlan -> deterministic Render Planner -> RenderJob
                         |
                         v
Render Plugin / Adapter
  RenderJob -> validate -> compile -> queue -> WebGAL DSL
                         |
                         v
MyGO / WebGAL
  scene / sentence / animation / audio / viewer playback
```

MyGO/WebGAL 内部已有 Sentence 推进、Auto/Fast Timer、Pixi 动画帧和每秒 `SYNCFC`，但这些只服务演出。它没有可供多 Agent 世界复用的权威 `world_time / tick_id / world_version`。因此 World Clock 必须由外部 Runtime 持有。

这里的“动态编译 Agent Runtime 产物”分成两步：Python + LangChain Core Runtime 内的 Broadcast Agent 只输出 strict `BroadcastPlan`，确定性 Render Planner 结合已提交 Event Log 生成 `RenderJob`；外置 Render Plugin/Adapter 再校验并编译成 WebGAL DSL。MyGO/WebGAL 底座只执行 DSL 和呈现演出。Planner/Compiler 都不进入世界权威，也不要求侵入原引擎。`RenderArtifact` 是这类演出投影产物的总称，一期跨进程契约统一使用 `RenderJob`。当前只实现 NPC DIY PoC，Broadcast 与该跨进程链仍是目标设计。

### 为什么不能用 Sentence 当 Tick

- 一次点击可能只结束当前演出，也可能推进一条或多条 `-next` 命令；
- Auto/Fast 由用户速度和阻塞演出控制；
- 不同文本长度导致不同播放时间；
- 历史回放、暂停和黑屏会改变 Viewer Time，却不应改变 World Time。

### 为什么不能用 `SYNCFC` 当 Tick

`SYNCFC` 是播放器每秒上报 Scene/Sentence/Stage 的心跳，用于观测播放和近似确认 Render 开始/完成。网络抖动、后台降频或断线都会改变它的频率，因此它只能推进 Viewer Cursor，不能提交世界事实。

### 稳定接口

World Runtime 向 Render 侧发布不可变 `RenderJob`：

```text
schema_version / producer_id / world_id / runtime_session_id
render_id / event_session_id / event_revision / attempt_id
based_on_world_version / log_seq_start / log_seq_end
evidence_transaction_ids / title / location / characters
structured_beats / estimated_play_ms / content_hash
```

Render 侧回传的只是：

```text
attempt_id / render_id / event_session_id
accepted / validation_error / ready / playing / completed / failed / unknown
error / viewer_cursor
```

这些状态不能反向将未发生的 Event 写进 World Ledger。

### 生成完成驱动的 Actual Runtime

模型推理、工具调用和 Director 补完的真实耗时可以成为 World Time，但它们不是一个独立仿真 timer 的旁路输入。Agent 尚未返回时，Runtime 不会自行提交“排队结束”“咖啡煮好”或其它故事事实；否则 Character 输出一返回就会面对一套未经它参与、已经推进到未来的世界。

一次生成波次采用 post-hoc temporalization：

```text
T0: 固定 committed snapshot，开始 Character generation
    -> 记录 Character/tool 的 started_at / returned_at
    -> Director 读取输出与 measured latency，生成 Temporal/Causal Completion
    -> Director 返回后，Temporal Binder 得到本波次完整 elapsed
    -> Validator 检查时间、角色、知识、对象和因果不变量
T1: 原子提交完整 World Segment
```

这不是冻结或忽略 Agent 响应时间：`T1 - T0` 可以包含真实生成耗时。区别是世界状态只在结果已知后被语义化、校验和提交，不由外部 timer 在中途猜测发生了什么。

Director 输出应优先表达相对顺序、并发关系、阶段和可伸缩区间，而不是依赖发起调用时还未知的最终 elapsed。Director 返回后，Binder 才能看见其自身耗时并绑定完整区间。Binder 不发明剧情，只做测量绑定；如何处理超出 Director 建议范围的剩余时间仍是 Open Research。

### Director Completion 与 Broadcast Projection 不同

Director Completion 决定世界里**发生了什么以及这些事情之间如何占据时间**：

```text
角色谁先行动 / 是否并发
排队何时获得推进 / 咖啡何时改变状态
等待是角色选择、环境阻塞还是无叙事信息的 generation gap
哪些输出属于同一个 Event，哪些需要桥接 Event
```

Broadcast Projection 决定这些已提交时间**如何被玩家看见**。Render Compiler 不做“世界经过 4 秒，就插入 `wait 4s`”的机械转换；Broadcast Agent 先形成：

```text
source_interval / source_event_ids
projection_mode: omit | compress | cutaway | montage | summarize | dramatic_pause
target_duration / artistic_reason / evidence_ids
```

只有 `dramatic_pause` 等明确艺术选择会生成等待或停顿命令。被 Director 标为无叙事信息的 generation span 默认在 WebGAL 中不可见；它仍可被保留为 World Time 证据。

相关问题与代价集中维护在[难点、卡点与代价账本](difficulty-ledger.md)，特别是 H-008、H-010、H-016、H-024 和 H-025。

### 实时时钟的可测性

生产运行使用 monotonic clock 测量每次 generation span，避免系统时间跳变；它是测量仪器，不是故事状态的自主推进器。离线测试使用录制好的 Agent Result Cassette 和 Latency Trace。算法需要同时验证：

- Character 输出与不同 latency 组合后，Director 是否能补出自然且自洽的 Segment；
- 同一 Cassette + Latency Trace 是否能重放同一 Transaction/Event；
- 多 Event 并发生成时，共享角色、对象和跨地点因果是否发生冲突；
- Director 自身耗时是否产生无法解释的递归尾巴；
- Broadcast Temporal Projection 是否保留因果与关键信息，而非机械复刻等待。

### Director / Broadcast 反馈防火墙

- Runtime 拒绝的 DirectorProposal 可以在线返回原因，供 Director 改选未来刺激；
- Buffer、编译和播放故障可在线反馈给 Broadcast，只用于重新选择可展示 Event；
- 当前局的热度、弹幕和“高潮评分”默认不能在线反馈给 Director/Character，避免世界围绕注意力奖励持续制造高刺激事件；
- 局后聚合指标可以进入下一局策略评估，但必须与角色自主性、事实准确率、连贯性和多样性联合约束；
- 若玩家输入需要影响世界，必须转成 Runtime 可提交、角色可感知且可拒绝的正式事件。

### 地点事实与信息发现

地点不再只是 Event 上的字符串标签。World 层为每个地点维护稳定身份、版本化 `LocationFact` 和时效性 `LocationInfo`，并通过 `location_id` 查询该地点的 WorldEvent；完整模型见[地点 World Model](location-world-model.md)。

这形成两条不同的一致性链：

```text
LocationFact
  -> Validator 验证 Proposal 是否与当前地点事实一致

LocationInfo / Location Event
  -> Director 选择是否以及怎样创造发现机会
  -> Committer 提交传播 Event
  -> PerceptionProjector 裁剪
  -> Character Observation / Memory
```

因此，“羽丘高中有钢琴”不会被角色随口一句话覆盖；“Tomori 每周六在 RiNG 独自 Live”也不会因为挂在 RiNG 就自动成为所有角色的知识。前者是世界真值，后者是可被查询和传播的地点信息，角色获知仍必须拥有可追溯渠道。

## 1. 零侵入 Plugin Host

兄弟目录 `../MyGO_v3.1.1_ForScript` 只有 WebGAL/MyGO 构建产物，没有适合直接维护的引擎 React 源码。直接 patch 压缩 Bundle 会产生版本漂移和不可审查耦合，也违反复用成熟底层的初衷。

新增独立 Plugin Host，把 WebGAL/MyGO 作为 iframe 或同源子页面中的黑盒播放器：

```text
Plugin Host
├── Event Hub / Black Screen Overlay
├── World Runtime Client / Viewer State
├── Render Compiler / Queue
├── WebGAL Adapter
└── Original MyGO/WebGAL Player
```

Plugin 只从外部 World / Agent Runtime 接收不可变 RenderJob；WorldEvent 只在 Python Runtime 内供 Broadcast Agent 与 Render Planner 使用，不跨越 Plugin 边界。Plugin 不在浏览器内成为世界权威，不读取内部 Redux，不使用 WebGAL Backlog 保存 Event Log，也不要求原引擎新增命令。原静态作品入口保持可独立运行。

Render Adapter 固定放在本工程的 `extensions/dynamic-render/`，Python World / Agent Runtime 固定放在根级 `agent_runtime/`；新功能不得散落进第三方 Bundle。

## 2. WebGAL 黑盒适配

当前 MyGO `3.1.1` / WebGAL `4.5.19` Bundle 在 HTTP(S) 页面会连接：

```text
ws(s)://<same-origin>/api/webgalsync
```

| command | 名称 | 当前行为 |
|---:|---|---|
| `0` | `JUMP` | 跳到可读取的 Scene/Sentence；普通跳转会重置状态 |
| `1` | `SYNCFC` | 每秒上报 Scene、Sentence 和完整 Stage |
| `3` | `EXE_COMMAND` | 在当前现场叠加执行 DSL |
| `6` | `TEMP_SCENE` | 接收完整 DSL，强重置现场后播放临时场景 |

可靠兼容的消息外壳为：

```json
{
  "event": "message",
  "data": {
    "command": 6,
    "message": "changeBg:...;\n爱音:...;"
  }
}
```

### 为什么 MVP 选 `TEMP_SCENE`

- 不修改 Bundle；
- 不要求生成固定 Scene 文件；
- 切换后得到干净舞台；
- 符合“WebGAL 是无状态 Render Worker，Plugin 才是权威状态”。

它会清空 WebGAL Backlog、Stage、GameVar 和当前 Scene，因此每个 Render 必须自包含背景、BGM、人物、位置、表情和 Beats。

### 协议风险

- 属于固定版本内部协议，不是稳定公开 Plugin API；
- 没有 request ID、ACK 或错误响应；
- 客户端没有可靠重连；
- 当前协议没有鉴权。

因此必须锁定版本、补契约测试，并保留“虚拟挂载 Render + 重建 iframe”的保守降级路径。

## 3. 结构化动态编译

模型不能直出正式 DSL。Broadcast Agent 输出结构化 `BroadcastPlan`，确定性 Render Planner 生成 `RenderJob`，编译器再执行：

```text
Schema 校验
-> 角色和素材引用校验
-> Motion / Expression 能力校验
-> 文本与命令安全校验
-> WebGAL DSL 编译
-> content hash
-> Ready Queue
```

现有 [Authoring 编译器](../tools/mygo-author-lib.mjs) 已提供 Beat 白名单、素材引用和 Live2D 能力检查，应作为动态编译器基础。

每个 Render 都要显式建立完整现场，禁止依赖上一 Render 遗留的背景、人物或 BGM。

## 4. Render Queue

```text
Event
├── generating render
├── ready renders[]
├── active render
└── consumed renders[]
```

Render 入队前必须保证：

- `based_on_world_version` 仍有效；
- 覆盖的 Event Log 连续；
- `content_hash` 不重复；
- 校验和编译成功；
- 依赖素材全部存在。

基于已被其它提交取代的旧世界版本返回的 Render 直接丢弃。这里是 Render 版本失效，不代表 Character 结果仅因自身推理耗时就自动过期。

## 5. 有 Render 就播，没有就黑

黑屏属于 Host：

```text
目标 Event 有 Ready Render
  -> 覆盖黑屏
  -> 注入 Render
  -> 等待播放器状态变化
  -> 隐去黑屏

目标 Event 无 Ready Render
  -> 保持黑屏
  -> 不注入内容
```

WebGAL 可在黑屏下预热。当前协议没有 ACK，MVP 只能结合连接状态、下一次 `SYNCFC`、Scene/Sentence 变化和超时重建间接确认。

## 6. Event 切换

```text
保存当前 Viewer Cursor
-> 显示黑屏
-> 选择目标 Event Render
-> TEMP_SCENE 注入自包含 Render
-> SYNCFC 确认开始
-> 更新 current_render_id / sentence
-> 隐去黑屏
```

第一阶段只保证切到一个完整 Render，不承诺字符级恢复。`继续观看 / 从未读开始 / 跳到 Live` 应是显式不同操作。

## 7. Event Log 与 Backlog

Event Log 是 append-only 世界事实：玩家不观看时仍可增长，用于摘要、未读数、角色记忆和恢复。WebGAL Backlog 只服务当前 Render 内回看，可被切换清空。

更底层的 WorldTransaction Ledger 才是客观事实源；WorldEvent 是带 evidence 的语义聚合。Agent 对 Event 的感知和记忆是局部投影，不等于全员共享 Ledger。

## 8. 约 30 分钟 Warm-up 与异步补货

本机制解决“Agent 生成比玩家观看慢一拍”的根本错位：作品开播前，Character、Director、Broadcast 和 Compiler 已经真实运行约 30 分钟；开播后玩家消费库存，Agent 流继续在前方生产。

```text
Agent generation ------ committed actor script ------ ready render ------ viewer
                         ^ 世界前沿                 ^ 可播前沿         ^ 观看前沿
```

必须区分：

- `warmup_wall_time`：开播前真实跑了多久，目标约 30 分钟；
- `committed_world_lead`：已提交 World Segment 在世界时间上领先 Viewer 多少；
- `ready_render_playable_time`：Ready Render 按玩家播放速度还能撑多久；
- `production_to_consumption_ratio`：后台每分钟能生产多少可播放分钟。

只有 Event Plan 不是安全库存。要避免卡顿，近端必须已经有完整、已校验的演员剧本和 Ready Render。30 分钟预运行最终能换来多少库存没有固定换算，必须通过多 Event 压测和真实模型 latency 测量。

## 9. 故障与降级

| 故障 | 行为 |
|---|---|
| Agent 失败 | 不发布 Render，保留现有 Queue |
| Render 校验失败 | 记录诊断，禁止进入播放器 |
| 目标 Event 无 Render | 保持黑屏，后台重试 |
| WebSocket 断开 | 覆盖黑屏并重建 WebGAL 实例 |
| 注入后无确认 | 超时后重建实例并重试一次 |
| 迟到 Render | 世界版本不匹配后丢弃 |
| WebGAL 状态异常 | 丢弃其状态，按 Plugin 状态重建 |
| 同步协议升级失效 | 使用虚拟挂载 + iframe 重建 |

