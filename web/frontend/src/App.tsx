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
  Search,
  Shield,
  SlidersHorizontal,
  Timer,
  Weight,
  Wind,
  X,
} from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";
import type { BuildSolution, Catalog, Metric, OptimizationResult, UpgradePlan, UpgradeResult } from "./types";

type UpgradeState = { loading: boolean; error: string; result: UpgradeResult | null };
type SearchOption = { id: string; name: string; meta: string };

const RARITY_LABELS: Record<string, string> = {
  common: "Обычный",
  uncommon: "Необычный",
  special: "Особый",
  rare: "Редкий",
  exclusive: "Исключительный",
  legendary: "Легендарный",
};

const QUALITY_LIMITS = [
  { value: "common", label: "Обычное · до 100%" },
  { value: "uncommon", label: "Необычное · до 115%" },
  { value: "special", label: "Особое · до 130%" },
  { value: "rare", label: "Редкое · до 145%" },
  { value: "exclusive", label: "Исключительное · до 160%" },
  { value: "legendary", label: "Легендарное · до 175%" },
] as const;

const STAT_LABELS: Record<string, string> = {
  effective_durability: "Приведенка",
  hp_regen_score: "Лечение/с",
  movement_speed: "Скорость",
  total_sprint_speed: "Итоговая скорость бега",
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

const ROLE_LABELS: Record<string, string> = {
  speed: "скорость",
  durability: "жир",
  regeneration: "реген",
  endurance: "выносливость",
  weight: "вес",
  support: "защита",
  cleanse: "контрарт",
};

const FOCUS_LABELS: Record<string, string> = {
  price: "Дешевле",
  speed: "Макс. скорость",
  durability: "Макс. приведёнка",
  balanced: "Компромисс",
  alternative: "Другой состав",
  frontier: "Граница параметров",
  diverse: "Другой состав",
};

function formatPrice(value: number) {
  return `${(value / 1_000_000).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн`;
}

function signed(value: number, digits = 2) {
  const rounded = Math.abs(value) < 0.0005 ? 0 : value;
  return `${rounded > 0 ? "+" : ""}${rounded.toLocaleString("ru-RU", { maximumFractionDigits: digits })}`;
}

function decimal(value: number, digits = 2) {
  const rounded = Math.abs(value) < 0.0005 ? 0 : value;
  return rounded.toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

function valueForStat(solution: BuildSolution, key: string) {
  if (key === "effective_durability" || key === "hp_regen_score") return solution.derived[key] ?? 0;
  if (key === "total_sprint_speed") return solution.derived[key] ?? 100;
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
  const [maxQualityTier, setMaxQualityTier] = useState("exclusive");
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [excludedArmorIds, setExcludedArmorIds] = useState<string[]>([]);
  const [excludedContainerIds, setExcludedContainerIds] = useState<string[]>([]);
  const [excludedArtifactIds, setExcludedArtifactIds] = useState<string[]>([]);
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
  const secondaryMetrics = useMemo(() => catalog?.metrics.filter((metric) => metric.group === "secondary") ?? [], [catalog]);
  const armorOptions = useMemo<SearchOption[]>(() => catalog?.armors.map((armor) => ({
    id: armor.id,
    name: armor.name,
    meta: armor.rank,
  })) ?? [], [catalog]);
  const containerOptions = useMemo<SearchOption[]>(() => catalog?.containers.map((container) => ({
    id: container.id,
    name: container.name,
    meta: `${container.category === "backpacks" ? "Рюкзак" : "Контейнер"} · ${container.capacity} сл.`,
  })) ?? [], [catalog]);
  const requirementsValid = Object.keys(targets).length > 0
    && Object.values(targets).every((value) => Number.isFinite(parseDecimal(value)));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!requirementsValid) {
      setError(Object.keys(targets).length === 0 ? "Задайте хотя бы одно обязательное значение" : "Проверьте числовые значения условий");
      return;
    }
    const parsedTargets = Object.fromEntries(Object.entries(targets).map(([key, value]) => [key, parseDecimal(value)]));
    setLoading(true);
    setError("");
    setUpgradeStates({});
    try {
      const response = await fetch("/api/optimize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          budget: Math.round(budgetMillions * 1_000_000),
          armor_id: armorId || null,
          container_id: containerId || null,
          targets: parsedTargets,
          max_quality_tier: maxQualityTier,
          excluded_armor_ids: excludedArmorIds,
          excluded_container_ids: excludedContainerIds,
          excluded_artifact_ids: excludedArtifactIds,
          max_results: 10,
          min_quality_percent: 95,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Ошибка расчета");
      setResult(payload as OptimizationResult);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Ошибка расчета");
    } finally {
      setLoading(false);
    }
  }

  async function loadUpgrades(solution: BuildSolution) {
    setUpgradeStates((current) => ({
      ...current,
      [solution.build_id]: { loading: true, error: "", result: null },
    }));
    const parsedTargets = Object.fromEntries(Object.entries(targets).map(([key, value]) => [key, parseDecimal(value)]));
    try {
      const response = await fetch("/api/upgrade-plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_build: solution,
          targets: parsedTargets,
          max_quality_tier: maxQualityTier,
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
          <div><div className="brand-name">STALZONE</div><div className="brand-section">Сборки артефактов</div></div>
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
                <span>{Object.keys(targets).length} условий</span>
              </div>
            </div>

            <section className="form-section">
              <div className="section-heading"><Coins size={17} /><h2>Бюджет</h2></div>
              <div className="budget-row">
                <input aria-label="Бюджет в миллионах" className="budget-input" type="number" min={2.5} step={2.5} value={budgetMillions} onChange={(event) => setBudgetMillions(Number(event.target.value))} />
                <span>млн</span>
              </div>
            </section>

            <section className="form-section equipment-section">
              <div className="section-heading"><Shield size={17} /><h2>Экипировка</h2></div>
              <label className="field-label" htmlFor="armor">Костюм</label>
              <SearchableSelect id="armor" value={armorId} options={armorOptions} placeholder="Любой ветеранский или мастерский" onChange={(value) => { setArmorId(value); setExcludedArmorIds((current) => current.filter((id) => id !== value)); }} disabled={!catalog} />
              <label className="field-label" htmlFor="container">Контейнер / рюкзак</label>
              <SearchableSelect id="container" value={containerId} options={containerOptions} placeholder="Любой контейнер или рюкзак" onChange={(value) => { setContainerId(value); setExcludedContainerIds((current) => current.filter((id) => id !== value)); }} disabled={!catalog} />
              <label className="field-label quality-limit-label" htmlFor="max-quality">Максимальное качество артефактов</label>
              <div className="select-wrap quality-limit-select">
                <select id="max-quality" value={maxQualityTier} onChange={(event) => setMaxQualityTier(event.target.value)}>
                  {QUALITY_LIMITS.map((quality) => <option key={quality.value} value={quality.value}>{quality.label}</option>)}
                </select>
                <ChevronDown size={15} />
              </div>
            </section>

            <section className="form-section requirement-section">
              <div className="section-heading"><SlidersHorizontal size={17} /><h2>Обязательные значения</h2></div>
              <RequirementList metrics={mainMetrics} targets={targets} setTargets={setTargets} />
              <details className="secondary-settings">
                <summary>Второстепенные свойства <ChevronDown size={15} /></summary>
                <RequirementList metrics={secondaryMetrics} targets={targets} setTargets={setTargets} />
              </details>
            </section>

            <section className="form-section exclusion-section">
              <details className="exclusion-settings">
                <summary><span><Ban size={16} /> Исключить из расчета</span><span className="exclusion-count">{excludedArmorIds.length + excludedContainerIds.length + excludedArtifactIds.length}</span><ChevronDown size={15} /></summary>
                <div className="exclusion-groups">
                  <ExclusionGroup title="Костюмы" options={catalog?.armors ?? []} selected={excludedArmorIds} setSelected={setExcludedArmorIds} disabledId={armorId} />
                  <ExclusionGroup title="Контейнеры и рюкзаки" options={catalog?.containers ?? []} selected={excludedContainerIds} setSelected={setExcludedContainerIds} disabledId={containerId} />
                  <ExclusionGroup title="Артефакты" options={catalog?.artifacts ?? []} selected={excludedArtifactIds} setSelected={setExcludedArtifactIds} />
                </div>
              </details>
            </section>

            <button className="submit-button" type="submit" disabled={!catalog || loading}>
              {loading ? <><span className="spinner" /> Проверка...</> : <><Search size={18} /> Найти сборки</>}
            </button>
          </form>
        </aside>

        <main className="results-panel">
          <div className="results-heading">
            <div><div className="eyebrow">Результаты</div><div className="results-title-row"><h1>Подходящие сборки</h1>{result && <span className="result-count">{result.solutions.length}</span>}</div></div>
            {result && <div className="runtime"><Timer size={16} /> {result.diagnostics.elapsed_seconds.toFixed(2)} с</div>}
          </div>
          {error && <div className="error-banner"><CircleAlert size={18} />{error}</div>}
          {loading && <LoadingState />}
          {!loading && !result && !error && <EmptyState />}
          {!loading && result && result.solutions.length === 0 && <div className="empty-state"><CircleAlert size={28} /><h2>Сборок с такими условиями не найдено</h2></div>}
          {!loading && result && result.solutions.length > 0 && <div className="build-list">{result.solutions.map((solution, index) => (
            <BuildCard
              key={solution.build_id}
              solution={solution}
              index={index}
              upgradeState={upgradeStates[solution.build_id]}
              onLoadUpgrades={() => loadUpgrades(solution)}
            />
          ))}</div>}
        </main>
      </div>
    </div>
  );
}

function SearchableSelect({ id, value, options, placeholder, onChange, disabled }: { id: string; value: string; options: SearchOption[]; placeholder: string; onChange: (value: string) => void; disabled?: boolean }) {
  const selected = options.find((option) => option.id === value);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const normalized = query.trim().toLocaleLowerCase("ru-RU");
  const filtered = options.filter((option) => !normalized || `${option.name} ${option.meta}`.toLocaleLowerCase("ru-RU").includes(normalized)).slice(0, 30);

  useEffect(() => {
    setQuery(selected?.name ?? "");
  }, [selected?.id, selected?.name]);

  function choose(option: SearchOption) {
    setQuery(option.name);
    onChange(option.id);
    setOpen(false);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" && open && filtered.length > 0) {
      event.preventDefault();
      choose(filtered[0]);
    } else if (event.key === "Escape") {
      setOpen(false);
    } else if (event.key === "ArrowDown") {
      setOpen(true);
    }
  }

  return <div className={`search-select ${open ? "open" : ""}`}>
    <Search className="search-select-icon" size={14} />
    <input
      id={id}
      role="combobox"
      aria-expanded={open}
      aria-controls={`${id}-options`}
      autoComplete="off"
      type="search"
      value={query}
      placeholder={placeholder}
      disabled={disabled}
      onFocus={() => setOpen(true)}
      onBlur={() => window.setTimeout(() => setOpen(false), 120)}
      onKeyDown={handleKeyDown}
      onChange={(event) => {
        setQuery(event.target.value);
        if (value) onChange("");
        setOpen(true);
      }}
    />
    {query && <button type="button" className="search-select-clear" aria-label="Очистить выбор" title="Очистить" onMouseDown={(event) => event.preventDefault()} onClick={() => { setQuery(""); onChange(""); setOpen(true); }}><X size={14} /></button>}
    {open && !disabled && <div className="search-select-options" id={`${id}-options`} role="listbox">
      <button type="button" className={!value ? "selected" : ""} onMouseDown={(event) => event.preventDefault()} onClick={() => { setQuery(""); onChange(""); setOpen(false); }}><span>{placeholder}</span><small>Без ограничения</small></button>
      {filtered.map((option) => <button type="button" role="option" aria-selected={option.id === value} className={option.id === value ? "selected" : ""} key={option.id} onMouseDown={(event) => event.preventDefault()} onClick={() => choose(option)}><span>{option.name}</span><small>{option.meta}</small></button>)}
      {filtered.length === 0 && <div className="search-select-empty">Ничего не найдено</div>}
    </div>}
  </div>;
}

function RequirementList({ metrics, targets, setTargets }: { metrics: Metric[]; targets: Record<string, string>; setTargets: (value: Record<string, string>) => void }) {
  function toggle(metric: Metric) {
    if (metric.key in targets) {
      setTargets(Object.fromEntries(Object.entries(targets).filter(([key]) => key !== metric.key)));
    } else {
      setTargets({ ...targets, [metric.key]: "" });
    }
  }
  return <div className="requirement-list">{metrics.map((metric) => {
    const active = metric.key in targets;
    const valid = !active || Number.isFinite(parseDecimal(targets[metric.key]));
    return <label className={`requirement-row ${active ? "active" : ""} ${valid ? "" : "invalid"}`} key={metric.key}>
      <input type="checkbox" checked={active} onChange={() => toggle(metric)} />
      <span className="requirement-name">{metric.label}</span>
      <span className="requirement-operator">{metric.direction === "min" ? "≤" : "≥"}</span>
      <input aria-label={`${metric.label}: обязательное значение`} type="text" inputMode="decimal" disabled={!active} value={targets[metric.key] ?? ""} onChange={(event) => setTargets({ ...targets, [metric.key]: event.target.value })} />
    </label>;
  })}</div>;
}

function ExclusionGroup({ title, options, selected, setSelected, disabledId = "" }: { title: string; options: Array<{ id: string; name: string }>; selected: string[]; setSelected: (value: string[]) => void; disabledId?: string }) {
  const [query, setQuery] = useState("");
  const visibleOptions = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("ru-RU");
    return normalized ? options.filter((option) => option.name.toLocaleLowerCase("ru-RU").includes(normalized)) : options;
  }, [options, query]);
  return <details className="exclusion-group">
    <summary><span>{title}</span><small>{selected.length > 0 ? selected.length : options.length}</small><ChevronDown size={14} /></summary>
    <div className="exclusion-search"><Search size={14} /><input aria-label={`Поиск: ${title}`} type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск" /></div>
    <div className="exclusion-list">{visibleOptions.map((option) => <label key={option.id} className={disabledId === option.id ? "disabled" : ""}>
      <input type="checkbox" checked={selected.includes(option.id)} disabled={disabledId === option.id} onChange={() => setSelected(selected.includes(option.id) ? selected.filter((item) => item !== option.id) : [...selected, option.id])} />
      <span>{option.name}</span>
    </label>)}{visibleOptions.length === 0 && <div className="no-options">Ничего не найдено</div>}</div>
  </details>;
}

