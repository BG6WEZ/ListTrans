# -*- coding: utf-8 -*-
"""Shared zero-side-effect RuleContext & scope matching single source of truth (V016-M1 / F27).

Unifies dynamic rule scope evaluation across:
- text cleaning (normalizer)
- structured parameter extraction (query_preprocessor)
- candidate scoring & hard exclusion (matcher / binding_decisions)
- flywheel pattern mining & rule suggestion grouping (feedback_miner)

Strict field separation:
- `professional`: engineering discipline (e.g. 暖通, 给排水, 强电, 医气)
- `material_category`: procurement/material category column (e.g. 甲供材, 阀门)
- `domain`: routed extractor domain (e.g. pipe_copper, ahu)
- `name` / `raw_name` / `original_name`: item name candidates

Missing required context fields NEVER silently fall back to universal or cross-discipline
defaults (such as '给排水' or '通用'); restricted rules are skipped with explicit diagnostics.
"""
from dataclasses import dataclass
import re
from typing import Any, Dict, List, Tuple

PROFESSIONAL_ALIASES: Dict[str, str] = {
    "医气": "医气",
    "医用气体": "医气",
    "医疗气体": "医气",
    "气体": "医气",
}

DYNAMIC_UNIVERSAL_TOKENS = frozenset({"通用", "全部", "all", "universal"})
SUPPORTED_SCOPE_LEVELS = frozenset({"universal", "discipline", "category"})

_SPLIT_TARGETS_RE = re.compile(r"[/,，、|\s]+")
_LEADING_INDEX_RE = re.compile(r"^\d+[\.．、\s]*")


def strip_professional_prefix(value: Any) -> str:
    """Strip leading numbering prefixes (e.g. '1.暖通' -> '暖通') without fabricating defaults."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return _LEADING_INDEX_RE.sub("", text).strip()


def normalize_professional_category(value: Any) -> str:
    """Normalize known professional display names/aliases; empty -> '', unknown preserved."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return PROFESSIONAL_ALIASES.get(text, text)


def canonicalize_professional(value: Any) -> str:
    """Strip numbering prefix and normalize professional aliases; never guesses from category/name."""
    stripped = strip_professional_prefix(value)
    if not stripped:
        return ""
    return normalize_professional_category(stripped)


def split_scope_targets(scope_tgt: str) -> List[str]:
    if not scope_tgt:
        return []
    return [t.strip() for t in _SPLIT_TARGETS_RE.split(str(scope_tgt)) if t.strip()]


def dynamic_scope_matches(
    scope_tgt: str,
    candidate_names: List[Any],
    *,
    strict_min_substr_len: int = 2,
) -> bool:
    """Compare target pattern(s) against candidate names/categories without cross-domain defaults."""
    if not scope_tgt:
        return True
    raw_targets = split_scope_targets(scope_tgt)
    targets: List[str] = []
    for t in raw_targets:
        t_low = t.lower()
        if t_low not in targets:
            targets.append(t_low)
        norm_t = normalize_professional_category(t).lower()
        if norm_t and norm_t not in targets:
            targets.append(norm_t)
    for c in candidate_names:
        if not c:
            continue
        c_str = str(c).strip()
        if not c_str:
            continue
        c_low = c_str.lower()
        c_norm = normalize_professional_category(c_str).lower()
        for cand in (c_low, c_norm):
            if not cand:
                continue
            for t in targets:
                if cand == t:
                    return True
                if (
                    len(cand) >= strict_min_substr_len
                    and len(t) >= strict_min_substr_len
                    and (t in cand or cand in t)
                ):
                    return True
    return False


