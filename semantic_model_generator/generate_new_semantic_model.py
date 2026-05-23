"""
generate_new_semantic_model.py

Uses rdflib to extract all ontology elements (classes, object properties,
datatype properties, SKOS vocabularies) from lpp-ontology.ttl, and all
R2RML business subdomains (TriplesMaps) with their source tables/SQL,
columns, and connections from lpp-r2rml.ttl.

Output: semantic_model_generator/output/new.yml
"""

from pathlib import Path
import re
import yaml
from rdflib import Graph, RDF, RDFS, OWL, Namespace, URIRef
from rdflib.namespace import SKOS

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent / "backend" / "data"
OUT_DIR  = BASE_DIR / "output"
OUT_DIR.mkdir(exist_ok=True)

ONTOLOGY_TTL = DATA_DIR / "lpp-ontology.ttl"
R2RML_TTL    = DATA_DIR / "lpp-r2rml.ttl"
OUTPUT_YAML  = OUT_DIR  / "new.yml"

RR    = Namespace("http://www.w3.org/ns/r2rml#")
LPP   = Namespace("https://lpp.example/ontology#")
BRAIN = Namespace("https://lpp.example/ontology/brain#")
PROV  = Namespace("http://www.w3.org/ns/prov#")

XSD_TYPE_MAP = {
    "decimal":  "decimal",
    "integer":  "integer",
    "date":     "date",
    "dateTime": "timestamp",
    "boolean":  "boolean",
    "string":   "varchar",
    "anyURI":   "varchar",
}


# ── URI helpers ────────────────────────────────────────────────────────────

def local(uri: str) -> str:
    for sep in ("#", "/"):
        if sep in uri:
            return uri.rsplit(sep, 1)[-1]
    return uri


def prefixed(uri: str) -> str:
    prefixes = {
        "https://lpp.example/ontology#":                "lpp:",
        "https://lpp.example/ontology/brain#":          "brain:",
        "http://www.w3.org/ns/prov#":                   "prov:",
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#":  "rdf:",
        "http://www.w3.org/2000/01/rdf-schema#":        "rdfs:",
        "http://www.w3.org/2002/07/owl#":               "owl:",
        "http://www.w3.org/2001/XMLSchema#":            "xsd:",
    }
    for ns, pfx in prefixes.items():
        if uri.startswith(ns):
            return pfx + uri[len(ns):]
    return local(uri)


def normalise_table(raw: str) -> str:
    return raw.strip().lower()


def xsd_to_sqltype(datatype_uri: str | None) -> str:
    if not datatype_uri:
        return "varchar"
    return XSD_TYPE_MAP.get(local(datatype_uri), "varchar")


def extract_tables_from_sql(sql: str) -> list[str]:
    matches = re.findall(r'\b(?:FROM|JOIN)\s+([\w.]+)', sql, re.IGNORECASE)
    return sorted({normalise_table(t) for t in matches})


def map_name_from_uri(tm_uri: str) -> str:
    name = local(tm_uri)
    if name.endswith("Map"):
        name = name[:-3]
    return name


# ── Ontology extraction ────────────────────────────────────────────────────

