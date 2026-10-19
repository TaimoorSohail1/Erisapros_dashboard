"""Guard the provider version verified against the configured ingest service."""
from pathlib import Path


def test_release_keeps_the_verified_groundx_ingestion_version():
    requirements = (Path(__file__).parents[1] / "requirements.txt").read_text()
    groundx = [line.strip() for line in requirements.splitlines()
               if line.strip().startswith("groundx[")]
    assert groundx == ["groundx[extract]==4.0.9"], (
        "GroundX upgrades require fresh ingestion verification before release; "
        "4.1.1 failed ingestion with the existing production credentials."
    )
