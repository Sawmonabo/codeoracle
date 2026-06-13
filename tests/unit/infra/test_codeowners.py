"""Tests for .github/CODEOWNERS."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CODEOWNERS = REPO_ROOT / ".github" / "CODEOWNERS"


def _parse_codeowners(path: Path) -> list[tuple[str, list[str]]]:
    entries: list[tuple[str, list[str]]] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern, *owners = parts
        entries.append((pattern, owners))
    return entries


def test_codeowners_exists() -> None:
    assert CODEOWNERS.exists()


def test_codeowners_has_default_owner() -> None:
    entries = _parse_codeowners(CODEOWNERS)
    assert any(pattern == "*" for pattern, _ in entries), (
        "CODEOWNERS must define a default-owner rule with '*'"
    )


def test_codeowners_protects_security_paths() -> None:
    entries = _parse_codeowners(CODEOWNERS)
    patterns = [p for p, _ in entries]
    required = [
        "/.github/workflows/",
        "/infra/security/",
        "/src/codeoracle/services/verifier/",
    ]
    for r in required:
        assert any(p.startswith(r) for p in patterns), (
            f"Missing protection for {r}"
        )


def test_codeowners_owners_use_team_or_user_prefix() -> None:
    entries = _parse_codeowners(CODEOWNERS)
    for pattern, owners in entries:
        for owner in owners:
            assert owner.startswith("@"), (
                f"Pattern {pattern!r} owner {owner!r} must start with '@'"
            )
