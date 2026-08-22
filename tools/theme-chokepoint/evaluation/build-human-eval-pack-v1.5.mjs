import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../../..");
const out = path.join(root, "outputs/theme-chokepoint-v1.5-evaluation-20260820-001");
const contractPath = path.join(root, "tools/theme-chokepoint/semantic-task-contract-v1.5.json");
const validatorPath = path.join(here, "semantic-validator-v1.5.mjs");
const validateCliPath = path.join(here, "validate-submission-v1.5.mjs");
const agreementPath = path.join(here, "calculate-agreement-v1.5.mjs");
const shaBytes = (value) => crypto.createHash("sha256").update(value).digest("hex");
const shaFile = (file) => shaBytes(fs.readFileSync(file));
const writeJson = (file, value) => { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`, "utf8"); };
const writeText = (file, value) => { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, value, "utf8"); };

const contractSha = shaFile(contractPath);
const segmentWeights = { demand_pressure: 15, downstream_criticality: 20, effective_supply_concentration: 15, qualification_barrier: 15, capacity_inelasticity: 15, substitute_weakness: 20 };
const dimensions = Object.keys(segmentWeights);

const goldenPack = {
  evidence_pack_id: "theme-chokepoint-golden-evidence-v1.5",
  contract_id: "theme-chokepoint-scoring-v1.5",
  pack_type: "human_golden_real_source_boundary",
  as_of_date: "2026-08-20",
  proof_boundary: "Real-source boundary Golden. Missing facts must remain unknown; this pack does not contain expected labels.",
  evidence: [
    { evidence_id: "G15-E01", publisher: "Samsung Electronics", publication_date: "2026-04-30", source_type: "official_quarterly_earnings_presentation", url: "https://images.samsung.com/is/content/samsung/assets/global/ir/docs/2026_1Q_conference_eng.pdf", location: "PDF p.7, Memory, 1Q 2026 Results", exact_quote: "Commenced industry’s first mass product sales of HBM4 and SOCAMM2 for NVIDIA Vera Rubin platform", content_sha256: "33140e1501207e19741efa7220b9cd39a4f68ba288854b526ab26a6a17e1db83", limitations: "Proves scoped HBM4 product sales, not annual demand growth, effective supplier shares, failover, qualification duration, physical gap-closure time, or alternative-route coverage." },
    { evidence_id: "G15-E02", publisher: "Samsung Electronics", publication_date: "2026-07-30", source_type: "official_quarterly_earnings_presentation", url: "https://images.samsung.com/is/content/samsung/assets/global/ir/docs/2026_2Q_conference_eng.pdf", location: "PDF p.7, Memory, 2Q 2026 Results", exact_quote: "Scaled up HBM4 sales with industry-leading performance", content_sha256: "e90b8e4829403339206fa77821aeadd71e39a611f3830a4cbcd349321de9e0cf", limitations: "Supplier-authored performance language lacks a like-for-like test and does not establish the v1.5 Segment denominators or counter-search completion." },
    { evidence_id: "G15-E03", publisher: "Hitachi Energy", publication_date: "2024-04-23", source_type: "official_capacity_release", url: "https://www.hitachienergy.com/news-and-events/press-releases/2024/04/hitachi-energy-to-invest-additional-1-5-billion-to-ramp-up-global-transformer-production-by-2027", location: "Opening and capacity-expansion paragraphs", exact_quote: "Hitachi Energy today revealed investments of over $1.5 billion to ramp up its global transformer manufacturing capacity to keep pace with the growing demand and support the long-term plans and electrification efforts. The investments will gradually expand the company’s global transformer capacity by 2027. Transformers play a key role across the power value chain, enabling efficient transmission and distribution of electricity. They are a key component for applications such as integrating renewables, grid interconnections, powering data centers and electrifying transportation.", content_sha256: "88e38353aa1de9b6cb50ced2a6a9986781b6e1ffa9d4df90c8d67b19d98e18d1", limitations: "Does not provide like-for-like annual growth, a standardized shock cohort, effective shares, qualification duration, stable qualified output date, or substitute coverage." },
  ],
  cases: [
    { case_id: "G15-C01", case_type: "real_source_boundary", industry: "semiconductors", assessment_scope: { company_id: null, product_id: "hbm4_stack", segment_id: "seg_hbm_stack", customer_or_platform_scope: "NVIDIA Vera Rubin evidence only", geography: "Global", time_horizon_months: 12, as_of_date: "2026-08-20" }, evidence_ids: ["G15-E01", "G15-E02"], state_context: { demand_direct_evidence: null, supply_direct_evidence: null, counter_evidence_search_complete: false, mandatory_conflict: false, independent_supply_constraint_count: null, key_source_quality_high: true } },
    { case_id: "G15-C02", case_type: "real_source_boundary", industry: "electrical_equipment", assessment_scope: { company_id: null, product_id: "large_power_transformers", segment_id: "seg_large_power_transformers_global", customer_or_platform_scope: "global transformer applications including data centers", geography: "Global", time_horizon_months: 36, as_of_date: "2026-08-20" }, evidence_ids: ["G15-E03"], state_context: { demand_direct_evidence: true, supply_direct_evidence: true, counter_evidence_search_complete: false, mandatory_conflict: false, independent_supply_constraint_count: null, key_source_quality_high: true } },
  ],
};

const controlled = (evidence_id, case_id, dimension, exact_quote) => ({ evidence_id, case_id, publisher: "Independent Evaluation Secretariat", publication_date: "2026-08-20", source_type: "sealed_controlled_vignette", location: dimension, exact_quote, content_sha256: shaBytes(exact_quote), limitations: "Controlled semantic-evaluation fact pattern. It is not a claim about a real company or market." });
const holdoutEvidence = [
  controlled("H15-E01", "H15-C01", "demand_pressure", "For the same product, platform, geography and twelve-month delivery basis, target demand increased from 1,000 units in the prior year to 1,150 units in the current year. The increase transmits directly into orders for this Segment; beginning and ending finished-goods inventory were reconciled on the same basis."),
  controlled("H15-E02", "H15-C01", "downstream_criticality", "Permanent removal of this Segment would not block every qualified unit in the atomic Scope. Under the prescribed 90-day loss of the largest effective supplier, after existing inventory and already-qualified same-route failover, 80 of 1,000 planned Scope units are delayed by exactly two months during the following twelve months; no material performance degradation occurs."),
  controlled("H15-E03", "H15-C01", "effective_supply_concentration", "In stable monthly units, Supplier A has effective output 70 and Supplier B has effective output 30 after nameplate, yield, qualification, target allocation and availability adjustments. Target demand is 100. During 90 days after Supplier A fails, Supplier B can add 5 qualified, uncommitted units beyond its baseline; inventory and baseline output are excluded."),
  controlled("H15-E04", "H15-C01", "qualification_barrier", "A replacement supplier for this exact Scope requires fourteen months from start to completed customer qualification."),
  controlled("H15-E05", "H15-C01", "capacity_inelasticity", "The Top-1 loss is 70 stable monthly units and qualified 90-day failover is 5, so required stable increment is 65. A proven plan can deliver 65. Its DAG is: facility work 5 months; equipment installation 4 months after facility work; yield ramp and manufacturing release 2 months after installation. The additional line must then sustain three complete months at 65 qualified good units."),
  controlled("H15-E06", "H15-C01", "substitute_weakness", "The frozen route registry covers product, material, process, system architecture, software reduction and vertical integration. Two consecutive discovery rounds found no new material route and no route is unresolved. One different-route Production substitute supplies 20 mutually exclusive qualified units against target demand 100; all other routes have zero Ready output and do not share its capacity pool."),
  controlled("H15-E07", "H15-C02", "demand_pressure", "For the identical atomic Scope and annual denominator, demand rose from 500 units to 710 units. Direct transmission into Segment orders and the complete inventory reconciliation were independently verified."),
  controlled("H15-E08", "H15-C02", "downstream_criticality", "The permanent structural test removes 100% of this Segment and excludes redesign and technical substitution. Every qualified output in the atomic product-by-platform-by-region Scope then fails the mandatory compliance requirement and cannot be placed into operation."),
  controlled("H15-E09", "H15-C02", "effective_supply_concentration", "Effective stable monthly output is 90 units from Supplier A and 10 from Supplier B, with target demand 100. If Supplier A is lost, all additional qualified, uncommitted same-route output available from other suppliers within 90 days is exactly zero."),
  controlled("H15-E10", "H15-C02", "qualification_barrier", "Completion of customer qualification for a replacement supplier in this exact Scope requires nineteen months."),
  controlled("H15-E11", "H15-C02", "capacity_inelasticity", "The required increment is 90 stable qualified monthly units and the plan reaches 90. The critical path contains equipment fabrication and delivery for twelve months followed by a four-month yield-and-manufacturing-release task. These are independent equipment and yield constraints. Three full stable-output months follow the sixteen-month physical path."),
  controlled("H15-E12", "H15-C02", "substitute_weakness", "The frozen registry includes product, material, process, system architecture, software reduction and vertical integration. After two no-new-route rounds, every category is resolved: its routes are explicitly failed, ineligible, cancelled, outside the observation window, or proven to have insufficient capacity. No route is unresolved, no Ready output exists, and the search budget was not exhausted."),
  controlled("H15-E13", "H15-C03", "demand_pressure", "Like-for-like annual demand for the same Scope declined from 1,000 units to 970 units."),
  controlled("H15-E14", "H15-C03", "downstream_criticality", "The structural hard-block test fails. The standardized operational shock was fully absorbed without delay, unit impact, performance degradation, compliance failure or any other downstream consequence; the no-impact finding is explicit."),
  controlled("H15-E15", "H15-C03", "effective_supply_concentration", "Four qualified suppliers each provide effective output 25 against demand 100. After loss of one of them, the other suppliers can add 30 qualified, uncommitted units within 90 days beyond baseline."),
  controlled("H15-E16", "H15-C03", "qualification_barrier", "A replacement supplier completes qualification in two months."),
  controlled("H15-E17", "H15-C03", "capacity_inelasticity", "Top-1 loss is 25 stable monthly units and qualified 90-day failover is 30. Therefore the required physical capacity increment is zero, with complete quantity evidence."),
  controlled("H15-E18", "H15-C03", "substitute_weakness", "After complete two-round discovery across every required category, a different-route Production substitute provides 35 mutually exclusive qualified units against demand 100. Shared pools and duplicate customer allocations were removed; no route remains unresolved."),
];
const holdoutPack = {
  evidence_pack_id: "theme-chokepoint-holdout-evidence-v1.5",
  contract_id: "theme-chokepoint-scoring-v1.5",
  pack_type: "sealed_controlled_semantic_holdout",
  as_of_date: "2026-08-20",
  contamination_scan: { performed_before_creation: true, result: "zero_hits", patterns: ["H15-C01", "H15-C02", "H15-C03", "subsea HVDC export cable controlled case", "single-use bioprocess sterile connector controlled case", "modular industrial thermal-loop controlled case"] },
  proof_boundary: "Anonymous controlled cases validate scoring semantics and inter-annotator consistency. They do not establish real-world market prevalence or company investment conclusions.",
  evidence: holdoutEvidence,
  cases: [
    { case_id: "H15-C01", case_type: "controlled_counterfactual", industry: "subsea_power_transmission", assessment_scope: { company_id: null, product_id: "anonymous_hvdc_export_cable_system", segment_id: "seg_hvdc_export_cable_controlled", customer_or_platform_scope: "platform_alpha", geography: "region_alpha", time_horizon_months: 12, as_of_date: "2026-08-20" }, evidence_ids: ["H15-E01", "H15-E02", "H15-E03", "H15-E04", "H15-E05", "H15-E06"], state_context: { demand_direct_evidence: true, supply_direct_evidence: true, counter_evidence_search_complete: true, mandatory_conflict: false, independent_supply_constraint_count: 3, key_source_quality_high: true } },
    { case_id: "H15-C02", case_type: "controlled_counterfactual", industry: "bioprocess_consumables", assessment_scope: { company_id: null, product_id: "anonymous_sterile_connector", segment_id: "seg_sterile_connector_controlled", customer_or_platform_scope: "platform_beta", geography: "region_beta", time_horizon_months: 12, as_of_date: "2026-08-20" }, evidence_ids: ["H15-E07", "H15-E08", "H15-E09", "H15-E10", "H15-E11", "H15-E12"], state_context: { demand_direct_evidence: true, supply_direct_evidence: true, counter_evidence_search_complete: true, mandatory_conflict: false, independent_supply_constraint_count: 2, key_source_quality_high: true } },
    { case_id: "H15-C03", case_type: "controlled_counterfactual", industry: "industrial_thermal_management", assessment_scope: { company_id: null, product_id: "anonymous_modular_heat_exchanger", segment_id: "seg_modular_heat_exchanger_controlled", customer_or_platform_scope: "platform_gamma", geography: "region_gamma", time_horizon_months: 12, as_of_date: "2026-08-20" }, evidence_ids: ["H15-E13", "H15-E14", "H15-E15", "H15-E16", "H15-E17", "H15-E18"], state_context: { demand_direct_evidence: true, supply_direct_evidence: true, counter_evidence_search_complete: true, mandatory_conflict: false, independent_supply_constraint_count: 0, key_source_quality_high: true } },
  ],
};

function taskFor(kind, pack) {
  const prefix = kind === "golden" ? "G15" : "H15";
  const ordinal = []; const hard = []; const states = [];
  pack.cases.forEach((item, caseIndex) => {
    const number = caseIndex + 1; const ordIds = [];
    dimensions.forEach((dimension, dimensionIndex) => { const itemId = `${prefix}-O${String(number).padStart(2, "0")}-${String(dimensionIndex + 1).padStart(2, "0")}`; ordIds.push(itemId); ordinal.push({ item_id: itemId, case_id: item.case_id, score_family: "segment", dimension }); });
    const gateDefs = [["segment_discovery", "Segment discovery eligibility"], ["counter_search_complete", "Counter-search completion"], ["strong_candidate_evidence", "Strong-candidate evidence quality"]];
    const gateIds = gateDefs.map(([gate, description], index) => { const itemId = `${prefix}-H${String(number).padStart(2, "0")}-${String(index + 1).padStart(2, "0")}`; hard.push({ item_id: itemId, case_id: item.case_id, gate, description }); return itemId; });
    states.push({ item_id: `${prefix}-S${String(number).padStart(2, "0")}`, case_id: item.case_id, state_family: "segment", ordinal_item_ids: ordIds, hard_gate_item_ids: gateIds });
  });
  return { task_id: `theme-chokepoint-${kind}-double-blind-v1.5`, task_type: kind === "golden" ? "human_golden_double_annotation" : "sealed_holdout_double_annotation", contract_id: "theme-chokepoint-scoring-v1.5", machine_contract_id: "theme-chokepoint-semantic-task-contract-v1.5", machine_contract_sha256: contractSha, evidence_pack_id: pack.evidence_pack_id, blinding: { independent_submissions_required: 2, adjudication_visible_before_submission: false, expected_labels_in_pack: false }, allowed_annotator_roles: ["internal_blind_annotator", "external_blind_annotator"], segment_weights: segmentWeights, ordinal_tasks: ordinal, hard_gate_tasks: hard, state_tasks: states, allowed_primary_states: [null, "not_supported", "watch_segment", "candidate_chokepoint", "strong_candidate_chokepoint"], required_output_fields: ["task_id", "contract_id", "evidence_pack_id", "annotator_id", "annotator_role", "annotation_started_at", "annotation_completed_at", "independent_work_attestation", "input_hashes", "ordinal", "hard_gate", "state"] };
}

const instructions = (kind) => `# Theme Chokepoint v1.5 ${kind === "golden" ? "人工 Golden" : "封存 Holdout"} 双盲标注说明\n\n你只能使用包内 Evidence Pack、v1.5评分合同和机器合同。不得查看另一位标注员的结果、历史裁决或任何答案文件。\n\n## 六个维度\n\n1. Demand Pressure：同Scope年度增长；<=0为0，(0,10%)为1，[10%,20%)为2，[20%,30%]为3，>30%且直接传导与库存验证均完成为4。缺直接传导不得保留正向floor。\n2. Downstream Criticality：先做永久移除100%能力的结构测试；只有整个原子Scope硬阻断才为4。否则按Top-1停供90天、允许库存与90天内已认证同路线Failover、观察12个月：明确无影响0；<1个月且<5%为1；1–3个月或5%–10%为2；>3个月、>10%或明显性能下降为3。临时停产最高3。\n3. Effective Supply Concentration：effective_output与份额按合同计算；N_eff、Top1、90天增量Failover分别映射0–4，再按30/30/40 half-up合成。Top1子分4且Failover子分4时exact 4。\n4. Qualification Barrier：<=3个月0；>3–6为1；>6–12为2；>12–18为3；>=18为4。\n5. Capacity Inelasticity：DeltaQ=max(0,Top1 loss-90天Failover)。DAG串行相加、并行取最长路径，并在物理路径后加入连续3个完整月稳定合格良品。<=3月0；>3–6为1；>6–12为2；>12–<18为3；>=18且关键路径至少两类独立物理约束为4；只有一类为[3,4]。\n6. Substitute Weakness：不同路线才进入本维度。去重后的Ready/Production覆盖>=30%为0，10%–<30%为1，credible但Ready<10%为2，仅原型/测试/未兑现扩产为3。exact 4要求两轮无新增、全类别完成、所有路线明确负面、无unresolved且预算未耗尽。\n\n## Bound Basis\n\n- exact 4：natural_cap。\n- exact 0–3：direct_upper_bound或contract_exclusivity，并列出所有更高档至4。\n- lower_bound [k,4]：只填写floor；upper_bound [0,k]：只填写ceiling；interval同时填写；unknown固定为none/[0,4]且三个basis字段均为null。\n\n## 工作顺序\n\n先逐项填写Ordinal，再按合同机械填写Hard Gate、derived_metrics和Final State。Validator只检查结构与机械派生，不判断你选择证据锚点是否正确。提交前运行：\n\n\`\`\`powershell\nnode validate-submission-v1.5.mjs annotation-task-v1.5.json evidence-pack-v1.5.json annotator-result-<你的ID>-v1.5.json\n\`\`\`\n\n只有输出 valid=true 才提交。不要修改Task、Evidence Pack、Validator或输入哈希。\n`;

function blankResult(task, packPath, taskPath, role) {
  return { task_id: task.task_id, contract_id: task.contract_id, evidence_pack_id: task.evidence_pack_id, annotator_id: "REPLACE_WITH_RANDOM_ID", annotator_role: role, annotation_started_at: null, annotation_completed_at: null, independent_work_attestation: false, input_hashes: { machine_contract_sha256: contractSha, evidence_pack_sha256: shaFile(packPath), task_sha256: shaFile(taskPath) }, ordinal: task.ordinal_tasks.map((item) => ({ item_id: item.item_id, evidence_state: null, bound_type: null, rating_min: null, rating_max: null, primary_evidence_ids: [], bound_basis: { floor_anchor: null, ceiling_anchor: null, exact_basis: null, unresolved_higher_anchors: [], excluded_higher_anchors: [] }, withheld_reason: null, rationale: "" })), hard_gate: task.hard_gate_tasks.map((item) => ({ item_id: item.item_id, label: null, evidence_ids: [], rationale: "" })), state: task.state_tasks.map((item) => ({ item_id: item.item_id, primary_state: null, achieved_hard_gates: [], derived_metrics: { score_min: null, score_max: null, presence_coverage: null, resolved_coverage: null, decision_coverage: null }, withheld_reason: null, rationale: "" })) };
}

function build(kind, pack) {
  const common = path.join(out, kind, "common"); fs.mkdirSync(common, { recursive: true });
  const packPath = path.join(common, "evidence-pack-v1.5.json"); writeJson(packPath, pack);
  const task = taskFor(kind, pack); const taskPath = path.join(common, "annotation-task-v1.5.json"); writeJson(taskPath, task);
  writeText(path.join(common, "blind-annotator-instructions-v1.5.zh-CN.md"), instructions(kind));
  for (const [source, name] of [[contractPath, "semantic-task-contract-v1.5.json"], [validatorPath, "semantic-validator-v1.5.mjs"], [validateCliPath, "validate-submission-v1.5.mjs"]]) fs.copyFileSync(source, path.join(common, name));
  for (const role of ["internal_blind_annotator", "external_blind_annotator"]) {
    const lane = role.startsWith("internal") ? "internal-blind" : "external-blind"; const dir = path.join(out, kind, lane); fs.mkdirSync(dir, { recursive: true });
    for (const name of fs.readdirSync(common)) fs.copyFileSync(path.join(common, name), path.join(dir, name));
    writeJson(path.join(dir, "annotator-result-template-v1.5.json"), blankResult(task, packPath, taskPath, role));
  }
  return { task, packPath, taskPath };
}

const expectedOutputParent = path.resolve(root, "outputs");
if (path.dirname(path.resolve(out)) !== expectedOutputParent || path.basename(out) !== "theme-chokepoint-v1.5-evaluation-20260820-001") throw new Error("refusing unsafe evaluation output reset");
fs.rmSync(out, { recursive: true, force: true }); fs.mkdirSync(out, { recursive: true });
const golden = build("golden", goldenPack); const holdout = build("holdout", holdoutPack);
for (const [source, name] of [[agreementPath, "calculate-agreement-v1.5.mjs"], [validateCliPath, "validate-submission-v1.5.mjs"], [validatorPath, "semantic-validator-v1.5.mjs"]]) fs.copyFileSync(source, path.join(out, name));
writeJson(path.join(out, "pollution-scan-v1.5.json"), { scan_time: "2026-08-20T20:00:00+08:00", scanned_before_pack_creation: true, exclusions: ["new output directory"], patterns: holdoutPack.contamination_scan.patterns, result: "zero_hits", limitation: "String scan proves only that precommitted identifiers and phrases were absent; controlled vignettes are not real market cases." });
writeJson(path.join(out, "precommit-manifest-v1.5.json"), { evaluation_id: "theme-chokepoint-v1.5-human-evaluation-20260820-001", contract_id: "theme-chokepoint-scoring-v1.5", status: "annotation_pack_ready", golden: { evidence_pack_id: goldenPack.evidence_pack_id, case_ids: goldenPack.cases.map((item) => item.case_id), ordinal_items: golden.task.ordinal_tasks.length }, holdout: { evidence_pack_id: holdoutPack.evidence_pack_id, case_ids: holdoutPack.cases.map((item) => item.case_id), ordinal_items: holdout.task.ordinal_tasks.length, case_type: "controlled_counterfactual" }, gates: { hard_gate_agreement_min: 0.95, final_state_agreement_min: 0.90, exact_exact_pairs_min: 10, linear_weighted_kappa_min: 0.70, high_risk_one_sided_upgrades_max: 0 }, proof_boundary: "No annotation result, agreement, adjudication, HBM gate or Top-10 gate is claimed by this manifest." });
writeText(path.join(out, "README.zh-CN.md"), `# Theme Chokepoint v1.5 人工评估包\n\n- Golden：真实来源边界案例，HBM/先进封装与大型变压器。\n- Holdout：三个此前不存在的匿名控制案例，共18个Segment Ordinal，用于检验v1.5评分语义和双标一致性。\n- 内部与外部标注员只打开各自的 \`internal-blind\` / \`external-blind\` 目录。\n- 两份结果都通过Validator并封存SHA后，才运行根目录的agreement计算器。\n- Holdout是控制语义测试，不冒充真实市场事实；真实世界外部有效性由Golden、HBM回归与Top-10复核补充。\n`);
const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => entry.isDirectory() ? walk(path.join(dir, entry.name)) : [path.join(dir, entry.name)]);
const flat = walk(out).filter((file) => !file.endsWith("SHA256SUMS-v1.5.txt"));
writeText(path.join(out, "SHA256SUMS-v1.5.txt"), `${flat.sort().map((file) => `${shaFile(file)}  ${path.relative(out, file).replaceAll("\\", "/")}`).join("\n")}\n`);
console.log(JSON.stringify({ output: out, golden_cases: goldenPack.cases.length, holdout_cases: holdoutPack.cases.length, holdout_ordinals: holdout.task.ordinal_tasks.length }, null, 2));
