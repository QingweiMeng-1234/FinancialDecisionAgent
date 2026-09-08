const DEFAULT_K1 = 1.5;
const DEFAULT_B = 0.75;
const SEGMENT_DIMENSIONS = [
  "demand_pressure",
  "downstream_criticality",
  "effective_supply_concentration",
  "qualification_barrier",
  "capacity_inelasticity",
  "substitute_weakness",
];

const normalize = (value) => String(value ?? "")
  .normalize("NFKC")
  .replace(/[’‘]/g, "'")
  .toLowerCase();

export function tokenize(value) {
  return normalize(value).match(/[a-z0-9]+(?:[.-][a-z0-9]+)*|[\p{Script=Han}]/gu) ?? [];
}

function termFrequency(tokens) {
  const counts = new Map();
  for (const token of tokens) counts.set(token, (counts.get(token) ?? 0) + 1);
  return counts;
}

function numericKind(raw) {
  if (/%|percent|percentage/i.test(raw)) return "percentage";
  if (/\b(?:19|20)\d{2}\b/.test(raw)) return "year";
  if (/\b(?:day|days|week|weeks|month|months|quarter|quarters|year|years)\b/i.test(raw)) return "duration";
  if (/[$€£¥]|\b(?:usd|eur|rmb|cny)\b/i.test(raw)) return "currency";
  return "number";
}

export function extractNumericSignals(value) {
  const text = String(value ?? "");
  const pattern = /(?:[$€£¥]\s*)?[+-]?\d+(?:[.,]\d+)*(?:\s*(?:%|percent(?:age)?|bps|basis points?|x|times?|days?|weeks?|months?|quarters?|years?|usd|eur|rmb|cny|million|billion|trillion|mn|bn))?/gi;
  const signals = [];
  for (const match of text.matchAll(pattern)) {
    const raw = match[0].trim();
    if (!raw) continue;
    signals.push({ raw, kind: numericKind(raw), start: match.index, end: match.index + match[0].length });
  }
  return signals;
}

export function chunkSourceText(document) {
  const sourceText = String(document.text ?? "");
  const chunks = [];
  const paragraphPattern = /[^\r\n](?:[\s\S]*?)(?=(?:\r?\n){2,}|$)/g;
  for (const match of sourceText.matchAll(paragraphPattern)) {
    const raw = match[0];
    const text = raw.trim();
    if (!text) continue;
    const leading = raw.indexOf(text);
    const quoteStart = (match.index ?? 0) + leading;
    chunks.push({
      ...document,
      chunk_id: `${document.source_id}#${chunks.length + 1}`,
      text,
      quote_start: quoteStart,
      quote_end: quoteStart + text.length,
    });
  }
  return chunks;
}

function containsTerm(text, term) {
  const haystack = normalize(text);
  const needle = normalize(term).trim();
  return needle.length > 0 && haystack.includes(needle);
}

function hasAny(text, terms) {
  return terms.some((term) => containsTerm(text, term));
}

function numericProximityScore(text, signals, terms, radius = 120) {
  const normalizedText = normalize(text);
  let best = 0;
  for (const signal of signals) {
    const start = Math.max(0, signal.start - radius);
    const end = Math.min(normalizedText.length, signal.end + radius);
    const window = normalizedText.slice(start, end);
    const matches = terms.filter((term) => window.includes(normalize(term))).length;
    best = Math.max(best, matches);
  }
  return best;
}

function signalWindow(text, signal, radius = 120) {
  return String(text ?? "").slice(
    Math.max(0, signal.start - radius),
    Math.min(String(text ?? "").length, signal.end + radius),
  );
}

function tableYearHeader(lines) {
  for (const line of lines) {
    const years = [...line.matchAll(/\b((?:19|20)\d{2})e?\b/gi)].map((match) => Number(match[1]));
    if (years.length < 2) continue;
    if (years.every((year, index) => index === 0 || year > years[index - 1])) return years;
  }
  return [];
}

