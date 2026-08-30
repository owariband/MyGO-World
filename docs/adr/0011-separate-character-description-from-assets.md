# Character Skill 与 Asset Manifest 分离

Character Skill 使用自然语言描述角色的人格、动机、关系、形象和表演倾向；具体 Live2D、表情、动作和背景文件由独立 Asset Manifest 通过稳定领域 ID 映射。Broadcast 只能从 Manifest 提供的合法资源中选择，避免创作文本与易变文件路径耦合，也避免模型虚构素材。

Manifest 是纳入版本控制的人工确认白名单，路径分别相对于 WebGAL `game/background`、`game/bgm`、`game/figure` 等分类根；运行配置提供外部 `game` 目录。扫描工具以后可以生成候选草稿，但不能让未确认文件自动成为 Broadcast 可选素材；Live2D 以 `model.json` 为加载单元，并从中校验 motion 和 expression 能力。
