"""The one record every check produces."""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

ERROR = "error"
WARNING = "warning"


@dataclass
class Finding:
    level: str  # "error" or "warning"
    code: str  # short stable id, e.g. "schema", "sequence", "deprecated"
    message: str
    index: Optional[int] = None  # position of the event in the stream (0-based)
    event_type: Optional[str] = None
    path: str = ""  # JSON pointer inside the event, "" for the event itself
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "index": self.index,
            "eventType": self.event_type,
            "path": self.path,
        }
        d.update(self.extra)
        return d


def pointer(parts) -> str:
    out = []
    for p in parts:
        s = str(p).replace("~", "~0").replace("/", "~1")
        out.append(s)
    return "/" + "/".join(out) if out else ""
