"""
schema_explorer.py

Builds a true ER-diagram YAML from lpp-ontology.ttl + lpp-r2rml.ttl.

Each class entry mirrors a DB table:
  ClassName:
    source_table: LPP.TABLE_NAME          # actual DB table / SQL query
    subclass_of:  ParentClass             # from ontology hierarchy
    columns:                              # datatype properties (literals)
      - property:  code
        db_column: CODE
        type:      string
    relationships:                        # object properties (FK → other class)
      - property:  ownedBy
        db_column: COMPANY_REF
        target:    Company

Plus two extra sections:
  enumerations:   SKOS concept instances (CardNetwork, PaymentMethod, etc.)
  schema_only:    classes declared in ontology but with no DB table mapping

Usage:
    python schema_explorer.py
"""

import re
from pathlib import Path
import yaml
from rdflib import Graph, RDF, RDFS, OWL, Namespace, URIRef
from rdflib.namespace import SKOS

DATA_DIR = Path(__file__).resolve().parents[1] / "backend" / "data"
OUT_DIR  = Path(__file__).resolve().parent / "output"
OUT_DIR.mkdir(exist_ok=True)

RR    = Namespace("http://www.w3.org/ns/r2rml#")
LPP   = Namespace("https://lpp.example/ontology#")
BRAIN = Namespace("https://lpp.example/ontology/brain#")
PROV  = Namespace("http://www.w3.org/ns/prov#")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def local(uri) -> str:
    s = str(uri)
    for sep in ("#", "/"):
        if sep in s:
            return s.rsplit(sep, 1)[-1]
    return s


def prefixed(uri: str) -> str:
    mapping = {
        "https://lpp.example/ontology#":        "",
        "https://lpp.example/ontology/brain#":  "brain:",
        "http://www.w3.org/ns/prov#":           "prov:",
    }
    for ns, prefix in mapping.items():
        if uri.startswith(ns):
            return prefix + uri[len(ns):]
    return local(uri)


def template_to_classname(tmpl: str) -> str:
    parts = [p for p in tmpl.split("/") if p and not p.startswith("{") and "." not in p]
    if parts:
        return "".join(w.capitalize() for w in parts[-1].split("-"))
    return ""


def get_source_table(r2rml: Graph, tm) -> str:
    lt = r2rml.value(tm, RR.logicalTable)
    if lt is None:
        return ""
    table = r2rml.value(lt, RR.tableName)
    if table:
        return str(table).lower()
    query = r2rml.value(lt, RR.sqlQuery)
    if query:
        m = re.search(r'\bFROM\s+(\S+)', str(query), re.IGNORECASE)
        if m:
            after = re.sub(r"[^\w\.]", "", m.group(1))
            return f"(sql) {after.lower()}"
    return ""


# ---------------------------------------------------------------------------
# Step 1 — collect ontology class hierarchy + SKOS enums
# ---------------------------------------------------------------------------

def collect_ontology_hierarchy(onto: Graph) -> dict[str, dict]:
    """class_name -> {subclass_of, comment}"""
    classes: dict[str, dict] = {}

    def add(uri: URIRef):
        name = prefixed(str(uri))
        if name in classes:
            return
        entry: dict = {}
        parents = [
            prefixed(str(p))
            for p in onto.objects(uri, RDFS.subClassOf)
            if isinstance(p, URIRef)
        ]
        if parents:
            entry["subclass_of"] = parents[0] if len(parents) == 1 else parents
        comment = next(onto.objects(uri, RDFS.comment), None)
        if comment:
            entry["comment"] = str(comment)
        classes[name] = entry

    for cls in onto.subjects(RDF.type, OWL.Class):
        if isinstance(cls, URIRef):
            add(cls)
    for s, _, o in onto.triples((None, RDFS.subClassOf, None)):
        if isinstance(s, URIRef):
            add(s)
        if isinstance(o, URIRef):
            add(o)

    return classes


