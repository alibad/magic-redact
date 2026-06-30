"""Coherent synthetic-identity generator.

The "magic" of one-click redaction is that every field maps to ONE consistent
fake person: the name, nationality, dates, document number and MRZ all agree.
This module produces that person and resolves any detected field to its
replacement value.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from . import mrz

_POOLS = Path(__file__).parent / "pools"


@lru_cache(maxsize=1)
def _load(name: str):
    return json.loads((_POOLS / name).read_text(encoding="utf-8"))


# Canonical field keys a detector may assign to a text region. Anything a
# detector emits should normalize to one of these via FIELD_ALIASES.
FIELD_ALIASES = {
    "surname": "surname", "last_name": "surname", "family_name": "surname",
    "given_names": "given_names", "given_name": "given_names", "first_name": "given_names", "forename": "given_names",
    "name": "full_name", "full_name": "full_name", "holder": "full_name",
    "dob": "dob", "date_of_birth": "dob", "birth_date": "dob", "birth": "dob",
    "nationality": "nationality", "citizenship": "nationality",
    "sex": "sex", "gender": "sex",
    "doc_number": "doc_number", "passport_number": "doc_number", "document_number": "doc_number", "number": "doc_number",
    "expiry": "expiry", "date_of_expiry": "expiry", "expiration": "expiry",
    "issue": "issue", "date_of_issue": "issue",
    "place_of_birth": "place_of_birth", "birthplace": "place_of_birth", "pob": "place_of_birth",
    "authority": "authority", "issuing_authority": "authority",
    "mrz": "mrz", "machine_readable_zone": "mrz",
    "photo": "photo", "portrait": "photo", "face": "photo",
}


def normalize_field(name: str | None) -> str:
    if not name:
        return "unknown"
    return FIELD_ALIASES.get(name.strip().lower(), "unknown")


@dataclass
class Identity:
    sex: str                 # M / F / X
    given_names: str
    surname: str
    nationality_iso3: str
    nationality_name: str
    issuing_iso3: str
    dob: date
    expiry: date
    issue: date
    doc_number: str
    place_of_birth: str
    personal_number: str = ""
    seed: int | None = None
    _date_fmt: str = field(default="%d %b %Y", repr=False)

    # --- formatting helpers -------------------------------------------------
    @property
    def full_name(self) -> str:
        return f"{self.given_names} {self.surname}"

    def _d(self, d: date) -> str:
        return d.strftime(self._date_fmt).upper()

    @property
    def mrz_lines(self) -> tuple[str, str]:
        return mrz.td3(
            issuing_country=self.issuing_iso3,
            surname=self.surname,
            given_names=self.given_names,
            doc_number=self.doc_number,
            nationality=self.nationality_iso3,
            dob=self.dob.strftime("%y%m%d"),
            sex=self.sex,
            expiry=self.expiry.strftime("%y%m%d"),
            personal_number=self.personal_number,
        )

    # --- field resolution ---------------------------------------------------
    def value_for(self, field_name: str | None) -> str | None:
        """Replacement string for a detected field. None => not a text field
        (e.g. 'photo', handled by a face strategy) or unknown."""
        f = normalize_field(field_name)
        return {
            "surname": self.surname.upper(),
            "given_names": self.given_names.upper(),
            "full_name": self.full_name.upper(),
            "dob": self._d(self.dob),
            "expiry": self._d(self.expiry),
            "issue": self._d(self.issue),
            "nationality": self.nationality_name.upper(),
            "sex": self.sex,
            "doc_number": self.doc_number,
            "place_of_birth": self.place_of_birth.upper(),
            "authority": f"MIN OF INTERIOR / {self.issuing_iso3}",
            "personal_number": self.personal_number,
            "mrz": "\n".join(self.mrz_lines),
        }.get(f)

    def to_dict(self) -> dict:
        l1, l2 = self.mrz_lines
        return {
            "sex": self.sex,
            "given_names": self.given_names,
            "surname": self.surname,
            "nationality": {"iso3": self.nationality_iso3, "name": self.nationality_name},
            "issuing_iso3": self.issuing_iso3,
            "dob": self.dob.isoformat(),
            "expiry": self.expiry.isoformat(),
            "issue": self.issue.isoformat(),
            "doc_number": self.doc_number,
            "place_of_birth": self.place_of_birth,
            "personal_number": self.personal_number,
            "mrz": [l1, l2],
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Identity":
        """Rebuild from a to_dict() payload. MRZ is derived, so it is ignored."""
        nat = d.get("nationality") or {}
        return cls(
            sex=d["sex"],
            given_names=d["given_names"],
            surname=d["surname"],
            nationality_iso3=nat.get("iso3", d.get("nationality_iso3", "")),
            nationality_name=nat.get("name", d.get("nationality_name", "")),
            issuing_iso3=d.get("issuing_iso3", nat.get("iso3", "")),
            dob=date.fromisoformat(d["dob"]),
            expiry=date.fromisoformat(d["expiry"]),
            issue=date.fromisoformat(d["issue"]),
            doc_number=d["doc_number"],
            place_of_birth=d.get("place_of_birth", ""),
            personal_number=d.get("personal_number", ""),
            seed=d.get("seed"),
        )


def _rand_date(rng: random.Random, start_year: int, end_year: int) -> date:
    start = date(start_year, 1, 1)
    end = date(end_year, 12, 31)
    return start + timedelta(days=rng.randint(0, (end - start).days))


def _doc_number(rng: random.Random) -> str:
    # Common passport style: 1 letter + 8 digits (varies by country; good enough).
    alpha = rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ")
    return alpha + "".join(rng.choice("0123456789") for _ in range(8))


def generate_identity(
    *,
    seed: int | None = None,
    sex: str | None = None,
    nationality_iso3: str | None = None,
) -> Identity:
    rng = random.Random(seed)
    given_pool = _load("given_names.json")
    surnames = _load("surnames.json")
    countries = _load("countries.json")
    cities = _load("cities.json")

    sex = sex or rng.choices(["M", "F", "X"], weights=[48, 48, 4])[0]
    given_list = given_pool.get(sex, given_pool["X"])
    # 60% chance of a middle name for realism.
    given = rng.choice(given_list)
    if rng.random() < 0.6:
        given = f"{given} {rng.choice(given_pool['M'] + given_pool['F'])}"

    if nationality_iso3:
        nat = next((c for c in countries if c["iso3"] == nationality_iso3), rng.choice(countries))
    else:
        nat = rng.choice(countries)

    dob = _rand_date(rng, 1955, 2006)
    issue = _rand_date(rng, 2017, 2024)
    expiry = date(issue.year + 10, issue.month, min(issue.day, 28))

    return Identity(
        sex=sex,
        given_names=given,
        surname=rng.choice(surnames),
        nationality_iso3=nat["iso3"],
        nationality_name=nat["name"],
        issuing_iso3=nat["iso3"],
        dob=dob,
        expiry=expiry,
        issue=issue,
        doc_number=_doc_number(rng),
        place_of_birth=rng.choice(cities),
        personal_number="".join(rng.choice("0123456789") for _ in range(rng.choice([0, 9, 11]))),
        seed=seed,
    )


if __name__ == "__main__":
    import json as _j
    idn = generate_identity(seed=7)
    print(_j.dumps(idn.to_dict(), indent=2))
