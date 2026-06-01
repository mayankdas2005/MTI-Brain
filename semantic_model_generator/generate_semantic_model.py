import sys
import logging
from decimal import Decimal
from pathlib import Path
from datetime import datetime, timezone

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from graph.config import rs
from graph.extract.redshift import RedshiftExtractor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

table_names = ['ach_return',
 'acquirer',
 'acquirer_contract',
 'acquirer_sla_metric',
 'ap_invoice',
 'app_user',
 'ar_invoice',
 'audit_trail',
 'bank',
 'bank_account',
 'bank_account_group',
 'bank_account_group_member',
 'bank_account_signatory',
 'bank_branch',
 'bank_fee',
 'bank_group',
 'bank_group_member',
 'bank_service_type',
 'bank_statement_balance',
 'benchmark_rate',
 'borrowing',
 'brain_evaluation',
 'budget_code',
 'capital_allocation_actual',
 'card_authorization',
 'card_bin_range',
 'card_network',
 'card_rebate_earning',
 'card_rebate_program',
 'card_settlement_batch',
 'card_settlement_line',
 'cash_balance',
 'cash_flow',
 'cash_flow_code',
 'chargeback',
 'company',
 'company_financial_metric',
 'company_group',
 'company_group_member',
 'counterparty_exposure',
 'credit_facility',
 'credit_rating',
 'cross_border_payment_leg',
 'currency',
 'data_permission',
 'data_permission_profile',
 'derivative_mtm',
 'equity_action',
 'fee_rate_card',
 'forecast_cash_flow',
 'forecast_snapshot',
 'forecast_vs_actual',
 'fraud_detection_event',
 'fraud_loss',
 'fx_exposure_forecast',
 'fx_forward',
 'fx_rate',
 'gen_company_region',
 'gl_account',
 'gl_balance',
 'gl_reconciliation',
 'hedge_dedesignation',
 'hedge_relationship',
 'intercompany_transaction',
 'interest_accrual',
 'investment_instrument',
 'investment_position',
 'investment_transaction',
 'kg_relationship',
 'letter_of_credit',
 'liquidity_policy',
 'macro_indicator',
 'mapping_entry',
 'mapping_table',
 'membership_fee',
 'payment_exception',
 'payment_file',
 'payment_hub_throughput',
 'payment_transaction',
 'peer_company',
 'peer_company_metric',
 'pension_plan',
 'pension_valuation',
 'pos_transaction',
 'sme_feedback_session',
 'sme_transcript_chunk',
 'stress_run_result',
 'stress_scenario',
 'sweep_execution',
 'sweep_instruction',
 'third_party',
 'third_party_bank_account',
 'third_party_category',
 'third_party_category_assignment',
 'transfer',
 'tribal_knowledge_entity_link',
 'tribal_knowledge_fact',
 'user_group',
 'user_group_member',
 'user_profile_assignment',
 'v_sme_transcript_chunk_indexable',
 'v_tribal_fact_indexable',
 'wcf_document',
 'webhook_event',
 'working_capital_metric']
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_FILE = OUTPUT_DIR / "lpp_semantic_model.yml"


# table_names = [
#     "ach_return", "acquirer", "acquirer_contract", "acquirer_sla_metric", "ap_invoice",
#     "app_user", "ar_invoice", "bank", "bank_account", "bank_account_signatory",
#     "bank_branch", "bank_fee"]
# ── YAML helpers ──────────────────────────────────────────────────────────────

