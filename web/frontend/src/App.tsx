import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  Ban,
  Box,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Coins,
  Footprints,
  Gauge,
  HeartPulse,
  Layers3,
  LoaderCircle,
  Plus,
  Search,
  Shield,
  SlidersHorizontal,
  Timer,
  Trash2,
  Weight,
  Wind,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import type { BuildSolution, Catalog, Metric, OptimizationResult, UpgradePlan, UpgradeResult } from "./types";

type SelectionStrategy = "best_now" | "balanced" | "upgrade";
type UpgradeState = { loading: boolean; error: string; result: UpgradeResult | null };

const LEVEL_SHORT = ["Не нужно", "Паритет", "Хорошо бы", "Важно", "Очень важно"];

const RARITY_LABELS: Record<string, string> = {
  common: "Обычный",
  uncommon: "Необычный",
  special: "Особый",
  rare: "Редкий",
  exclusive: "Исключительный",
  legendary: "Легендарный",
};

const STAT_LABELS: Record<string, string> = {
  effective_durability: "Приведенка",
  hp_regen_score: "Лечение/с",
  movement_speed: "Скорость",
  total_sprint_speed: "Скорость бега",
  bullet_resistance: "Пулестойкость",
  vitality: "Живучесть",
  stamina: "Выносливость",
  stamina_regeneration: "Восст. выносливости",
  healing_effectiveness: "Эффективность лечения",
  carry_weight: "Переносимый вес",
};

const INFECTION_LABELS: Record<string, string> = {
  radiation: "Радиация",
  temperature: "Температура",
  biological: "Биозаражение",
  psycho: "Пси",
  frost: "Холод",
};

function formatPrice(value: number) {
  return `${(value / 1_000_000).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн`;
}

function signed(value: number, digits = 2) {
  const rounded = Math.abs(value) < 0.0005 ? 0 : value;
  return `${rounded > 0 ? "+" : ""}${rounded.toLocaleString("ru-RU", {
    maximumFractionDigits: digits,
  })}`;
}

