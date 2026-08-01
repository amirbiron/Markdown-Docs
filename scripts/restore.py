#!/usr/bin/env python3
"""שחזור מארכיון גיבוי.

    python3 scripts/restore.py backup-20260801-030000.zip --url https://docs.example.com
    python3 scripts/restore.py backup.zip.enc --url ... --decrypt     # מטלגרם

הסקריפט עומד בפני עצמו: הוא לא מייבא מהאפליקציה ולא נוגע בבסיס הנתונים
ישירות — הוא מדבר עם אותו API שהאתר מדבר איתו. זו החלטה, ויש לה שני
צדדים.

בעד: הכתיבה עוברת דרך אותה ולידציה, אותה יצירת slug ואותו עדכון של
עמודת החיפוש. שחזור שנכתב ישירות ל-DB היה מייצר שורות שנראות תקינות
ומתנהגות שונה.

נגד: השחזור דורש שהשרת יעלה. אם השרת מת — פורסים אותו מחדש (המיגרציות
רצות בעלייה) ומשחזרים אל הפריסה החדשה. הארכיון הוא ברמת האפליקציה,
קבצי Markdown ו-JSON, ולא dump של טבלאות; אין בו מה שדורש גישה נמוכה
יותר.

הסיסמה לפענוח נקראת ממשתנה הסביבה BACKUP_PASSPHRASE או מ-stdin, ולעולם
לא כארגומנט — ארגומנטים נכנסים להיסטוריית ה-shell ונראים ב-ps.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import zipfile
from pathlib import Path
from urllib.parse import quote

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.exit("httpx חסר. הריצו: pip install httpx")


MODES = ("skip", "upsert", "replace")


class Client:
    """עטיפה דקה סביב ה-API, עם cookie של session.

    אסינכרוני ולא סינכרוני, כדי שאותה פונקציית restore תרוץ גם מכאן וגם
    מהבדיקות מול ה-ASGI client — בלי גרסה שנייה של הלוגיקה שתתפצל ממנה
    בשקט.
    """

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.http = httpx.AsyncClient(timeout=60, follow_redirects=False)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def login(self, email: str, password: str) -> None:
        response = await self.http.post(
            f"{self.base}/api/auth/login",
            json={"email": email, "password": password},
            # ה-OriginGuard דורש Origin בכל בקשה משנת מצב, גם מסקריפט.
            headers={"Origin": self.base},
        )
        if response.status_code != 200:
            sys.exit(f"הכניסה נכשלה ({response.status_code}). בדקו כתובת, מייל וסיסמה.")

    async def get(self, path: str):
        return await self.http.get(f"{self.base}{path}")

    async def post(self, path: str, payload: dict):
        return await self.http.post(f"{self.base}{path}", json=payload, headers={"Origin": self.base})

    async def put(self, path: str, payload: dict):
        return await self.http.put(f"{self.base}{path}", json=payload, headers={"Origin": self.base})

    async def patch(self, path: str, payload: dict):
        return await self.http.patch(f"{self.base}{path}", json=payload, headers={"Origin": self.base})

    async def delete(self, path: str):
        return await self.http.delete(f"{self.base}{path}", headers={"Origin": self.base})


def read_archive(path: Path, decrypt: bool) -> dict:
    """קורא את הארכיון ומחזיר מבנה של פרויקטים ומסמכים."""
    raw = path.read_bytes()

    if decrypt:
        passphrase = os.environ.get("BACKUP_PASSPHRASE") or getpass.getpass("סיסמת הפענוח: ")
        # הייבוא כאן ולא למעלה: מי שמשחזר קובץ לא מוצפן לא צריך
        # ש-cryptography יהיה מותקן בכלל.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from app.crypto import CryptoError, decrypt as do_decrypt

        try:
            raw = do_decrypt(raw, passphrase)
        except CryptoError:
            sys.exit("הפענוח נכשל — סיסמה שגויה, או שהקובץ אינו בפורמט הזה.")

    import io

    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        sys.exit("הקובץ אינו ZIP תקין. אם הוא מוצפן, הוסיפו --decrypt.")

    if archive.testzip() is not None:
        sys.exit("הארכיון פגום.")

    names = archive.namelist()
    manifest = {}
    if "manifest.json" in names:
        manifest = json.loads(archive.read("manifest.json"))

    projects: dict[str, dict] = {}
    for name in names:
        if "/" not in name:
            continue
        folder, _, leaf = name.partition("/")
        entry = projects.setdefault(folder, {"meta": None, "documents": []})
        if leaf == "links.json":
            entry["meta"] = json.loads(archive.read(name))
        elif leaf.endswith(".md"):
            entry["documents"].append(
                {"slug": leaf[:-3], "content": archive.read(name).decode("utf-8")}
            )

    for entry in projects.values():
        entry["documents"].sort(key=lambda d: d["slug"])

    return {"manifest": manifest, "projects": projects}


def title_of(document: dict) -> str:
    """הכותרת מהשורה הראשונה שמתחילה ב-#, ובהיעדרה מה-slug."""
    for line in document["content"].splitlines():
        if line.startswith("# "):
            return line[2:].strip() or document["slug"]
    return document["slug"]


