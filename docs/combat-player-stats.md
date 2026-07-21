# Combat Player Stats Collection

The collector is intentionally source-agnostic at the output level: every source
should be flattened into the same CSV columns and preserved as raw JSONL.

## Current Source

`stalzone.wiki` is used through the public character pages:

- candidates: `/api/characters/recent/`
- candidates: `/api/characters/popular/?period=week|month|all`
- candidates: `/api/characters/suggestions/?query=<text>`
- profile: `/characters/<region>/<nickname>`

The profile page embeds a Next.js payload with `profile.stats`. The collector
extracts that object and saves the full profile to `data/combat_player_stats_raw.jsonl`.

## Important Stat Ids

| Output field | Source stat id | Meaning |
| --- | --- | --- |
| `kills` | `kil` | Raw total kills |
| `deaths` | `dea` | Raw total deaths |
| `kd_raw` | computed | `kills / deaths`, without any PvP corrections |
| `shots_fired` | `sho-fir` | Shots fired |
| `shots_hit` | `sho-hit` | Number of hits |
| `hit_head` | `sho-hea` | Hits to head |
| `hit_body` | `sho-bod` | Hits to torso/body |
| `hit_limbs` | `sho-lim` | Hits to limbs |
| `death_bul_dea` | `bul-dea` | Deaths from bullets |
| `death_exp_dea` | `exp-dea` | Deaths from explosions |
| `death_fal_dea` | `fal-dea` | Deaths from falling |
| `death_rad_dea` | `rad-dea` | Deaths from radiation |
| `death_col_dea` | `col-dea` | Deaths from cold |
| `death_ble_to_dea` | `ble-to-dea` | Deaths from blood loss |
| `death_ano_dea` | `ano-dea` | Deaths from anomalies, total |
| `suicides` | `suicides` | Suicides |

Anomaly subtype columns keep their original ids in the field name, for example
`death_lig_ano_dea`, because the internal abbreviations should be verified
before assigning user-facing names.

## Usage

```powershell
python tools\fetch_combat_player_stats.py --limit 40
```

By default the collector keeps only characters with `lastLogin` not older than
365 days, which matches the combat-simulator sampling goal. Use
`--max-last-login-days -1` to disable that filter.
