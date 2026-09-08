import assert from "node:assert/strict";
import test from "node:test";

import {
  rankNumericEvidence,
  assessScorability,
  chunkSourceText,
  evaluateCaseAdmission,
  extractAnnualGrowthCandidates,
  extractSupplyShareCandidates,
  extractSubstituteSignals,
} from "./numeric-bm25-retriever-v1.mjs";

test("same-scope annual percentage outranks a larger broad-platform percentage", () => {
  const candidates = rankNumericEvidence({
    query: {
      dimension: "demand_pressure",
      scope_terms: ["payload-linker", "ADC"],
      anchor_terms: ["demand", "year over year", "YoY", "growth"],
    },
    documents: [
      {
        source_id: "broad",
        publication_date: "2026-07-22",
        text: "Advanced Synthesis sales grew 27.7% versus H1 2025 across Small Molecules and Bioconjugates.",
      },
      {
        source_id: "atomic",
        publication_date: "2026-07-25",
        text: "Global ADC payload-linker qualified demand grew 18% year over year versus H1 2025.",
      },
    ],
  });

  assert.equal(candidates[0].source_id, "atomic");
  assert.equal(candidates[0].numeric_signals[0].kind, "percentage");
  assert.equal(candidates[0].scope_match, true);
});

test("demand-pressure scorability rejects a percentage without atomic scope or annual demand basis", () => {
  const broad = assessScorability({
    dimension: "demand_pressure",
    scope_terms: ["payload-linker", "ADC"],
    text: "Advanced Synthesis sales grew 27.7% versus H1 2025 across Small Molecules and Bioconjugates.",
  });

  assert.equal(broad.scorable, false);
  assert.deepEqual(broad.missing_requirements.sort(), [
    "annual_demand_basis",
    "atomic_scope",
  ]);
});

test("a company revenue percentage cannot attach to a distant atomic-scope demand sentence", () => {
  const result = assessScorability({
    dimension: "demand_pressure",
    scope_terms: ["CoWoS"],
    text: `CoWoS demand remains robust. ${"Operational commentary without a quantified demand denominator. ".repeat(4)}Company revenue is expected to grow 40% year over year.`,
  });

  assert.equal(result.scope_match, true);
  assert.equal(result.scorable, false);
  assert.deepEqual(result.missing_requirements, ["annual_demand_basis"]);
});

test("capacity evidence is recalled by dates but remains unscorable without the pressure-scenario quantity", () => {
  const result = assessScorability({
    dimension: "capacity_inelasticity",
    scope_terms: ["payload-linker", "ADC"],
    text: "The ADC payload-linker facility is expected to be operational in 2028.",
  });

  assert.equal(result.numeric_signals[0].kind, "year");
  assert.equal(result.scorable, false);
  assert.ok(result.missing_requirements.includes("required_increment_or_top1_loss"));
  assert.ok(result.missing_requirements.includes("stable_qualified_output_clock"));
});

test("undated and out-of-window material is excluded before ranking", () => {
  const candidates = rankNumericEvidence({
    query: {
      dimension: "demand_pressure",
      scope_terms: ["CoWoS"],
      anchor_terms: ["demand", "growth", "YoY"],
      window_start: "2025-08-21",
      window_end: "2026-08-21",
    },
    documents: [
      { source_id: "old", publication_date: "2024-06-01", text: "CoWoS demand grew 40% YoY." },
      { source_id: "undated", publication_date: null, text: "CoWoS demand grew 35% YoY." },
      { source_id: "current", publication_date: "2026-07-16", text: "CoWoS demand grew 22% YoY." },
    ],
  });

  assert.deepEqual(candidates.map((item) => item.source_id), ["current"]);
});

test("full documents are split into original-text windows that keep nearby numbers and scope terms together", () => {
  const chunks = chunkSourceText({
    source_id: "release",
    publication_date: "2026-07-25",
    text: [
      "General corporate introduction without useful figures.",
      "Global ADC payload-linker qualified demand grew 18% year over year versus H1 2025. The increase reflects commercial program volume rather than acquisitions.",
      "Safe harbor statement.",
    ].join("\n\n"),
  });

  const numeric = chunks.find((item) => item.text.includes("18%"));
  assert.ok(numeric);
  assert.equal(numeric.source_id, "release");
  assert.match(numeric.text, /payload-linker qualified demand grew 18%/);
  assert.equal(numeric.quote_start, 56);
  assert.equal(numeric.quote_end, 212);
});

test("case admission requires contract-scorable demand plus another resolved dimension", () => {
  const accepted = evaluateCaseAdmission({
    scope_terms: ["CoWoS"],
    minimum_scorable_dimensions: 2,
    required_dimensions: ["demand_pressure"],
    evidence_texts: [
      "CoWoS qualified demand units grew 22% year over year versus 2025.",
      "CoWoS customer qualification and switching takes 14 months.",
    ],
  });
  const rejected = evaluateCaseAdmission({
    scope_terms: ["CoWoS"],
    minimum_scorable_dimensions: 2,
    required_dimensions: ["demand_pressure"],
    evidence_texts: [
      "CoWoS demand continues to increase and customers are asking for capacity.",
      "Backend capacity remains in shortage mode.",
    ],
  });

  assert.equal(accepted.accepted, true);
  assert.deepEqual(accepted.scorable_dimensions, ["demand_pressure", "qualification_barrier"]);
  assert.equal(rejected.accepted, false);
  assert.ok(rejected.rejection_reasons.includes("required_dimension_unscorable:demand_pressure"));
  assert.ok(rejected.rejection_reasons.includes("scorable_dimensions_below_minimum:0<2"));
});

