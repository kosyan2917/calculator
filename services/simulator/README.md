# Simulator service

Independent shooting simulator backend. The calculation engine lives in the
top-level `combat_simulator` package and has no FastAPI dependency.

Public API prefix: `/api/simulator/*`.

Current contracts:

- `GET /api/simulator/health`
- `POST /api/simulator/shooting`

The shooting endpoint accepts a weapon damage profile, ammunition modifiers,
target bullet resistance and vitality, distance, and one of the `low`,
`medium`, or `high` accuracy tiers.

Armor penetration reduces total bullet resistance before vitality is applied:

```text
effective_br = bullet_resistance * (1 - armor_penetration / 100)
effective_health = (100 + effective_br) * (1 + vitality / 100)
```

Negative armor penetration therefore increases effective bullet resistance.
Armor plate penetration and damage-over-time effects are separate mechanics
and are not part of this direct-damage calculation.
