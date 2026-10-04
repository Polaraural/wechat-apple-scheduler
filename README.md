# WeChat Apple Scheduler

把微信聊天记录、文字和图片转换成 macOS 的 Apple 日历事件与提醒事项，并将相关图片作为原生附件保留。

## 功能

- 解析聊天记录 ZIP、文本和图片
- 按消息时间理解“今天 / 明天 / 周五”等相对日期
- 将明确日程写入 Apple 日历，将任务写入提醒事项
- 没有具体时间时设为全天，不擅自猜测
- 将相关图片作为原生附件添加到对应事项
- 使用本地状态文件避免重复创建，支持后续更新
- 维护每天 09:00 / 21:00 的规划提醒

## 安装到 Codex Skills

```bash
mkdir -p ~/.codex/skills
unzip wechat-apple-scheduler.zip -d ~/.codex/skills
```

安装后目录应为：

```text
~/.codex/skills/wechat-apple-scheduler/
```

在 Codex 中调用：

```text
$wechat-apple-scheduler 分析这份聊天记录，把明确属于我的日程写入 Apple 日历，待办写入提醒事项。相关图片要作为附件。
```

## WorkBuddy 说明

WorkBuddy 缺少 Apple Events 权限时，Skill 会使用独立 Helper：

```bash
python3 ~/.codex/skills/wechat-apple-scheduler/scripts/install_helper.py
python3 ~/.codex/skills/wechat-apple-scheduler/scripts/check_permissions.py
```

Helper 当前仍保留旧名称 `Apple Chat Scheduler Helper.app` 和旧 bundle id，以保持已经授予的 macOS TCC 权限稳定。Skill 名称与仓库名称已经是 `wechat-apple-scheduler`。

## 目录结构

```text
SKILL.md
agents/openai.yaml
references/install-and-usage.md
scripts/extract_chat_archive.py
scripts/apple_scheduler.py
scripts/install_helper.py
scripts/check_permissions.py
```