test("reported-consumption annual table values form a mechanical YoY demand candidate", () => {
  const text = [
    "GALLIUM",
    "Salient Statistics—United States: 2021 2022 2023 2024 2025e",
    "Consumption, reported1 17,100 19,700 17,800 18,700 19,000",
  ].join("\n");

  const candidates = extractAnnualGrowthCandidates(text, ["gallium"]);
  const result = assessScorability({
    dimension: "demand_pressure",
    scope_terms: ["gallium"],
    text,
  });

  assert.equal(candidates.length, 1);
  assert.deepEqual(candidates[0], {
    metric_name: "reported_consumption",
    unit: null,
    base_period_start: "2024-01-01",
    base_period_end: "2024-12-31",
    base_value: 18700,
    comparison_period_start: "2025-01-01",
    comparison_period_end: "2025-12-31",
    comparison_value: 19000,
    annual_growth_basis: "annual_table_yoy",
    annual_growth_rate: 0.016043,
    inventory_treatment: "reported_consumption",
  });
  assert.equal(result.scorable, true);
  assert.deepEqual(result.missing_requirements, []);
  assert.deepEqual(result.annual_growth_candidates, candidates);
});

test("a reported row nested under a Consumption heading is parsed without treating footnotes as values", () => {
  const text = [
    "BAUXITE AND ALUMINA",
    "Salient Statistics—United States: 2021 2022 2023 2024 2025e",
    "Consumption:",
    "Apparent3 W W W W W",
    "Reported 2,790 2,170 2,050 1,640 1,700",
  ].join("\n");

  const [candidate] = extractAnnualGrowthCandidates(text, ["bauxite"]);

  assert.equal(candidate.base_value, 1640);
  assert.equal(candidate.comparison_value, 1700);
  assert.equal(candidate.annual_growth_rate, 0.036585);
});

test("apparent-consumption table values remain unscorable without a reported net-demand basis", () => {
  const text = [
    "ANTIMONY",
    "Salient Statistics—United States: 2021 2022 2023 2024 2025e",
    "Consumption, apparent2 27,800 24,500 20,700 28,600 45,000",
  ].join("\n");

  const candidates = extractAnnualGrowthCandidates(text, ["antimony"]);
  const result = assessScorability({
    dimension: "demand_pressure",
    scope_terms: ["antimony"],
    text,
  });

  assert.deepEqual(candidates, []);
  assert.equal(result.scorable, false);
  assert.deepEqual(result.missing_requirements, ["annual_demand_basis"]);
});

test("a quantified largest source share forms a partial concentration bound without inventing failover", () => {
  const text = [
    "CHROMIUM",
    "Import Sources (2021–24): Chromite ores and concentrates: South Africa, 96%; Turkey, 3%; and other, 1%.",
  ].join("\n");

  const candidates = extractSupplyShareCandidates(text, ["chromium"]);
  const result = assessScorability({
    dimension: "effective_supply_concentration",
    scope_terms: ["chromium"],
    text,
  });

  assert.deepEqual(candidates, [{
    component: "largest_effective_share",
    basis: "import_source_share",
    supplier_label: "South Africa",
    share: 0.96,
    component_score: 4,
    missing_components: ["effective_supplier_count", "qualified_failover_ratio"],
    aggregate_bound: { rating_min: 1, rating_max: 4 },
  }]);
  assert.equal(result.scorable, true);
  assert.ok(result.missing_requirements.includes("qualified_failover"));
  assert.deepEqual(result.supply_share_candidates, candidates);
});

test("net import reliance is not treated as a supplier-share denominator", () => {
  const text = "GALLIUM net import reliance as a percentage of reported consumption was 100%.";

  assert.deepEqual(extractSupplyShareCandidates(text, ["gallium"]), []);
});

test("an explicit different-material substitute route creates a conservative upper bound", () => {
  const text = [
    "VANADIUM",
    "Substitutes: Manganese, molybdenum, niobium, titanium, and tungsten are to some degree interchangeable with vanadium as alloying elements in steel.",
  ].join("\n");

  const signals = extractSubstituteSignals(text, ["vanadium"]);
  const result = assessScorability({
    dimension: "substitute_weakness",
    scope_terms: ["vanadium"],
    text,
  });

  assert.deepEqual(signals, [{
    route_state: "material_route_identified_readiness_unknown",
    bound_capability: "upper_bound_excludes_4",
    rating_min: 0,
    rating_max: 3,
  }]);
  assert.equal(result.scorable, true);
  assert.deepEqual(result.substitute_signals, signals);
});