def collect_enumerations(onto: Graph) -> dict[str, list[str]]:
    enums: dict[str, list[str]] = {}
    for concept_cls in onto.subjects(RDFS.subClassOf, SKOS.Concept):
        if not isinstance(concept_cls, URIRef):
            continue
        values = []
        for ind in onto.subjects(RDF.type, concept_cls):
            if not isinstance(ind, URIRef):
                continue
            lbl = onto.value(ind, SKOS.prefLabel)
            values.append(str(lbl) if lbl else local(str(ind)))
        if values:
            enums[prefixed(str(concept_cls))] = sorted(values)
    return dict(sorted(enums.items()))


# ---------------------------------------------------------------------------
# Step 2 — index property metadata (object vs datatype, range)
# ---------------------------------------------------------------------------

def collect_property_meta(onto: Graph) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    for prop in onto.subjects(RDF.type, OWL.ObjectProperty):
        if not isinstance(prop, URIRef):
            continue
        entry: dict = {"kind": "object"}
        r = next((o for o in onto.objects(prop, RDFS.range)  if isinstance(o, URIRef)), None)
        if r:
            entry["range"] = prefixed(str(r))
        meta[str(prop)] = entry

    for prop in onto.subjects(RDF.type, OWL.DatatypeProperty):
        if not isinstance(prop, URIRef):
            continue
        entry = {"kind": "datatype"}
        r = next((o for o in onto.objects(prop, RDFS.range) if isinstance(o, URIRef)), None)
        if r:
            entry["range"] = local(str(r))
        meta[str(prop)] = entry

    return meta


# ---------------------------------------------------------------------------
# Step 3 — walk every TriplesMap; build ER model keyed by class
# ---------------------------------------------------------------------------

def build_er_model(
    r2rml: Graph,
    prop_meta: dict[str, dict],
) -> dict[str, dict]:
    """
    Returns {class_name: {source_table, columns: [...], relationships: [...]}}
    Each column:      {property, db_column, type}
    Each relationship:{property, db_column, target}
    """
    model: dict[str, dict] = {}
    direct_source: set[str] = set()  # classes whose source_table came from rr:class (not template)
    sql_pom_count: dict[str, int] = {}  # class -> pom count of best SQL TriplesMap so far

    for tm in set(r2rml.subjects(RR.subjectMap, None)):
        sm = r2rml.value(tm, RR.subjectMap)
        if sm is None:
            continue

        # class name
        cls_uri = r2rml.value(sm, RR["class"])
        subj_tmpl = r2rml.value(sm, RR.template)
        if cls_uri and isinstance(cls_uri, URIRef):
            cls_name = prefixed(str(cls_uri))
            is_direct = True
        elif subj_tmpl:
            cls_name = template_to_classname(str(subj_tmpl))
            if not cls_name:
                continue
            is_direct = False
        else:
            continue

        source = get_source_table(r2rml, tm)

        # capture raw SQL query (if any) for this TriplesMap
        lt = r2rml.value(tm, RR.logicalTable)
        raw_sql = ""
        if lt is not None:
            q_node = r2rml.value(lt, RR.sqlQuery)
            if q_node:
                raw_sql = str(q_node).strip()
        pom_count = sum(1 for _ in r2rml.objects(tm, RR.predicateObjectMap))

        if cls_name not in model:
            model[cls_name] = {"source_table": source, "sql_query": raw_sql,
                               "columns": [], "relationships": []}
            if is_direct and source:
                direct_source.add(cls_name)
            if raw_sql:
                sql_pom_count[cls_name] = pom_count
        else:
            # direct rr:class always wins; template-derived only fills empty slots
            if source and is_direct:
                model[cls_name]["source_table"] = source
                direct_source.add(cls_name)
            elif source and not model[cls_name].get("source_table") and cls_name not in direct_source:
                model[cls_name]["source_table"] = source
            # keep the SQL query from the TriplesMap with the most mapped properties
            if raw_sql and pom_count > sql_pom_count.get(cls_name, 0):
                model[cls_name]["sql_query"] = raw_sql
                sql_pom_count[cls_name] = pom_count

        for pom in r2rml.objects(tm, RR.predicateObjectMap):
            pred_uri = r2rml.value(pom, RR.predicate)
            if pred_uri is None:
                continue
            pred_name     = prefixed(str(pred_uri))
            pred_str      = str(pred_uri)
            pmeta         = prop_meta.get(pred_str, {})

            om            = r2rml.value(pom, RR.objectMap)
            if om is None:
                continue
            col_node      = r2rml.value(om, RR.column)
            tmpl_node     = r2rml.value(om, RR.template)
            datatype_node = r2rml.value(om, RR.datatype)
            termtype_node = r2rml.value(om, RR.termType)

            is_object = (
                tmpl_node is not None
                or termtype_node == RR.IRI
                or pmeta.get("kind") == "object"
            )

            if is_object:
                target = pmeta.get("range", "")
                if tmpl_node and not target:
                    target = template_to_classname(str(tmpl_node))
                db_col = str(col_node).lower() if col_node else ""

                rel = {"property": pred_name, "target": target or "?"}
                if db_col:
                    rel["db_column"] = db_col
                elif tmpl_node:
                    import re
                    cols = re.findall(r"\{(\w+)\}", str(tmpl_node))
                    if cols:
                        rel["db_column"] = ", ".join(c.lower() for c in cols)
                if rel not in model[cls_name]["relationships"]:
                    model[cls_name]["relationships"].append(rel)

            else:
                if datatype_node:
                    xsd_type = local(str(datatype_node))
                else:
                    xsd_type = pmeta.get("range", "string")
                db_col = str(col_node).lower() if col_node else ""

                col = {"property": pred_name, "db_column": db_col, "type": xsd_type}
                if col not in model[cls_name]["columns"]:
                    model[cls_name]["columns"].append(col)

    return model


