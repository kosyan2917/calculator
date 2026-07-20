# Build stat mapping

Stable stat ids in `stalzone-database` use this prefix:

```text
stalker.artefact_properties.factor.
```

## Primary stats

| Параметр в калькуляторе | Stat id в файлах | Поле в локальных выгрузках |
|---|---|---|
| Пулестойкость | `stalker.artefact_properties.factor.bullet_dmg_factor` | `bullet_resistance` |
| Живучесть | `stalker.artefact_properties.factor.health_bonus` | `vitality` |
| Приведенка | derived | `effective_durability` |
| Скорость передвижения | `stalker.artefact_properties.factor.speed_modifier` | `movement_speed` |
| Скорость бега | `stalker.artefact_properties.factor.sprint_speed_modifier` | `sprint_speed` |
| Итог скорости бега | derived | `total_sprint_speed` |
| Восстановление выносливости | `stalker.artefact_properties.factor.stamina_regeneration_bonus` | `stamina_regeneration` |
| Регенерация здоровья | `stalker.artefact_properties.factor.regeneration_bonus` | `health_regeneration` |
| Периодическое лечение | `stalker.artefact_properties.factor.artefakt_heal` | `periodic_healing` |
| Реген хп | derived | `hp_regen_score` |
| Эффективность лечения | `stalker.artefact_properties.factor.heal_efficiency` | `healing_effectiveness` |
| Переносимый вес | `stalker.artefact_properties.factor.max_weight_bonus` | `carry_weight` |

Derived formulas:

```text
effective_durability = (bullet_resistance + 100) * (vitality + 100)
total_sprint_speed = 100 + movement_speed + sprint_speed
hp_regen_score = health_regeneration / 5 + periodic_healing * (100 + healing_effectiveness)
```

## Secondary tie-breaker stats

| Параметр в калькуляторе | Stat id в файлах | Поле в локальных выгрузках |
|---|---|---|
| Выносливость | `stalker.artefact_properties.factor.stamina_bonus` | `stamina` |
| Вывод кровотечения | `stalker.artefact_properties.factor.bleeding_accumulation` | `bleeding_output` |
| Сопротивление кровотечению | `stalker.artefact_properties.factor.bleeding_protection` | `bleeding_resistance` |
| Реакция на ожог | `stalker.artefact_properties.factor.reaction_to_burn` | `burn_reaction` |
| Реакция на разрыв | `stalker.artefact_properties.factor.reaction_to_tear` | `tear_reaction` |

## Other useful protection stats

| Параметр | Stat id в файлах | Поле в локальных выгрузках |
|---|---|---|
| Защита от разрыва | `stalker.artefact_properties.factor.tear_dmg_factor` | `tear_protection` |
| Защита от взрыва | `stalker.artefact_properties.factor.explosion_dmg_factor` | `explosion_protection` |
| Электрозащита | `stalker.artefact_properties.factor.electra_dmg_factor` | `electricity_protection` |
| Защита от огня | `stalker.artefact_properties.factor.burn_dmg_factor` | `fire_protection` |
| Химзащита | `stalker.artefact_properties.factor.chemical_burn_dmg_factor` | `chemical_protection` |
| Защита от радиации | `stalker.artefact_properties.factor.radiation_protection` | `radiation_protection` |
| Защита от температуры | `stalker.artefact_properties.factor.thermal_protection` | `thermal_protection` |
| Защита от биозаражения | `stalker.artefact_properties.factor.biological_protection` | `biological_protection` |
| Защита от пси-излучения | `stalker.artefact_properties.factor.psycho_protection` | `psycho_protection` |
| Защита от холода | `stalker.artefact_properties.factor.frost_protection` | `frost_protection` |
| Стойкость | `stalker.artefact_properties.factor.stopping_protection` | `stability` |

## Other accumulation/reaction stats

| Параметр | Stat id в файлах | Поле в локальных выгрузках |
|---|---|---|
| Радиация | `stalker.artefact_properties.factor.radiation_accumulation` | `radiation` |
| Пси-излучение | `stalker.artefact_properties.factor.psycho_accumulation` | `psycho` |
| Температура | `stalker.artefact_properties.factor.thermal_accumulation` | `temperature` |
| Биологическое заражение | `stalker.artefact_properties.factor.biological_accumulation` | `biological` |
| Холод | `stalker.artefact_properties.factor.frost_accumulation` | `frost` |
| Горение | `stalker.artefact_properties.factor.combustion_accumulation` | `combustion` |
| Реакция на хим. ожог | `stalker.artefact_properties.factor.reaction_to_chemical_burn` | `chemical_burn_reaction` |
| Реакция на электричество | `stalker.artefact_properties.factor.reaction_to_electroshock` | `electroshock_reaction` |
| Отдача | `stalker.artefact_properties.factor.recoil_bonus` | `recoil` |
| Покачивание | `stalker.artefact_properties.factor.wiggle_bonus` | `wiggle` |

## Boost-only stats also preserved

| Parameter | Stat id in files | Local field |
|---|---|---|
| Radiation resistance | `stalker.artefact_properties.factor.radiation_dmg_factor` | `radiation_resistance` |
| Psy-emission resistance | `stalker.artefact_properties.factor.psycho_dmg_factor` | `psycho_resistance` |
| Temperature resistance | `stalker.artefact_properties.factor.thermal_dmg_factor` | `temperature_resistance` |
| Bioinfection resistance | `stalker.artefact_properties.factor.biological_dmg_factor` | `biological_resistance` |
| Frost resistance | `stalker.artefact_properties.factor.frost_dmg_factor` | `frost_resistance` |
| Bleeding damage resistance | `stalker.artefact_properties.factor.bleeding_dmg_factor` | `bleeding_damage_resistance` |
| Toxicity | `stalker.artefact_properties.factor.toxic_accumulation` | `toxicity` |
| Satiety | `stalker.artefact_properties.factor.filler_modifier` | `satiety` |
