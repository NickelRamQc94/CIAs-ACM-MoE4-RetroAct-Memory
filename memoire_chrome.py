#!/usr/bin/env python3
"""Mémoire Chrome Active — catalogue local, traçable et consultable.

Choix d'implémentation : Python 3 (orchestration et CLI), SQLite (stockage et
requêtes locales), JSON/CSV (échange). Ce choix est un compromis explicite,
pas une preuve mathématique qu'un langage est universellement supérieur.

Aucun accès réseau n'est effectué. Les URL initiales sont des références
mentionnées dans le dépôt source, pas des liens dont l'accessibilité a été
vérifiée. Les imports et ajouts sont locaux. Les données restent dans le fichier
SQLite indiqué par --db (par défaut: memoire_chrome.db).

Exemples:
  python memoire_chrome.py init
  python memoire_chrome.py search "mémoire active"
  python memoire_chrome.py list --type GitHub
  python memoire_chrome.py add --type GitHub --name Mon dépôt --url https://github.com/owner/repo --description "Notes" --source "mon inventaire"
  python memoire_chrome.py import-csv ressources.csv
  python memoire_chrome.py import-json ressources.json
  python memoire_chrome.py export-json sauvegarde.json
  python memoire_chrome.py stats
  python memoire_chrome.py selftest
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY,
    resource_type TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'mentionné_non_vérifié',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resources_type ON resources(resource_type);
CREATE INDEX IF NOT EXISTS idx_resources_name ON resources(name);
"""

