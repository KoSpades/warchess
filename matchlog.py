"""Match logging — a record of games played against the AI, kept so the AI can
be made better from real play rather than only from bots hitting each other.

One file per game, JSON Lines, under `logs/`:

    logs/pve-20260805-142233-a1b2c3.jsonl

Every line is one record with a `t` (type) and a `ts` (seconds since the game
started). The types, in the order they appear:

    start        seed, mode, which seat the bot holds, roster size
    draft        one pick: side, hero taken, what was on offer. The bot's picks
                 carry `scores` — what it thought each card on offer was worth.
    deploy       one side's whole placement, once it locks in
    turn         one committed order. This is the record that matters:
                   before   board snapshot taken *before* the order resolves
                   side, entity, order   what was actually played
                   ranked   every hero that could have acted, with the score the
                            AI gave its best order — for the bot, the reasoning
                            behind the move it made; for the player, what the AI
                            *would* have done in the same position
                   eval     what the AI thinks the board is worth, from the
                            bot's side, before the order resolves
    cmd          any other command the player sent (draft/lock/victim/…), with
                 the request `body` verbatim. With `seed` from `start`, the cmd
                 + turn stream is enough to replay a game exactly.
    end          winner, rounds, exchanges, final board

The point of `ranked` plus `eval`: replaying a game gives a series of positions
labelled with both what the AI scored and what actually happened, so a change to
`ai.py` can be checked against real games — did it agree with the moves that won,
did its evaluation track the result — instead of only against itself.

Nothing here is on the hot path of a headless tournament: only PvE writes logs.
"""

import json
import os
import time

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def snapshot(m):
    """The board, small enough to write on every turn and complete enough to set
    a position back up: who is standing where with how much left."""
    out = []
    for e in m.entities:
        if not e.alive:
            continue
        cells = sorted(e.cells)
        out.append({
            "id": e.id,
            "key": e.key,
            "side": e.side,
            "cells": [list(c) for c in cells],
            "hp": e.hp,
            "max_hp": e.max_hp,
            "ap": e.ap,
            "atk": e.atk,
            "acted": e.has_acted,
        })
    return {"round": m.round, "exchange": m.exchange, "phase": m.phase, "units": out}


class MatchLog:
    """Append-only JSONL for one game. Every record is flushed as it is written,
    so a server killed mid-game still leaves everything up to that point."""

    def __init__(self, tag="pve", directory=LOG_DIR):
        os.makedirs(directory, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.path = os.path.join(directory, f"{tag}-{stamp}-{os.urandom(3).hex()}.jsonl")
        self.t0 = time.time()
        self.closed = False
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, kind, **fields):
        if self.closed:
            return
        rec = {"t": kind, "ts": round(time.time() - self.t0, 3)}
        rec.update(fields)
        try:
            self._fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            self._fh.flush()
        except Exception:
            # A game must never die because its log could not be written.
            self.closed = True

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self._fh.close()
        except Exception:
            pass
