> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# CAD-to-SketchUp 建模交付报告

## 交付状态

- 当前状态：已交付 / 待用户验收
- 交付级别：full_deliverable
- 项目 ID：
- 源图、拓扑、构建、独立 QA 合同及 SHA-256：
- 用户最终确认 ID：
- 交付清单及 SHA-256：
- DELIVERED 收据及 SHA-256：

## 模型

- 活动 SKP 及 SHA-256：
- 生产根组：
- 建模单位：mm
- 楼层、总高度与交付构件范围：

## 输入依据与解释

- CAD 源文件：
- 完整图框与模型驱动视图数量：
- 平面 / 屋顶平面 / 立面 / 剖面视图：
- 控制体量或用户控制线：
- 已使用的缺失信息默认值及依据：
- 平、立、剖冲突及处理记录：无 / 详见

## 独立 QA

- CAD QA / 活动 SKP 导出 / 独立复核派生 ID：
- 对齐容差与最大坐标偏差：
- 每层平面、屋顶平面、四立面、剖面覆盖：PASS / FAIL
- 全部视图漏项数：0
- 全部视图无依据多项数：0
- 轮廓、方向、比例及真实洞口：PASS / FAIL
- 转角线脚、雨棚、屋顶女儿墙、材料边界：PASS / FAIL
- 生产根组外未追踪几何：0
- 未解决问题：无

## 证据目录

- 每视图 CAD JPG：
- 每视图模型正投影 JPG：
- 同坐标叠图：
- 机器结果与独立复核：

只有 `scripts/workflow_gate.py --stage delivery` 返回 `PASS`，且
`scripts/finalize_delivery.py` 已签发当前清单对应的 `DELIVERED` 收据后，
状态才可填写为“已交付”。
