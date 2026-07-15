# 全量任务自动接续机制

## 目标

该机制持续执行 `CR00`—`CR16`，直到所有可执行任务通过各自的产物验收，条件任务形成“完成”或“有证据跳过”的正式结论，并由 `CR16` 完成 297 城总面板验收。

它不改变以下研究边界：空气质量暂停；智联招聘暂停；`historical_IV_city.dta` 排除；缺失值不静默补零；30 城只能用于程序测试，正式结果必须覆盖 297 城。

## 云端恢复

云端 systemd 用户服务 `tech-soft-power-orchestrator.service` 每分钟检查依赖和任务状态，运行已就绪 worker，逐项验证产物。进程退出、SSH 断开或服务器重启后自动恢复。本项目不使用本地 Codex heartbeat 或定时自动化。

云端服务只执行已经部署的确定性 worker，不具备自行编写代码的能力。公开示例中 `CR02`—`CR15` 的来源专用 worker 均已实现；每个可运行任务必须同时声明命令和产物门禁。如果以后新增任务仍标为 `AWAITING_WORKER`，队列会保留该状态，不会将其伪装成已完成。

## 完成语义

- `COMPLETE`：命令退出为 0，且任务清单中的全部输出门禁通过。
- `SKIPPED_WITH_EVIDENCE`：仅用于条件任务；必须有不可得证据、替代口径和缺失规则等规定产物。
- `WAITING_EXTERNAL`：外部长流程仍在运行，定期自动复核。
- `WAITING_DEPENDENCY`：前置任务尚未通过。
- `AWAITING_WORKER`：来源专用解析器还没有完成，不能伪装为已经执行。
- `RETRY_WAIT`：命令或验收失败，按 1、5、15、60 分钟到 6 小时的上限持续重试；无永久失败次数上限。

总体完成条件是全部任务处于 `COMPLETE` 或 `SKIPPED_WITH_EVIDENCE`，并生成：

`10_qc/orchestrator/all_tasks_complete.flag`

## 运行与状态

云端项目根目录通过 `PROJECT_ROOT` 设置，政策项目通过 `POLICY_PROJECT` 设置，不在代码中写死服务器地址、账号或密钥。

```bash
python scripts/orchestrator/cloud_queue_runner.py --validate-manifest
python scripts/orchestrator/cloud_queue_runner.py --once
python scripts/orchestrator/cloud_queue_runner.py --status
systemctl --user status tech-soft-power-orchestrator.service
journalctl --user -u tech-soft-power-orchestrator.service -f
```

机器可读状态：`10_qc/orchestrator/state.json`。

便于人工查看的状态：`10_qc/orchestrator/STATUS.md` 和 `task_status.csv`。

每项任务的追加日志：`10_qc/orchestrator/logs/CRxx.log`。

## 安全与可行性约束

- 调度器使用文件锁，禁止重复实例同时修改同一任务。
- 命令以参数数组执行，不通过 shell 拼接动态字符串。
- 旧的 `COMPLETE` 状态会在启动时重新验收；产物丢失或不合格时自动回到恢复队列。
- worker 从 `pending` 升级为 `ready` 后，旧状态文件中的 `AWAITING_WORKER` 会自动转回可运行状态，无需人工修改 JSON。
- 政策工具分类不仅检查行数；含 `tool_error`、非法 JSON、重复 ID 或缺失 ID 的记录会被清出并自动重试，错误未清零前不写完成标记。
- 当前云主机资源较小，worker 默认单任务串行；外部文化政策流程可独立继续运行。
- worker 不存在时调度器继续检查其他独立任务，但不会把任务伪装成完成。
- 移动硬盘未挂载不阻塞云端任务；硬盘同步只在用户挂载硬盘并发起交互式检查时执行。