def discipline_scope_matches(scope_tgt: str, professional: str) -> bool:
    """Strict discipline matcher honoring verified aliases (医用气体=医气) without category fallback."""
    p_raw = strip_professional_prefix(professional)
    p_norm = normalize_professional_category(p_raw)
    if not p_norm:
        return False
    p_low = p_norm.lower()
    p_raw_low = p_raw.lower()
    if p_low in DYNAMIC_UNIVERSAL_TOKENS or p_norm == "其它":
        return False

    raw_targets = split_scope_targets(scope_tgt)
    if not raw_targets:
        return False

    target_norms: List[str] = []
    long_aliases: List[str] = []
    for t in raw_targets:
        t_clean = strip_professional_prefix(t)
        if not t_clean:
            continue
        t_norm = normalize_professional_category(t_clean)
        for val in (t_clean.lower(), t_norm.lower()):
            if val and val not in target_norms:
                target_norms.append(val)
        for alias_key, alias_canon in PROFESSIONAL_ALIASES.items():
            if alias_canon == t_norm and len(alias_key) >= 3:
                low_a = alias_key.lower()
                if low_a not in long_aliases:
                    long_aliases.append(low_a)

    for cand in (p_low, p_raw_low):
        if not cand:
            continue
        if cand in target_norms:
            return True
        for t in target_norms:
            if len(cand) >= 2 and len(t) >= 2 and (t in cand or cand in t):
                return True
        for alias in long_aliases:
            if alias in cand:
                return True
    return False


@dataclass(frozen=True)
class RuleScopeResult:
    matched: bool
    reason: str
    scope_level: str
    scope_target: str