function BuildCard({ solution, index, upgradeState, onLoadUpgrades }: { solution: BuildSolution; index: number; upgradeState?: UpgradeState; onLoadUpgrades: () => void }) {
  const keyStats = [["effective_durability", Shield], ["hp_regen_score", HeartPulse], ["movement_speed", Wind], ["total_sprint_speed", Footprints], ["stamina_regeneration", Activity], ["carry_weight", Weight]] as const;
  const infections = Object.entries(solution.infection.by_type).map(([key, value]) => [key, value, value.after_inner_protection + value.container] as const).filter(([, value, exposure]) => Math.abs(exposure) > 0.0005 || value.margin < 0.05);
  const focusLabel = solution.search_focus.split("+").map((focus) => FOCUS_LABELS[focus] ?? focus).join(" · ");
  return <article className="build-card">
    <header className="build-header"><div className="build-rank">#{index + 1}</div><div className="build-equipment"><h2>{solution.armor.name}</h2><div><Box size={15} /> {solution.container.name} · {solution.container.capacity} слотов</div></div><div className="build-meta"><strong>{formatPrice(solution.total_price)}</strong><span className="focus-badge">{focusLabel}</span><span className="potential-badge">Потенциал {decimal(solution.upgrade_potential.score, 0)}</span><span className={`solver-badge ${solution.solver_status.toLowerCase()}`}>{solution.solver_status === "OPTIMAL" ? <CheckCircle2 size={13} /> : <Gauge size={13} />}{solution.solver_status === "FEASIBLE_SEED" ? "Проверено" : solution.solver_status}</span></div></header>
    <div className="artifact-strip">{solution.artifacts.map((artifact, artifactIndex) => <div className={`artifact-row rarity-${artifact.quality_tier}`} key={`${artifact.group_id}-${artifactIndex}`}><span className="rarity-swatch" /><div className="artifact-name"><strong>{artifact.name}</strong><small>{RARITY_LABELS[artifact.quality_tier]}</small></div><div className="artifact-quality">{artifact.quality_percent.toFixed(2)}%</div><div className="artifact-level">+{artifact.upgrade_level}</div><div className="artifact-price">{formatPrice(artifact.price)}</div></div>)}</div>
    <div className="stat-grid">{keyStats.map(([key, Icon]) => <div className="stat-cell" key={key}><Icon size={16} /><span>{STAT_LABELS[key]}</span><strong>{key === "total_sprint_speed" ? decimal(valueForStat(solution, key)) : signed(valueForStat(solution, key))}{key === "effective_durability" || key === "carry_weight" ? "" : "%"}</strong></div>)}</div>
    <footer className="build-footer"><div className="infection-summary"><CheckCircle2 size={15} />{infections.length === 0 ? <span>Заражения после защиты нет</span> : infections.map(([key, value, exposure]) => <span className={value.margin < 0.05 ? "near-limit" : ""} key={key}>{INFECTION_LABELS[key] ?? key}: {decimal(exposure)} / {decimal(value.limit)}</span>)}</div><div className="build-actions"><button className="upgrade-button" type="button" onClick={onLoadUpgrades} disabled={upgradeState?.loading}>{upgradeState?.loading ? <LoaderCircle className="spin-icon" size={14} /> : <ArrowUpRight size={14} />}{upgradeState?.loading ? "Считаем" : upgradeState?.result ? "Пересчитать" : "Улучшить"}</button><details className="all-stats"><summary>Все свойства <ChevronDown size={14} /></summary><div className="all-stats-grid">{Object.entries(solution.stats).sort(([left], [right]) => left.localeCompare(right)).map(([key, value]) => <div key={key}><span>{STAT_LABELS[key] ?? key}</span><strong>{signed(value)}</strong></div>)}</div></details></div></footer>
    {upgradeState?.error && <div className="upgrade-error"><CircleAlert size={15} />{upgradeState.error}</div>}
    {upgradeState?.result && <UpgradePlans current={solution} result={upgradeState.result} />}
  </article>;
}

