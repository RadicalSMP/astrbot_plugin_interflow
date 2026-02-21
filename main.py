import asyncio
import datetime
import traceback
from typing import Dict, List, Optional, Set

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp

# 发送失败时的最大重试次数
MAX_RETRY = 3
# 重试间隔基数（秒），实际间隔为 base * 2^(retry_count-1)
RETRY_BASE_DELAY = 1.0


@register(
    "astrbot_plugin_interflow",
    "RadicalSMP-devs",
    "跨平台群消息互通插件，支持创建消息池实现多群消息转发，支持自定义转发格式。",
    "v0.2.0",
)
class InterflowPlugin(Star):
    """Interflow - 群消息互通插件

    核心功能：
    - 用户可通过 WebUI 配置创建多个「消息池」
    - 每个消息池包含若干群组（通过 unified_msg_origin 标识）
    - 消息池内任意群组中发送的消息会被自动转发到池内其他所有群组
    - 支持跨平台转发（如 QQ 群 <-> Telegram 群）
    - 支持自定义转发格式模板
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # unified_msg_origin -> 所属消息池列表 的快速查找索引
        # 一个群组可以同时属于多个消息池
        self._umo_to_pools: Dict[str, List[dict]] = {}

        # 构建索引
        self._build_index()

    def _build_index(self):
        """根据配置构建 unified_msg_origin -> 消息池 的快速查找索引"""
        self._umo_to_pools.clear()

        pools: list = self.config.get("pools", [])
        for pool in pools:
            # 跳过未启用的消息池
            if not pool.get("enabled", True):
                continue

            groups: list = pool.get("groups", [])
            for umo in groups:
                if umo not in self._umo_to_pools:
                    self._umo_to_pools[umo] = []
                self._umo_to_pools[umo].append(pool)

        pool_count = len([p for p in pools if p.get("enabled", True)])
        group_count = len(self._umo_to_pools)
        logger.info(
            f"[Interflow] 索引构建完成: {pool_count} 个活跃消息池, {group_count} 个群组已注册"
        )

    def _format_message(
        self,
        template: str,
        sender_name: str,
        sender_id: str,
        group_name: str,
        pool_name: str,
        platform: str,
        message_text: str,
        timestamp: Optional[int] = None,
    ) -> str:
        """使用模板格式化转发消息的文本部分

        支持的模板变量：
        - {sender_name}: 消息发送者昵称
        - {sender_id}: 消息发送者 ID
        - {group_name}: 源群组名称/标识
        - {pool_name}: 消息池名称
        - {platform}: 消息来源平台名称
        - {message}: 消息纯文本内容
        - {time}: 消息时间 (HH:MM:SS)
        - {date}: 消息日期 (YYYY-MM-DD)
        """
        now = datetime.datetime.now()
        if timestamp:
            try:
                now = datetime.datetime.fromtimestamp(timestamp)
            except (OSError, ValueError):
                pass

        return template.format(
            sender_name=sender_name,
            sender_id=sender_id,
            group_name=group_name,
            pool_name=pool_name,
            platform=platform,
            message=message_text,
            time=now.strftime("%H:%M:%S"),
            date=now.strftime("%Y-%m-%d"),
        )

    def _extract_media_components(
        self, message_chain: list
    ) -> List[Comp.BaseMessageComponent]:
        """从消息链中提取需要转发的媒体消息段（图片、文件、视频、语音）

        根据配置决定是否包含各类媒体。
        """
        media = []
        forward_image = self.config.get("forward_image", True)
        forward_file = self.config.get("forward_file", False)
        forward_video = self.config.get("forward_video", False)
        forward_voice = self.config.get("forward_voice", False)

        for comp in message_chain:
            if forward_image and isinstance(comp, Comp.Image):
                media.append(comp)
            elif forward_file and isinstance(comp, Comp.File):
                media.append(comp)
            elif forward_video and isinstance(comp, Comp.Video):
                media.append(comp)
            elif forward_voice and isinstance(comp, Comp.Record):
                media.append(comp)

        return media

    def _build_chain(
        self, formatted_text: str, media_components: List[Comp.BaseMessageComponent]
    ) -> MessageChain:
        """构建转发用的消息链：格式化文本 + 媒体附件"""
        chain = MessageChain()
        chain.message(formatted_text)

        # 追加媒体消息段
        for media_comp in media_components:
            if isinstance(media_comp, Comp.Image):
                # 优先使用 URL，其次使用文件路径
                url = getattr(media_comp, "url", None) or getattr(
                    media_comp, "file", None
                )
                if url:
                    chain.image(url)
            elif isinstance(media_comp, (Comp.File, Comp.Video, Comp.Record)):
                # 文件、视频、语音直接追加到消息链
                chain.chain.append(media_comp)

        return chain

    async def _send_with_retry(
        self, target_umo: str, chain: MessageChain, pool_name: str
    ):
        """带重试机制的消息发送

        使用指数退避策略，最多重试 MAX_RETRY 次。
        针对 RuntimeError("Session is closed") 等瞬态错误进行重试。
        """
        last_exc = None
        for attempt in range(1, MAX_RETRY + 1):
            try:
                await self.context.send_message(target_umo, chain)
                return  # 发送成功，直接返回
            except RuntimeError as e:
                # 捕获 "Session is closed" 等运行时错误，这类错误通常是瞬态的
                last_exc = e
                if attempt < MAX_RETRY:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        f"[Interflow] [{pool_name}] 发送到 {target_umo} 失败 "
                        f"(第{attempt}次, {e}), {delay:.1f}s 后重试..."
                    )
                    await asyncio.sleep(delay)
            except Exception as e:
                # 非瞬态错误（如目标不存在、权限不足等），不重试
                logger.warning(
                    f"[Interflow] [{pool_name}] 发送到 {target_umo} 失败 (不可重试): "
                    f"{e}"
                )
                return

        # 重试耗尽仍然失败
        logger.error(
            f"[Interflow] [{pool_name}] 发送到 {target_umo} 在 {MAX_RETRY} 次重试后仍失败: "
            f"{last_exc}"
        )

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听所有群组消息，判断是否需要转发到消息池内的其他群组"""

        # 获取当前消息来源的 unified_msg_origin
        source_umo = event.unified_msg_origin

        # 检查该群组是否属于任何消息池
        if source_umo not in self._umo_to_pools:
            return  # 不属于任何消息池，跳过

        # 防循环：跳过 Bot 自身发送的消息
        message_obj = event.message_obj
        sender_id = event.get_sender_id()
        bot_self_id = message_obj.self_id if message_obj else None

        if bot_self_id and sender_id == bot_self_id:
            return  # Bot 自己发的消息，跳过以避免无限循环

        # 获取消息的基本信息
        sender_name = event.get_sender_name()
        message_text = event.message_str
        platform_name = event.get_platform_name()
        group_id = message_obj.group_id if message_obj else source_umo
        timestamp = message_obj.timestamp if message_obj else None

        # 获取原始消息链中的媒体消息段
        original_chain = event.get_messages()
        media_components = self._extract_media_components(original_chain)

        # 默认转发格式
        default_format = self.config.get(
            "default_format",
            "[{platform} | {pool_name}] {sender_name}:\n{message}",
        )

        # 记录已发送过的目标 UMO，避免同一条消息因多个消息池重复发送到同一目标
        # 例如：群A 同时在池1和池2中，群B也同时在池1和池2中，
        # 群A发消息时，群B 只需要收到一次转发即可
        sent_targets: Set[str] = set()

        # 遍历该群组所属的所有消息池
        pools = self._umo_to_pools[source_umo]
        for pool in pools:
            pool_name = pool.get("name", "未命名消息池")
            # 消息池自定义格式，为空则用默认格式
            pool_format = pool.get("format", "") or default_format

            # 格式化转发文本
            try:
                formatted_text = self._format_message(
                    template=pool_format,
                    sender_name=sender_name,
                    sender_id=sender_id,
                    group_name=group_id,
                    pool_name=pool_name,
                    platform=platform_name,
                    message_text=message_text,
                    timestamp=timestamp,
                )
            except (KeyError, ValueError, IndexError) as e:
                logger.warning(
                    f"[Interflow] 消息池 '{pool_name}' 的转发格式模板有误: {e}，使用原始消息"
                )
                formatted_text = f"[{pool_name}] {sender_name}: {message_text}"

            # 转发到该消息池内的所有其他群组
            groups: list = pool.get("groups", [])
            for target_umo in groups:
                # 跳过消息来源群组自身
                if target_umo == source_umo:
                    continue

                # 去重：如果此目标已在其他消息池中被转发过，则跳过
                if target_umo in sent_targets:
                    continue
                sent_targets.add(target_umo)

                # 构建消息链并发送（带重试）
                chain = self._build_chain(formatted_text, media_components)
                await self._send_with_retry(target_umo, chain, pool_name)

        # 停止事件继续传播，避免消息被 LLM 等后续流程处理
        event.stop_event()

    @filter.command("interflow_reload", alias={"ifreload"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def reload_config(self, event: AstrMessageEvent):
        """重新加载消息池配置索引（仅管理员可用）"""
        self._build_index()
        pool_count = len(
            [p for p in self.config.get("pools", []) if p.get("enabled", True)]
        )
        group_count = len(self._umo_to_pools)
        yield event.plain_result(
            f"[Interflow] 配置已重新加载: {pool_count} 个活跃消息池, {group_count} 个群组已注册"
        )

    @filter.command("interflow_list", alias={"iflist"})
    async def list_pools(self, event: AstrMessageEvent):
        """查看当前所有消息池的信息"""
        pools: list = self.config.get("pools", [])
        if not pools:
            yield event.plain_result("[Interflow] 当前没有配置任何消息池。")
            return

        lines = ["[Interflow] 消息池列表:"]
        for i, pool in enumerate(pools, 1):
            name = pool.get("name", "未命名")
            enabled = pool.get("enabled", True)
            status = "启用" if enabled else "停用"
            groups = pool.get("groups", [])
            fmt = pool.get("format", "") or "(使用默认格式)"
            lines.append(f"\n{i}. {name} [{status}]")
            lines.append(f"   群组数: {len(groups)}")
            lines.append(f"   格式: {fmt}")
            if groups:
                for g in groups:
                    lines.append(f"   - {g}")

        yield event.plain_result("\n".join(lines))

    @filter.command("interflow_umo", alias={"ifumo"})
    async def show_umo(self, event: AstrMessageEvent):
        """显示当前会话的 unified_msg_origin，方便用户配置群组"""
        umo = event.unified_msg_origin
        yield event.plain_result(
            f"[Interflow] 当前会话的 unified_msg_origin:\n{umo}\n\n"
            f"请将此值添加到消息池配置的 groups 列表中。"
        )

    async def terminate(self):
        """插件卸载/停用时清理资源"""
        self._umo_to_pools.clear()
        logger.info("[Interflow] 插件已停用，索引已清理。")