def describe(data: dict, mode: str, base: str) -> None:
    manifest = data["manifest"]
    print()
    print(f"  גיבוי מ:   {manifest.get('created_at', 'לא ידוע')}")
    print(f"  יעד:       {base}")
    print(f"  מצב:       {mode}")
    print(f"  פרויקטים:  {len(data['projects'])}")
    print()
    for folder, entry in sorted(data["projects"].items()):
        meta = entry["meta"] or {}
        name = meta.get("name", folder)
        links = len(meta.get("links", []))
        print(f"    {name}  ({folder})  — {len(entry['documents'])} מסמכים, {links} קישורים")
        for document in entry["documents"]:
            print(f"        {document['slug']}.md")
    print()


async def restore(client, data: dict, mode: str) -> tuple[int, int, list[str]]:
    """מחזיר (פרויקטים שנוצרו, מסמכים שנכתבו, אזהרות)."""
    made_projects = 0
    made_documents = 0
    warnings: list[str] = []

    for folder, entry in sorted(data["projects"].items()):
        meta = entry["meta"] or {}
        slug = meta.get("slug") or folder
        name = meta.get("name") or folder

        existing = await client.get(f"/api/projects/{quote(slug, safe='')}")
        project_exists = existing.status_code == 200

        if project_exists and mode == "skip":
            warnings.append(f"הפרויקט {slug} כבר קיים — דילוג (skip)")
            continue

        if project_exists and mode == "replace":
            # replace מוחק את הפרויקט על מסמכיו, גרסאותיו וקישוריו
            # (CASCADE), ובונה אותו מחדש בדיוק כמו בגיבוי.
            await client.delete(f"/api/projects/{quote(slug, safe='')}")
            project_exists = False

        if not project_exists:
            # ה-slug נשלח במפורש ולא נגזר מהשם. זה מה ששומר על הכתובות:
            # פרויקט ציבורי ששוחזר תחת slug אחר הוא פרויקט שכל קישור
            # שנשלח אליו מפסיק לעבוד, והשחזור "הצליח" בשקט.
            created = await client.post(
                "/api/projects",
                {
                    "name": name,
                    "slug": slug,
                    "description": meta.get("description"),
                    "visibility": meta.get("visibility", "private"),
                },
            )
            if created.status_code != 201:
                warnings.append(f"יצירת הפרויקט {slug} נכשלה ({created.status_code})")
                continue
            actual_slug = created.json()["slug"]
            made_projects += 1
            if actual_slug != slug:
                warnings.append(f"הפרויקט {slug} נוצר תחת {actual_slug}")
        else:
            actual_slug = slug
            # פרויקט קיים (upsert): מיישרים את מה שאינו נכתב ביצירה.
            await client.patch(
                f"/api/projects/{quote(actual_slug, safe='')}",
                {
                    "description": meta.get("description"),
                    "visibility": meta.get("visibility", "private"),
                },
            )

        for document in entry["documents"]:
            path = f"/api/projects/{quote(actual_slug, safe='')}/docs/{quote(document['slug'], safe='')}"
            present = (await client.get(path)).status_code == 200

            if present and mode == "skip":
                warnings.append(f"המסמך {actual_slug}/{document['slug']} קיים — דילוג")
                continue

            if present:
                # upsert ו-replace: דורסים את התוכן הקיים. client_seq גבוה
                # כדי שהכתיבה לא תידחה כמאוחרת.
                response = await client.put(
                    path,
                    {
                        "content": document["content"],
                        "client_seq": 10**9,
                        "editor_id": "restore",
                    },
                )
            else:
                # ה-slug נשלח במפורש, מאותה סיבה שהוא נשלח בפרויקט:
                # בלעדיו הוא נגזר מהכותרת, והמסמך נוחת בכתובת אחרת מזו
                # שהייתה לו. השחזור "מצליח" וכל קישור ישן שבור.
                response = await client.post(
                    f"/api/projects/{quote(actual_slug, safe='')}/docs",
                    {
                        "title": title_of(document),
                        "slug": document["slug"],
                        "content": document["content"],
                    },
                )

            if response.status_code in (200, 201):
                made_documents += 1
            else:
                warnings.append(
                    f"המסמך {actual_slug}/{document['slug']} נכשל ({response.status_code})"
                )

        for link in meta.get("links", []):
            await client.post(
                f"/api/projects/{quote(actual_slug, safe='')}/links",
                {"title": link["title"], "url": link["url"]},
            )

    return made_projects, made_documents, warnings