function reportedConsumptionRowValues(lines) {
  let withinConsumptionBlock = false;
  const rows = [];

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;

    if (/^consumption(?:\s*\([^)]*\))?\s*:/i.test(line)) {
      withinConsumptionBlock = true;
      continue;
    }

    let valueText = null;
    const direct = line.match(/^.*?consumption\s*,\s*reported\d*(?:\s*[:;,])?\s*(.*)$/i);
    if (direct) {
      valueText = direct[1];
    } else if (withinConsumptionBlock) {
      const nested = line.match(/^reported\d*(?:,\s*[a-z][a-z -]*)?\s+(.*)$/i);
      if (nested) valueText = nested[1];
    }

    if (valueText !== null) {
      const values = [...valueText.matchAll(/\b[eE]?([+-]?\d[\d,]*(?:\.\d+)?)\b/g)]
        .map((match) => Number(match[1].replaceAll(",", "")))
        .filter(Number.isFinite);
      if (values.length > 0) rows.push(values);
      withinConsumptionBlock = false;
      continue;
    }

    if (withinConsumptionBlock && /^(?:price|stocks?|net import reliance|recycling|production|imports?|exports?)[,:\s]/i.test(line)) {
      withinConsumptionBlock = false;
    }
  }

  return rows;
}

export function extractAnnualGrowthCandidates(text, scopeTerms = []) {
  if (!hasAny(text, scopeTerms)) return [];

  const lines = String(text ?? "").split(/\r?\n/);
  const years = tableYearHeader(lines);
  if (years.length < 2) return [];

  const baseYear = years.at(-2);
  const comparisonYear = years.at(-1);
  if (comparisonYear - baseYear !== 1) return [];

  return reportedConsumptionRowValues(lines)
    .filter((values) => values.length >= years.length)
    .map((values) => {
      const aligned = values.slice(-years.length);
      const baseValue = aligned.at(-2);
      const comparisonValue = aligned.at(-1);
      if (!(baseValue > 0) || !Number.isFinite(comparisonValue)) return null;
      return {
        metric_name: "reported_consumption",
        unit: null,
        base_period_start: `${baseYear}-01-01`,
        base_period_end: `${baseYear}-12-31`,
        base_value: baseValue,
        comparison_period_start: `${comparisonYear}-01-01`,
        comparison_period_end: `${comparisonYear}-12-31`,
        comparison_value: comparisonValue,
        annual_growth_basis: "annual_table_yoy",
        annual_growth_rate: Number((comparisonValue / baseValue - 1).toFixed(6)),
        inventory_treatment: "reported_consumption",
      };
    })
    .filter(Boolean);
}

function concentrationComponentScore(share) {
  if (share >= 0.8) return 4;
  if (share >= 0.6) return 3;
  if (share >= 0.4) return 2;
  if (share >= 0.3) return 1;
  return 0;
}

function halfUp(value) {
  return Math.min(4, Math.max(0, Math.floor(value + 0.5)));
}

function supplyShareCandidate({ basis, supplierLabel, percentage }) {
  const share = percentage / 100;
  const componentScore = concentrationComponentScore(share);
  return {
    component: "largest_effective_share",
    basis,
    supplier_label: supplierLabel.trim(),
    share: Number(share.toFixed(6)),
    component_score: componentScore,
    missing_components: ["effective_supplier_count", "qualified_failover_ratio"],
    aggregate_bound: {
      rating_min: halfUp(0.3 * componentScore),
      rating_max: halfUp(0.3 * componentScore + 0.3 * 4 + 0.4 * 4),
    },
  };
}

