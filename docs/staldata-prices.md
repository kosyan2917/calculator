# STALDATA artifact prices

## API endpoints

STALDATA exposes the browser API under:

```text
https://staldata.org/api/stalcraft/public/
```

The frontend uses these market endpoints:

```text
GET /market/artifact-matrix?region=RU&item_id=<id>
GET /market/sales?region=RU&item_id=<id>&from=<iso>&to=<iso>&limit=<n>&offset=<n>
GET /market/history?region=RU&item_id=<id>&from=<iso>&to=<iso>&limit=<n>
GET /market/fresh?region=RU&item_id=<id>
GET /market/intelligence/item?region=RU&item_id=<id>&quality_tier=<tier>&quality_raw=<raw>&upgrade_level=<level>
```

For local artifact price tables we use `artifact-matrix`, because it already returns
one row per artifact segment:

```text
item_id + quality_tier + quality_raw + upgrade_level
```

Each row includes `fair_price`, `fair_price_source`, active asks, sales counts,
liquidity/risk/confidence scores, and market depth fields.

## Local price basis

The generated `price` field is selected like this:

```text
recent_7d        fair_price from raw_7d and at least --min-recent-sales sales
recent_30d       fair_price from raw_7d/raw_30d when the 30-day sample is usable
older_history    fair_price from history_3h with enough sales
low_data_history fair_price from history_3h or another low-data source
active_ask_only  best_ask when no fair_price exists
unavailable      no usable price
```

Default `--min-recent-sales` is `5`.

This matches the intended weighting: recent sales are preferred, thin segments fall
back to broader/older history, and ask-only prices are kept but marked as low trust.

## Refresh commands

Fetch fresh STALDATA cache:

```powershell
.\tools\fetch_staldata_matrix.ps1
```

Build local JSON and CSV from the cache:

```powershell
python tools\staldata_prices.py --offline
```

Outputs:

```text
data/artifact_prices.json
data/artifact_prices.csv
data/staldata_cache/artifact_matrix/RU/*.json
```
