"""The board's own state layer (spec 7.5).

Cells carry effects independent of their occupants. The fire mage's tiles are
permanent, stack additively, and belong to the side that placed them — a tile
only burns that side's enemies.
"""

import damage as DMG


class BurningTile:
    kind = "burning"

    def __init__(self, owner_side, damage_per_stack=2):
        self.owner_side = owner_side
        self.stacks = 1
        self.damage_per_stack = damage_per_stack

    @property
    def damage(self):
        return self.stacks * self.damage_per_stack

    def describe(self):
        return f"Burning x{self.stacks} ({self.damage} fire)"


class Board:
    def __init__(self, topology):
        self.topology = topology
        self.effects = {}  # cell -> list of effects

    def effects_at(self, cell):
        return self.effects.get(cell, [])

    def add_burning(self, cell, owner_side, stacks=1):
        existing = None
        for eff in self.effects_at(cell):
            if eff.kind == "burning" and eff.owner_side == owner_side:
                existing = eff
                break
        if existing:
            existing.stacks += stacks
            return existing
        tile = BurningTile(owner_side)
        tile.stacks = stacks
        self.effects.setdefault(cell, []).append(tile)
        return tile

    def burning_damage_for(self, cell, entity):
        """Total fire damage a given entity would take starting its turn here.
        A tile never burns its owner's own side."""
        total = 0
        for eff in self.effects_at(cell):
            if eff.kind == "burning" and eff.owner_side != entity.side:
                total += eff.damage
        return total

    def serialise(self):
        out = []
        for cell, effs in self.effects.items():
            for eff in effs:
                out.append(
                    {
                        "cell": list(cell),
                        "kind": eff.kind,
                        "owner": eff.owner_side,
                        "stacks": eff.stacks,
                        "damage": eff.damage,
                    }
                )
        return out
