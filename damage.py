"""Damage as an object flowing through a pipeline (spec 7.2).

Nothing anywhere should write `target.hp -= n`. Damage is constructed, passed
through BEFORE_DAMAGE where handlers may amplify, reduce, or cancel it, then
applied. Source category is load-bearing: at least one passive triggers on
attack and ability damage but explicitly not on tile damage.
"""

from dataclasses import dataclass, field

import events as EV

# --- source categories ---
NORMAL_ATTACK = "normal_attack"
ABILITY = "ability"
TILE = "tile"
STATUS = "status"

# --- elements (inert on their own; tags for later designs to key off) ---
NONE = None
FIRE = "fire"
WATER = "water"
THUNDER = "thunder"
WOOD = "wood"


@dataclass
class DamageEvent:
    source: object
    target: object
    amount: int
    category: str
    element: str = None
    tags: set = field(default_factory=set)
    cancelled: bool = False
    cancel_reason: str = None

    def cancel(self, reason=None):
        self.cancelled = True
        self.cancel_reason = reason


class FlatReduction:
    """A flat per-target cut on all incoming damage — the target's accumulated
    `damage_reduction` (e.g. 森林之子's guard). Applies to every category and
    never takes an instance below zero."""

    @EV.hook(priority=40)
    def on_before_damage(self, match, ev):
        dr = ev.target.vars.get("damage_reduction", 0)
        if dr and not ev.cancelled and ev.amount > 0:
            ev.amount = max(0, ev.amount - dr)


class HalvingRule:
    """The gunslinger's second shot halves *after* every other modifier, so it
    sits at the very end of the pipeline rather than being folded into the
    attack's base damage."""

    @EV.hook(priority=90)
    def on_before_damage(self, match, ev):
        if "halve" in ev.tags and not ev.cancelled:
            ev.amount = ev.amount // 2


def deal(match, ev):
    """Apply a single already-built DamageEvent. Deaths are NOT resolved here;
    the caller sweeps for them so that a batch can be applied simultaneously."""
    match.bus.emit(EV.BEFORE_DAMAGE, ev)
    if ev.cancelled or ev.amount <= 0:
        if ev.cancelled and ev.cancel_reason:
            match.log_line(
                f"{match.label(ev.target)} takes no damage ({ev.cancel_reason}).", quiet=True
            )
        return 0
    ev.target.hp = max(0, ev.target.hp - ev.amount)
    match.bus.emit(EV.AFTER_DAMAGE, ev)
    return ev.amount


def apply_batch(match, damage_events):
    """Simultaneous application: every event is run through BEFORE_DAMAGE and
    applied before any death is resolved, so mutual kills work and two hits
    arriving in the same instant both land."""
    dealt = []
    for ev in damage_events:
        n = deal(match, ev)
        if n:
            dealt.append((ev, n))
    match.sweep_deaths()
    return dealt


def heal(match, target, amount, source=None):
    if not target.alive:
        return 0
    before = target.hp
    target.hp = min(target.max_hp, target.hp + amount)
    return target.hp - before
