# Bot Obsidian 笔记

把日记、笔记写进本机 Obsidian 仓库（普通 Markdown 目录），支持搜索和读取来补充记忆。

## 功能

- 对它说「写进今天日记」「新建笔记 xxx」「找找笔记里关于……的内容」「读一下 xxx」即可
- 自动日记：按频率限制主动把最近的对话总结成日记，素材按所有者私聊 / 群聊 / 其他私聊加权
- **日记按月归档**：同一个月的日记写进一个 `YYYY-MM.md`，每天一个 `## YYYY-MM-DD` 小节；补写过去日期会按时间顺序插入
- 会话作用域：笔记和日记可按会话分目录，也可以全局共享
- 搜索与列出会遍历整个仓库（含所有会话目录）；读取时优先当前会话，找不到自动找全局

## 目录

```text
<Vault>\
├─ 日记\2026-11.md          # 全局日记，按天分小节
├─ 日记\2026-10.md
├─ 笔记\                    # 全局共享笔记
├─ 日记\私聊-<QQ>\2026-11.md # 会话模式下的个人日记
└─ 笔记\群聊-<群号>\        # 会话模式下的群专属笔记
```

## 配置

| 键 | 默认 | 说明 |
|---|---|---|
| vault_path | `~/Documents/BotMemory` | Obsidian 仓库目录（可用任意目录，用 Obsidian 打开它即可） |
| diary_folder / notes_folder | 日记 / 笔记 | 两类内容的子目录名 |
| owner_session | 空 | 所有者私聊会话（`napcat:FriendMessage:<QQ>`），留空只允许 AstrBot 管理员 |
| max_read_chars | 6000 | 单次读取笔记的最大字符数 |
| note_scope_mode / diary_scope_mode | flat | `flat` 全局共享，`session` 按会话分目录 |
| auto_diary_enabled | true | 自动日记总开关 |
| auto_diary_min_interval_minutes | 120 | 两次自动日记最小间隔 |
| auto_diary_max_per_day | 3 | 每天自动日记上限 |
| auto_diary_min_exchanges | 3 | 至少多少轮对话才写 |
| auto_diary_max_chars | 120 | 单篇日记字数上限（40–300） |
| auto_diary_quiet_hours | 0-7 | 免打扰时段，不写日记 |
| auto_diary_include_groups / include_other_private | true / true | 是否参考群聊 / 其他人的私聊 |
| auto_diary_owner_weight / group_weight / other_weight | 3 / 2 / 1 | 自动日记素材的侧重权重 |
| auto_diary_per_scope | false | 每个会话各有独立频率限制并各自写会话日记 |

## 说明

- 插件内部状态（自动日记限频）写在 AstrBot 插件数据目录（`StarTools.get_data_dir()`）；日记/笔记本体写进你配置的 `vault_path`。
- 语义记忆由 LivingMemory 等插件负责，本插件只负责 Markdown 文件。
