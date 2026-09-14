# Calibration procedure clarification v1

This is a reading procedure for section 4 of the frozen scoring contract. It
changes neither scoring anchors nor the policy overlay. It contains no case IDs,
company labels, reference answers, or new evidence.

## Downstream Criticality: evaluate the two tests separately

1. **Structural test first.** Permanently remove every qualified capability in
   the assessed Segment, across all suppliers, from the current scoped design.
   This hypothetical disallows technical substitution and redesign. Determine
   whether source-grounded dependency means the scoped downstream product cannot
   ship, operate, comply, or meet required performance. The source must establish
   the dependency and its scope; it need not literally describe the hypothetical
   removal. The contract supplies the counterfactual, not an invented source fact.
2. When that complete structural block is established, the structural test passes
   and Downstream Criticality is exact 4 with rule-derived natural-cap bounds.
   Missing largest-supplier shares, outage quantities, 90-day cohorts, or measured
   delay ratios do not veto a proven structural block. Those are inputs to the
   separate operational test.
3. **Operational test second.** If the complete structural block is not established,
   evaluate loss of the largest effective supplier for 90 days, using the
   contract's permitted inventory, same-route failover, and impact cohorts.
   A temporary single-supplier interruption alone cannot establish structural
   exact 4. Apply the existing merge rules: structural unknown preserves 4 as
   the upper bound; missing evidence stays unknown, not exact 0.

Do not infer a full-Scope block from a generic statement that a product is useful,
from delays affecting only some batches, or from a dependency belonging to another
product, platform, or region. Distinguish a necessary input to the current scoped
design from a replaceable convenience. The structural test's ban on redesign does
not make every commercially attractive input structurally necessary.

For each Downstream Criticality rationale, state the structural dependency finding
first, then whether the operational test is needed and why. This is an ordering
requirement, not permission to fill evidence gaps. Other dimensions retain their
own pressure scenarios: this clarification does not remove the Top1/90-day
requirements from Effective Supply Concentration or Capacity Inelasticity.

## Combine case-bound evidence before declaring a scope gap

Dependency may be established jointly: one source identifies the assessed product
as a necessary input to the current platform, while another explains the
architectural dependency of that input family. Read complete quote context and
connect those facts explicitly. Do not require one sentence to repeat the entire
product/platform/geography tuple and the counterfactual. Conversely, a statement
about an input family alone is insufficient when nothing binds the assessed
product to that dependency. Record the actual bridge and its source IDs; never
silently borrow a dependency from an adjacent product.

When a source limitation says a 90-day impact fraction is unquantified, apply that
limitation to the operational test. It is not a contradiction of a separately
established structural dependency. A limitation addressing the product or platform
scope itself still applies to both tests.

## Keep the counterfactual inside the current design

The structural test concerns the scoped downstream designs that use the assessed
Segment. It does not ask whether every project in the broader end market uses
that Segment. First identify a source-bound current design or committed project
and the function of its specified component. Then ask whether that same design
can operate without the entire Segment, with substitution and redesign disallowed.
Do not expand the test to unrelated projects using other technical routes. Those
routes belong in Substitute Weakness; their existence alone does not refute a
necessary component in the current design. A mere order or delivery delay is
still insufficient without an explained functional dependency.
