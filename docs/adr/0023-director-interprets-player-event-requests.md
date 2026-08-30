# Director 解释玩家外部事件请求但不提交事实

第二阶段允许玩家用自然语言提出版本绑定的 Player Event Request；Director 将其解释并补全为带来源证据的 External Event Candidate，Segment Validator 检查意图保真、版本、实体、角色所有权、时间与因果，只有 World Committer 能将通过校验的结果提交为世界事实。环境与无主体事件可以在合法时按玩家要求发生，但请求不能借“外部事件”绕过 Character Action Proposal 强制持久 Character 作出重要选择、说出台词或改变动机；MVP 只保留无主体事件和通用来源证据的兼容 seam，不实现玩家输入入口、存储或自然语言解析。
