from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class Action(StrEnum):
    IDLE = "idle"
    WALK = "walk"
    RUN = "run"
    JUMP = "jump"
    SLEEP = "sleep"
    HAPPY = "happy"
    ANGRY = "angry"
    TALK = "talk"
    DRAG = "drag"
    FEED = "feed"


class Needs(Model):
    energy: float = Field(80, ge=0, le=100)
    boredom: float = Field(25, ge=0, le=100)
    curiosity: float = Field(65, ge=0, le=100)
    social: float = Field(30, ge=0, le=100)
    mood: float = Field(65, ge=0, le=100)
    trust: float = Field(20, ge=0, le=100)

    def change(self, name: str, amount: float):
        setattr(self, name, max(0, min(100, getattr(self, name) + amount)))


class Personality(Model):
    curiosity: float = Field(.8, ge=0, le=1)
    playfulness: float = Field(.7, ge=0, le=1)
    independence: float = Field(.65, ge=0, le=1)
    friendliness: float = Field(.85, ge=0, le=1)


class Emotion(Model):
    valence: float = Field(.3, ge=-1, le=1)
    arousal: float = Field(.25, ge=-1, le=1)

    @property
    def name(self):
        if self.arousal < -.4:
            return "sleepy"
        if self.valence < -.25:
            return "angry"
        if self.arousal > .72:
            return "surprised"
        if self.arousal > .35:
            return "curious"
        return "happy" if self.valence > .45 else "calm"


class Environment(Model):
    hour: int = Field(12, ge=0, le=23)
    idle_seconds: float | None = None
    focused: bool = False
    interacting: bool = False
    phone_open: bool = False


class Settings(Model):
    wechat_batch_seconds: float = Field(1.2, ge=0, le=5)
    wechat_silence: bool = True
    proactive: bool = True
    perception: bool = False
    tools_enabled: bool = False
    dnd: bool = False
    follow_mouse: bool = False
    online: bool = False
    model: str = "deepseek-flash"
    sprite_manifest: str = ""
    size: int = Field(180, ge=96, le=400)
    fps: int = Field(60, ge=30, le=60)
    daily_requests: int = Field(60, ge=0, le=1000)
    daily_tokens: int = Field(2000000, ge=0, le=2000000)
    proactive_cooldown: int = Field(900, ge=60, le=86400)
    proactive_per_hour: int = Field(3, ge=0, le=12)
    personality: Personality = Field(default_factory=Personality)
