---
status: superseded by ADR-0021
---

# Agent Runtime 采用 Python 技术栈

MVP 的 Agent Runtime 使用 Python、Pydantic v2、PydanticAI、SQLite、SQLAlchemy 2.x 和 Alembic，取代旧 Wiki 中的 Go + Eino 方案。选择 Python 是为了降低模型接入、评测和 Agent 生态集成成本；World、Event、Agent 与 Render 的既有所有权边界保持不变。
