# 分离世界补全与演出编排

Director 只负责根据 Character Agent 提案补全时间、因果关系和 Event Session 生命周期，Broadcast 只根据已提交的世界历史剪辑并生成结构化 BroadcastPlan；Render 再由确定性的 RenderPlanner 与 RenderCompiler 形成。两者都使用真实模型，但 Broadcast 的演出需求不能反向新增或修改世界事实，从而保持 World Ledger 的权威性。
