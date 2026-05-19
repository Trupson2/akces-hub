"""
PHASE 4 — testy regresyjne pakowania wydania.

Tło: `tools/build_release.py` pakuje przez KOPIOWANIE drzewa katalogów
(nie `git archive`), opierając się na ręcznej liście EXCLUDE. PHASE 4
audyt wykrył realne luki: `.license_secret`, `.env.key`, `.env.extra`,
`nohup.out`/`*.out` NIE były wykluczone → paczka mogła zawierać
sekret podpisu licencji, klucz-master Fernet i plik z incydentu
PHASE 1. Domknięte + dodano niezależną weryfikację gotowego ZIP.

Ten test pilnuje OBU warstw:
1) should_exclude() wyklucza każdy krytyczny plik z listy PHASE 1,
2) verify_release() wykrywa zakazany plik wstrzyknięty do ZIP
   (druga linia obrony niezależna od EXCLUDE_*).
"""
import importlib.util
import os
import zipfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BR = os.path.join(_ROOT, "tools", "build_release.py")


def _load_br():
    spec = importlib.util.spec_from_file_location("build_release", _BR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# PHASE 1 "NIE MOŻE wejść do paczki" (docs/PHASE1_INCIDENT_REPORT.md)
_CRITICAL = [
    ".secret_key",
    ".license_secret",
    ".env",
    ".env.key",
    ".env.extra",
    "akces_hub.db",
    "akces_hub.db-wal",
    "akces_hub.db-shm",
    "nohup.out",
    "server.out",
    "print_debug.log",
]


@pytest.mark.parametrize("fname", _CRITICAL)
def test_critical_file_is_excluded(fname):
    """Każdy sekret/artefakt z listy PHASE 1 MUSI być wykluczony."""
    br = _load_br()
    assert br.should_exclude("/some/dir", fname), (
        f"{fname} NIE wykluczony przez build_release — regresja luki "
        f"sekretów (klasa incydentu PHASE 1)"
    )


def test_tools_dir_excluded():
    """Cały katalog tools/ (generator licencji) poza paczką."""
    br = _load_br()
    assert br.should_exclude("/x", "tools"), "tools/ musi być wykluczony"


def test_verify_release_catches_injected_secret(tmp_path):
    """verify_release() MUSI odrzucić ZIP z wstrzykniętym sekretem
    (druga linia obrony — działa nawet gdy EXCLUDE rozszczelnione)."""
    br = _load_br()
    bad_zip = str(tmp_path / "bad.zip")
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("akces-hub/app.py", "print('ok')\n")
        zf.writestr("akces-hub/.license_secret", "deadbeef" * 8)
    with pytest.raises(SystemExit) as e:
        br.verify_release(bad_zip)
    assert e.value.code == 1


def test_verify_release_catches_jwt_token(tmp_path):
    """verify_release() MUSI wykryć sygnaturę tokenu eyJ… w treści
    (dokładny wektor incydentu PHASE 1 — token w pliku tekstowym)."""
    br = _load_br()
    bad_zip = str(tmp_path / "tok.zip")
    # Realistyczny kształt JWT: eyJ + długi base64url . payload . sig
    fake = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + "A" * 40 + "." + "S" * 20
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("akces-hub/notes.txt", f"refresh_token={fake}\n")
    with pytest.raises(SystemExit):
        br.verify_release(bad_zip)


def test_verify_release_passes_clean_zip(tmp_path):
    """Czysty ZIP (kod + pusty .gitkeep) przechodzi weryfikację."""
    br = _load_br()
    ok_zip = str(tmp_path / "ok.zip")
    with zipfile.ZipFile(ok_zip, "w") as zf:
        zf.writestr("akces-hub/app.py", "print('hello')\n")
        zf.writestr("akces-hub/backups/.gitkeep", "")
    assert br.verify_release(ok_zip) is True
