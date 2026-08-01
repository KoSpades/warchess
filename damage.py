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


class OutgoingShift:
    """A unit's own `damage_dealt` stat shifts everything it deals — the outgoing
    mirror of `damage_reduction`. Nothing sets it directly: it rides the modifier
    stack, so any hero can weaken (or sharpen) another's output for a while.
    Applies before the target's defences, since it changes the blow itself."""

    @EV.hook(priority=15)
    def on_before_damage(self, match, ev):
        if ev.cancelled or ev.source is None or ev.amount <= 0:
            return
        shift = ev.source.stat("damage_dealt")
        if shift:
            ev.amount = max(0, ev.amount + shift)


class Vulnerability:
    """The mirror of `damage_reduction`: a unit carrying `vulnerable` stacks takes
    that much more from everything — attacks, abilities and burning ground alike
    (剑客's 增伤). Stacks are just a count, so a second mark simply makes it worse.

    Sits beside the flat cut, and only touches a blow that is still going to land:
    something already softened to nothing is not revived by being easier to hurt.

    Generic like the rest of this file — it reads a var and names no hero, so
    anything that can mark a unit works through it."""

    @EV.hook(priority=35)
    def on_before_damage(self, match, ev):
        n = ev.target.vars.get("vulnerable", 0)
        if n and not ev.cancelled and ev.amount > 0:
            ev.amount += n


class FlatReduction:
    """A flat per-target cut on all incoming damage — the target's permanent
    `damage_reduction` (e.g. 森林之子's guard) plus any temporary `stance_dr`
    (e.g. 武器大师's 剑盾). Applies to every category, never below zero."""

    @EV.hook(priority=40)
    def on_before_damage(self, match, ev):
        dr = ev.target.vars.get("damage_reduction", 0) + ev.target.vars.get("stance_dr", 0)
        if dr and not ev.cancelled and ev.amount > 0:
            ev.amount = max(0, ev.amount - dr)


class AbilityImmunity:
    """A unit flagged `ability_immune` takes nothing from enemy active abilities
    (e.g. 武器大师's 太刀). Normal attacks and tile damage still land."""

    @EV.hook(priority=20)
    def on_before_damage(self, match, ev):
        if ev.cancelled or ev.category != ABILITY:
            return
        if ev.target.vars.get("ability_immune") and ev.source is not None and ev.source.side != ev.target.side:
            ev.cancel("warded (太刀)")


class Blessing:
    """A unit marked `blessed` turns aside the next attack or ability that would
    damage it, then the blessing is spent. Burning ground neither triggers it nor
    is stopped by it — the same two categories 圣骑士's shield answers to.

    Generic, like every other rule in here: it reads a var and names no hero, so
    anything that can bless works through it."""

    TRIGGERS = (NORMAL_ATTACK, ABILITY)

    @EV.hook(priority=25)
    def on_before_damage(self, match, ev):
        if ev.cancelled or ev.amount <= 0 or ev.category not in self.TRIGGERS:
            return
        if not ev.target.vars.get("blessed"):
            return
        ev.target.vars["blessed"] = None
        for m in list(ev.target.modifiers):
            if m.source == "blessing":
                ev.target.modifiers.remove(m)
        ev.cancel("blessing")


class CurseTrap:
    """诅咒娃娃's 咒毒 sits on whoever it marked: the first attack or ability that
    damages that unit costs its source the next two rounds. Generic like the other
    global rules — it reads a var off the target and never names a hero. Fires once,
    then clears the mark."""

    TRIGGERS = (NORMAL_ATTACK, ABILITY)

    @EV.hook(priority=80)
    def on_after_damage(self, match, ev):
        if ev.category not in self.TRIGGERS or ev.amount <= 0:
            return
        if not ev.target.vars.get("curse_mark") or ev.source is None:
            return
        doll = match.entity(ev.target.vars["curse_mark"])
        ev.target.vars["curse_mark"] = None
        if doll is not None:
            doll.vars["curse_live"] = False      # 再咒 may lay another now
        match.freeze(ev.source, reason="咒毒")


class Poison:
    """A poisoned unit loses 1 at the end of every round until the doses run out
    (蛇帝's bite). STATUS damage, so it sits where burning ground sits: 圣骑士's
    shield neither stops it nor is spent on it, 石像鬼's stone does not blunt it
    and a blessing does not turn it aside. Flat reduction and 增伤 still apply —
    it is a real hit, just not one anybody swung.

    Every tick of a round lands in the same instant, so two units dying of venom
    together both die. Generic like the rest of this file: it reads a var and
    names no hero. `poison_source` is only ever used for the log."""

    @EV.hook(priority=50)
    def on_round_end(self, match, ctx):
        batch = []
        for e in match.living():
            left = e.vars.get("poison_rounds", 0)
            if left <= 0:
                continue
            e.vars["poison_rounds"] = left - 1
            batch.append(DamageEvent(source=match.entity(e.vars.get("poison_source")),
                                     target=e, amount=1, category=STATUS))
        if batch:
            apply_batch(match, batch)


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
    if target.hp != before:
        match.bus.emit(EV.HEAL, {"entity": target, "source": source})
    return target.hp - before
