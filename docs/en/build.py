"""Build the PDF documents from the Markdown in this folder.

03_DATABASE_SCHEMA.md is generated from schema_snapshot.json (see introspect.sql), so it always matches the
real database. Run from anywhere:  python3 docs/en/build.py
Needs: pandoc, Google Chrome, pdftotext (poppler). Mermaid is loaded from a CDN, so the build needs internet.
"""
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "pdf"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SNAP = json.loads((HERE / "schema_snapshot.json").read_text())

PURPOSE = {"catalog": "Reference lists, datasets, layers, tilesets and import history. Editable by trained staff through the admin UI.",
           "audit": "Automatic history of every change to catalog data."}
FDESC = {
    "current_app_user": "Returns the signed-in user name for this transaction; raises an error when empty.",
    "stamp": "Trigger: fills created_by/updated_by and timestamps from the signed-in user.",
    "log_change": "Trigger: writes one row to audit.change_log with old and new values.",
    "dataset_guard": "Trigger: blocks changing dataset.code once 3D objects carry the lineage tag.",
    "dataset_archive_cascade": "Trigger: archiving a dataset archives its layers.",
    "layer_guard": "Trigger: layer identity freeze, allowed status moves, validation and publish preconditions.",
    "layer_archive_cascade": "Trigger: archiving a layer retires its tilesets.",
    "tileset_guard": "Trigger: allowed tileset status moves and published_at.",
    "check_published_has_tileset": "Deferred constraint trigger: a published layer has exactly one active Ready tileset at COMMIT.",
    "import_job_guard": "Trigger: sets finished_at when a job leaves the running state.",
    "layer_lineage": "Builds the lineage tag dataset.theme.lodN for a layer.",
    "refresh_derived": "Run after an import: refreshes object counts and recomputes bounding boxes.",
}
RDESC = {"twin_ro": "Read catalog, audit and the 3D city model (API, viewers)",
         "twin_editor": "Insert and update catalog rows (admin UI)",
         "twin_admin": "Editor rights plus DELETE and refresh_derived()",
         "twin_importer": "Editor rights plus writing citydb (citydb-tool) and refresh_derived()",
         "twin_app": "Login used by the Django admin (member of twin_admin; Django groups decide who edits or deletes)",
         "twin_api": "Login used by the read-only API (member of twin_ro, read-only transactions)"}


def cell(s):
    return (s or "").replace("|", "\\|").replace("\n", " ")


ON_RE = re.compile(r" ON [a-z_]+\.[a-z_0-9]+")
TRIG_RE = re.compile(r"^CREATE (CONSTRAINT )?TRIGGER \w+ ")
FN_RE = re.compile(r"EXECUTE FUNCTION (.+)$")
PAGES_RE = re.compile(r"Pages:\s+(\d+)")


def strip_on(i):
    return cell(ON_RE.sub("", i))


def trig_line(tr):
    d = tr["def"]
    return f"- `{tr['name']}`: {cell(TRIG_RE.sub('', d).split(' EXECUTE ')[0])} runs `{FN_RE.search(d).group(1)}`"