def extract_ontology(onto: Graph) -> dict:

    # --- Classes ---
    class_uris: set[URIRef] = set()
    for cls in onto.subjects(RDF.type, OWL.Class):
        if isinstance(cls, URIRef):
            class_uris.add(cls)
    for s, _, o in onto.triples((None, RDFS.subClassOf, None)):
        for u in (s, o):
            if isinstance(u, URIRef):
                class_uris.add(u)

    classes: dict[str, dict] = {}
    for uri in class_uris:
        name = prefixed(str(uri))
        entry: dict = {}
        parents = [prefixed(str(p)) for p in onto.objects(uri, RDFS.subClassOf)
                   if isinstance(p, URIRef)]
        if parents:
            entry["subclass_of"] = parents[0] if len(parents) == 1 else parents
        label = next((str(l) for l in onto.objects(uri, RDFS.label)), None)
        if label:
            entry["label"] = label
        desc = next((str(c) for c in onto.objects(uri, RDFS.comment)), None)
        if desc:
            entry["description"] = desc
        classes[name] = entry

    # --- Object Properties ---
    obj_props: dict[str, dict] = {}
    for prop in onto.subjects(RDF.type, OWL.ObjectProperty):
        if not isinstance(prop, URIRef):
            continue
        name = prefixed(str(prop))
        entry: dict = {}
        domain = [prefixed(str(d)) for d in onto.objects(prop, RDFS.domain) if isinstance(d, URIRef)]
        range_ = [prefixed(str(r)) for r in onto.objects(prop, RDFS.range) if isinstance(r, URIRef)]
        sub_of = [prefixed(str(p)) for p in onto.objects(prop, RDFS.subPropertyOf) if isinstance(p, URIRef)]
        label  = next((str(l) for l in onto.objects(prop, RDFS.label)), None)
        desc   = next((str(c) for c in onto.objects(prop, RDFS.comment)), None)
        if domain:
            entry["domain"] = domain[0] if len(domain) == 1 else domain
        if range_:
            entry["range"] = range_[0] if len(range_) == 1 else range_
        if sub_of:
            entry["sub_property_of"] = sub_of[0] if len(sub_of) == 1 else sub_of
        if label:
            entry["label"] = label
        if desc:
            entry["description"] = desc
        obj_props[name] = entry

    # --- Datatype Properties ---
    dt_props: dict[str, dict] = {}
    for prop in onto.subjects(RDF.type, OWL.DatatypeProperty):
        if not isinstance(prop, URIRef):
            continue
        name = prefixed(str(prop))
        entry: dict = {}
        domain = [prefixed(str(d)) for d in onto.objects(prop, RDFS.domain) if isinstance(d, URIRef)]
        range_ = [prefixed(str(r)) for r in onto.objects(prop, RDFS.range) if isinstance(r, URIRef)]
        label  = next((str(l) for l in onto.objects(prop, RDFS.label)), None)
        desc   = next((str(c) for c in onto.objects(prop, RDFS.comment)), None)
        if domain:
            entry["domain"] = domain[0] if len(domain) == 1 else domain
        if range_:
            entry["xsd_range"] = range_[0] if len(range_) == 1 else range_
        if label:
            entry["label"] = label
        if desc:
            entry["description"] = desc
        dt_props[name] = entry

    # --- SKOS Concept vocabularies ---
    vocabularies: dict[str, list] = {}
    for concept_cls in onto.subjects(RDFS.subClassOf, SKOS.Concept):
        if not isinstance(concept_cls, URIRef):
            continue
        name   = prefixed(str(concept_cls))
        values = []
        for ind in onto.subjects(RDF.type, concept_cls):
            if not isinstance(ind, URIRef):
                continue
            lbl = onto.value(ind, SKOS.prefLabel)
            values.append(str(lbl) if lbl else local(str(ind)))
        if values:
            vocabularies[name] = sorted(values)

    return {
        "classes":              dict(sorted(classes.items())),
        "object_properties":    dict(sorted(obj_props.items())),
        "datatype_properties":  dict(sorted(dt_props.items())),
        "concept_vocabularies": dict(sorted(vocabularies.items())),
    }


# ── R2RML extraction ───────────────────────────────────────────────────────