# Sélection représentative des URL exactes répertoriées dans les documents du dépôt.
# Statut volontairement conservateur : le catalogue les mentionne, mais le script
# ne teste pas le réseau, les permissions, ni l'existence actuelle de chaque cible.
SEED = [
    ("GitHub", "CIAs-ACM-MoE4-RetroAct-Memory", "https://github.com/NickelRamQc94/CIAs-ACM-MoE4-RetroAct-Memory", "Dépôt central indiqué comme source prioritaire."),
    ("GitHub", "README du dépôt", "https://github.com/NickelRamQc94/CIAs-ACM-MoE4-RetroAct-Memory/blob/main/README.md", "Document de présentation et inventaire."),
    ("GitHub", "Prompting the Scripts of Dynamic Active Chromatic Memory .txt", "https://github.com/NickelRamQc94/CIAs-ACM-MoE4-RetroAct-Memory/blob/main/Prompting%20the%20Scripts%20of%20Dynamic%20Active%20Chromatic%20Memory%20.txt", "Document complémentaire ciblé; son URL est conservée telle que fournie."),
    ("GitHub", "Github-HuggingFace", "https://github.com/NickelRamQc94/CIAs-ACM-MoE4-RetroAct-Memory/blob/main/Github-HuggingFace", "Texte décrivant un graphe thématique et des extensions possibles."),
    ("GitHub", "Synchro", "https://github.com/NickelRamQc94/CIAs-ACM-MoE4-RetroAct-Memory/blob/main/Synchro", "Texte décrivant un registre multi-sources et des manifestes."),
    ("GitHub", "Memoire-Chrome-Active", "https://github.com/NickelRamQc94/Memoire-Chrome-Active", "Dépôt cité dans l'inventaire."),
    ("GitHub", "ecosystem-index-nexus", "https://github.com/NickelRamQc94/ecosystem-index-nexus", "Méta-index cité dans l'inventaire."),
    ("GitHub", "qwen-remote-workspace", "https://github.com/NickelRamQc94/qwen-remote-workspace", "Workspace cité dans l'inventaire."),
    ("Hugging Face", "PinnochIA_C-iIA_Consci_Int_Art", "https://huggingface.co/NickelRamQc94/PinnochIA_C-iIA_Consci_Int_Art", "Modèle cité; accès/gating non vérifiés."),
    ("Hugging Face", "Nickeldavidgrenier", "https://huggingface.co/NickelRamQc94/Nickeldavidgrenier", "Modèle cité dans l'inventaire."),
    ("Hugging Face", "Junior", "https://huggingface.co/NickelRamQc94/Junior", "Modèle cité dans l'inventaire."),
    ("Hugging Face", "JuniorGeminiNickelGrenier", "https://huggingface.co/NickelDGre/JuniorGeminiNickelGrenier", "Modèle cité dans l'inventaire."),
    ("Hugging Face dataset", "Contenus", "https://huggingface.co/datasets/NickelRamQc94/Contenus", "Dataset cité dans l'inventaire."),
    ("Hugging Face dataset", "Junior-bucket", "https://huggingface.co/datasets/NickelRamQc94/Junior-bucket", "Bucket cité dans l'inventaire."),
    ("Hugging Face dataset", "PinnochIA bucket", "https://huggingface.co/datasets/NickelRamQc94/PinnochIA_C-iIA_Consci_Int_Art-bucket", "Bucket cité dans l'inventaire."),
    ("Google Drive", "Drive principal", "https://drive.google.com/drive/folders/1m5YRcseFu0BxDg1oG1l57XYRlmQx2Nop", "Dossier mentionné; contenu et permissions non vérifiés."),
    ("Google Drive", "Drive secondaire", "https://drive.google.com/drive/folders/1gLWMtudU5jY8Nkhp3HSjjbtmmpr_6TEn", "Dossier mentionné; contenu et permissions non vérifiés."),
    ("Google Drive", "Pont NotebookLM-Ni", "https://drive.google.com/drive/u/0/folders/1PgEn4UTUmwvAUzC5KuWPNyekIdTtnF9n", "Dossier mentionné; contenu et permissions non vérifiés."),
    ("Google Docs", "Registre d'ingénierie publié", "https://docs.google.com/document/d/e/2PACX-1vRZGyfeXkaRtib5ft-Uk-wQaO1CH6kr09U-qDwWGlj_2Cc-WfbbepPmTVHrCEDSqLLWwi0BOsS5VYb/pub", "Publication mentionnée dans l'inventaire."),
    ("Google Colab", "Notebook JGNL-SKU", "https://colab.research.google.com/drive/1wZ_8Bo8JxS3ODTiP6Zyw1hm4q6zjs4yv", "Notebook mentionné; accès et contenu non vérifiés."),
    ("Google Colab", "AlphaEvolve Results", "https://colab.research.google.com/github/google-deepmind/alphaevolve_results/blob/master/mathematical_results.ipynb", "Notebook référencé dans l'inventaire."),
    ("NotebookLM", "NotebookLM — page d'accueil", "https://notebooklm.google.com/", "Plateforme citée; aucun accès utilisateur vérifié."),
    ("NotebookLM", "Notebook mentionné dans Synchro", "https://notebooklm.google.com/notebook/0d6791ba-3e40-4de4-a526-b035f0e48fdc", "URL trouvée dans le texte Synchro; accès non vérifié."),
    ("Medium", "Profil nickeldgrenier", "https://medium.com/@nickeldgrenier", "Profil cité dans l'inventaire."),
    ("Medium", "Profil nickeldavidgrenier-roioosk", "https://medium.com/@nickeldavidgrenier-roioosk", "Profil cité dans l'inventaire."),
    ("Medium", "Article du fondateur", "https://medium.com/@nickeldavidgrenier-roioosk/nickel-david-grenier-7d431c8ebaa2", "Article cité dans l'inventaire."),
    ("Workspace", "Déploiement Qwen", "https://chat.qwen.ai/s/deploy/t_63e1dbbf-0b21-4f51-8d62-a38d2e5fd503", "Lien de session mentionné; ne pas traiter comme preuve d'accès."),
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_url(value: str) -> str:
    """Accept only HTTPS URLs without embedded credentials or session-like params."""
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("L'URL doit être absolue et utiliser HTTPS.")
    if parsed.username or parsed.password:
        raise ValueError("Les identifiants intégrés dans une URL sont refusés.")
    sensitive = {key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if {"plid", "mid"} & sensitive:
        raise ValueError("Les URL de session Gmail (paramètres plid/mid) sont refusées.")
    return value


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def upsert(conn: sqlite3.Connection, resource: dict[str, str]) -> None:
    url = validate_url(resource["url"])
    timestamp = now_utc()
    conn.execute(
        """INSERT INTO resources
           (resource_type, name, url, description, provenance,
            verification_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET
             resource_type=excluded.resource_type,
             name=excluded.name,
             description=excluded.description,
             provenance=excluded.provenance,
             verification_status=excluded.verification_status,
             updated_at=excluded.updated_at""",
        (resource["resource_type"].strip(), resource["name"].strip(), url,
         resource.get("description", "").strip(), resource.get("provenance", "").strip(),
         resource.get("verification_status", "mentionné_non_vérifié").strip(), timestamp, timestamp),
    )


def seed(conn: sqlite3.Connection) -> int:
    source = "CIAs-ACM-MoE4-RetroAct-Memory / inventaire des fichiers consultés"
    before = conn.total_changes
    for kind, name, url, description in SEED:
        upsert(conn, {"resource_type": kind, "name": name, "url": url,
                      "description": description, "provenance": source})
    conn.commit()
    return conn.total_changes - before


def search(conn: sqlite3.Connection, term: str, limit: int = 50) -> list[sqlite3.Row]:
    needle = f"%{term.strip()}%"
    return conn.execute(
        """SELECT resource_type, name, url, description, provenance, verification_status
           FROM resources
           WHERE name LIKE ? COLLATE NOCASE
              OR description LIKE ? COLLATE NOCASE
              OR resource_type LIKE ? COLLATE NOCASE
              OR url LIKE ? COLLATE NOCASE
           ORDER BY resource_type, name LIMIT ?""",
        (needle, needle, needle, needle, limit),
    ).fetchall()


def import_rows(conn: sqlite3.Connection, rows: list[dict], provenance: str) -> int:
    count = 0
    for row in rows:
        normalized = {
            "resource_type": str(row.get("resource_type", row.get("type", "Autre"))),
            "name": str(row.get("name", row.get("nom", ""))),
            "url": str(row.get("url", "")),
            "description": str(row.get("description", "")),
            "provenance": str(row.get("provenance", row.get("source", provenance))),
            "verification_status": str(row.get("verification_status", "mentionné_non_vérifié")),
        }
        if not normalized["name"].strip():
            raise ValueError("Chaque ligne importée doit avoir un name/nom non vide.")
        upsert(conn, normalized)
        count += 1
    conn.commit()
    return count


def print_rows(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("Aucun résultat.")
        return
    for row in rows:
        print(f"[{row['resource_type']}] {row['name']}\n  {row['url']}\n"
              f"  statut={row['verification_status']} | source={row['provenance']}\n"
              f"  {row['description']}\n")


def run_selftest() -> None:
    """Tests déterministes des invariants locaux; aucune requête réseau."""
    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(str(Path(tmp) / "test.db"))
        seed(conn)
        initial = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
        assert initial == len(SEED), (initial, len(SEED))
        seed(conn)
        assert conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == initial
        assert search(conn, "Notebook Colab")
        assert search(conn, "Prompting the Scripts")
        try:
            validate_url("http://example.org")
            raise AssertionError("HTTP aurait dû être refusé")
        except ValueError:
            pass
        try:
            validate_url("https://mail.google.com/mail/?plid=x&mid=y")
            raise AssertionError("Lien de session aurait dû être refusé")
        except ValueError:
            pass
        conn.close()
    print(f"SELFTEST OK — {len(SEED)} références seedées; idempotence, recherche et validation HTTPS vérifiées; aucun accès réseau.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Catalogue local consultable de Mémoire Chrome Active")
    parser.add_argument("--db", default="memoire_chrome.db", help="Chemin de la base SQLite locale")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Créer la base et charger le catalogue de départ")
    p_search = sub.add_parser("search", help="Rechercher dans nom, URL, type et description")
    p_search.add_argument("term")
    p_search.add_argument("--limit", type=int, default=50)
    p_list = sub.add_parser("list", help="Lister les ressources")
    p_list.add_argument("--type", dest="resource_type")
    p_list.add_argument("--limit", type=int, default=100)
    p_add = sub.add_parser("add", help="Ajouter ou mettre à jour une ressource")
    p_add.add_argument("--type", dest="resource_type", required=True)
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--url", required=True)
    p_add.add_argument("--description", default="")
    p_add.add_argument("--source", default="ajout_local")
    p_import = sub.add_parser("import-csv", help="Importer CSV avec colonnes name/type/url/description/source")
    p_import.add_argument("file")
    p_import_json = sub.add_parser("import-json", help="Importer un tableau JSON de ressources")
    p_import_json.add_argument("file")
    p_export = sub.add_parser("export-json", help="Exporter le catalogue en JSON")
    p_export.add_argument("file")
    sub.add_parser("stats", help="Afficher les totaux par type")
    sub.add_parser("selftest", help="Tester les invariants locaux, sans réseau")
    args = parser.parse_args()

    if args.command == "selftest":
        run_selftest()
        return 0

    conn = connect(args.db)
    try:
        if args.command == "init":
            added = seed(conn)
            total = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
            print(f"Base prête: {args.db} — {total} ressources; {added} insertion(s)/mise(s) à jour.")
        elif args.command == "search":
            print_rows(search(conn, args.term, args.limit))
        elif args.command == "list":
            if args.resource_type:
                rows = conn.execute(
                    """SELECT resource_type, name, url, description, provenance, verification_status
                       FROM resources WHERE resource_type LIKE ? COLLATE NOCASE
                       ORDER BY name LIMIT ?""", (f"%{args.resource_type}%", args.limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT resource_type, name, url, description, provenance, verification_status
                       FROM resources ORDER BY resource_type, name LIMIT ?""", (args.limit,)
                ).fetchall()
            print_rows(rows)
        elif args.command == "add":
            upsert(conn, {"resource_type": args.resource_type, "name": args.name,
                          "url": args.url, "description": args.description,
                          "provenance": args.source})
            conn.commit()
            print("Ressource enregistrée localement.")
        elif args.command == "import-csv":
            with open(args.file, newline="", encoding="utf-8-sig") as handle:
                count = import_rows(conn, list(csv.DictReader(handle)), f"CSV:{args.file}")
            print(f"Import terminé: {count} ligne(s) traitée(s).")
        elif args.command == "import-json":
            with open(args.file, encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, list):
                raise ValueError("Le JSON doit être un tableau d'objets ressource.")
            count = import_rows(conn, payload, f"JSON:{args.file}")
            print(f"Import terminé: {count} ressource(s) traitée(s).")
        elif args.command == "export-json":
            rows = conn.execute(
                """SELECT resource_type, name, url, description, provenance,
                          verification_status, created_at, updated_at
                   FROM resources ORDER BY resource_type, name"""
            ).fetchall()
            Path(args.file).write_text(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Export terminé: {len(rows)} ressource(s) → {args.file}")
        elif args.command == "stats":
            rows = conn.execute("SELECT resource_type, COUNT(*) AS n FROM resources GROUP BY resource_type ORDER BY resource_type").fetchall()
            for row in rows:
                print(f"{row['resource_type']}: {row['n']}")
            print(f"Total: {sum(row['n'] for row in rows)}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
        print(f"Erreur: {exc}", file=sys.stderr)
        raise SystemExit(2)