def build_schema_md():
    tables = SNAP["tables"]
    md = ["# Introduction", "",
          "This document is the reference for every table, view, column, constraint, index, trigger, function and role "
          "added by the catalog migrations. It is generated from the live database catalogue "
          f"(PostgreSQL {SNAP['versions']['postgres'].split()[1]}, PostGIS {SNAP['versions']['postgis']}, "
          f"3DCityDB {SNAP['versions']['citydb'].strip('()').split(',')[0]}). "
          "The 3DCityDB schema `citydb` is documented by its project and is not repeated here.", "",
          "# Conventions", "", "| Topic | Convention |", "|---|---|",
          "| Migrations | Plain SQL files in `db/migrations`, applied by dbmate in order. Applied files are never edited. |",
          "| Keys | `uuid` primary keys (`gen_random_uuid()`); reference tables use a short text `code`. |",
          "| Names and time | snake_case names, `timestamptz` for every time. |",
          "| Coordinates | `bbox` is `geometry(Polygon, 4326)`, computed from the data, never typed. The source CRS is only recorded in `dataset.crs_epsg`; the database SRID is fixed at creation. |",
          "| LOD | 0 to 4 (CityGML 2.0). LOD4 of CityGML 2.0 is stored by 3DCityDB as deprecated properties; layers still use `lod = 4`. |",
          "| Lineage tag | `dataset.theme.lodN` in `citydb.feature.lineage` links 3D objects to a layer. |",
          "| Code lists | Reference tables, not ENUM types. Add a row to extend; no migration needed. |",
          "| Audit columns | `created_at`, `created_by`, `updated_at`, `updated_by` on `dataset`, `import_job`, `layer`, `tileset`. Filled by trigger from the signed-in user. |",
          "| Mandatory user | Set `app.user` per transaction: `SELECT set_config('app.user', 'name', true);`. Without it every write is rejected. |",
          "| Deletion | Archive through status. DELETE is allowed to `twin_admin` only. Old rows stay in `audit.change_log`. |",
          "| Comments | Every table and column has a plain-language `COMMENT`, shown in the admin UI. |", "",
          "# Schemas", "", "| Schema | Purpose | Tables | Views |", "|---|---|---:|---:|"]
    for s, p in PURPOSE.items():
        ts = [t for t in tables if t["schema"] == s]
        tb = [t for t in ts if t["kind"] == "table"]
        md.append(f"| `{s}` | {p} | {len(tb)} | {len(ts) - len(tb)} |")
    md += ["", "Other schemas: `citydb` (3DCityDB v5, untouched except the index `feature_lineage_inx`) and `app` (Django tables).", ""]
    for s, p in PURPOSE.items():
        md += [f"# Schema {s}", "", p, ""]
        for t in [t for t in tables if t["schema"] == s]:
            key = f"{s}.{t['name']}"
            md += [f"## {key}", "", f"*{t['kind']}.* {t['comment'] or ''}", "",
                   "| Column | Type | Null | Default | Meaning |", "|---|---|---|---|---|"]
            for c in t["columns"]:
                md.append(f"| `{c['name']}` | `{cell(c['type'])}` | {'no' if c['not_null'] else 'yes'} | "
                          f"{('`' + cell(c['default']) + '`') if c['default'] else '-'} | {cell(c['comment']) or '-'} |")
            cons = [c for c in t["constraints"] or [] if c["type"] in ("p", "u", "c", "f", "x")]
            if cons:
                md += ["", "Constraints:", ""] + [f"- `{cell(c['def'])}`" for c in cons]
            idx = [i for i in t["indexes"] or [] if "_pkey" not in i]
            if idx:
                md += ["", "Indexes:", ""] + [f"- `{strip_on(i)}`" for i in idx]
            if t["triggers"]:
                md += ["", "Triggers:", ""] + [trig_line(tr) for tr in t["triggers"]]
            md.append("")
    md += ["# Functions", "", "| Function | Returns | Security | Purpose |", "|---|---|---|---|"]
    for f in SNAP["functions"]:
        md.append(f"| `{f['schema']}.{f['name']}({cell(f['args'])})` | `{cell(f['returns'])}` | "
                  f"{'definer' if f['security_definer'] else 'invoker'} | {FDESC.get(f['name'], cell(f['comment']) or '-')} |")
    md += ["", "# Roles and privileges", "",
           "Roles are NOLOGIN group roles. Create one login user per person or service and grant one group, for example "
           "`CREATE ROLE api LOGIN PASSWORD '...' IN ROLE twin_ro;`.", "",
           "| Role | Login | Member of | Purpose |", "|---|---|---|---|"]
    for r in SNAP["roles"]:
        md.append(f"| `{r['name']}` | {'yes' if r['login'] else 'no'} | {', '.join(r['member_of'] or []) or '-'} | {RDESC.get(r['name'], '-')} |")
    return "\n".join(md) + "\n"


DOCS = [
    ("01_DDD.md", "Domain-Driven Design", "Domain-Driven Design Document",
     "Bounded contexts, shared language, aggregates and business rules of the digital twin catalog."),
    ("02_ERD.md", "Entity Relationship Diagram", "Entity Relationship Diagram",
     "Entities, relationships and the link between the catalog and the 3DCityDB city model."),
    ("03_DATABASE_SCHEMA.md", "Database Schema", "Database Schema Reference",
     "Every table, view, column, constraint, index, trigger, function and role of the catalog database."),
    ("04_SAD.md", "System Architecture", "System Architecture Document",
     "Structure, deployment, data flow, security, risks and architecture decisions."),
    ("05_API.md", "API and Connector", "API and Frontend Connector",
     "Read-only REST API, tile to database link, Cesium viewer and Postman collection."),
    ("06_DEPLOYMENT.md", "Deployment", "Deployment Guide",
     "Configuration, local Docker, Railway and how to start a new project from the template."),
    ("07_RUNBOOK.md", "Runbook", "Operations Runbook",
     "Import, re-import, publish, backup, restore and user management."),
    ("08_RAILWAY_GUIDE.md", "Railway Guide", "Railway Deployment Guide",
     "Step-by-step deployment of the database, API and backup job on Railway, with checks and troubleshooting."),
]


def check_text(name, text):
    assert "\u2014" not in text, f"{name}: em dash"


def main():
    (HERE / "03_DATABASE_SCHEMA.md").write_text(build_schema_md())
    OUT.mkdir(exist_ok=True)
    for fname, short, title, subtitle in DOCS:
        src = HERE / fname
        check_text(fname, src.read_text())
        html = HERE / (src.stem + ".html")
        subprocess.run(["pandoc", str(src), "-f", "markdown-smart", "-t", "html5", "--standalone", "--toc",
                        "--toc-depth=2", "--wrap=none", f"--template={HERE / 'template.html'}", "-M", f"title={title}",
                        "-M", f"short={short}", "-M", "kicker=Digital Twin Catalog",
                        "-M", f"subtitle={subtitle}", "-o", str(html)], check=True)
        pdf = OUT / f"{src.stem}.pdf"
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        "--virtual-time-budget=30000", "--run-all-compositor-stages-before-draw",
                        f"--print-to-pdf={pdf}", html.as_uri()], check=True, capture_output=True)
        text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True).stdout
        assert "Syntax error" not in text, f"{pdf.name}: mermaid syntax error"
        pages = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
        print(f"{pdf.name}: {PAGES_RE.search(pages).group(1)} pages")
        html.unlink()


if __name__ == "__main__":
    main()
