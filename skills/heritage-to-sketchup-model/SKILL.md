---
name: heritage-to-sketchup-model
description: Build or revise Chinese traditional and heritage-style architectural SketchUp models from CAD, reference images, or existing SKP files. Use for courtyard timber frames, roof and eave junctions, gables, tilework, reference-driven detailing, and construction-stage scenes. Ordinary modern-building work stays with its existing skill.
---

# 古建与仿古建筑 SketchUp 建模

独立复制自 CAD-to-SketchUp 911。本版本经用户同意发布为实验版；尚未做新模型运行回归，不声称已有参数化古建构件库。不得修改或加载现代版 skill 内的运行文件来完成本分支任务。使用本目录自带脚本；年代化 schema 名属于继承协议，不是唐代依据。

## 接续与分流

先读项目状态、用户最近确认和当前 SKP 路径。恢复已批准的范围和审核方式，不重复询问；仅缺少审核选择时询问人工或用户已有 Agent，默认不另行调度 Agent。

依据资料条件选择流程，建筑风格另行判断：
- 完整图纸复原：使用 [图纸模式](references/heritage-modes.md)，再按原 reading → topology → production → QA → delivery 流程执行。
- 平面加效果图推演：使用 [效果图模式](references/heritage-modes.md)，明确实测与推定，不要求不存在的立剖面通过图纸闸门。
- 既有模型局部修订：使用同一文件的增量模式；仅核对本次范围和关联交接，不重新全院读图。

读 [构件及交接](references/heritage-junctions.md) 处理屋面、梁架、墙体和门窗；做标记或动画时再读 [标记与场景](references/heritage-animation.md)。控制 SketchUp 前读 [执行](references/sketchup-execution.md)。提交结果前读 [专项审核](references/heritage-review.md)。

## 共同约束

1. 为位置、标高、屋顶形态、材料分别确定资料权威。用户明确修正优先；参考模型只能补缺，不自动取代主效果图。AI 图像不是历史证据，异视角冲突需记录并选定一致解释。
2. 区分实测复原、仿古设计与视觉推演。历史构件名称、年代与造型拿不准时查可靠来源；避免把晚期构造直接称作唐代特有。
3. 先整体比例，再典型开间、山面与高低交接，通过相关核对后批量推广，最后做纹样材质。只在后续改动推翻样板依据时重审。
4. 每个改动追踪所属建筑、构件、来源、连接对象和受影响的系统。修改构件族时同步检查同类部位；依赖范围不等于全模型重建。
5. 保留用户编辑与旧版本；脚本成功、实体闭合或截图生成均不等于建筑合理。未核实的视觉相似度不填百分比，不把推定称为结构工程认证。
6. 标记从初次建模就指定，后续新增沿用；场景和几何都属于交付对象。

## 资料加载边界

复制保留的旧 contracts、tests、scripts 用于完整图纸路径；只有在任务匹配时才读取旧阶段文档。效果图/局部模式采用专项审核记录，不伪造旧合同的 PASS，也不随意放宽原严格校验器。具体项目参数写入项目状态，不写成全局古建默认值。
