"""Board geometry.

Per spec 7.6, nothing outside this module may compute neighbours or distance
with raw arithmetic. Every adjacency, distance, and legality question routes
through a Topology instance so that later heroes can rewrite the board's shape
(diagonal adjacency, linked cells, removed columns, attached sub-maps) without
touching the rest of the engine.
"""

LEFT = "L"
RIGHT = "R"


def other_side(side):
    return RIGHT if side == LEFT else LEFT


class Topology:
    def __init__(self, cols=9, rows=5):
        self.cols = cols
        self.rows = rows
        self.removed = set()

    # --- basic queries -------------------------------------------------

    def in_bounds(self, cell):
        c, r = cell
        return 1 <= c <= self.cols and 1 <= r <= self.rows and cell not in self.removed

    def all_cells(self):
        return [
            (c, r)
            for c in range(1, self.cols + 1)
            for r in range(1, self.rows + 1)
            if (c, r) not in self.removed
        ]

    def neighbours(self, cell, entity=None):
        """Orthogonal only. Diagonal adjacency is a per-entity property that no
        currently implemented hero has, hence the unused `entity` hook."""
        c, r = cell
        cands = [(c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)]
        return [x for x in cands if self.in_bounds(x)]

    def distance(self, a, b, entity=None):
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def cells_within(self, origin, rng, entity=None):
        return [
            cell
            for cell in self.all_cells()
            if self.distance(origin, cell, entity) <= rng
        ]

    # --- lines ---------------------------------------------------------

    def column(self, col):
        return [(col, r) for r in range(1, self.rows + 1) if self.in_bounds((col, r))]

    def row(self, row):
        return [(c, row) for c in range(1, self.cols + 1) if self.in_bounds((c, row))]

    # --- sides ---------------------------------------------------------

    def deployment_zone(self, side):
        cols = (1, 2, 3) if side == LEFT else (7, 8, 9)
        return [c for col in cols for c in self.column(col)]

    def forward_step(self, side):
        """+1 column is 'forward' for Left, -1 for Right."""
        return 1 if side == LEFT else -1
