import {
  Activity,
  Box,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Coins,
  Footprints,
  Gauge,
  HeartPulse,
  Layers3,
  Search,
  Shield,
  SlidersHorizontal,
  Timer,
  Weight,
  Wind,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import type { BuildSolution, Catalog, Metric, OptimizationResult } from "./types";

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

function valueForStat(solution: BuildSolution, key: string) {
  if (key === "effective_durability" || key === "hp_regen_score") return solution.derived[key] ?? 0;
  if (key === "total_sprint_speed") return (solution.derived[key] ?? 100) - 100;
  return solution.stats[key] ?? 0;
}

function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [budgetMillions, setBudgetMillions] = useState(50);
  const [armorId, setArmorId] = useState("");
  const [containerId, setContainerId] = useState("");
  const [preferences, setPreferences] = useState<Record<string, number>>({
    durability: 3,
    speed: 2,
    regen: 1,
  });
  const [result, setResult] = useState<OptimizationResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

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
    setLoading(true);
    setError("");
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
      const elapsed = performance.now() - started;
      if (elapsed < 300) await new Promise((resolve) => window.setTimeout(resolve, 300 - elapsed));
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-mark"><Layers3 size={20} /></div>
        <div>
          <div className="brand-name">STALZONE</div>
          <div className="brand-section">Сборки артефактов</div>
        </div>
        <div className="topbar-status"><span className="status-dot" /> +15</div>
      </header>

      <div className="workspace">
        <aside className="control-panel">
          <form onSubmit={submit}>
            <section className="form-section">
              <div className="section-heading"><Coins size={17} /><h2>Бюджет</h2></div>
              <div className="budget-row">
                <input
                  aria-label="Бюджет в миллионах"
                  className="budget-input"
                  type="number"
                  min={2.5}
                  max={150}
                  step={2.5}
                  value={budgetMillions}
                  onChange={(event) => setBudgetMillions(Number(event.target.value))}
                />
                <span>млн</span>
              </div>
              <input
                aria-label="Ползунок бюджета"
                className="budget-slider"
                type="range"
                min={2.5}
                max={150}
                step={2.5}
                value={budgetMillions}
                onChange={(event) => setBudgetMillions(Number(event.target.value))}
              />
              <div className="range-labels"><span>2,5</span><span>150 млн</span></div>
            </section>

            <section className="form-section equipment-section">
              <div className="section-heading"><Shield size={17} /><h2>Экипировка</h2></div>
              <label className="field-label" htmlFor="armor">Костюм</label>
              <div className="select-wrap">
                <select id="armor" value={armorId} onChange={(event) => setArmorId(event.target.value)} disabled={!catalog}>
                  <option value="">Любой ветеранский или мастерский</option>
                  {catalog?.armors.map((armor) => <option key={armor.id} value={armor.id}>{armor.name}</option>)}
                </select>
                <ChevronDown size={16} />
              </div>
              <label className="field-label" htmlFor="container">Контейнер</label>
              <div className="select-wrap">
                <select id="container" value={containerId} onChange={(event) => setContainerId(event.target.value)} disabled={!catalog}>
                  <option value="">Любой допустимый</option>
                  {catalog?.containers.map((container) => (
                    <option key={container.id} value={container.id}>{container.name} · {container.capacity} сл.</option>
                  ))}
                </select>
                <ChevronDown size={16} />
              </div>
            </section>

            <section className="form-section preference-section">
              <div className="section-heading"><SlidersHorizontal size={17} /><h2>Желаемые свойства</h2></div>
              <MetricList metrics={mainMetrics} preferences={preferences} setPreferences={setPreferences} />
              <details className="secondary-settings">
                <summary>Второстепенные свойства <ChevronDown size={15} /></summary>
                <MetricList metrics={secondaryMetrics} preferences={preferences} setPreferences={setPreferences} />
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
              <h1>Подходящие сборки</h1>
            </div>
            {result && (
              <div className="runtime"><Timer size={16} /> {result.diagnostics.elapsed_seconds.toFixed(2)} с</div>
            )}
          </div>

          {error && <div className="error-banner"><CircleAlert size={18} />{error}</div>}
          {loading && <LoadingState />}
          {!loading && !result && !error && <EmptyState />}
          {!loading && result && result.solutions.length === 0 && (
            <div className="empty-state"><CircleAlert size={28} /><h2>Сборки не найдены</h2></div>
          )}
          {!loading && result && result.solutions.length > 0 && (
            <div className="build-list">
              {result.solutions.map((solution, index) => (
                <BuildCard key={solution.build_id} solution={solution} index={index} />
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
                  className={selected === level ? "active" : ""}
                  role="radio"
                  aria-checked={selected === level}
                  title={label}
                  onClick={() => setPreferences({ ...preferences, [metric.key]: level })}
                >
                  <span className="segment-number">{level + 1}</span>
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

function BuildCard({ solution, index }: { solution: BuildSolution; index: number }) {
  const keyStats = [
    ["effective_durability", Shield],
    ["hp_regen_score", HeartPulse],
    ["movement_speed", Wind],
    ["total_sprint_speed", Footprints],
    ["stamina_regeneration", Activity],
    ["carry_weight", Weight],
  ] as const;
  const infections = Object.entries(solution.infection.by_type).filter(([, value]) => Math.abs(value.final) > 0.0005);
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
          {infections.length === 0 ? <span>Без остаточного заражения</span> : infections.map(([key, value]) => (
            <span key={key}>{INFECTION_LABELS[key] ?? key}: {signed(value.final)}</span>
          ))}
        </div>
        <details className="all-stats">
          <summary>Все свойства <ChevronDown size={14} /></summary>
          <div className="all-stats-grid">
            {Object.entries(solution.stats).sort(([left], [right]) => left.localeCompare(right)).map(([key, value]) => (
              <div key={key}><span>{STAT_LABELS[key] ?? key}</span><strong>{signed(value)}</strong></div>
            ))}
          </div>
        </details>
      </footer>
    </article>
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
      <h2>Задайте приоритеты</h2>
      <div className="empty-stats"><span>Приведенка</span><span>Скорость</span><span>Реген</span></div>
    </div>
  );
}

export default App;