function UpgradePlans({ current, result }: { current: BuildSolution; result: UpgradeResult }) {
  const potential = result.diagnostics.current_potential;
  return <section className="upgrade-panel">
    <div className="upgrade-heading"><div><ArrowUpRight size={16} /><strong>Потенциал и пути улучшения</strong></div><span>{result.diagnostics.elapsed_seconds.toFixed(2)} с</span></div>
    <div className="potential-overview">
      <div><span>Переносимость артефактов</span><strong>{decimal(potential.artifact_reuse_score, 0)} / 100</strong></div>
      <div><span>Смена контейнера</span><strong>{decimal(potential.container_upgrade_score, 0)} / 100</strong></div>
      <div><span>Подходящие контейнеры</span><strong>{potential.compatible_container_count}</strong><small>из них вместительнее: {potential.larger_container_count}</small></div>
      <div><span>Следующий контейнер</span><strong>{potential.best_container_upgrade?.name ?? "Нет"}</strong>{potential.best_container_upgrade && <small>{potential.best_container_upgrade.capacity} слотов · защита {decimal(potential.best_container_upgrade.inner_protection)}%</small>}</div>
    </div>
    <details className="artifact-reuse-details">
      <summary>Использование артефактов в других сборках <ChevronDown size={14} /></summary>
      <div className="artifact-reuse-list">{potential.artifacts.map((artifact, index) => <div key={`${artifact.item_id}-${index}`}><strong>{artifact.name}</strong><span>{artifact.roles.length > 0 ? artifact.roles.map((role) => ROLE_LABELS[role] ?? role).join(", ") : "узкая специализация"}</span><b>{decimal(artifact.score, 0)}</b></div>)}</div>
    </details>
    {result.plans.length === 0 ? <div className="upgrade-empty">Конкретных изменений в пределах +10 млн не найдено.</div> : <div className="upgrade-list">{result.plans.map((plan, index) => <UpgradePlanRow key={`${plan.extra_budget}-${plan.result_build.build_id}-${index}`} current={current} plan={plan} />)}</div>}
  </section>;
}

