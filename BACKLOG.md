# Backlog

The known debt and deferred work of skrode, one item each: what is
wrong or missing, why it was deferred, and what picking it up needs. An
item does not say how to fix it; that belongs to the session that does the
fix. What works today and what is limited on purpose are facts, not work,
and live in `docs/`.

## Labels

Each item has an ID heading, then one label line with four keys:

- `type`: `feat`, `fix`, `perf`, `refactor`, `docs` or `chore`, the
  Conventional Commits words, so an item's type is the type of the commit
  that closes it.
- `priority`: `P1` is next, or puts a wrong result in front of the user;
  `P2` is to do when its area is next touched; `P3` waits on evidence or is
  optional.
- `status`: `ready`, `needs-decision`, `needs-data`, `blocked` (on a person
  or a party outside the code) or `in-progress`.
- `area`: `bench`, `docs`, `implementer`, `profiles`, `prompts`, `schemas`, `scripts`, `skrode` or `tests`.

The sections go by type: Features, Bugs, Performance, Code health, and
Outside the code. Status is a label, so an item never changes section, and
a status change is a one-line diff.

## Adding and closing an item

When you knowingly leave something behind, add an item at the end of its
section with the ID `Next ID` gives, and move `Next ID` on by one. An ID is
`BL-NNN`, given once and never reused or renumbered. Two open branches that
both add an item collide on `Next ID`; the second to merge renumbers.

When you finish an item, delete it in the commit that finishes it, with a
`Closes: BL-NNN` trailer, so `git log --grep 'BL-NNN'` finds its history.
There is no Done section.

An item looks like this:

    ### BL-001 · A short noun phrase

    type: fix · priority: P2 · status: ready · area: api

    What is wrong or missing, in a few sentences.

    **Why deferred:** ...

    **Needs:** ...

Next ID: BL-002

## Features

## Bugs

### BL-001 · Bench energy priced at the pre-tax tariff

type: fix · priority: P2 · status: ready · area: bench

`skrode/benchsummary.py` prices worker GPU energy at R$ 0.89181/kWh, the
tariff before ICMS and PIS/COFINS. The bill's cost per extra kWh is
1.146179 with taxes, plus the bandeira (0.022609 under amarela), about 31%
more. The published power dollars in `docs/benchmark.md` and the README
are low by that much. They are fractions of a cent per job, so no
conclusion changes.

**Why deferred:** the fix changes published numbers, so it waits for a
session that regenerates the tables.

**Needs:** a decision on which flag to price at (verde, amarela, or the one
in force at each run), then the rate in `benchsummary.py` and the text at
`docs/benchmark.md` line 138, and the tables regenerated.

## Performance

## Code health

## Outside the code
