from enum import Enum, auto


class PlatformType(str, Enum):
    LINKEDIN = "linkedin"
    FACEBOOK = "facebook"
    TWITTER = "twitter"
    INSTAGRAM = "instagram"


class EventType(Enum):
    MESSAGE = auto()
    COMMENT = auto()
    MENTION = auto()
    LIKE = auto()
    FOLLOW = auto()
    CONNECTION_REQUEST = auto()
    SHARE = auto()
    PROFILE_VIEW = auto()
    NOTIFICATION = auto()
    UNKNOWN = auto()


class WatcherState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class ContentCategory(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    LINK = "link"
    DOCUMENT = "document"
    STORY = "story"
    REEL = "reel"
    CAROUSEL = "carousel"
    POLL = "poll"
