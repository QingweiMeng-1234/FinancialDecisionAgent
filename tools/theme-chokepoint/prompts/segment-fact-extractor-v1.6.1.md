# Theme Chokepoint Segment Fact Extractor v1.6.1

Status: `inference_candidate`

Extract only case-bound observable facts. Never assign ratings, intervals, Bound
Basis, Hard Gates, or final states. Return one JSON object conforming to
`theme-chokepoint-segment-facts-v1.6.1.schema.json`. Any `rating`, `rating_min`,
`rating_max`, `score`, `bound_type`, or `primary_state` key is forbidden.

Use only the supplied Evidence IDs and their complete quote context. Do not use
Gold, prior answers, general knowledge, or search absence. Every dimension must
set `unresolved` and include non-empty case-bound `evidence_ids`. Set
`unresolved=true` only when neither canonical facts nor the conservative proxies
below can be populated. Otherwise set `unresolved=false`, populate every field,
and use null, false, or an empty list for facts the evidence does not establish.

## Demand Pressure

Prefer like-for-like annual growth: monthly YoY, quarterly YoY, TTM YoY, CAGR,
or explicitly seasonally-adjusted annualized growth. Preserve direct transmission
and inventory verification separately. Missing inventory proof must remain false.

When no canonical net-demand series exists, `demand_proxy_annual_growth` may use
an explicitly annual capacity/order expansion only when the source directly
attributes it to customer demand. Set `demand_proxy_capacity_prebooked=true` only
when the same Scope's expanded capacity is explicitly booked, nearly fully booked,
or supported by committed orders. A proxy establishes a floor only; do not treat
it as inventory-adjusted net demand.

## Downstream Criticality

Set `scope_dependency_verified=true` when the source explicitly states that the
Segment is required, critical, architecturally necessary, project-schedule
controlling, or reserved as a necessary input/equipment for the assessed current
design. The deterministic scorer owns the counterfactual: permanent removal of
the whole Segment excludes technical substitutes and redesign. Do not require the
source itself to narrate that hypothetical. Otherwise preserve the structural
test and any operational cohorts without inventing delay or impact ratios.

## Effective Supply Concentration

Use full effective-output quantities when public. When they are absent, extract:

- `sole_effective_supplier_verified=true` only for an explicit sole/only maker in
  the exact product route;
- `effective_supplier_count_upper_bound` when the source provides a complete
  supplier set or an explicit maximum count;
- `qualified_failover_absence_verified=true` only when sole-source structure or
  public backlog/lead-time/booking facts rule out incremental qualified,
  uncommitted same-route output inside 90 days.

Do not convert announced factories, baseline output, inventory, or booked capacity
into 90-day failover.

## Qualification Barrier

Preserve exact duration only when it is explicitly supplier qualification time.
Otherwise extract formal qualification and observable process signals. Testing,
first product exposure, customer-specific application validation, FAT/SAT,
commissioning, regulatory approval, and requalification are valid signals when
stated. Set `scope_coverage=partial_subtype` when evidence covers a subtype inside
the assessed Segment; this may support a conservative floor but never exact
full-Scope coverage. Lead time and construction time are not qualification time.

## Capacity Inelasticity

Use full shock quantities and a task DAG when public. Otherwise preserve
observable physical proxies:

- `minimum_physical_lead_time_months` for an explicitly reported manufacturing,
  delivery, construction, or capacity-availability clock, excluding customer
  qualification time;
- `physical_expansion_required=true` when closing the gap requires physical
  manufacturing expansion;
- `physical_constraint_signals` only from stated constraints such as advanced
  process, cleanroom, greenfield fab, advanced packaging, long-lead components,
  equipment build, construction, skilled labor, or commissioning.

Do not invent output quantities or a complete DAG. Public physical proxies create
conservative floors only.

## Substitute Weakness

Exclude same-route second suppliers. Preserve materially different product,
material, process, architecture, software-reduction, bypass, refurbishment, or
vertical-integration routes. A credible, ordered, ready, or production route may
use `qualified_output=null` when the evidence does not provide a comparable
same-Scope numerator. Do not divide mismatched order pipelines. Unknown route
coverage establishes only a ceiling. Preserve discovery rounds, categories,
unresolved routes, explicit negatives, and budget exhaustion exactly.

## Evidence boundary

Keep `company_id=null` at Segment Scope. Company names are factual attribution,
not a company moat or investment conclusion. Preserve source limitations and the
assessment as-of date. Never silently widen product, platform, geography, or
time horizon.
