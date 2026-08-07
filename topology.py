"""Board geometry.

Per spec 7.6, nothing outside this module may compute neighbours or distance
with raw arithmetic. Every adjacency, distance, and legality question routes
through a Topology instance so that later heroes can rewrite the board's shape
(diagonal adjacency, linked cells, attached sub-maps) without
touching the rest of the engine.
"""

LEFT = "L"
RIGHT = "R"


def other_side(side):
    return RIGHT if side == LEFT else LEFT


class Door:
    """Two squares joined for one side, and the count of how many times its own
    side may still walk through.

    Muting is how the engine asks "was this door needed?" — the walk is worked out
    again with the door shut, and a destination that falls out of reach must have
    gone through it. Cheaper than remembering the path a hero took, and it answers
    the only question anybody asks of it."""

    __slots__ = ("a", "b", "side", "passages", "muted")

    def __init__(self, a, b, side, passages=None):
        self.a, self.b, self.side = tuple(a), tuple(b), side
        self.passages = passages
        self.muted = False

    @property
    def open(self):
        return not self.muted and (self.passages is None or self.passages > 0)

    def holds(self, cell):
        return tuple(cell) in (self.a, self.b)

    def across(self, cell):
        """The square on the other side of this door, or None if it does not open
        onto the one asked about."""
        cell = tuple(cell)
        return self.b if cell == self.a else self.a if cell == self.b else None

    def walked(self):
        """Spend one passage. A door with no count never runs down."""
        if self.passages is not None:
            self.passages -= 1


