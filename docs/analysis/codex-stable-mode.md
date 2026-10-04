# Codex 稳定模式

## 适用问题

已观察到代理再次等待已结束的 code-mode cell，收到 `not found` 后错误地声称终端工具不可用；也有主线程给自己发送协作消息的情况。这些现象不能证明 Pairlet 删除了终端工具。提示词约束只能降低误用概率，不能代替运行时状态管理。

## 启用与作用范围

在 **Pairlet daemon 的进程环境**中设置 `CC_POCKET_CODEX_STABLE_MODE=1`。`CodexLauncher` 为它启动的 `codex app-server` 追加：

```text
--disable multi_agent --disable multi_agent_v2
--enable shell_tool --enable unified_exec
```

此配置不修改用户的全局 Codex 配置，不改变其他 Agent 后端的参数，也不修改沙箱和审批策略。多代理任务在此模式下不可用。终端仍有句柄生命周期，稳定模式不保证句柄误用都消失。

2026-10-04 对当前默认模型 `gpt-6.1-sol` 的真实测试发现：关闭 `code_mode_host` 后模型仍调用 `exec`，返回 `code-mode host is disabled`，终端未执行。因此不能把关闭该宿主当作稳定方案部署；保留宿主，只关闭协作工具。更换模型不属于本补丁的隐含操作。

systemd 用户服务可以使用 drop-in：

```ini
[Service]
Environment=CC_POCKET_CODEX_STABLE_MODE=1
```

完成测试并确认没有其他活跃会话后，重载 unit 并重启目标 daemon。现有会话下一次由新 app-server 恢复时才使用新参数；不会追溯修改已运行的进程。恢复会话仍保留旧历史，因此历史中的错误叙述不等于当前工具状态。

回退时将该环境变量设为 `0`，重载并重启目标 daemon；需要撤回代码时恢复部署前备份的 daemon JAR。升级必须保留 `CodexLauncher` 补丁，并重新执行探针。仅保留环境变量而换回未包含补丁的 daemon 不会生效。

## 验证

```bash
./gradlew :daemon:test --tests dev.ccpocket.daemon.codex.CodexLauncherTest :daemon:installDist
python3 scripts/probe-codex-stable.py
```

探针使用独立、只读的真实会话，依次验证终端 `printf` 输出、运行中的 `sleep` 被中断、app-server 退出后恢复同一会话并执行命令、再次退出和恢复。最后检查原始 rollout 中存在执行工具调用，没有调用代理协作工具。`PAIRLET_PROBE_DIR` 可指定本地诊断目录，`CC_POCKET_CODEX_BIN` 可指定 CLI 路径。原始 wire 日志保留在仓库外，权限为 `600`，不提交或发布。

启动参数单元测试证明参数构造；真实探针证明该版本和该模型组合的执行、恢复能力；daemon 的进程参数与真实会话才证明部署效果。客户端按钮、中继连接和 UI 展示需要独立验收。

## 后续边界

本补丁关闭多代理调度，但不绕开 code-mode 的外层 cell 等待。没有修改 Codex 内部句柄注册表，也没有实现自动重放命令。真正的句柄状态修复需要执行层明确区分已结束、失效和仍运行的任务，并正确区分外层 cell 与内层终端 session；不能在失败时盲目重跑写入操作。

## 本次验证记录

2026-10-04，Codex CLI 0.160.0、默认模型 `gpt-6.1-sol`：

- 12 组 Codex 后端测试、137 项用例全部通过；其中包括稳定模式参数与正常模式参数的测试。
- 真实只读终端输出、运行中断、同一会话两次跨 app-server 进程恢复全部通过；rollout 中使用 `exec`，未调用协作工具。
- 本机 Pairlet 2.2.0 已部署新 daemon JAR 并启用独立 systemd drop-in。实际运行进程读取到稳定模式变量，已安装 JAR 的启动器生成上述禁用协作、启用终端的参数。
- 目标 daemon 重启后恢复中继连接；手机端完整操作和长期复发率尚未验收。
- 未部署关闭 `code_mode_host` 的失败候选方案，未修改全局模型或沙箱权限。失效 cell 的执行层修复仍未完成。
