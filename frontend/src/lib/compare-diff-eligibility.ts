import {
  readCapabilityRenderSubstrates,
  type CapabilitiesResponse,
  type CapabilityModel,
} from "@/lib/api";
import { PRECIP_ANOM_VAR_KEY_PATTERN } from "@/lib/compare-diff-scales";

/**
 * Diff-eligibility rules for compare difference mode (v1).
 *
 * Strategy is a conservative allowlist of high-value continuous fields — see
 * docs/COMPARE_DIFFERENCE_MODE_DESIGN.md (Open Decisions — Resolved #1). Do not
 * switch to a blocklist or add var_keys without explicit product instruction.
 */

/** Exact var_keys allowed for difference mode in v1. */
const DIFF_ELIGIBLE_VAR_KEYS = new Set<string>([
  // Temperature
  "tmp2m",
  "dp2m",
  "tmp850",
  "tmp2m_anom",
  "tmp850_anom",
  // Wind
  "wspd10m",
  "wgst10m",
  "wspd850",
  "wspd300",
  // Height / dynamics
  "hgt500",
  "hgt500_anom",
  "vort500",
  // Moisture
  "pwat",
  // Precip
  "apcp",
  "precip_total",
  "snowfall_total",
  // Instability
  "mlcape",
]);

/** var_keys explicitly excluded even if otherwise present. */
const EXCLUDED_VAR_KEYS = new Set<string>([
  "rh", // v1 — revisit in v2 once continuous palette treatment is verified
]);

/** Substring/prefix patterns excluded from difference mode. */
const EXCLUDED_VAR_KEY_PATTERNS: RegExp[] = [
  /reflectivity/i,
  /^ptype_/i,
  /^radar_/i,
];

/** palette/variable `kind` values whose subtraction is meaningless. */
const EXCLUDED_VARIABLE_KINDS = new Set<string>([
  "indexed",
  "categorical",
  "radar_ptype",
]);

/**
 * True when a var_key is on the v1 difference-mode allowlist. Operates on the
 * var_key alone — model/capability-level checks live in
 * {@link mutualDiffEligibleVariables}.
 */
export function isDiffEligible(varKey: string): boolean {
  const key = String(varKey ?? "").trim();
  if (!key) {
    return false;
  }
  if (EXCLUDED_VAR_KEYS.has(key)) {
    return false;
  }
  if (EXCLUDED_VAR_KEY_PATTERNS.some((pattern) => pattern.test(key))) {
    return false;
  }
  if (DIFF_ELIGIBLE_VAR_KEYS.has(key)) {
    return true;
  }
  // Rolling precip anomaly keys (`precip_5d_anom` … `precip_16d_anom`) —
  // design doc allowlist entry "`*_anom` precip keys". The pattern is shared
  // with compare-diff-scales so eligibility and the ±2 in scale stay in
  // lockstep.
  if (PRECIP_ANOM_VAR_KEY_PATTERN.test(key)) {
    return true;
  }
  return false;
}

/** Grid-backed var_keys for a model, preserving capability declaration order. */
function gridVariableKeys(modelCapability: CapabilityModel | null | undefined): string[] {
  if (!modelCapability?.variables) {
    return [];
  }
  return Object.entries(modelCapability.variables)
    .filter(([, variable]) => readCapabilityRenderSubstrates(variable).includes("grid"))
    .map(([id]) => String(id).trim())
    .filter(Boolean);
}

/** True when a model's variable has an excluded (non-continuous) kind. */
function hasExcludedKind(
  modelCapability: CapabilityModel | null | undefined,
  varKey: string,
): boolean {
  const kind = modelCapability?.variables?.[varKey]?.kind;
  if (typeof kind !== "string") {
    return false;
  }
  return EXCLUDED_VARIABLE_KINDS.has(kind.trim().toLowerCase());
}

/**
 * var_keys usable in difference mode for the given model pair: the intersection
 * of (a) grid-backed variables supported by both models and (b) the v1
 * diff-eligible allowlist, with non-continuous `kind`s excluded on either side.
 *
 * Order follows the left model's capability declaration order so auto-selection
 * of "the first variable" is deterministic (left wins — see Architectural
 * Decisions Locked #8).
 */
export function mutualDiffEligibleVariables(
  lModel: string,
  rModel: string,
  capabilities: CapabilitiesResponse,
): string[] {
  const leftCapability = capabilities?.model_catalog?.[lModel] ?? null;
  const rightCapability = capabilities?.model_catalog?.[rModel] ?? null;
  if (!leftCapability || !rightCapability) {
    return [];
  }

  const rightGridKeys = new Set(gridVariableKeys(rightCapability));

  const result: string[] = [];
  const seen = new Set<string>();
  for (const varKey of gridVariableKeys(leftCapability)) {
    if (seen.has(varKey)) {
      continue;
    }
    if (!rightGridKeys.has(varKey)) {
      continue;
    }
    if (!isDiffEligible(varKey)) {
      continue;
    }
    if (hasExcludedKind(leftCapability, varKey) || hasExcludedKind(rightCapability, varKey)) {
      continue;
    }
    seen.add(varKey);
    result.push(varKey);
  }
  return result;
}
