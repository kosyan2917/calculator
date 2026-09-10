export type Metric = {
  key: string;
  label: string;
  group: "main" | "secondary";
  direction: "max" | "min";
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
  category: string;
  equipment_class: string;
  capacity: number;
  inner_protection: number;
  effectiveness: number;
};

export type ArtifactOption = {
  id: string;
  name: string;
  quality_tiers: string[];
};

export type Catalog = {
  generated_at: string;
  armors: ArmorOption[];
  containers: ContainerOption[];
  artifacts: ArtifactOption[];
  metrics: Metric[];
  limits: {
    budget_min: number;
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
  market_price: number;
  owned_instance_id: string | null;
  price_estimate?: {
    available: boolean;
    confidence: string;
    basis: string;
    observed_at?: string;
    sales_7d: number;
    sales_30d: number;
  };
  stats: Record<string, number>;
  infections: Record<string, number>;
};

export type BuildSolution = {
  build_id: string;
  search_focus: string;
  upgrade_potential: UpgradePotential;
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
    by_type: Record<string, {
      raw_artifact: number;
      container: number;
      after_inner_protection: number;
      base_output: number;
      final: number;
      limit: number;
      margin: number;
      valid: boolean;
    }>;
  };
  solver_status: string;
  solver_gap: number | null;
  solve_seconds: number;
};

export type ArtifactReuse = {
  item_id: string;
  name: string;
  roles: string[];
  compatible_containers: number;
  container_candidates: number;
  score: number;
};

export type UpgradePotential = {
  score: number;
  artifact_reuse_score: number;
  container_upgrade_score: number;
  reusable_roles: string[];
  compatible_container_count: number;
  larger_container_count: number;
  best_container_upgrade: {
    container_id: string;
    name: string;
    capacity: number;
    inner_protection: number;
  } | null;
  artifacts: ArtifactReuse[];
};

export type UpgradePlan = {
  extra_budget: number;
  purchase_cost: number;
  resale_credit: number;
  estimated_net_cost: number;
  kept_count: number;
  current_count: number;
  kept_value: number;
  container_changed: boolean;
  potential_gain: number;
  upgrade_potential: UpgradePotential;
  removed_artifacts: Artifact[];
  added_artifacts: Artifact[];
  result_build: BuildSolution;
};

export type UpgradeResult = {
  plans: UpgradePlan[];
  diagnostics: {
    elapsed_seconds: number;
    owned_artifacts: number;
    eligible_containers: number;
    current_potential: UpgradePotential;
    searches: Array<{
      extra_budget: number;
      candidates: number;
      selected: number;
      elapsed_seconds: number;
    }>;
  };
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
    search_complete: boolean;
  };
};
