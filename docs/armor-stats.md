# Armor stats export

Generated from:

```text
stalzone-database/ru/items/armor
```

Refresh:

```powershell
python tools\extract_armor_stats.py
```

Outputs:

```text
data/armor_stats.json
data/armor_stats.csv
```

The export includes base armor and all upgrade variants:

```text
upgrade_level = 0    base item
upgrade_level = 1-15 item from _variants/<base_id>/<level>.json
```

Stable columns for build scoring:

```text
bullet_resistance
vitality
movement_speed
sprint_speed
stamina
stamina_regeneration
health_regeneration
periodic_healing
healing_effectiveness
carry_weight
bleeding_output
bleeding_resistance
burn_reaction
tear_reaction
```

Derived fields:

```text
effective_durability = (bullet_resistance + 100) * (vitality + 100)
total_sprint_speed = 100 + movement_speed + sprint_speed
hp_regen_score = health_regeneration / 5 + periodic_healing * (100 + healing_effectiveness)
```

Armor variant files duplicate `bullet_dmg_factor`: first as the final bullet
resistance and later as the upgrade delta. The export keeps the first value in
`stats.bullet_resistance` and stores the later value under `duplicate_stats`.