class Topology:
    def __init__(self, cols=9, rows=5):
        self.cols = cols
        self.rows = rows
        # Squares that belong to a sub-map rather than the board proper (探险家's
        # 岛屿): cell -> region name. A cell with no entry is the main map. Two
        # squares in different regions are never neighbours, never within reach of
        # one another, and only the main map is ever listed by `all_cells`, so a
        # region is genuinely a separate board until it is joined back on.
        self.regions = {}
        # Pairs of squares one side may step between as though they touched
        # (工匠's doors): (a, b, owner_side).
        self.links = []

    # --- basic queries -------------------------------------------------

    def in_bounds(self, cell):
        c, r = cell
        return 1 <= c <= self.cols and 1 <= r <= self.rows

    def region(self, cell):
        """Which sub-map this square belongs to. None is the board proper."""
        return self.regions.get(tuple(cell))

    def same_region(self, a, b):
        return self.region(a) == self.region(b)

    def region_cells(self, name):
        return [c for c, n in self.regions.items() if n == name]

    def detach(self, cells, name):
        for c in cells:
            self.regions[tuple(c)] = name

    def rejoin(self, name):
        """Fold a sub-map back into the board. Everything standing on it simply
        finds itself on the main map, where it always physically was."""
        for c in self.region_cells(name):
            self.regions.pop(c, None)

    def all_cells(self):
        """The board proper. A detached region is deliberately absent: every list of
        squares an ability may name is built from this, so nothing can reach into a
        sub-map without asking for it by name."""
        return [
            (c, r)
            for c in range(1, self.cols + 1)
            for r in range(1, self.rows + 1)
            if (c, r) not in self.regions
        ]

    def link(self, a, b, side, passages=None):
        """Join two squares for one side. They are neighbours for that side and
        nobody else, and the board is otherwise unchanged.

        `passages` is how many times it may be walked through before the way
        closes; None is a door that never wears out. Whoever builds the door owns
        that number — the board only counts."""
        self.links.append(Door(a, b, side, passages))
        return self.links[-1]

    def closed_to(self, cell, side):
        """True if that side may not stand on this square at all. A door is already
        a per-side property of the board — one side may step between its two squares
        and nobody else — and this is the same fact read the other way round: to the
        other side those two squares are wall.

        A spent door is still a door: what runs out is the way through, not the
        thing standing there, so those squares are no more open to the other side
        than they were on the first round."""
        cell = tuple(cell)
        return any(d.holds(cell) and d.side != side for d in self.links)

    def linked_from(self, cell, entity):
        """The far side of any door this unit may walk through from here. A door
        with no passages left is not one of them, nor is one muted while the
        engine works out whether a walk needed it."""
        if entity is None:
            return []
        out = []
        for d in self.links:
            if entity.side != d.side or not d.open:
                continue
            far = d.across(cell)
            if far is not None:
                out.append(far)
        return out

    def neighbours(self, cell, entity=None):
        """Orthogonal, plus any square linked to this one for that particular unit.
        The `entity` hook is what makes adjacency a per-unit question: a door is a
        neighbour for the side that built it and a plain square for everyone else.
        Callers that mean "the board's own neighbours" simply pass nothing."""
        c, r = cell
        cands = [(c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)]
        out = [x for x in cands if self.in_bounds(x) and self.same_region(x, cell)]
        for x in self.linked_from(cell, entity):
            if self.in_bounds(x) and self.same_region(x, cell) and x not in out:
                out.append(x)
        return out

    def distance(self, a, b, entity=None):
        # Nothing on a sub-map is at any reach from the board proper. Returning a
        # distance past the far corner keeps every caller's `<= range` test honest
        # without any of them having to know regions exist.
        if not self.same_region(a, b):
            return self.cols + self.rows
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def cells_within(self, origin, rng, entity=None):
        return [
            cell
            for cell in self.all_cells()
            if self.distance(origin, cell, entity) <= rng
        ]

    # --- lines ---------------------------------------------------------

    def column(self, col):
        return [(col, r) for r in range(1, self.rows + 1)
                if self.in_bounds((col, r)) and self.region((col, r)) is None]

    def row(self, row):
        return [(c, row) for c in range(1, self.cols + 1)
                if self.in_bounds((c, row)) and self.region((c, row)) is None]

    def lane(self, origin, step):
        """The squares a straight line crosses from `origin`, leaving out any that
        belong to another map — a line passes clean over a sub-map rather than
        stopping at it. Every lane walk reads this (狙击手's shot, 渔夫's hook), so
        none of them can disagree about what a line crosses."""
        return [c for c in self.ray(origin, step) if self.same_region(c, origin)]

    def connected(self, cells):
        """True if the group is one piece — every square touching at least one other
        through the rest of the set. Used by squad placement (哥布林团伙 must go down
        as one blob) and by any ability that asks for a contiguous shape (水法师)."""
        pool = {tuple(c) for c in cells}
        if len(pool) < 2:
            return True
        start = next(iter(pool))
        seen, frontier = {start}, [start]
        while frontier:
            cur = frontier.pop()
            for n in self.neighbours(cur):
                if n in pool and n not in seen:
                    seen.add(n)
                    frontier.append(n)
        return len(seen) == len(pool)

    # --- sides ---------------------------------------------------------

    def deployment_zone(self, side):
        cols = (1, 2, 3) if side == LEFT else (7, 8, 9)
        return [c for col in cols for c in self.column(col)
                if self.region(c) is None and not self.closed_to(c, side)]

    def forward_step(self, side):
        """+1 column is 'forward' for Left, -1 for Right."""
        return 1 if side == LEFT else -1

    # --- directions and rays ------------------------------------------

    DIRECTIONS = ("forward", "backward", "up", "down")
    # The four straight ones plus the corners, for anything thrown rather than
    # walked (渔夫's hook). Named from the thrower's point of view like the rest.
    DIRECTIONS8 = DIRECTIONS + ("fwd_up", "fwd_down", "back_up", "back_down")

    def direction_step(self, side, name):
        """A named direction as a (dc, dr) step, from that side's point of view.
        None for anything unrecognised, so callers can validate by asking."""
        fwd = self.forward_step(side)
        return {"forward": (fwd, 0), "backward": (-fwd, 0),
                "up": (0, -1), "down": (0, 1),
                "fwd_up": (fwd, -1), "fwd_down": (fwd, 1),
                "back_up": (-fwd, -1), "back_down": (-fwd, 1)}.get(name)

    def cone(self, origin, step):
        """The square one step away plus the two flanking it — a three-cell arc
        in that direction. Used by a spread weapon."""
        perp = (0, 1) if step[0] else (1, 0)
        c, r = origin
        cells = [(c + step[0], r + step[1]),
                 (c + step[0] + perp[0], r + step[1] + perp[1]),
                 (c + step[0] - perp[0], r + step[1] - perp[1])]
        return [x for x in cells if self.in_bounds(x)]

    def ray(self, origin, step, limit=None):
        """Cells walking outward from (but not including) origin, in order, until
        the board runs out — the lane a charge or a sniper's shot travels."""
        c, r = origin
        out = []
        while limit is None or len(out) < limit:
            c, r = c + step[0], r + step[1]
            if not self.in_bounds((c, r)):
                break
            out.append((c, r))
        return out
