const UNKNOWN = "unknown";

function threeValued(op, left, right) {
  if (op === "and") {
    if (left === "fail" || right === "fail") return "fail";
    if (left === UNKNOWN || right === UNKNOWN) return UNKNOWN;
    return "pass";
  }
  if (op === "or") {
    if (left === "pass" || right === "pass") return "pass";
    if (left === UNKNOWN || right === UNKNOWN) return UNKNOWN;
    return "fail";
  }
  throw new Error(`unsupported_three_valued_operator:${op}`);
}

export function evaluateGoldenAssertion(fixture) {
  switch (fixture.rule) {
    case "segment_scope_separation":
      return fixture.left.segment_id !== fixture.right.segment_id;

    case "forbid_capability_implication": {
      const allowed = new Set(fixture.allowed);
      return fixture.requested.every((capability) => !allowed.has(capability));
    }

    case "readiness_not_production": {
      const evidence = new Set(fixture.evidence);
      return evidence.has("mass_production_readiness")
        && !evidence.has("mass_production")
        && !evidence.has("commercial_production");
    }

    case "project_scope_non_transfer":
      return fixture.evidence_scope !== fixture.target_scope;

    case "ambiguous_source_floor":
      return fixture.source_ambiguity
        && fixture.human_review_required
        && fixture.scoring_role === "context_only"
        ? fixture.before
        : fixture.proposed;

    case "replacement_noncompensatory": {
      const performancePass = fixture.performance.evidence_state === "supported"
        && fixture.performance.rating_min >= 3;
      if (["replacement_ready", "realized_replacement", "scaled_replacement"].includes(fixture.requested_state)) {
        return performancePass;
      }
      return true;
    }

    case "segment_watch_eligibility":
      return fixture.dimensions.every((state) => state === UNKNOWN) ? null : "watch";

    case "defensibility_customer_procurement_gate":
      return fixture.customer_procurement === UNKNOWN ? null : "eligible";

    case "earnings_margin_gate":
      return fixture.margin_transmission === UNKNOWN ? null : "eligible";

    case "competition_mapping":
      if (fixture.defensibility === "high_defensibility" && fixture.challenger === "realized_replacement") {
        return "vulnerable_incumbent";
      }
      return "competition_state_uncertain";

    case "industry_event_type":
      return fixture.event_type !== "knowledge_revision";

    case "compound_anchor":
      return fixture.required_predicates.every((state) => state === "supported");

    case "independent_evidence_count":
      return new Set(fixture.evidence.map((item) => `${item.origin_event_id}\u0000${item.evidence_family_id}`)).size;

    case "realized_replacement_gates":
      return Object.values(fixture.dimensions).every(([ratingMin, evidenceState]) => (
        evidenceState === "supported" && ratingMin >= 3
      ));

    case "lifecycle_state":
      return ["cancelled", "internal_test_only", "withdrawn"].includes(fixture.commercialization)
        ? "failed_or_withdrawn"
        : "early_signal";

    case "mixed_current_future": {
      const mixed = fixture.statements.includes("being_deployed")
        && fixture.statements.includes("future_gw_commitment");
      const proven = !(mixed && !fixture.separable);
      return { production: proven, capacity_3: proven, adoption_3: proven };
    }

    case "demand_without_growth_denominator":
      return fixture.absolute_backlog && !fixture.same_scope_growth_denominator
        ? { demand_pressure: UNKNOWN, positive_demand_presence: true }
        : { demand_pressure: "resolved", positive_demand_presence: Boolean(fixture.absolute_backlog) };

    case "canonical_payload_fields":
      if (Object.hasOwn(fixture.payload, "capital_intensity")) return "reject:capital_intensity";
      return Object.hasOwn(fixture.payload, "capital_cash_burden") ? "accept" : "reject:missing_capital_cash_burden";

    case "earnings_overlay_isolation":
      return { ...fixture.segment };

    case "three_valued_logic":
      return fixture.checks.map(([op, left, right]) => threeValued(op, left, right));

    case "atomic_product_scope":
      return new Set(fixture.products).size > 1 ? "atomic_product_scope_error" : "valid";

    default:
      throw new Error(`unsupported_golden_rule:${fixture.rule}`);
  }
}

