# Query-Time Search and Personal Feedback

## Independent Components

- `ArtifactCatalogCompiler`: compiles item properties and market estimates, not builds.
- `FrontierBuildGenerator`: query-time speed/durability portfolio generation.
- `ArtifactBuildOptimizer`: focused constrained optimization, usable without the web service.
- `FeedbackRanker`: reorders an existing portfolio using context-matched examples.
- `FeedbackStore`: optional SQLite persistence for search snapshots and feedback.

The search never reads feedback when checking mechanics or hard constraints.
The ranker does not remove candidates. Thus speed and durability extremes remain
available even after personalized ordering. It cannot discover a composition that
the solver did not generate. No offline build index or separate benchmark service
is required.

## Equipment Selection

The catalog includes veteran/master equipment plus ZIVCAS M2-C, Albatross
Stormtrooper (both +15), Chitin backpack, and the existing Tri-Zip exception.
Original item ranks and properties are preserved.

`POST /api/optimize` accepts optional `armor_rank: "master"` and
`container_rank: "master"`. These select only the exact Master rank, not Veteran
or Legend, and apply before generation in both normal and reaction searches.
The container filter includes Master backpacks. Explicit item IDs intersect
the rank filter; incompatible combinations return 422. Exclusions still apply.
Omitting the rank means no rank restriction within the compiled catalog.
The API translates ranks into item ID sets for the independent search engine.

## Stat Display

Every solution, including upgrade results, exposes `loadout_stats`: the sum of
container properties and artifact properties after container effectiveness,
without armor or character base stats. Infection exposure remains in the
separate `infection` report, with the existing protection and base-output rules.
`stats` and `derived` retain their original full-build meaning; search conditions
and ranking are unchanged.

Cards switch between full results and artifact/container bonuses. The latter
show bullet resistance and vitality separately, raw movement/sprint bonuses,
regeneration and periodic healing rather than an invented additive durability
or healing contribution. Expanded properties follow the selected view. Reaction
builds also expose passive `loadout_stats`: only reaction bonuses from artifacts
and the container appear there, without armor reaction bonuses.

## Budget and Prices

`POST /api/optimize` accepts an integer budget of at least 100000 RUB, with no
2.5-million step. `budget: null` means unlimited. In unlimited mode the search
does not optimize purchase cost, penalize expensive builds, or require a price
estimate. `targets` may be empty in this mode. All supplied stat thresholds remain
hard constraints in both modes.

The default catalog uses observed +15 market estimates without a flat upgrade
surcharge. STALDATA's fair-price source identifies recent 7/30-day or older
history. Aligned sales windows take precedence over legacy counter fields.
Low-sample/history and ask-only estimates remain explicitly uncertain. Sales of
+12..14 are retained as reference metadata, not substituted for +15 prices.
The API does not promise that a historical price is currently executable, or
that multiple copies can all be purchased at that price.

Unknown +15 prices are excluded from a finite-budget search. In unlimited mode
they are allowed, `price_estimate.available` is false, and the UI must not present
the sum of known prices as a complete build price. The internal zero price for
such a group is a placeholder, not a free purchase.

## Search Details

Each search has a target wall-clock budget of 19 seconds. Solver startup and
postprocessing may add overhead; this is not a hard real-time guarantee. Time is
shared across containers, focused solves and adaptive frontier gaps. HiGHS models
reuse their unchanged matrix prefix and feasible incumbents within one request.
Candidate states never leak across requests. CPU searches are bounded per worker
process and identical in-flight requests share work.

The main composition problem relaxes quality sums; if rounding to hundredths
violates exact validation, a small integer problem repairs quality for that fixed
composition. This avoids making every unused quality variable integral. Failed
exact validation does not certify infeasibility.

For positive effective durability T, the requirement is
`V >= 100*T/(B+100)-100`, in the physical domain `B+100 > 0`, `V+100 > 0`.
Tangents of this convex boundary are valid outer constraints. Maximizing durability
uses supporting planes of the concave geometric mean `sqrt((B+100)*(V+100))`.
Every returned build is checked with the original formulas and infection limits.
Healing targets retain the existing MILP-seed/CP-SAT fallback and are not claimed
to be globally optimized by the durability cuts.

`search_complete` is false for the sampled portfolio, even when individual
focused problems are optimal. An empty result is not a proof that no legal build
exists. `focused_searches_complete`, `solver_statuses`, `milp_calls` and
`model_reuses` expose more specific diagnostics.

## Feedback API

An opaque HttpOnly, SameSite cookie identifies a browser-local preference profile.
This is not an account or cross-device login. Clearing cookies loses access to
the profile. There is no automatic global learning from anonymous votes.

- Optimize responses include `search_id`. The server stores the original query
  and returned builds. Client-supplied invented stats are not accepted as training
  examples.
- `POST /api/feedback`: `search_id`, `build_id`, `rating` (-1 or 1), `reason`,
  optional `other_id`. A comparison means the positively rated build is preferred
  over `other_id`, from the same search.
- Reasons: `balance`, `price`, `composition`, `availability`, `upgrade`,
  `data_error`. Data-error reports are retained but excluded from learning.
- `DELETE /api/feedback/{id}` undoes a personal rating.
- `GET /api/feedback` lists the most recent 50 personal ratings.
- `personalize: false` in the optimize payload bypasses ranking without deleting
  history. Personalized ordering is applied after the shared calculation cache.

The first version combines context-sensitive similarity memory with a small
regularized pairwise logistic ranker. Features describe stat tradeoffs, price
relative to the budget, acquisition uncertainty and the existing upgrade-potential
estimate. Composition-specific votes transfer mainly to similar item sets. Context
matching considers armor, container, quality limit, target keys and magnitudes,
and budget scale. Price features and price-only votes are ignored in unlimited
mode. One negative vote is not converted into a global artifact ban.

Search snapshots expire after 30 days, with at most 100 snapshots per profile.
At most 1000 feedback records per profile are retained. Re-rating the same build
and query updates the existing record instead of multiplying its influence.

## Deployment and Verification

Compose mounts `feedback_data` at `/app/data/feedback`; it survives application
rebuilds and normal `git pull` deployment. Do not remove this volume when updating.
Outside Docker, `ARTCALC_FEEDBACK_DB` overrides the local SQLite path. SQLite uses
WAL and transactions and supports multiple API worker processes on the same host.
Back up the database with SQLite's backup API, not an isolated copy of a live main
file while ignoring its WAL. This deployment is single-host, not a distributed DB.

`ARTCALC_CONCURRENT_SEARCHES=1` limits CPU work per API worker; total possible
parallelism also depends on `WEB_CONCURRENCY`. Feedback is private operational
data and is excluded from Git. Do not expose the database via static hosting.

Focused verification:

```sh
python -m unittest tests.test_optimizer tests.test_frontier tests.test_market_prices tests.test_feedback tests.test_search_jobs tests.test_web_api
cd web/frontend
npm run build
```
