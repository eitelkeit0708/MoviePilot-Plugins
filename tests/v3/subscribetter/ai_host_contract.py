"""Executable MoviePilot V3 DTO excerpt (not a live-host test).
Source: jxxghp/MoviePilot e195cc164fc8ff869ffee0ea44a49c7ec475310c, GPL-3.0.
Exact Message, MessageType, ContentType, NotificationChannel class sources.
Only imports narrowed to the dependencies these classes actually use.
"""

from enum import Enum

from typing import Optional, Union, List

from pydantic import BaseModel

class MessageType(Enum):
    # 资源下载
    Download = "资源下载"
    # 整理入库
    Organize = "整理入库"
    # 订阅
    Subscribe = "订阅"
    # 站点消息
    SiteMessage = "站点"
    # 媒体服务器通知
    MediaServer = "媒体服务器"
    # 处理失败需要人工干预
    Manual = "手动处理"
    # 插件消息
    Plugin = "插件"
    # 智能体消息
    Agent = "智能体"
    # 其它消息
    Other = "其它"

class ContentType(str, Enum):
    """
    消息内容类型
    操作状态的通知消息类型标识
    """
    # 订阅添加成功
    SubscribeAdded = "subscribeAdded"
    # 订阅完成
    SubscribeComplete = "subscribeComplete"
    # 入库成功
    OrganizeSuccess = "organizeSuccess"
    # 下载开始(添加下载任务成功)
    DownloadAdded = "downloadAdded"

class NotificationChannel(Enum):
    """
    通知渠道
    """
    Wechat = "微信"
    Feishu = "飞书"
    WechatClawBot = "微信ClawBot"
    Telegram = "Telegram"
    Slack = "Slack"
    Discord = "Discord"
    DingTalk = "钉钉"
    SynologyChat = "SynologyChat"
    VoceChat = "VoceChat"
    Web = "Web"
    WebAgent = "WebAgent"
    WebPush = "WebPush"
    QQ = "QQ"

class Message(BaseModel):
    """
    消息
    """

    # 消息渠道
    channel: Optional[NotificationChannel] = None
    # 消息来源
    source: Optional[str] = None
    # 消息类型
    mtype: Optional[MessageType] = None
    # 内容类型
    ctype: Optional[ContentType] = None
    # 标题
    title: Optional[str] = None
    # 文本内容
    text: Optional[str] = None
    # 图片
    image: Optional[str] = None
    # 语音文件路径
    voice_path: Optional[str] = None
    # 本地文件路径
    file_path: Optional[str] = None
    # 发送时展示的文件名
    file_name: Optional[str] = None
    # 语音消息附带说明文字
    voice_caption: Optional[str] = None
    # 链接
    link: Optional[str] = None
    # 用户ID
    userid: Optional[Union[str, int]] = None
    # 用户名称
    username: Optional[Union[str, int]] = None
    # 时间
    date: Optional[str] = None
    # 消息方向
    action: Optional[int] = 1
    # 消息目标用户ID字典，未指定用户ID时使用
    targets: Optional[dict] = None
    # 按钮列表，格式：[[{"text": "按钮文本", "callback_data": "回调数据", "url": "链接"}]]
    buttons: Optional[List[List[dict]]] = None
    # Telegram ForceReply 回复标记
    force_reply: bool = False
    # 原消息ID，用于编辑消息
    original_message_id: Optional[Union[str, int]] = None
    # 原消息的聊天ID，用于编辑消息
    original_chat_id: Optional[str] = None
    # 是否必须按用户身份投递到私聊，禁止回退原会话或最近会话映射
    private_delivery: bool = False
    # 是否禁用链接预览（仅Telegram支持）
    disable_web_page_preview: Optional[bool] = None
    # 消息文本格式；Telegram 支持 MarkdownV2、HTML、plain，飞书直发支持 plain
    parse_mode: Optional[str] = None
    # Telegram Rich Message 完整 Markdown 正文；其他渠道可使用 text 作为回退
    rich_message: Optional[str] = None
    # 是否写入消息历史
    save_history: bool = True

    def to_dict(self):
        """
        转换为字典
        """
        items = self.model_dump()
        for k, v in items.items():
            if isinstance(v, NotificationChannel) or isinstance(v, MessageType):
                items[k] = v.value
        return items
