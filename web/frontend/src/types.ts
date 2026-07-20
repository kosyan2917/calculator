export type Metric = {
  key: string;
  label: string;
  group: "main" | "secondary";
  direction: "max" | "min";
};

export type PreferenceLevel = {
  value: number;
  label: string;
  weight: number;
};

export type ArmorOption = {
  id: string;
  name: string;
  rank: string;
  category: string;
};

export type ContainerOption = {
  id: string;
  name: string;
  rank: string;
  capacity: number;
  inner_protection: number;
  effectiveness: number;
};

export type Catalog = {
  generated_at: string;
  armors: ArmorOption[];
  containers: ContainerOption[];
  metrics: Metric[];
  preference_levels: PreferenceLevel[];
  limits: {
    budget_min: number;
    budget_max: number;
    budget_step: number;
    quality_min: number;
    quality_max: number;
  };
};

export type Artifact = {
  group_id: string;
  item_id: string;
  name: string;
  quality_tier: string;
  quality_percent: number;
  upgrade_level: number;
  price: number;
  stats: Record<string, number>;
  infections: Record<string, number>;
};

export type BuildSolution = {
  build_id: string;
  objective_score: number;
  total_price: number;
  armor: { item_id: string; name: string; rank: string; category: string };
  container: {
    container_id: string;
    name: string;
    rank: string;
    capacity: number;
    inner_protection: number;
    effectiveness: number;
  };
  artifacts: Artifact[];
  stats: Record<string, number>;
  derived: Record<string, number>;
  infection: {
    valid: boolean;
    by_type: Record<string, { final: number; margin: number; valid: boolean }>;
  };
  solver_status: string;
  solver_gap: number | null;
  solve_seconds: number;
};

export type OptimizationResult = {
  solutions: BuildSolution[];
  diagnostics: {
    elapsed_seconds: number;
    artifact_groups: number;
    armors: number;
    containers: number;
    candidate_solutions: number;
    returned_solutions: number;
  };
};
