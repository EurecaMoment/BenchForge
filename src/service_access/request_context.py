"""Optional, service-owned identifiers that follow a BenchForge run."""
from dataclasses import dataclass
from dataclasses import replace
from typing import Mapping


_EXTERNAL_FIELDS = {"workspace_id", "session_id", "parent_run_id"}
_SERVICE_FIELDS = {"run_id", "stage_id", "attempt_id"}


@dataclass(frozen=True)
class RunContext:
    workspace_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    parent_run_id: str | None = None
    stage_id: str | None = None
    attempt_id: str | None = None

    # 校验外部调用上下文字段，拒绝调用方指定仅由服务分配的运行或阶段身份。
    @classmethod
    def from_request(cls, value):
        """Validate caller identity without accepting service-owned IDs."""
        if value is None:
            return None
        if isinstance(value, cls):
            raw = value.to_dict()
        elif isinstance(value, Mapping):
            raw = dict(value)
        else:
            raise ValueError("context must be an object")

        forbidden = set(raw) & _SERVICE_FIELDS
        if forbidden:
            raise ValueError(
                "context fields are service-owned and cannot be supplied: "
                + ", ".join(sorted(forbidden))
            )
        unknown = set(raw) - _EXTERNAL_FIELDS
        if unknown:
            raise ValueError("unknown context fields: " + ", ".join(sorted(unknown)))

        fields = {}
        for name, item in raw.items():
            if item is None:
                continue
            if not isinstance(item, str) or not item.strip() or len(item) > 200:
                raise ValueError(f"context.{name} must be a non-empty string of at most 200 characters")
            fields[name] = item
        return cls(**fields)

    # 恢复服务已持久化的完整上下文，检查未知字段及非字符串身份值。
    @classmethod
    def from_record(cls, value):
        """Load a context already stored by the service."""
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("stored run context must be an object")
        unknown = set(value) - (_EXTERNAL_FIELDS | _SERVICE_FIELDS)
        if unknown:
            raise ValueError("unknown stored context fields: " + ", ".join(sorted(unknown)))
        for name, item in value.items():
            if item is not None and (not isinstance(item, str) or not item.strip() or len(item) > 200):
                raise ValueError(f"context.{name} must be a non-empty string of at most 200 characters")
        return cls(**{name: item for name, item in value.items() if item is not None})

    # 复制上下文并绑定运行编号，同时清除前一阶段和尝试身份。
    def for_run(self, run_id):
        return replace(self, run_id=run_id, stage_id=None, attempt_id=None)

    # 在已绑定运行的上下文上复制并设置阶段与尝试编号。
    def for_stage(self, stage_id, attempt_id):
        if not self.run_id:
            raise ValueError("run context must be bound to a run before a stage")
        return replace(self, stage_id=stage_id, attempt_id=attempt_id)

    # 将上下文转成不含空值的字典，供请求和回执持久化。
    def to_dict(self):
        return {key: value for key, value in vars(self).items() if value is not None}


class RunCancelled(RuntimeError):
    """Raised when a running scene stage observes its cancellation signal."""


# 发现停止事件已触发时抛出包含当前阶段的运行取消异常。
def check_cancelled(stop, stage):
    if stop is not None and stop.is_set():
        raise RunCancelled(f"BenchForge run canceled during {stage}")