def _scalar(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def _setup_yaml():
    yaml.add_representer(
        Decimal,
        lambda d, v: d.represent_int(_scalar(v)) if isinstance(_scalar(v), int) else d.represent_float(_scalar(v)),
    )
    yaml.add_representer(type(None), lambda d, _: d.represent_scalar("tag:yaml.org,2002:null", ""))


def _write(model: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        yaml.dump(model, fh, default_flow_style=False, allow_unicode=True, sort_keys=False, indent=2)


# ── Index builder (runs once after raw fetch) ─────────────────────────────────

def _build_indexes(raw: dict, tbl_filter: set) -> dict:
    tables_meta = {r["table_name"]: r for r in raw["tables"] if r["table_name"] in tbl_filter}

    columns_by_table: dict[str, list] = {}
    for col in raw["columns"]:
        if col["table_name"] in tbl_filter:
            columns_by_table.setdefault(col["table_name"], []).append(col)

    pk_map: dict[str, list] = {}
    fk_map: dict[str, list] = {}

    for c in raw["constraints"]:
        tbl = c["table_name"]
        if tbl not in tbl_filter:
            continue
        if c["constraint_type"] == "PRIMARY KEY":
            pk_map.setdefault(tbl, []).append(c["column_name"])
        elif c["constraint_type"] == "FOREIGN KEY":
            fk_map.setdefault(tbl, []).append({
                "from_column": c["column_name"],
                "to_table": c.get("ref_table") or "",
                "to_column": c.get("ref_column") or "",
            })

    for c in raw.get("cross_schema", []):
        if c.get("constraint_type") != "FOREIGN KEY":
            continue
        tbl = c.get("from_table", "")
        if tbl not in tbl_filter:
            continue
        entry = {
            "from_column": c.get("from_col", ""),
            "to_table": c.get("to_table", ""),
            "to_column": c.get("to_col", ""),
        }
        if entry not in fk_map.get(tbl, []):
            fk_map.setdefault(tbl, []).append(entry)

    key_cols_by_table: dict[str, list] = {}
    for r in raw["key_cols"]:
        if r["table_name"] in tbl_filter:
            key_cols_by_table.setdefault(r["table_name"], []).append(r)

    return {
        "tables_meta": tables_meta,
        "columns_by_table": columns_by_table,
        "pk_map": pk_map,
        "fk_map": fk_map,
        "key_cols_by_table": key_cols_by_table,
    }


# ── Single-table entry builder ────────────────────────────────────────────────

def _build_table_entry(tbl_name: str, idx: dict) -> dict:
    meta = idx["tables_meta"].get(tbl_name, {})
    cols_raw = sorted(
        idx["columns_by_table"].get(tbl_name, []),
        key=lambda x: x.get("ordinal_position", 0),
    )
    pks = idx["pk_map"].get(tbl_name, [])
    fks = idx["fk_map"].get(tbl_name, [])
    fk_col_set = {f["from_column"] for f in fks}

    distkey_col = meta.get("distkey_col") or ""
    sort_keys = []
    for kc in sorted(
        idx["key_cols_by_table"].get(tbl_name, []),
        key=lambda x: int(x.get("sortkey", 0) or 0),
    ):
        sp = int(kc.get("sortkey", 0) or 0)
        if sp > 0:
            sort_keys.append({"column": kc["column_name"], "position": sp})

    columns_output = []
    for col in cols_raw:
        col_name = col["column_name"]
        data_type = col.get("data_type", "")
        max_len = col.get("max_length")
        num_prec = col.get("numeric_precision")
        num_scale = col.get("numeric_scale")

        if max_len:
            type_str = f"{data_type}({int(_scalar(max_len))})"
        elif num_prec and num_scale and _scalar(num_scale) > 0:
            type_str = f"{data_type}({int(_scalar(num_prec))},{int(_scalar(num_scale))})"
        elif num_prec:
            type_str = f"{data_type}({int(_scalar(num_prec))})"
        else:
            type_str = data_type

        is_notnull = col.get("is_notnull", False)
        is_nullable_str = str(col.get("is_nullable", "YES")).upper()
        nullable = (not is_notnull) if (is_notnull is not None) else (is_nullable_str in ("YES", "TRUE", "1"))

        fk_ref = next((f for f in fks if f["from_column"] == col_name), None)

        col_entry: dict = {
            "name": col_name,
            "data_type": type_str,
            "ordinal_position": col.get("ordinal_position"),
            "nullable": nullable,
            "default": col.get("column_default"),
            "is_primary_key": col_name in pks,
            "is_foreign_key": col_name in fk_col_set,
            "is_distkey": bool(col.get("is_distkey", False)),
            "sortkey_position": int(col.get("sortkey_pos", 0) or 0),
            "encoding": col.get("encoding", "none") or "none",
        }
        if fk_ref:
            col_entry["references"] = {
                "table": fk_ref["to_table"],
                "column": fk_ref["to_column"],
            }
        columns_output.append(col_entry)

    fk_summary = [
        {"from_column": f["from_column"], "to_table": f["to_table"], "to_column": f["to_column"]}
        for f in fks
    ]

    entry: dict = {
        "name": tbl_name,
        "schema": "lpp",
        "row_count": _scalar(meta.get("row_count")),
        "size_mb": _scalar(meta.get("size_mb")),
        "dist_style": meta.get("diststyle") or "AUTO",
        "distkey_column": distkey_col if distkey_col else None,
        "sort_keys": sort_keys if sort_keys else None,
        "primary_keys": pks if pks else None,
        "foreign_keys": fk_summary if fk_summary else None,
        "columns": columns_output,
    }
    return {k: v for k, v in entry.items() if v is not None}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    _setup_yaml()

    extractor = RedshiftExtractor(
        host=rs.host,
        database=rs.db,
        user=rs.user,
        password=rs.password,
        port=rs.port,
        schema=rs.schema,
    )

    tbl_filter = set(table_names)
    total = len(table_names)

    log.info("Phase 1 — fetching all metadata for %d tables from Redshift …", total)
    raw = extractor.run_all(table_names=tbl_filter)

    log.info("Phase 2 — building indexes …")
    idx = _build_indexes(raw, tbl_filter)

    # Flat FK edge list across all tables
    all_relationships = [
        {"from_table": tbl, "from_column": fk["from_column"],
         "to_table": fk["to_table"], "to_column": fk["to_column"]}
        for tbl, fks in idx["fk_map"].items()
        for fk in fks
    ]

    model: dict = {
        "version": "1.0",
        "schema": "lpp",
        "database": rs.db,
        "host": rs.host,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "table_count": 0,
        "relationships": all_relationships if all_relationships else None,
        "tables": [],
    }

    log.info("Phase 3 — writing YAML incrementally (%d tables) …", total)
    for i, tbl_name in enumerate(sorted(tbl_filter), 1):
        entry = _build_table_entry(tbl_name, idx)
        model["tables"].append(entry)
        model["table_count"] = i
        _write(model)
        log.info("[%d/%d] %s — written", i, total, tbl_name)

    log.info("Done → %s", OUTPUT_FILE)
    if all_relationships:
        log.info("Relationships: %d FK edges captured", len(all_relationships))


if __name__ == "__main__":
    main()