def extract_r2rml(r2rml: Graph) -> dict:
    """
    Walk every TriplesMap; produce one entry per map keyed by business name
    (TriplesMap URI local-name minus trailing 'Map').
    """
    subdomains: dict[str, dict] = {}

    all_tms: set = set()
    for tm in r2rml.subjects(RR.subjectMap, None):
        all_tms.add(tm)
    for tm in r2rml.subjects(RR.logicalTable, None):
        all_tms.add(tm)

    for tm in all_tms:
        sm = r2rml.value(tm, RR.subjectMap)
        if sm is None:
            continue
        lt = r2rml.value(tm, RR.logicalTable)
        if lt is None:
            continue

        name = map_name_from_uri(str(tm))

        # ── source ────────────────────────────────────────────────────────
        table_node = r2rml.value(lt, RR.tableName)
        sql_node   = r2rml.value(lt, RR.sqlQuery)

        if table_node:
            table_name = normalise_table(str(table_node))
            source = {"type": "table", "table": table_name}
        elif sql_node:
            raw_sql = str(sql_node).strip()
            source  = {
                "type":   "sql_query",
                "tables": extract_tables_from_sql(raw_sql),
                "sql":    raw_sql,
            }
        else:
            continue

        # ── ontology class ─────────────────────────────────────────────────
        cls_uri = r2rml.value(sm, RR["class"])
        onto_cls = prefixed(str(cls_uri)) if cls_uri else None

        # ── named graph ────────────────────────────────────────────────────
        graph_node = r2rml.value(sm, RR.graph)
        named_graph = str(graph_node) if graph_node else None

        # ── predicateObjectMaps → columns + connections ───────────────────
        columns:     dict[str, dict] = {}
        connections: list[dict]      = []

        for pom in r2rml.objects(tm, RR.predicateObjectMap):
            pred_uri = r2rml.value(pom, RR.predicate)
            if pred_uri is None:
                continue
            pred = prefixed(str(pred_uri))

            om           = r2rml.value(pom, RR.objectMap)
            if om is None:
                continue
            col_node     = r2rml.value(om, RR.column)
            tmpl_node    = r2rml.value(om, RR.template)
            dtype_node   = r2rml.value(om, RR.datatype)
            termtype_node = r2rml.value(om, RR.termType)

            if tmpl_node is not None or termtype_node == RR.IRI:
                # Object property / FK → connection
                template = str(tmpl_node) if tmpl_node else ""
                via_cols = [c.lower() for c in re.findall(r"\{(\w+)\}", template)]
                path_segs = [p for p in template.split("/") if p and not p.startswith("{")]
                target_type = path_segs[-1] if path_segs else ""
                connections.append({
                    "predicate":           pred,
                    "via_columns":         via_cols,
                    "target_entity_type":  target_type,
                    "iri_template":        template,
                })
            elif col_node is not None:
                # Scalar / datatype property → column
                db_col   = str(col_node).lower()
                sql_type = xsd_to_sqltype(str(dtype_node) if dtype_node else None)
                columns[db_col] = {"predicate": pred, "type": sql_type}

        entry: dict = {"source": source}
        if onto_cls:
            entry["ontology_class"] = onto_cls
        if named_graph:
            entry["named_graph"] = named_graph
        if columns:
            entry["columns"] = dict(sorted(columns.items()))
        if connections:
            entry["connections"] = connections

        subdomains[name] = entry

    return dict(sorted(subdomains.items()))


# ── YAML helpers ───────────────────────────────────────────────────────────

class _LiteralStr(str):
    pass


def _literal_rep(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(_LiteralStr, _literal_rep)


def _prepare(obj):
    if isinstance(obj, str) and "\n" in obj:
        return _LiteralStr(obj)
    if isinstance(obj, dict):
        return {k: _prepare(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_prepare(i) for i in obj]
    return obj


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    print("Parsing lpp-ontology.ttl ...")
    onto = Graph()
    onto.parse(ONTOLOGY_TTL, format="turtle")
    print(f"  {len(onto)} triples loaded")

    print("Parsing lpp-r2rml.ttl ...")
    r2rml = Graph()
    r2rml.parse(R2RML_TTL, format="turtle")
    print(f"  {len(r2rml)} triples loaded")

    print("Extracting ontology elements ...")
    ontology   = extract_ontology(onto)

    print("Extracting R2RML business subdomains ...")
    subdomains = extract_r2rml(r2rml)

    model = {
        "metadata": {
            "version":     "1.0",
            "source_files": {
                "ontology":  "lpp-ontology.ttl",
                "r2rml":     "lpp-r2rml.ttl",
            },
            "description": (
                "Semantic model extracted directly from LPP ontology (lpp-ontology.ttl) "
                "and R2RML mappings (lpp-r2rml.ttl) using rdflib. "
                "Ontology section lists all OWL classes, object properties, and datatype properties. "
                "Business subdomains section maps each R2RML TriplesMap to its source table/SQL, "
                "columns (predicate + type), and connections (FK links to other entities)."
            ),
        },
        "ontology":            ontology,
        "business_subdomains": subdomains,
    }

    print(f"Writing {OUTPUT_YAML} ...")
    with open(OUTPUT_YAML, "w", encoding="utf-8") as fh:
        yaml.dump(
            _prepare(model),
            fh,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )

    n_cls     = len(ontology["classes"])
    n_obj     = len(ontology["object_properties"])
    n_dt      = len(ontology["datatype_properties"])
    n_vocab   = len(ontology["concept_vocabularies"])
    n_sub     = len(subdomains)
    print(
        f"\nDone.\n"
        f"  {n_cls} OWL classes\n"
        f"  {n_obj} object properties\n"
        f"  {n_dt} datatype properties\n"
        f"  {n_vocab} SKOS concept vocabularies\n"
        f"  {n_sub} business subdomains (TriplesMaps)\n"
        f"  Output: {OUTPUT_YAML}"
    )


if __name__ == "__main__":
    main()
