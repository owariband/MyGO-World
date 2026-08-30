# MVP 使用显式 ModelGateway 而非 PydanticAI

MVP 保留 Python、Pydantic v2、SQLite、SQLAlchemy 2.x 和 Alembic，但不依赖 PydanticAI。Character、Director 和 Broadcast 都通过项目自有的窄 `ModelGateway` 发起一次性结构化生成；FixtureGateway 与首个真实 ProviderGateway 实现同一接口，领域层显式负责 Prompt 构造、Pydantic Schema 校验、一次语义修复、超时、传输重试、并发限制、用量统计和 Generation Trace。

首期没有模型工具调用、框架 Memory 或内部 Agent Loop，PydanticAI 提供的抽象不足以抵消其对消息构造、重试和结构化修复边界的遮蔽。领域请求与响应不依赖 Provider SDK；未来需要多 Provider 或工具调用时，可以在不改变 World Runtime 的前提下新增 PydanticAI Gateway 或其他适配器。

首个真实适配器使用 OpenAI-compatible API，并通过进程环境配置 Base URL、API Key 和模型名。Provider 支持 JSON Schema 时优先使用原生结构化输出；不支持时接受 JSON 文本并交给同一 Pydantic Schema 校验，仍只允许一次带诊断的语义修复。

默认自动化测试只使用 FixtureGateway；真实模型链路必须显式触发并沿用请求预算，不进入默认 CI。MVP 验收仍要求至少运行一次双 Character、Director、Broadcast、真实素材校验和 WebGAL 脚本生成的完整 Live 流程，而不是只做 Provider 连通性测试。
