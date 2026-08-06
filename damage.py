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

# A blow tagged with this is not softened by anything: no ward turns it aside, no
# guard blunts it, no mark sharpens it. Handlers still run — they may redirect it
# (a shared body) — but whatever they decide about the size of it is discarded.
UNBLOCKABLE = "unblockable"

# --- elements (inert on their own; tags for later designs to key off) ---
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
    # Health this actually removed, filled in once it lands. Not the same as
    # `amount`: a hero on 2 struck for 7 loses 2. Anything that pays out on damage
    # done (水法师 mending itself) reads this.
    dealt: int = 0

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


def pool_holder(match, entity):
    """The body that actually holds this unit's health, or None if it is its own.
    蛇帝's tail names its head: one creature standing on two squares can be neither
    wounded nor mended twice."""
    if entity is None:
        return None
    hid = entity.vars.get("pool_holder")
    if not hid:
        return None
    holder = match.entity(hid)
    return holder if (holder is not None and holder.alive and holder is not entity) else None


class SharedPool:
    """Sends a blow aimed at one body to the body that holds its health. Sits at the
    very front of the pipeline, so every rule after it — wards, guards, 增伤, the
    lot — reads the real target. Heals route through the same var (see `heal`).

    Generic like the rest of this file: it reads a var and names no hero."""

    @EV.hook(priority=5)
    def on_before_damage(self, match, ev):
        holder = pool_holder(match, ev.target)
        if holder is not None:
            ev.target = holder


class Vulnerability:
    """The mirror of `damage_reduction`: a unit carrying `vulnerable` stacks takes
    that much more from everything — attacks, abilities and burning ground alike
    (剑客's 增伤). Stacks are just a count, so a second mark simply makes it worse.

    Sits beside the flat cut, and only touches a blow that is still going to land:
    something already softened to nothing is not revived by being easier to hurt.

    Generic like the rest of this file — it reads a var and names no hero, so
    anything that can mark a unit works through it.

    Applied after the halving, which is what makes it worth the same to every
    blow that lands. Folded in before it, a +1 mark was worth only half a point
    to 双枪手's second shot and floored away to nothing: two shots into a marked
    hero dealt 5 and 2, exactly what an unmarked one took from the second barrel.
    A mark that says "everything hurts more" has to mean the small blows too."""

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


class Ledger:
    """Every unit's running tally of what it has dealt in the turn it is taking.
    Zeroed when its turn begins, added to as its blows land. Anything that pays out
    on "what it dealt this turn" (法官's verdicts) reads it.

    Generic: it names no hero and every unit keeps one."""

    def on_turn_start(self, match, ctx):
        e = ctx.get("entity")
        if e is not None:
            e.vars["dealt_this_turn"] = 0

    @EV.hook(priority=99)          # last, so it counts what really got through
    def on_after_damage(self, match, ev):
        if ev.source is not None and ev.dealt > 0:
            ev.source.vars["dealt_this_turn"] = (
                ev.source.vars.get("dealt_this_turn", 0) + ev.dealt)


class Verdict:
    """A judgement laid on somebody comes due at the end of their *next* turn, and is
    measured by what they did with it (法官). Reads a var and names no hero.

    The exchange it was laid in is remembered, so a hero acting in the same breath as
    the judge is not judged on that turn — it is the turn after that counts."""

    @EV.hook(priority=50)
    def on_turn_end(self, match, ctx):
        e = ctx.get("entity")
        mark = e.vars.get("judged") if e is not None else None
        if not mark or match.exchange <= mark["exchange"]:
            return
        e.vars["judged"] = None          # it comes due once, dealt or not
        total = e.vars.get("dealt_this_turn", 0)
        judge = match.entity(mark.get("judge"))
        if total <= 0 or not e.alive:
            return
        if mark["kind"] == "reward":
            got = heal(match, e, total, source=judge)
            if got:
                match.log_line(
                    f"{match.label(e)} is answered for what it did — {got} mended.")
        else:
            match.log_line(
                f"{match.label(e)} is answered for what it did — {total} back on it.")
            apply_batch(match, [DamageEvent(source=judge, target=e, amount=total,
                                            category=ABILITY)])


class HalvingRule:
    """The gunslinger's second shot is half a shot, so it halves before anything
    that is charged per blow rather than per point — the mark a hero carries, the
    guard it is holding, the ceiling stone puts on what can get through. Otherwise
    a +1 mark is worth only half a point to the second barrel and floors away to
    nothing, and both shots off a marked hero land for what one of them does.

    It still sits after `OutgoingShift`, which shifts the swing itself: that is
    part of the shot, so half a shot carries half of it."""

    @EV.hook(priority=30)
    def on_before_damage(self, match, ev):
        if "halve" in ev.tags and not ev.cancelled:
            ev.amount = ev.amount // 2


def deal(match, ev):
    """Apply a single already-built DamageEvent. Deaths are NOT resolved here;
    the caller sweeps for them so that a batch can be applied simultaneously."""
    fixed = ev.amount if UNBLOCKABLE in ev.tags else None
    match.bus.emit(EV.BEFORE_DAMAGE, ev)
    if fixed is not None:
        # Let the pipeline have its say — redirection still matters — then put the
        # number back. Cheaper and far safer than teaching every rule to opt out.
        ev.cancelled, ev.cancel_reason, ev.amount = False, None, fixed
    if ev.cancelled or ev.amount <= 0:
        if ev.cancelled and ev.cancel_reason:
            match.log_line(
                f"{match.label(ev.target)} takes no damage ({ev.cancel_reason}).", quiet=True
            )
        return 0
    before = ev.target.hp
    ev.target.hp = max(0, ev.target.hp - ev.amount)
    # The health actually taken, which is not the same as the size of the blow: a
    # hero on 2 struck for 7 loses 2. Callers that undo a hit (教皇 stepping in
    # front of it) must put back exactly what was removed and no more.
    removed = ev.dealt = before - ev.target.hp
    match.bus.emit(EV.AFTER_DAMAGE, ev)
    return removed


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
    # Mending half a two-bodied creature mends the creature (蛇帝) — without this
    # the health goes onto the half that does not hold the pool and is lost.
    target = pool_holder(match, target) or target
    if not target.alive:
        return 0
    before = target.hp
    target.hp = min(target.max_hp, target.hp + amount)
    if target.hp != before:
        match.bus.emit(EV.HEAL, {"entity": target, "source": source})
    return target.hp - before