# ---------------------------------------------------------------------------
# Step 4 — merge ontology hierarchy; separate schema-only classes
# ---------------------------------------------------------------------------

def merge_and_split(
    model: dict[str, dict],
    hierarchy: dict[str, dict],
) -> tuple[dict, dict]:
    """
    - Attaches subclass_of + comment from ontology to mapped classes
    - Splits out classes that exist only in ontology (no DB table)
    Returns (mapped_classes, schema_only_classes)
    """
    mapped: dict[str, dict]      = {}
    schema_only: dict[str, dict] = {}

    # annotate existing model entries
    for cls_name, entry in model.items():
        h = hierarchy.get(cls_name, {})
        out: dict = {}
        if "source_table" in entry and entry["source_table"]:
            out["source_table"] = entry["source_table"]
        if entry.get("sql_query"):
            out["sql_query"] = entry["sql_query"]
        if "subclass_of" in h:
            out["subclass_of"] = h["subclass_of"]
        if "comment" in h:
            out["comment"] = h["comment"]
        cols = sorted(entry.get("columns", []),      key=lambda x: x["property"])
        rels = sorted(entry.get("relationships", []), key=lambda x: x["property"])
        if cols:
            out["columns"] = cols
        if rels:
            out["relationships"] = rels
        mapped[cls_name] = out

    # classes only in ontology
    for cls_name, h in hierarchy.items():
        if cls_name in model:
            continue
        out = {}
        if "subclass_of" in h:
            out["subclass_of"] = h["subclass_of"]
        if "comment" in h:
            out["comment"] = h["comment"]
        schema_only[cls_name] = out

    return dict(sorted(mapped.items())), dict(sorted(schema_only.items()))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Parsing lpp-ontology.ttl ...")
    onto = Graph()
    onto.parse(DATA_DIR / "lpp-ontology.ttl", format="turtle")
    print(f"  {len(onto)} triples")

    print("Parsing lpp-r2rml.ttl ...")
    r2rml = Graph()
    r2rml.parse(DATA_DIR / "lpp-r2rml.ttl", format="turtle")
    print(f"  {len(r2rml)} triples\n")

    hierarchy    = collect_ontology_hierarchy(onto)
    prop_meta    = collect_property_meta(onto)
    enumerations = collect_enumerations(onto)
    er_model     = build_er_model(r2rml, prop_meta)
    mapped, schema_only = merge_and_split(er_model, hierarchy)

    output = {
        "classes":      mapped,
        "schema_only":  schema_only,
        "enumerations": enumerations,
    }

    out_path = OUT_DIR / "semantic_model.yml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(output, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    print(f"Written -> {out_path}")
    print(f"  classes (with DB table): {len(mapped)}")
    print(f"  schema_only (no table):  {len(schema_only)}")
    print(f"  enumerations:            {len(enumerations)}")


if __name__ == "__main__":
    main()
