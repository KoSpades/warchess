"""Global event bus (spec 7.1).

Handlers are not registered and unregistered; they are discovered by walking the
living entities and asking each of their passives whether it implements a hook.
That keeps bookkeeping out of death, spawning, and side-switching, all of which
the roster requires.
"""

MATCH_START = "match_start"
ROUND_START = "round_start"
ROUND_END = "round_end"
TURN_START = "turn_start"
TURN_END = "turn_end"
AFTER_MOVE = "after_move"
BEFORE_DAMAGE = "before_damage"
AFTER_DAMAGE = "after_damage"
HEAL = "heal"
BEFORE_DEATH = "before_death"
DEATH = "death"
# The exchange is over: every hit has landed, every effect has run and the
# dead are off the board, but nobody has been picked for the next one yet.
# Context carries what happened — `landed` is whether that unit's attack got
# through — so a hero can react to the outcome of its own turn.
TURN_RESOLVED = "turn_resolved"


def hook(priority=50):
    """Mark a passive method as an event handler with an explicit pipeline
    position. Ordering must never depend on registration order (spec 7.2)."""

    def deco(fn):
        fn.hook_priority = priority
        return fn

    return deco


class EventBus:
    def __init__(self, match):
        self.match = match

    def _handlers(self, event):
        found = []
        for entity in list(self.match.entities):
            if not entity.alive:
                continue
            for passive in entity.passives:
                fn = getattr(passive, "on_" + event, None)
                if fn is None:
                    continue
                found.append((getattr(fn, "hook_priority", 50), entity, fn))
        # Global rules sit at the end of the pipeline and belong to no entity.
        for passive in self.match.global_rules:
            fn = getattr(passive, "on_" + event, None)
            if fn is not None:
                found.append((getattr(fn, "hook_priority", 50), None, fn))
        found.sort(key=lambda t: t[0])
        return found

    def emit(self, event, ctx):
        for _prio, entity, fn in self._handlers(event):
            if entity is None:
                fn(self.match, ctx)
            else:
                fn(self.match, entity, ctx)
        return ctx
