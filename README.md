# astrbot_plugin_interflow

**Interflow - 群消息互通** | AstrBot 跨平台群消息转发插件

## 功能介绍

- **消息池**：创建多个消息池，每个池内的群组消息会自动互相转发
- **跨平台**：同一消息池可包含不同平台的群组（QQ、Telegram、Discord 等）
- **自定义格式**：支持丰富的模板变量，每个消息池可独立设置转发格式
- **媒体转发**：支持图片、文件、视频、语音等媒体消息的转发（可单独开关）
- **防循环**：自动检测 Bot 自身发送的消息，避免无限转发

## 安装

在 AstrBot WebUI 的插件管理中搜索 `interflow` 安装，或手动克隆到 `data/plugins/` 目录。

## 配置说明

安装后在 AstrBot WebUI 的插件配置页面进行配置。

### 1. 获取群组标识

在需要互通的每个群中发送指令：

```
/interflow_umo
```

Bot 会回复该群的 `unified_msg_origin`（简称 UMO），这是 AstrBot 中唯一标识一个会话的字符串。请记录下来。

### 2. 配置消息池

在 WebUI 插件配置中，找到「消息池列表」配置项，使用 JSON 编辑器填入消息池配置。格式如下：

```json
[
  {
    "name": "我的互通池",
    "enabled": true,
    "format": "[{platform} | {pool_name}] {sender_name}:\n{message}",
    "groups": [
      "aiocqhttp:group:123456789",
      "telegram:group:-100987654321"
    ]
  },
  {
    "name": "另一个互通池",
    "enabled": true,
    "format": "",
    "groups": [
      "aiocqhttp:group:111111111",
      "aiocqhttp:group:222222222"
    ]
  }
]
```

各字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 消息池名称，用于展示和格式模板中的 `{pool_name}` |
| `enabled` | bool | 是否启用该消息池 |
| `format` | string | 转发格式模板，留空则使用全局默认格式 |
| `groups` | list | 群组的 `unified_msg_origin` 列表 |

### 3. 全局配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| 默认转发格式模板 | `[{platform} \| {pool_name}] {sender_name}:\n{message}` | 消息池未单独设置格式时使用 |
| 是否转发图片 | 开启 | 是否将消息中的图片一并转发 |
| 是否转发文件 | 关闭 | 是否将消息中的文件一并转发 |
| 是否转发视频 | 关闭 | 是否将消息中的视频一并转发 |
| 是否转发语音 | 关闭 | 是否将消息中的语音一并转发 |

## 模板变量

转发格式模板支持以下变量：

| 变量 | 说明 | 示例 |
|---|---|---|
| `{sender_name}` | 消息发送者昵称 | `张三` |
| `{sender_id}` | 消息发送者 ID | `123456789` |
| `{group_name}` | 源群组标识 | `group:123456` |
| `{pool_name}` | 消息池名称 | `我的互通池` |
| `{platform}` | 消息来源平台 | `aiocqhttp` |
| `{message}` | 消息纯文本内容 | `你好世界` |
| `{time}` | 消息时间 | `14:30:25` |
| `{date}` | 消息日期 | `2025-01-15` |

### 格式模板示例

```
# 简洁风格
{sender_name}: {message}

# 带平台标识
[{platform}] {sender_name}: {message}

# 完整信息
[{date} {time}] [{platform} | {pool_name}] {sender_name}({sender_id}):
{message}
```

## 可用指令

| 指令 | 别名 | 权限 | 说明 |
|---|---|---|---|
| `/interflow_umo` | `/ifumo` | 所有人 | 显示当前会话的 unified_msg_origin |
| `/interflow_list` | `/iflist` | 所有人 | 查看所有消息池的配置信息 |
| `/interflow_reload` | `/ifreload` | 管理员 | 重新加载消息池配置索引 |

## 注意事项

1. **UMO 获取**：配置消息池前，务必先在各群中使用 `/interflow_umo` 获取正确的 unified_msg_origin。
2. **配置修改后**：在 WebUI 修改配置后，请使用 `/interflow_reload` 或重载插件以使配置生效。
3. **跨平台图片**：跨平台转发图片时，部分平台的临时图片链接可能过期失效。
4. **消息频率**：高活跃群可能产生大量转发消息，请注意 Bot 的消息发送频率限制。
5. **事件阻断**：被转发的消息会停止后续事件传播（如 LLM 对话），如需同时使用 AI 对话功能，请将 AI 对话群组从消息池中移除。

## 许可证

AGPL-3.0
