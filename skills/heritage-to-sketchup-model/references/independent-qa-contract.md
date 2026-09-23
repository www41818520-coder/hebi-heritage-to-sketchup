> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# 第四大步：独立质量验证合同

第四步不复用生产建模结论。它用四类相互独立、哈希绑定的证据证明 CAD
与当前活动 SKP 在平、立、剖含义和坐标关系上相符：

```text
independent-qa-derivation -> independent-qa-plan
  -> sketchup-qa-evidence + machine-result
  -> independent-qa-review -> independent-qa
```

## 4.1 独立提取 CAD 期望

从已验证的 `source-index` 取得每个模型驱动视图的完整边界，并按所属完整
图框 JPG 的真实 CAD 坐标裁成单独 JPG。裁切不得重新自动适配、丢边或只截
建筑主体。独立 CAD QA 派生必须逐视图填写完整轮廓、不对称方向锚点、模型
驱动构件的类别/源 Handle/边界/轮廓，以及该视图可见的拓扑 ID。

```powershell
python scripts/create_independent_qa_derivation.py `
  --project <project> `
  --source-index work/contracts/source-index.json `
  --topology work/contracts/building-topology.json `
  --build work/contracts/sketchup-build.json `
  --out work/qa/independent-qa-derivation.json

python scripts/compile_independent_qa_plan.py `
  --project <project> `
  --derivation work/qa/independent-qa-derivation.json `
  --out work/qa/independent-qa-plan.json
```

完整交付硬门槛：每个模型驱动源视图恰好出现一次；每个建模楼层有平面
QA；建了屋顶就有屋顶平面 QA；至少四个独立立面 QA；源图轮廓、定位锚点
和关键构件不能为空。CAD QA 派生 ID 不得等于读图、拓扑或生产派生 ID。

## 4.2 从活动 SKP 只读导出

在用户已打开的生产 SKP 中调用：

```ruby
HEBIIndependentQA.export(
  'C:/project/work/qa/independent-qa-plan.json',
  'unique-active-skp-export-derivation-id'
)
```

导出器核对活动 SKP 路径、SHA-256 和唯一生产根组；不启动 operation，
不增删模型实体；只投影视图的 `visible_topology_ids`；按注册变换反算源
CAD 坐标；独立检查普通洞口是否仍被宿主墙面封堵；报告生产根组外可见
未追踪几何；导出前后均要求模型无未保存修改。

## 4.3 同坐标机器叠图

```powershell
python scripts/evaluate_independent_qa.py `
  --project <project> `
  --plan work/qa/independent-qa-plan.json `
  --model-evidence reports/qa/model-evidence.json `
  --out reports/qa/machine-result.json
```

叠图沿用源视图 CAD 坐标和像素尺寸，禁止 CAD 与模型分别自动缩放后再
叠加。逐视图记录漏项数、无依据多项数、最大坐标偏差、轮廓、方向、比例
和真实洞口。任何漏项、多项、超差、镜像、封堵洞口、未追踪几何或过期
哈希都输出 `FAIL`，不能通过缩小范围或删除期望项规避。

## 4.4 独立视觉与跨视图复核

机器 `PASS` 后生成复核模板：

```powershell
python scripts/finalize_independent_qa.py `
  --project <project> `
  --machine-result reports/qa/machine-result.json `
  --review-template work/qa/independent-review-template.json
```

按 [review.md](review.md) 创建证据包，由人工或另一 Agent 审核；保留原模板，
将完成的副本保存为 `work/qa/independent-review.json`。记录实际回复和回执后，
才运行下方晋级命令。无回执或证据变更都会被拦截。

另一派生逐图核对完整裁切、方向/比例、轮廓、洞口、标高、材料和无依据
几何，并跨图核对平立/平剖注册、相反立面方向、转角连续、竖向基准、
组件复用、雨棚、女儿墙/屋顶轮廓和材料交界。复核派生 ID 不得等于 CAD
或模型证据派生 ID。

```powershell
python scripts/finalize_independent_qa.py `
  --project <project> `
  --plan work/qa/independent-qa-plan.json `
  --machine-result reports/qa/machine-result.json `
  --review work/qa/independent-review.json `
  --out work/contracts/independent-qa.json
```

晋级合同最初保留 `user_confirmation.confirmed: false`。机器与独立复核
通过只代表可供最终验收；用户确认后更新合同及状态哈希，最后运行
`workflow_gate.py --stage delivery`。`qa_alignment_report.py` 只是局部数值
诊断工具，不能替代本合同链。