function decimal(value: number, digits = 2) {
  const rounded = Math.abs(value) < 0.0005 ? 0 : value;
  return rounded.toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

function valueForStat(solution: BuildSolution, key: string) {
  if (key === "effective_durability" || key === "hp_regen_score") return solution.derived[key] ?? 0;
  if (key === "total_sprint_speed") return (solution.derived[key] ?? 100) - 100;
  return solution.stats[key] ?? 0;
}

function parseDecimal(value: string) {
  const normalized = value.trim().replace(",", ".");
  return normalized === "" ? Number.NaN : Number(normalized);
}

function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [budgetMillions, setBudgetMillions] = useState(50);
  const [armorId, setArmorId] = useState("");
  const [containerId, setContainerId] = useState("");
  const [strategy, setStrategy] = useState<SelectionStrategy>("best_now");
  const [preferences, setPreferences] = useState<Record<string, number>>({
    durability: 3,
    speed: 2,
    regen: 1,
  });
  const [excludedArmorIds, setExcludedArmorIds] = useState<string[]>([]);
  const [excludedContainerIds, setExcludedContainerIds] = useState<string[]>([]);
  const [excludedArtifactIds, setExcludedArtifactIds] = useState<string[]>([]);
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [result, setResult] = useState<OptimizationResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [upgradeStates, setUpgradeStates] = useState<Record<string, UpgradeState>>({});

  useEffect(() => {
    fetch("/api/catalog")
      .then(async (response) => {
        if (!response.ok) throw new Error("Не удалось загрузить каталог");
        return response.json() as Promise<Catalog>;
      })
      .then(setCatalog)
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const mainMetrics = useMemo(() => catalog?.metrics.filter((metric) => metric.group === "main") ?? [], [catalog]);
  const secondaryMetrics = useMemo(
    () => catalog?.metrics.filter((metric) => metric.group === "secondary") ?? [],
    [catalog],
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const parsedTargets = Object.fromEntries(
      Object.entries(targets).map(([key, value]) => [key, parseDecimal(value)]),
    );
    if (Object.values(parsedTargets).some((value) => !Number.isFinite(value))) {
      setError("Укажите числовое значение для каждого минимального свойства");
      return;
    }
    setLoading(true);
    setError("");
    setUpgradeStates({});
    const started = performance.now();
    try {
      const response = await fetch("/api/optimize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          budget: Math.round(budgetMillions * 1_000_000),
          armor_id: armorId || null,
          container_id: containerId || null,
          preferences,
          targets: parsedTargets,
          excluded_armor_ids: excludedArmorIds,
          excluded_container_ids: excludedContainerIds,
          excluded_artifact_ids: excludedArtifactIds,
          max_results: 10,
          min_quality_percent: 95,
          strategy,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Ошибка расчета");
      setResult(payload as OptimizationResult);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Ошибка расчета");
    } finally {
      setLoading(false);
      const elapsed = performance.now() - started;
      if (elapsed < 300) await new Promise((resolve) => window.setTimeout(resolve, 300 - elapsed));
    }
  }

  async function loadUpgrades(solution: BuildSolution) {
    setUpgradeStates((current) => ({
      ...current,
      [solution.build_id]: { loading: true, error: "", result: null },
    }));
    const parsedTargets = Object.fromEntries(
      Object.entries(targets).map(([key, value]) => [key, parseDecimal(value)]),
    );
    try {
      const response = await fetch("/api/upgrade-plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_build: solution,
          preferences,
          targets: parsedTargets,
          excluded_container_ids: excludedContainerIds,
          excluded_artifact_ids: excludedArtifactIds,
          extra_budgets: [2_500_000, 5_000_000, 10_000_000],
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Ошибка расчета улучшений");
      setUpgradeStates((current) => ({
        ...current,
        [solution.build_id]: { loading: false, error: "", result: payload as UpgradeResult },
      }));
    } catch (reason) {
      setUpgradeStates((current) => ({
        ...current,
        [solution.build_id]: {
          loading: false,
          error: reason instanceof Error ? reason.message : "Ошибка расчета улучшений",
          result: null,
        },
      }));
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark"><Layers3 size={20} /></div>
          <div>
          <div className="brand-name">STALZONE</div>
          <div className="brand-section">Сборки артефактов</div>
          </div>
        </div>
        <div className="topbar-title">Калькулятор сборок</div>
        <div className="topbar-status"><span className="status-dot" /> Артефакты +15</div>
      </header>

      <div className="workspace">
        <aside className="control-panel">
          <form onSubmit={submit}>
            <div className="panel-heading">
              <div className="eyebrow">Параметры поиска</div>
              <div className="panel-title-row">
                <h1>Фильтры</h1>
                <span>{excludedArmorIds.length + excludedContainerIds.length + excludedArtifactIds.length} исключено</span>
              </div>
            </div>
            <section className="form-section">
              <div className="section-heading"><Coins size={17} /><h2>Бюджет</h2></div>
              <div className="budget-row">
                <input
                  aria-label="Бюджет в миллионах"
                  className="budget-input"
                  type="number"
                  min={2.5}
                  step={2.5}
                  value={budgetMillions}
                  onChange={(event) => setBudgetMillions(Number(event.target.value))}
                />
                <span>млн</span>
              </div>
            </section>

            <section className="form-section strategy-section">
              <div className="section-heading"><ArrowUpRight size={17} /><h2>Стратегия</h2></div>
              <div className="strategy-segments" role="radiogroup" aria-label="Стратегия подбора">
                {catalog?.strategies.map((option) => (
                  <button
                    type="button"
                    key={option.value}
                    className={strategy === option.value ? "active" : ""}
                    role="radio"
                    aria-checked={strategy === option.value}
                    onClick={() => setStrategy(option.value)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </section>

            <section className="form-section equipment-section">
              <div className="section-heading"><Shield size={17} /><h2>Экипировка</h2></div>
              <label className="field-label" htmlFor="armor">Костюм</label>
              <div className="select-wrap">
                <select
                  id="armor"
                  value={armorId}
                  onChange={(event) => {
                    setArmorId(event.target.value);
                    setExcludedArmorIds((current) => current.filter((id) => id !== event.target.value));
                  }}
                  disabled={!catalog}
                >
                  <option value="">Любой ветеранский или мастерский</option>
                  {catalog?.armors.map((armor) => <option key={armor.id} value={armor.id}>{armor.name}</option>)}
                </select>
                <ChevronDown size={16} />
              </div>
              <label className="field-label" htmlFor="container">Контейнер / рюкзак</label>
              <div className="select-wrap">
                <select
                  id="container"
                  value={containerId}
                  onChange={(event) => {
                    setContainerId(event.target.value);
                    setExcludedContainerIds((current) => current.filter((id) => id !== event.target.value));
                  }}
                  disabled={!catalog}
                >
                  <option value="">Любой контейнер или рюкзак</option>
                  <optgroup label="Контейнеры">
                    {catalog?.containers.filter((container) => container.category === "containers").map((container) => (
                      <option key={container.id} value={container.id}>{container.name} · {container.capacity} сл.</option>
                    ))}
                  </optgroup>
                  <optgroup label="Рюкзаки и разгрузки">
                    {catalog?.containers.filter((container) => container.category === "backpacks").map((container) => (
                      <option key={container.id} value={container.id}>{container.name} · {container.capacity} сл.</option>
                    ))}
                  </optgroup>
                </select>
                <ChevronDown size={16} />
              </div>
            </section>

            <section className="form-section preference-section">
              <div className="section-heading"><SlidersHorizontal size={17} /><h2>Желаемые свойства</h2></div>
              <div className="scale-legend" aria-hidden="true">
                {LEVEL_SHORT.map((label, level) => <span key={label}><b>{level}</b>{label}</span>)}
              </div>
              <MetricList metrics={mainMetrics} preferences={preferences} setPreferences={setPreferences} />
              <details className="secondary-settings">
                <summary>Второстепенные свойства <ChevronDown size={15} /></summary>
                <MetricList metrics={secondaryMetrics} preferences={preferences} setPreferences={setPreferences} />
              </details>
            </section>

            <section className="form-section target-section">
              <TargetFilters metrics={catalog?.metrics ?? []} targets={targets} setTargets={setTargets} />
            </section>

            <section className="form-section exclusion-section">
              <details className="exclusion-settings">
                <summary>
                  <span><Ban size={16} /> Исключить из расчета</span>
                  <span className="exclusion-count">
                    {excludedArmorIds.length + excludedContainerIds.length + excludedArtifactIds.length}
                  </span>
                  <ChevronDown size={15} />
                </summary>
                <div className="exclusion-groups">
                  <ExclusionGroup
                    title="Костюмы"
                    options={catalog?.armors ?? []}
                    selected={excludedArmorIds}
                    setSelected={setExcludedArmorIds}
                    disabledId={armorId}
                  />
                  <ExclusionGroup
                    title="Контейнеры и рюкзаки"
                    options={catalog?.containers ?? []}
                    selected={excludedContainerIds}
                    setSelected={setExcludedContainerIds}
                    disabledId={containerId}
                  />
                  <ExclusionGroup
                    title="Артефакты"
                    options={catalog?.artifacts ?? []}
                    selected={excludedArtifactIds}
                    setSelected={setExcludedArtifactIds}
                  />
                </div>
              </details>
            </section>

            <button className="submit-button" type="submit" disabled={!catalog || loading}>
              {loading ? <><span className="spinner" /> Расчет...</> : <><Search size={18} /> Подобрать сборки</>}
            </button>
          </form>
        </aside>

        <main className="results-panel">
          <div className="results-heading">
            <div>
              <div className="eyebrow">Результаты</div>
              <div className="results-title-row">
                <h1>Рекомендации</h1>
                {result && <span className="result-count">{result.solutions.length}</span>}
              </div>
            </div>
            {result && (
              <div className="runtime"><Timer size={16} /> {result.diagnostics.elapsed_seconds.toFixed(2)} с</div>
            )}
          </div>

          {error && <div className="error-banner"><CircleAlert size={18} />{error}</div>}
          {loading && <LoadingState />}
          {!loading && !result && !error && <EmptyState />}
          {!loading && result && result.solutions.length === 0 && (
              <div className="empty-state"><CircleAlert size={28} /><h2>Подходящих сборок нет</h2></div>
          )}
          {!loading && result && result.solutions.length > 0 && (
            <div className="build-list">
              {result.solutions.map((solution, index) => (
                <BuildCard
                  key={solution.build_id}
                  solution={solution}
                  index={index}
                  upgradeState={upgradeStates[solution.build_id]}
                  onLoadUpgrades={() => loadUpgrades(solution)}
                />
              ))}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function MetricList({
  metrics,
  preferences,
  setPreferences,
}: {
  metrics: Metric[];
  preferences: Record<string, number>;
  setPreferences: (value: Record<string, number>) => void;
}) {
  return (
    <div className="metric-list">
      {metrics.map((metric) => {
        const selected = preferences[metric.key] ?? 0;
        return (
          <div className="metric-control" key={metric.key}>
            <div className="metric-label"><span>{metric.label}</span><strong>{LEVEL_SHORT[selected]}</strong></div>
            <div className="segments" role="radiogroup" aria-label={metric.label}>
              {LEVEL_SHORT.map((label, level) => (
                <button
                  key={label}
                  type="button"
                  className={selected === level ? `active level-${level}` : ""}
                  role="radio"
                  aria-checked={selected === level}
                  title={label}
                  onClick={() => setPreferences({ ...preferences, [metric.key]: level })}
                >
                  <span className="segment-number">{level}</span>
                  <span className="segment-label">{label}</span>
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function TargetFilters({
  metrics,
  targets,
  setTargets,
}: {
  metrics: Metric[];
  targets: Record<string, string>;
  setTargets: (value: Record<string, string>) => void;
}) {
  const [draftMetric, setDraftMetric] = useState("");
  const [draftValue, setDraftValue] = useState("");
  const metricByKey = useMemo(
    () => Object.fromEntries(metrics.map((metric) => [metric.key, metric])),
    [metrics],
  );
  const availableMetrics = metrics.filter((metric) => !(metric.key in targets));
  const parsedDraft = parseDecimal(draftValue);

  function addTarget() {
    if (!draftMetric || !Number.isFinite(parsedDraft)) return;
    setTargets({ ...targets, [draftMetric]: draftValue.trim().replace(",", ".") });
    setDraftMetric("");
    setDraftValue("");
  }

  return (
    <div className="target-settings">
      <div className="target-heading">
        <span><SlidersHorizontal size={16} /> Минимальные значения</span>
        <span className="target-count">{Object.keys(targets).length}</span>
      </div>
      <div className="target-editor">
        {Object.entries(targets).map(([key, value]) => {
          const metric = metricByKey[key];
          const valid = Number.isFinite(parseDecimal(value));
          return (
            <div className={`target-row ${valid ? "" : "invalid"}`} key={key}>
              <span className="target-name">{metric?.label ?? key}</span>
              <span className="target-operator">≥</span>
              <input
                aria-label={`Минимум: ${metric?.label ?? key}`}
                type="text"
                inputMode="decimal"
                value={value}
                onChange={(event) => setTargets({ ...targets, [key]: event.target.value })}
              />
              <button
                type="button"
                aria-label={`Удалить минимум: ${metric?.label ?? key}`}
                title="Удалить условие"
                onClick={() => setTargets(Object.fromEntries(Object.entries(targets).filter(([item]) => item !== key)))}
              >
                <Trash2 size={14} />
              </button>
            </div>
          );
        })}
        {availableMetrics.length > 0 && (
          <div className="target-add-row">
            <div className="select-wrap">
              <select
                aria-label="Свойство для минимального значения"
                value={draftMetric}
                onChange={(event) => setDraftMetric(event.target.value)}
              >
                <option value="">Выберите свойство</option>
                {availableMetrics.map((metric) => <option key={metric.key} value={metric.key}>{metric.label}</option>)}
              </select>
              <ChevronDown size={15} />
            </div>
            <input
              aria-label="Новое минимальное значение"
              type="text"
              inputMode="decimal"
              value={draftValue}
              onChange={(event) => setDraftValue(event.target.value)}
              placeholder="Значение"
            />
            <button
              type="button"
              aria-label="Добавить минимальное значение"
              title="Добавить условие"
              disabled={!draftMetric || !Number.isFinite(parsedDraft)}
              onClick={addTarget}
            >
              <Plus size={15} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function ExclusionGroup({
  title,
  options,
  selected,
  setSelected,
  disabledId = "",
}: {
  title: string;
  options: Array<{ id: string; name: string }>;
  selected: string[];
  setSelected: (value: string[]) => void;
  disabledId?: string;
}) {
  const [query, setQuery] = useState("");
  const visibleOptions = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("ru-RU");
    if (!normalized) return options;
    return options.filter((option) => option.name.toLocaleLowerCase("ru-RU").includes(normalized));
  }, [options, query]);

  function toggle(id: string) {
    setSelected(selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id]);
  }

  return (
    <details className="exclusion-group">
      <summary>
        <span>{title}</span>
        <small>{selected.length > 0 ? selected.length : options.length}</small>
        <ChevronDown size={14} />
      </summary>
      <div className="exclusion-search">
        <Search size={14} />
        <input
          aria-label={`Поиск: ${title}`}
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Поиск"
        />
      </div>
      <div className="exclusion-list">
        {visibleOptions.map((option) => (
          <label key={option.id} className={disabledId === option.id ? "disabled" : ""}>
            <input
              type="checkbox"
              checked={selected.includes(option.id)}
              disabled={disabledId === option.id}
              onChange={() => toggle(option.id)}
            />
            <span>{option.name}</span>
          </label>
        ))}
        {visibleOptions.length === 0 && <div className="no-options">Ничего не найдено</div>}
      </div>
    </details>
  );
}

function BuildCard({
  solution,
  index,
  upgradeState,
  onLoadUpgrades,
}: {
  solution: BuildSolution;
  index: number;
  upgradeState?: UpgradeState;
  onLoadUpgrades: () => void;
}) {
  const keyStats = [
    ["effective_durability", Shield],
    ["hp_regen_score", HeartPulse],
    ["movement_speed", Wind],
    ["total_sprint_speed", Footprints],
    ["stamina_regeneration", Activity],
    ["carry_weight", Weight],
  ] as const;
  const infections = Object.entries(solution.infection.by_type)
    .map(([key, value]) => [key, value, value.after_inner_protection + value.container] as const)
    .filter(([, value, exposure]) => Math.abs(exposure) > 0.0005 || value.margin < 0.05);
  return (
    <article className="build-card">
      <header className="build-header">
        <div className="build-rank">#{index + 1}</div>
        <div className="build-equipment">
          <h2>{solution.armor.name}</h2>
          <div><Box size={15} /> {solution.container.name} · {solution.container.capacity} слотов</div>
        </div>
        <div className="build-meta">
          <strong>{formatPrice(solution.total_price)}</strong>
          <span className="potential-badge">Задел {decimal(solution.upgrade_potential, 0)}</span>
          <span className={`solver-badge ${solution.solver_status.toLowerCase()}`}>
            {solution.solver_status === "OPTIMAL" ? <CheckCircle2 size={13} /> : <Gauge size={13} />}
            {solution.solver_status === "HEURISTIC" ? "Подобрано" : solution.solver_status}
          </span>
        </div>
      </header>

      <div className="artifact-strip">
        {solution.artifacts.map((artifact, artifactIndex) => (
          <div className={`artifact-row rarity-${artifact.quality_tier}`} key={`${artifact.group_id}-${artifactIndex}`}>
            <span className="rarity-swatch" />
            <div className="artifact-name"><strong>{artifact.name}</strong><small>{RARITY_LABELS[artifact.quality_tier]}</small></div>
            <div className="artifact-quality">{artifact.quality_percent.toFixed(2)}%</div>
            <div className="artifact-level">+{artifact.upgrade_level}</div>
            <div className="artifact-price">{formatPrice(artifact.price)}</div>
          </div>
        ))}
      </div>

      <div className="stat-grid">
        {keyStats.map(([key, Icon]) => (
          <div className="stat-cell" key={key}>
            <Icon size={16} />
            <span>{STAT_LABELS[key]}</span>
            <strong>{signed(valueForStat(solution, key))}{key === "effective_durability" || key === "carry_weight" ? "" : "%"}</strong>
          </div>
        ))}
      </div>

      <footer className="build-footer">
        <div className="infection-summary">
          <CheckCircle2 size={15} />
          {infections.length === 0 ? <span>Заражения после защиты нет</span> : infections.map(([key, value, exposure]) => (
            <span className={value.margin < 0.05 ? "near-limit" : ""} key={key}>
              {INFECTION_LABELS[key] ?? key}: {decimal(exposure)} / {decimal(value.limit)}
            </span>
          ))}
        </div>
        <div className="build-actions">
          <button className="upgrade-button" type="button" onClick={onLoadUpgrades} disabled={upgradeState?.loading}>
            {upgradeState?.loading ? <LoaderCircle className="spin-icon" size={14} /> : <ArrowUpRight size={14} />}
            {upgradeState?.loading ? "Считаем" : upgradeState?.result ? "Пересчитать" : "Улучшить"}
          </button>
          <details className="all-stats">
            <summary>Все свойства <ChevronDown size={14} /></summary>
            <div className="all-stats-grid">
              {Object.entries(solution.stats).sort(([left], [right]) => left.localeCompare(right)).map(([key, value]) => (
                <div key={key}><span>{STAT_LABELS[key] ?? key}</span><strong>{signed(value)}</strong></div>
              ))}
            </div>
          </details>
        </div>
      </footer>
      {upgradeState?.error && <div className="upgrade-error"><CircleAlert size={15} />{upgradeState.error}</div>}
      {upgradeState?.result && <UpgradePlans current={solution} result={upgradeState.result} />}
    </article>
  );
}

function UpgradePlans({ current, result }: { current: BuildSolution; result: UpgradeResult }) {
  if (result.plans.length === 0) {
    return <div className="upgrade-empty">В пределах +10 млн улучшений по выбранным свойствам не найдено.</div>;
  }
  return (
    <section className="upgrade-panel">
      <div className="upgrade-heading">
        <div><ArrowUpRight size={16} /><strong>Пути улучшения</strong></div>
        <span>{result.diagnostics.elapsed_seconds.toFixed(2)} с</span>
      </div>
      <div className="upgrade-list">
        {result.plans.map((plan, index) => (
          <UpgradePlanRow key={`${plan.extra_budget}-${plan.result_build.build_id}-${index}`} current={current} plan={plan} />
        ))}
      </div>
    </section>
  );
}

function UpgradePlanRow({ current, plan }: { current: BuildSolution; plan: UpgradePlan }) {
  const deltas = [
    "effective_durability",
    "hp_regen_score",
    "movement_speed",
    "total_sprint_speed",
    "stamina_regeneration",
    "carry_weight",
  ].map((key) => [key, valueForStat(plan.result_build, key) - valueForStat(current, key)] as const)
    .filter(([, value]) => Math.abs(value) > 0.004);
  return (
    <div className="upgrade-row">
      <div className="upgrade-summary">
        <strong>Бюджет +{formatPrice(plan.extra_budget)}</strong>
        <span>{plan.kept_count} из {plan.current_count} артефактов остаются</span>
      </div>
      <div className="container-change">
        <Box size={14} />
        {plan.container_changed ? (
          <><span>{current.container.name}</span><ArrowRight size={13} /><strong>{plan.result_build.container.name}</strong></>
        ) : <strong>{current.container.name} оставить</strong>}
        <small>контейнер без учета стоимости</small>
      </div>
      <div className="artifact-changes">
        <ArtifactChangeList title="Убрать" artifacts={plan.removed_artifacts} empty="Ничего" />
        <ArtifactChangeList title="Купить" artifacts={plan.added_artifacts} empty="Ничего" />
      </div>
      <div className="upgrade-deltas">
        {deltas.map(([key, value]) => (
          <span className={value > 0 ? "positive" : "negative"} key={key}>
            {STAT_LABELS[key]} {signed(value)}{key === "effective_durability" || key === "carry_weight" ? "" : "%"}
          </span>
        ))}
      </div>
      <div className="upgrade-costs">
        <span>Покупка <strong>{formatPrice(plan.purchase_cost)}</strong></span>
        <span>Продажа старых <strong>{formatPrice(plan.resale_credit)}</strong></span>
        <span className="net-cost">Итого <strong>{formatPrice(plan.estimated_net_cost)}</strong></span>
      </div>
    </div>
  );
}

function ArtifactChangeList({ title, artifacts, empty }: { title: string; artifacts: BuildSolution["artifacts"]; empty: string }) {
  return (
    <div className="change-list">
      <b>{title}</b>
      {artifacts.length === 0 ? <span>{empty}</span> : artifacts.map((artifact, index) => (
        <span key={`${artifact.group_id}-${index}`}>
          {artifact.name} · {RARITY_LABELS[artifact.quality_tier]} · {artifact.quality_percent.toFixed(2)}%
        </span>
      ))}
    </div>
  );
}

function LoadingState() {
  return (
    <div className="loading-state">
      <div className="loading-pulse"><SlidersHorizontal size={25} /></div>
      <h2>Подбираем сочетания</h2>
      <div className="loading-lines"><span /><span /><span /></div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="empty-state">
      <div className="empty-visual"><Shield size={34} /><Activity size={22} /><Wind size={26} /></div>
      <h2>Результатов пока нет</h2>
      <div className="empty-stats"><span>Костюм</span><span>Контейнер</span><span>Артефакты</span></div>
    </div>
  );
}

export default App;