async def main() -> int:
    parser = argparse.ArgumentParser(description="שחזור מארכיון גיבוי")
    parser.add_argument("archive", type=Path, help="קובץ הגיבוי (.zip או .zip.enc)")
    parser.add_argument("--url", required=True, help="כתובת הבסיס של האתר")
    parser.add_argument("--email", default=os.environ.get("ADMIN_EMAIL"))
    parser.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD"))
    parser.add_argument("--decrypt", action="store_true", help="הקובץ מוצפן")
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="skip",
        help=(
            "skip — כותב רק מה שלא קיים (ברירת מחדל, לא יכול להזיק); "
            "upsert — דורס מסמכים קיימים ומשאיר את מה שנוצר אחרי הגיבוי; "
            "replace — מוחק כל פרויקט שבגיבוי ובונה אותו מחדש"
        ),
    )
    parser.add_argument("--yes", action="store_true", help="לדלג על האישור (לאוטומציה בלבד)")
    parser.add_argument("--dry-run", action="store_true", help="להציג בלבד, בלי לכתוב")
    args = parser.parse_args()

    if not args.archive.exists():
        print(f"הקובץ לא נמצא: {args.archive}")
        return 1

    data = read_archive(args.archive, args.decrypt)
    describe(data, args.mode, args.url)

    if args.dry_run:
        print("  --dry-run: לא נכתב כלום.")
        return 0

    if args.mode == "replace":
        print("  ⚠  replace מוחק כל פרויקט שמופיע בגיבוי, על מסמכיו והגרסאות שלו.")
        print("     כל מה שנוצר אחרי הגיבוי בפרויקטים האלה — יימחק.")
        print()

    if not args.yes:
        # ברירת המחדל היא לא. Enter בטעות לא משחזר כלום.
        answer = input("להמשיך? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("בוטל.")
            return 1

    if not args.email or not args.password:
        print("חסרים --email/--password או ADMIN_EMAIL/ADMIN_PASSWORD")
        return 1

    client = Client(args.url)
    try:
        await client.login(args.email, args.password)
        projects, documents, warnings = await restore(client, data, args.mode)
    finally:
        await client.aclose()

    print()
    print(f"  נוצרו {projects} פרויקטים, נכתבו {documents} מסמכים.")
    if warnings:
        print()
        print("  אזהרות:")
        for warning in warnings:
            print(f"    · {warning}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
