> 适用范围：继承的完整图纸复原流程。平面加效果图或局部修改先按 heritage-modes.md 与 heritage-review.md；不要求为不存在的图纸伪造验收。执行授权与目标保护统一按 sketchup-execution.md。

# 第五大步：验收、交付、失效与学习

第五步把已通过独立 QA 的候选模型封装成不可覆盖的交付包。不要手工把
`confirmed` 改成 `true`，不要直接复制一个 SKP 后宣称交付。

## 5.1 绑定最终用户验收

先验证未验收的 QA 合同。只有用户明确接受当前模型与证据包后运行：

```powershell
python scripts/accept_independent_qa.py `
  --project <project> `
  --qa work/contracts/independent-qa.json `
  --confirmation-id <stable-user-confirmation-id> `
  --instruction "<用户原始验收指令>" `
  --out work/contracts/independent-qa-accepted.json
```

验收绑定验收前 QA 指纹、活动 SKP、机器结果和独立复核四组 SHA-256。
拒绝覆盖已有验收记录。模型或证据变化后必须重新 QA 和重新验收。

## 5.2 创建交付包

```powershell
python scripts/create_delivery_package.py `
  --project <project> `
  --qa work/contracts/independent-qa-accepted.json `
  --manifest work/contracts/delivery-manifest.json
```

脚本在 `output/deliveries/` 生成不可覆盖的时间戳 SKP 副本，在
`reports/delivery/` 生成报告，并创建 `delivery-manifest`。清单必须精确覆盖
源图、拓扑、构建、验收 QA、所有 CAD/模型视图、叠图、机器结果和独立复核。
任何缺项、哈希过期或 SKP 字节变化都阻止交付。

## 5.3 通过门槛并签发收据

```powershell
python scripts/workflow_gate.py `
  --project <project> `
  --stage delivery

python scripts/finalize_delivery.py `
  --project <project> `
  --manifest work/contracts/delivery-manifest.json
```

只有 `finalize_delivery.py` 生成状态为 `DELIVERED` 的不可覆盖收据后，才称
模型“已交付”。交付清单之前只能称“已打包待门槛验证”。

## 5.4 用户报告错误时级联失效

先判断最早失败阶段，再运行：

```powershell
python scripts/invalidate_workflow.py `
  --project <project> `
  --earliest building-topology `
  --reported-by user `
  --reason "<具体可复核的不一致>"
```

可选最早阶段为 `source-index`、`building-topology`、`sketchup-build`、
`independent-qa` 或 `delivery-manifest`。脚本保留所有原始合同和交付文件，
保存失效前状态快照，只从活动工作流状态撤销最早失败项及全部下游引用。
历史 `DELIVERED` 收据仅是当时状态，不再代表当前有效交付。

## 5.5 可选：维护 Skill 时记录回归证据

本节仅用于用户明确要求更新 Skill 的任务，不是普通建模或交付的必经步骤。
项目修正写入项目报告即可，不自动修改共享或已安装的 Skill。

完成源头修正并增加回归测试后，先保存机器可读测试结果：

```json
{"status":"PASS","tests":["test_specific_regression"]}
```

再运行：

```powershell
python scripts/record_skill_mark.py `
  --project <project> `
  --invalidation work/invalidation/<id>.json `
  --issue-id <stable-rule-id> `
  --mistake "<错误>" `
  --root-cause "<根因>" `
  --rule "<可迁移规则>" `
  --prevention-check "<前置防错门槛>" `
  --rule-scope transferable `
  --promoted-to SKILL.md `
  --test-file tests/<test-file>.py `
  --test-id test_specific_regression `
  --test-result reports/tests/<result>.json
```

没有失效记录、真实测试函数、明确 PASS 结果或规则落点时，禁止写入
`requirements/skill-marks.md`。聊天中的“记住了”不算流程学习。
