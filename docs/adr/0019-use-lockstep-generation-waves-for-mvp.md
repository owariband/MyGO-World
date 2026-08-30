# MVP 使用 Lockstep Generation Wave

MVP 不引入角色独立时钟、进行中行动或事件驱动调度；每个 Generation Wave 从共同 World Version 和 World Time 开始，并在该轮全部行动完成后，以最晚结束时间作为统一屏障再进入下一 Wave。提前完成的角色在剩余时间隐式空闲，Director 不得替其生成填充行为，角色也不得在下一 Wave 追写这段空档。

共同 World Time 是所有 Character 的观察和决策截点，不要求其提案在同一瞬间执行。Director 可以在当前 Wave 内为已有提案安排执行顺序、开始时间、结束时间和客观结果，但不能借此改变提案意图或让角色回应同一 Wave 中尚未看到的内容；Runtime 校验时间范围、因果关系和角色所有权。

World Event 仍保存语义时间与因果引用，因此同一 Wave 内的事件可以有先后或重叠；该限制牺牲长行动期间的即时反应能力，以换取可判定的共同 Snapshot、原子提交和简单重放。后续若需要异步行为，可以在不改写既有 Ledger 的前提下增加角色可用时间、进行中行动和事件驱动调度。
