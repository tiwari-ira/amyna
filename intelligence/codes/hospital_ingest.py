"""Parse CMS hospital price-transparency machine-readable files (MRFs).

Builds a per-hospital ``code -> standard charges`` dictionary from the CMS v2
"tall" CSV or JSON layouts. The discrepancy engine uses it only to flag lines
billed above the hospital's own published gross charge.

# VERIFY against current docs: CMS template versions change (v2.0 -> v2.x/v3);
# confirm column names (``standard_charge|gross``, ``code|1|type``, ...) and the
# JSON keys against the current CMS hospital price transparency data dictionary.
"""

import csv
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from claimshield_contracts.enums import CodeSystem

DATA_DIR = Path(__file__).parent / "data"
SAMPLE_FILES = (DATA_DIR / "sample_hospital_mrf.csv",)
_CODE_TYPES = {"CPT": CodeSystem.CPT, "HCPCS": CodeSystem.HCPCS, "NDC": CodeSystem.NDC}


def _cents(value: str | float | None) -> int | None:
    if value in (None, ""):
        return None
    return round(float(value) * 100)


def normalize_hospital_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


@dataclass(frozen=True)
class StandardCharge:
    code_system: CodeSystem
    code: str
    description: str
    setting: str | None
    gross_cents: int | None
    discounted_cash_cents: int | None
    payer_name: str | None = None
    plan_name: str | None = None
    negotiated_cents: int | None = None


@dataclass
class HospitalPriceIndex:
    hospital_name: str
    last_updated_on: str | None
    charges: dict[tuple[CodeSystem, str], list[StandardCharge]] = field(default_factory=dict)

    def add(self, charge: StandardCharge) -> None:
        self.charges.setdefault((charge.code_system, charge.code.upper()), []).append(charge)

    def gross_charge_cents(self, system: CodeSystem, code: str) -> int | None:
        """Highest published gross charge for a code (None if not published)."""
        values = [
            c.gross_cents
            for c in self.charges.get((system, code.upper()), [])
            if c.gross_cents is not None
        ]
        return max(values) if values else None


def load_cms_csv(path: Path) -> HospitalPriceIndex:
    """CMS v2 'tall' CSV: general info (rows 1-2), header row 3, one charge per row."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    info = dict(zip(rows[0], rows[1], strict=False))
    header_index = next(i for i, row in enumerate(rows) if "standard_charge|gross" in row)
    index = HospitalPriceIndex(info.get("hospital_name", path.stem), info.get("last_updated_on"))
    for values in rows[header_index + 1 :]:
        row = dict(zip(rows[header_index], values, strict=False))
        for slot in ("1", "2", "3", "4"):
            system = _CODE_TYPES.get(row.get(f"code|{slot}|type", "").upper())
            code = row.get(f"code|{slot}", "")
            if system and code:
                index.add(
                    StandardCharge(
                        code_system=system,
                        code=code,
                        description=row.get("description", ""),
                        setting=row.get("setting") or None,
                        gross_cents=_cents(row.get("standard_charge|gross")),
                        discounted_cash_cents=_cents(row.get("standard_charge|discounted_cash")),
                        payer_name=row.get("payer_name") or None,
                        plan_name=row.get("plan_name") or None,
                        negotiated_cents=_cents(row.get("standard_charge|negotiated_dollar")),
                    )
                )
    return index


def load_cms_json(path: Path) -> HospitalPriceIndex:
    """CMS v2 JSON: ``standard_charge_information[*].code_information / standard_charges``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    index = HospitalPriceIndex(data.get("hospital_name", path.stem), data.get("last_updated_on"))
    for item in data.get("standard_charge_information", []):
        for code_info in item.get("code_information", []):
            system = _CODE_TYPES.get(str(code_info.get("type", "")).upper())
            if system is None:
                continue
            for charge in item.get("standard_charges", []):
                payers = charge.get("payers_information") or [{}]
                for payer in payers:
                    index.add(
                        StandardCharge(
                            code_system=system,
                            code=str(code_info["code"]),
                            description=item.get("description", ""),
                            setting=charge.get("setting"),
                            gross_cents=_cents(charge.get("gross_charge")),
                            discounted_cash_cents=_cents(charge.get("discounted_cash")),
                            payer_name=payer.get("payer_name"),
                            plan_name=payer.get("plan_name"),
                            negotiated_cents=_cents(payer.get("standard_charge_dollar")),
                        )
                    )
    return index


def load_mrf(path: Path) -> HospitalPriceIndex:
    return load_cms_json(path) if path.suffix.lower() == ".json" else load_cms_csv(path)


def build_directory(paths: Iterable[Path]) -> dict[str, HospitalPriceIndex]:
    """Normalized hospital name -> price index."""
    indexes = (load_mrf(path) for path in paths)
    return {normalize_hospital_name(i.hospital_name): i for i in indexes}


@cache
def default_directory() -> dict[str, HospitalPriceIndex]:
    """Price indexes for the bundled synthetic MRFs."""
    return build_directory(SAMPLE_FILES)


def find_price_index(*names: str | None) -> HospitalPriceIndex | None:
    """First bundled index matching any of the given provider/facility names."""
    directory = default_directory()
    for name in names:
        if name and (index := directory.get(normalize_hospital_name(name))):
            return index
    return None