@dataclass(frozen=True)
class RuleContext:
    """Explicitly separated context for rule scoping across cleaning/extraction/matching/mining."""

    professional: str = ""
    material_category: str = ""
    domain: str = ""
    name: str = ""
    raw_name: str = ""
    original_name: str = ""
    stage: str = "matching"

    @property
    def normalized_professional(self) -> str:
        return canonicalize_professional(self.professional)

    @property
    def name_candidates(self) -> List[str]:
        out: List[str] = []
        for v in (self.name, self.raw_name, self.original_name):
            s = str(v or "").strip()
            if s and s not in out:
                out.append(s)
        return out

    @property
    def category_candidates(self) -> List[str]:
        """Candidates for `scope_level='category'` (item names + material_category, never professional)."""
        out = list(self.name_candidates)
        mat_cat = str(self.material_category or "").strip()
        if mat_cat and mat_cat not in out:
            out.append(mat_cat)
        return out

    @classmethod
    def from_fields(
        cls,
        *,
        professional: Any = "",
        material_category: Any = "",
        domain: Any = "",
        name: Any = "",
        raw_name: Any = "",
        original_name: Any = "",
        stage: str = "matching",
    ) -> "RuleContext":
        return cls(
            professional=str(professional or "").strip(),
            material_category=str(material_category or "").strip(),
            domain=str(domain or "").strip(),
            name=str(name or "").strip(),
            raw_name=str(raw_name or "").strip(),
            original_name=str(original_name or "").strip(),
            stage=str(stage or "matching").strip() or "matching",
        )

    @classmethod
    def from_query(cls, query_info: Dict[str, Any], *, stage: str = "matching") -> "RuleContext":
        if not isinstance(query_info, dict):
            return cls(stage=stage)
        # Strict: professional comes ONLY from explicit professional/discipline keys,
        # NEVER from material category, original_category, domain, or item name.
        prof = str(query_info.get("professional") or query_info.get("discipline") or "").strip()
        name_val = str(query_info.get("name") or "").strip()
        raw_name_val = str(query_info.get("raw_name") or "").strip()
        orig_name_val = str(query_info.get("original_name") or "").strip()
        domain_val = str(query_info.get("domain") or "").strip()

        if "material_category" in query_info:
            mat_cat = str(query_info.get("material_category") or "").strip()
        elif "original_category" in query_info:
            mat_cat = str(query_info.get("original_category") or "").strip()
        else:
            raw_cat = str(query_info.get("category") or "").strip()
            # Guard against prepare_query's legacy fallback (`category = prof or spec_name`)
            if raw_cat and raw_cat not in (prof, name_val, raw_name_val, orig_name_val):
                mat_cat = raw_cat
            else:
                mat_cat = ""

        return cls(
            professional=prof,
            material_category=mat_cat,
            domain=domain_val,
            name=name_val,
            raw_name=raw_name_val,
            original_name=orig_name_val,
            stage=stage,
        )

    @classmethod
    def from_candidate(
        cls,
        candidate: Dict[str, Any],
        *,
        name: str = "",
        domain: str = "",
        professional: str = "",
        stage: str = "cleaning",
    ) -> "RuleContext":
        if not isinstance(candidate, dict):
            return cls(professional=professional, domain=domain, name=name, stage=stage)
        cand_raw_name = str(candidate.get("name") or "").strip()
        cand_cat = str(candidate.get("category") or "").strip()
        return cls(
            professional=str(professional or "").strip(),
            material_category=cand_cat,
            domain=str(domain or "").strip(),
            name=str(name or cand_raw_name).strip(),
            raw_name=cand_raw_name,
            original_name=cand_raw_name,
            stage=stage,
        )

    def evaluate_rule(self, rule: Dict[str, Any]) -> RuleScopeResult:
        if not isinstance(rule, dict):
            return RuleScopeResult(False, "invalid_rule_object", "", "")
        scope_lvl = str(rule.get("scope_level") or "category").strip().lower()
        scope_tgt = str(rule.get("scope_target") or "").strip()

        if scope_lvl == "universal":
            return RuleScopeResult(True, "universal_scope", scope_lvl, scope_tgt)

        if scope_lvl == "discipline":
            effective_tgt = scope_tgt or str(rule.get("discipline") or "").strip()
            if not effective_tgt:
                return RuleScopeResult(False, "missing_rule_scope_target", scope_lvl, scope_tgt)
            targets = split_scope_targets(effective_tgt)
            if any(t.lower() in DYNAMIC_UNIVERSAL_TOKENS for t in targets):
                return RuleScopeResult(True, "explicit_universal_target", scope_lvl, effective_tgt)
            if not self.normalized_professional:
                return RuleScopeResult(False, "missing_professional_context", scope_lvl, effective_tgt)
            if discipline_scope_matches(effective_tgt, self.normalized_professional):
                return RuleScopeResult(True, "discipline_matched", scope_lvl, effective_tgt)
            return RuleScopeResult(False, "discipline_mismatch", scope_lvl, effective_tgt)

        if scope_lvl == "category":
            if not scope_tgt:
                return RuleScopeResult(True, "unrestricted_category_target", scope_lvl, scope_tgt)
            targets = split_scope_targets(scope_tgt)
            if any(t.lower() in DYNAMIC_UNIVERSAL_TOKENS for t in targets):
                return RuleScopeResult(True, "explicit_universal_target", scope_lvl, scope_tgt)
            cands = self.category_candidates
            if not cands:
                return RuleScopeResult(False, "missing_category_or_name_context", scope_lvl, scope_tgt)
            if dynamic_scope_matches(scope_tgt, cands):
                return RuleScopeResult(True, "category_matched", scope_lvl, scope_tgt)
            return RuleScopeResult(False, "category_mismatch", scope_lvl, scope_tgt)

        return RuleScopeResult(False, f"unsupported_scope_level:{scope_lvl}", scope_lvl, scope_tgt)

    def matches_rule(self, rule: Dict[str, Any]) -> bool:
        return self.evaluate_rule(rule).matched

    def to_scope_dict(self) -> Dict[str, str]:
        return {
            "professional": self.normalized_professional,
            "raw_professional": self.professional,
            "material_category": self.material_category,
            "domain": self.domain,
            "name": self.name,
            "stage": self.stage,
        }


def dynamic_rule_matches_query(rule: Dict[str, Any], query_info: Dict[str, Any]) -> bool:
    """Shared dynamic rule scope predicate powered by RuleContext."""
    return RuleContext.from_query(query_info, stage="matching").matches_rule(rule)


def evaluate_dynamic_rule_scope(
    rule: Dict[str, Any],
    query_info: Dict[str, Any],
    *,
    stage: str = "matching",
) -> RuleScopeResult:
    """Return full RuleScopeResult with skip diagnostics for a query_info dict."""
    return RuleContext.from_query(query_info, stage=stage).evaluate_rule(rule)