export function extractSupplyShareCandidates(text, scopeTerms = []) {
  if (!hasAny(text, scopeTerms)) return [];

  const candidates = [];
  for (const line of String(text ?? "").split(/\r?\n/)) {
    if (!/import sources?/i.test(line)) continue;
    const total = line.match(/\bTotal(?:\s+imports?)?\s*:\s*([A-Z][A-Za-z .'-]*?),\s*(\d+(?:\.\d+)?)%/i);
    const first = total ?? line.match(/(?:^|[;:.]\s*)([A-Z][A-Za-z .'-]*?),\s*(\d+(?:\.\d+)?)%/);
    if (!first || /^other$/i.test(first[1].trim())) continue;
    candidates.push(supplyShareCandidate({
      basis: "import_source_share",
      supplierLabel: first[1],
      percentage: Number(first[2]),
    }));
  }

  for (const match of String(text ?? "").matchAll(/\b([A-Z][A-Za-z .'-]*?)\s+accounted for\s+(\d+(?:\.\d+)?)%\s+of\s+(?:worldwide|global|world)[^.]*?\b(?:production|supply)\b/gi)) {
    candidates.push(supplyShareCandidate({
      basis: "production_share",
      supplierLabel: match[1],
      percentage: Number(match[2]),
    }));
  }

  return candidates;
}

export function extractSubstituteSignals(text, scopeTerms = []) {
  if (!hasAny(text, scopeTerms) || !/substitutes?\s*:/i.test(text)) return [];
  const substituteSection = String(text).split(/substitutes?\s*:/i).slice(1).join(" ");
  const hasMaterialRoute = /\b(?:substitute(?:d|s)?\s+for|can substitute|may be substituted|compete(?:s|d)?\s+(?:for|with|as)|replace(?:s|d)?|interchangeable with|alternative)\b/i.test(substituteSection);
  if (!hasMaterialRoute) return [];
  return [{
    route_state: "material_route_identified_readiness_unknown",
    bound_capability: "upper_bound_excludes_4",
    rating_min: 0,
    rating_max: 3,
  }];
}

function bm25Scores(tokenizedDocuments, queryTokens, k1 = DEFAULT_K1, b = DEFAULT_B) {
  const count = tokenizedDocuments.length;
  const averageLength = tokenizedDocuments.reduce((sum, tokens) => sum + tokens.length, 0) / Math.max(1, count);
  const frequencies = tokenizedDocuments.map(termFrequency);
  const documentFrequency = new Map();
  for (const token of new Set(queryTokens)) {
    documentFrequency.set(token, frequencies.filter((counts) => counts.has(token)).length);
  }
  return tokenizedDocuments.map((tokens, index) => {
    const counts = frequencies[index];
    let score = 0;
    for (const token of new Set(queryTokens)) {
      const tf = counts.get(token) ?? 0;
      if (tf === 0) continue;
      const df = documentFrequency.get(token) ?? 0;
      const idf = Math.log(1 + (count - df + 0.5) / (df + 0.5));
      const denominator = tf + k1 * (1 - b + b * tokens.length / Math.max(1, averageLength));
      score += idf * (tf * (k1 + 1)) / denominator;
    }
    return score;
  });
}

function withinWindow(publicationDate, start, end) {
  if (!start && !end) return true;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(publicationDate ?? "")) return false;
  return (!start || publicationDate >= start) && (!end || publicationDate <= end);
}

export function assessScorability({ dimension, scope_terms: scopeTerms = [], text }) {
  const numericSignals = extractNumericSignals(text);
  const annualGrowthCandidates = dimension === "demand_pressure"
    ? extractAnnualGrowthCandidates(text, scopeTerms)
    : [];
  const supplyShareCandidates = dimension === "effective_supply_concentration"
    ? extractSupplyShareCandidates(text, scopeTerms)
    : [];
  const substituteSignals = dimension === "substitute_weakness"
    ? extractSubstituteSignals(text, scopeTerms)
    : [];
  const scopeMatch = hasAny(text, scopeTerms);
  const missing = [];

  if (!scopeMatch) missing.push("atomic_scope");

  if (dimension === "demand_pressure") {
    const locallyBoundAnnualDemand = numericSignals
      .filter((item) => item.kind === "percentage")
      .some((item) => {
        const window = signalWindow(text, item);
        const hasAnnualBasis = /\b(?:yoy|year[- ]over[- ]year|versus|vs\.?|compared with|compared to|cagr|ttm)\b/i.test(window);
        const hasDemandMeasure = /\b(?:demand|orders?|units?|consumption|usage|shipments?)\b/i.test(window);
        return hasAnnualBasis && hasDemandMeasure && hasAny(window, scopeTerms);
      });
    if (!locallyBoundAnnualDemand && annualGrowthCandidates.length === 0) missing.push("annual_demand_basis");
  } else if (dimension === "capacity_inelasticity") {
    if (!/(?:top\s*1|largest supplier|supplier loss|lost output|failover|incremental output|delta[-_ ]?q|Δq)/i.test(text)) {
      missing.push("required_increment_or_top1_loss");
    }
    if (!/(?:stable qualified|qualified good output|three (?:full )?months|3 (?:full )?months|yield ramp|high[- ]volume production)/i.test(text)) {
      missing.push("stable_qualified_output_clock");
    }
  } else if (dimension === "effective_supply_concentration") {
    if (supplyShareCandidates.length === 0 && !/(?:market share|effective share|top\s*1|largest supplier|supplier count|n_eff|qualified capacity)/i.test(text)) missing.push("supplier_share_or_count");
    if (!/(?:failover|uncommitted capacity|spare capacity|incremental output|90 days?)/i.test(text)) missing.push("qualified_failover");
  } else if (dimension === "qualification_barrier") {
    if (!/(?:qualif|certif|validation|approval|switching)/i.test(text)) missing.push("qualification_process");
    if (!numericSignals.some((item) => item.kind === "duration")) missing.push("qualification_duration");
  } else if (dimension === "downstream_criticality") {
    if (!/(?:outage|shutdown|stop(?:page)?|supplier loss|unavailable|shortage)/i.test(text)) missing.push("loss_scenario");
    if (!/(?:delay|affected|impact|unable|blocked|inventory|failover)/i.test(text)) missing.push("operational_or_structural_impact");
  } else if (dimension === "substitute_weakness") {
    if (substituteSignals.length === 0 && !/(?:alternative|substitute|replacement|redesign|different route|different technology)/i.test(text)) missing.push("substitute_route");
    if (!/(?:coverage|capacity|production|ready|prototype|sample|qualified)/i.test(text)) missing.push("route_readiness_or_coverage");
  }

  const partialBoundSupported = supplyShareCandidates.length > 0 || substituteSignals.length > 0;

  return {
    scorable: scopeMatch && (missing.length === 0 || partialBoundSupported),
    scope_match: scopeMatch,
    numeric_signals: numericSignals,
    annual_growth_candidates: annualGrowthCandidates,
    supply_share_candidates: supplyShareCandidates,
    substitute_signals: substituteSignals,
    missing_requirements: missing,
  };
}

export function evaluateCaseAdmission({
  scope_terms: scopeTerms = [],
  evidence_texts: evidenceTexts = [],
  minimum_scorable_dimensions: minimumScorableDimensions = 3,
  required_dimensions: requiredDimensions = ["demand_pressure"],
}) {
  const dimensionResults = Object.fromEntries(SEGMENT_DIMENSIONS.map((dimension) => {
    const candidates = evidenceTexts.map((text) => ({
      text,
      ...assessScorability({ dimension, scope_terms: scopeTerms, text }),
    }));
    return [dimension, {
      scorable: candidates.some((item) => item.scorable),
      candidates,
    }];
  }));
  const scorableDimensions = SEGMENT_DIMENSIONS.filter((dimension) => dimensionResults[dimension].scorable);
  const rejectionReasons = [];
  for (const dimension of requiredDimensions) {
    if (!dimensionResults[dimension]?.scorable) rejectionReasons.push(`required_dimension_unscorable:${dimension}`);
  }
  if (scorableDimensions.length < minimumScorableDimensions) {
    rejectionReasons.push(`scorable_dimensions_below_minimum:${scorableDimensions.length}<${minimumScorableDimensions}`);
  }
  return {
    accepted: rejectionReasons.length === 0,
    scorable_dimensions: scorableDimensions,
    rejection_reasons: rejectionReasons,
    dimension_results: dimensionResults,
  };
}

export function rankNumericEvidence({ query, documents }) {
  const eligible = documents.filter((document) => withinWindow(
    document.publication_date,
    query.window_start,
    query.window_end,
  ));
  if (eligible.length === 0) return [];

  const queryTerms = [...(query.scope_terms ?? []), ...(query.anchor_terms ?? [])];
  const tokenized = eligible.map((document) => tokenize(document.text));
  const scores = bm25Scores(tokenized, tokenize(queryTerms.join(" ")));

  return eligible.map((document, index) => {
    const numericSignals = extractNumericSignals(document.text);
    const scopeMatch = hasAny(document.text, query.scope_terms ?? []);
    const scopeProximity = numericProximityScore(document.text, numericSignals, query.scope_terms ?? []);
    const anchorProximity = numericProximityScore(document.text, numericSignals, query.anchor_terms ?? []);
    const numericBoost = numericSignals.length > 0 ? 1 : 0;
    const score = scores[index] + numericBoost + scopeProximity * 2 + anchorProximity;
    return {
      ...document,
      score: Number(score.toFixed(6)),
      bm25_score: Number(scores[index].toFixed(6)),
      scope_match: scopeMatch,
      numeric_signals: numericSignals,
      scorability: assessScorability({
        dimension: query.dimension,
        scope_terms: query.scope_terms ?? [],
        text: document.text,
      }),
    };
  }).sort((left, right) => right.score - left.score || left.source_id.localeCompare(right.source_id));
}
