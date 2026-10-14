"""
settings.py
-----------
The "tuning knobs" of the mini DBMS. Every recommendation that can be
applied automatically is just a change to one of these settings.

All optimisations are OFF by default, so a default run behaves exactly
like the original engine. Turning one on never changes *what* a query
returns -- only how much CPU time, memory and latency it needs (the only
visible difference is that a JOIN may return its rows in a different
order when no ORDER BY is given).

    thread_count           simulated vCPUs/threads for the FILTER step
    pushdown_filter        apply WHERE conditions to each table BEFORE the join
    compiled_predicates    inline the comparison in the filter loop instead of
                           calling a Python function for every row
    streaming_aggregation  keep running totals per group instead of keeping
                           every row of every group in memory
    build_smaller_side     build the join hash table on the smaller input
"""

from dataclasses import dataclass, asdict, fields

TOGGLE_NAMES = (
    "pushdown_filter",
    "compiled_predicates",
    "streaming_aggregation",
    "build_smaller_side",
)

_TRUE_WORDS = ("on", "true", "yes", "1", "enable", "enabled")
_FALSE_WORDS = ("off", "false", "no", "0", "disable", "disabled")


@dataclass
class ExecutionSettings:
    thread_count: int = 1
    pushdown_filter: bool = False
    compiled_predicates: bool = False
    streaming_aggregation: bool = False
    build_smaller_side: bool = False

    # ---- conversion -------------------------------------------------------
    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        """Builds settings from a (possibly old / partial) dict; unknown keys are ignored."""
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        settings = cls(**clean)
        settings.thread_count = max(1, int(settings.thread_count))
        return settings

    def copy(self):
        return ExecutionSettings(**self.to_dict())

    # ---- changing ---------------------------------------------------------
    def with_changes(self, changes):
        """Returns a NEW settings object with `changes` (a dict) applied."""
        new = self.copy()
        for name, value in (changes or {}).items():
            if name == "thread_count":
                new.thread_count = max(1, int(value))
            elif name in TOGGLE_NAMES:
                setattr(new, name, bool(value))
            else:
                raise ValueError("Unknown setting '%s'. Valid settings: thread_count, %s"
                                 % (name, ", ".join(TOGGLE_NAMES)))
        return new

    def diff(self, other):
        """{setting: (my value, other's value)} for every setting that differs."""
        mine, theirs = self.to_dict(), other.to_dict()
        return {k: (mine[k], theirs[k]) for k in mine if mine[k] != theirs[k]}

    # ---- display ----------------------------------------------------------
    def describe(self):
        parts = ["threads=%d" % self.thread_count]
        for name in TOGGLE_NAMES:
            parts.append("%s=%s" % (name, "on" if getattr(self, name) else "off"))
        return ", ".join(parts)


def parse_setting_value(name, text):
    """Turns the text typed in the terminal ('on', '4', ...) into a value for `name`."""
    text = text.strip().lower()
    if name == "thread_count":
        if not text.isdigit() or int(text) < 1:
            raise ValueError("thread_count must be a whole number >= 1")
        return int(text)
    if name in TOGGLE_NAMES:
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
        raise ValueError("%s must be on or off" % name)
    raise ValueError("Unknown setting '%s'. Valid settings: thread_count, %s"
                     % (name, ", ".join(TOGGLE_NAMES)))
