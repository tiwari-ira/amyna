"""CMS machine-readable price files (CSV + JSON) -> per-hospital code dictionary."""

from claimshield_contracts.enums import CodeSystem
from intelligence.codes.hospital_ingest import (
    DATA_DIR,
    find_price_index,
    load_cms_csv,
    load_cms_json,
)


def test_csv_tall_format() -> None:
    index = load_cms_csv(DATA_DIR / "sample_hospital_mrf.csv")
    assert index.hospital_name == "Bayview Imaging Center (synthetic)"
    assert index.gross_charge_cents(CodeSystem.CPT, "70553") == 320000
    charge = index.charges[(CodeSystem.HCPCS, "J2060")][0]
    assert charge.negotiated_cents == 2200 and charge.payer_name == "Acme Health Plan"


def test_json_format() -> None:
    index = load_cms_json(DATA_DIR / "sample_hospital_mrf.json")
    assert index.gross_charge_cents(CodeSystem.HCPCS, "G0463") == 30000
    assert index.gross_charge_cents(CodeSystem.CPT, "99999") is None


def test_lookup_by_provider_name() -> None:
    assert find_price_index(None, "Bayview Imaging Center (synthetic)") is not None
    assert find_price_index("Unknown Hospital") is None