function UpgradePlanRow({ current, plan }: { current: BuildSolution; plan: UpgradePlan }) {
  const deltas = ["effective_durability", "hp_regen_score", "movement_speed", "total_sprint_speed", "stamina_regeneration", "carry_weight"]
    .map((key) => [key, valueForStat(plan.result_build, key) - valueForStat(current, key)] as const)
    .filter(([, value]) => Math.abs(value) > 0.004);
  return <div className="upgrade-row">
    <div className="upgrade-summary"><strong>Бюджет +{formatPrice(plan.extra_budget)}</strong><span>{plan.kept_count} из {plan.current_count} артефактов остаются</span><span>Потенциал {signed(plan.potential_gain, 1)} → {decimal(plan.upgrade_potential.score, 0)}</span></div>
    <div className="container-change"><Box size={14} />{plan.container_changed ? <><span>{current.container.name}</span><ArrowRight size={13} /><strong>{plan.result_build.container.name}</strong></> : <strong>{current.container.name} оставить</strong>}<small>стоимость контейнера не учитывается</small></div>
    <div className="artifact-changes"><ArtifactChangeList title="Убрать" artifacts={plan.removed_artifacts} empty="Ничего" /><ArtifactChangeList title="Купить" artifacts={plan.added_artifacts} empty="Ничего" /></div>
    <div className="upgrade-deltas">{deltas.map(([key, value]) => <span className={value > 0 ? "positive" : "negative"} key={key}>{STAT_LABELS[key]} {signed(value)}{key === "effective_durability" || key === "carry_weight" ? "" : "%"}</span>)}</div>
    <div className="upgrade-costs"><span>Покупка <strong>{formatPrice(plan.purchase_cost)}</strong></span><span>Продажа старых <strong>{formatPrice(plan.resale_credit)}</strong></span><span className="net-cost">Итого <strong>{formatPrice(plan.estimated_net_cost)}</strong></span></div>
  </div>;
}

function ArtifactChangeList({ title, artifacts, empty }: { title: string; artifacts: BuildSolution["artifacts"]; empty: string }) {
  return <div className="change-list"><b>{title}</b>{artifacts.length === 0 ? <span>{empty}</span> : artifacts.map((artifact, index) => <span key={`${artifact.group_id}-${index}`}>{artifact.name} · {RARITY_LABELS[artifact.quality_tier]} · {artifact.quality_percent.toFixed(2)}%</span>)}</div>;
}

function LoadingState() {
  return <div className="loading-state"><div className="loading-pulse"><SlidersHorizontal size={25} /></div><h2>Проверяем обязательные условия</h2><div className="loading-lines"><span /><span /><span /></div></div>;
}

function EmptyState() {
  return <div className="empty-state"><div className="empty-visual"><Shield size={34} /><Activity size={22} /><Wind size={26} /></div><h2>Задайте обязательные значения</h2><div className="empty-stats"><span>Костюм</span><span>Контейнер</span><span>Артефакты</span></div></div>;
}

export default App;
