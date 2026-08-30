# Event Session 本身构成互动边界

MVP 不建立独立的 Interaction Group 实体；Event Session 的固定参与者集合与 Interaction Scope 共同定义互动边界。Runtime 根据待提交的角色位置或互动关系变化计算成员分区：若边界改变，则在同一提交中以 `partitioned` 关闭原 Session，并为每个分区创建带父 Session 引用的后继 Session，单人分区同样有效。

Location 是具有稳定 ID 和 Entity Revision 的持久 Entity；它可以拥有多个带稳定 `scope_key` 的 Interaction Scope，并以显式边定义 Scope 之间的可达关系，MVP 不实现坐标或寻路。Character 的当前位置由 `location_id + scope_key` 表示，Interaction Scope 本身只是 Location 拥有的值对象。

所有 Session 统一通过关闭转换结束，并持久化 `partitioned`、`resolved` 或 `limit_reached` 原因。Director 可以提议 `resolved`，Runtime 负责验证并执行状态转换；模型提案本身不能改变 Session 生命周期。

Session 只由初始场景种子或已提交的直接互动行为开启；角色仅仅处于同一 Interaction Scope 不会自动加入。新角色与已有 Session 成员发生直接互动后，Runtime 关闭受影响的旧 Session，并创建记录多个 `parent_session_ids` 的合并后继 Session，而不是原地修改参与者集合。

Director 只有在至少完成一轮且不存在未解决的直接回应、正在进行的动作或关键承诺时才能提议 `resolved`；Runtime 负责校验这些条件，并以最大轮数、最大耗时和失败上限提供 `limit_reached` 兜底。

`utterance.addressee_ids` 只标记主要接收者和待回应关系，不表示私密通道；同一 Interaction Scope 内的其他角色同样可以感知，MVP 不支持耳语。移动在当前 Wave 中按实际事件时间参与感知投影；Runtime 在事务最终提交前根据 Wave 结束位置计算分区，并将角色位置、旧 Session 关闭、全部后继 Session 创建与确定性入队一并原子提交，事务完成后才对外可见。
