"""TD3 (passport) Machine-Readable Zone construction.

Builds the two 44-character MRZ lines for a TD3 travel document, including the
ICAO 9303 check digits (7-3-1 weighting, mod 10). Pure stdlib — no deps.

The MRZ font is OCR-B; render it with a mono/OCR-B face (see core.fonts).
"""
from __future__ import annotations

_WEIGHTS = (7, 3, 1)


def char_value(c: str) -> int:
    """ICAO 9303 numeric value of an MRZ character."""
    if c.isdigit():
        return int(c)
    if c == "<":
        return 0
    return ord(c.upper()) - 55  # 'A' -> 10 ... 'Z' -> 35


def check_digit(s: str) -> str:
    """ICAO 9303 check digit over a field string."""
    total = sum(char_value(c) * _WEIGHTS[i % 3] for i, c in enumerate(s))
    return str(total % 10)


def _pad(s: str, n: int) -> str:
    return (s + "<" * n)[:n]


def _name_field(surname: str, given_names: str) -> str:
    sur = surname.upper().replace(" ", "<").replace("-", "<")
    giv = given_names.upper().replace(" ", "<").replace("-", "<")
    return f"{sur}<<{giv}"


def td3(
    *,
    issuing_country: str,
    surname: str,
    given_names: str,
    doc_number: str,
    nationality: str,
    dob: str,          # YYMMDD
    sex: str,          # M / F / <
    expiry: str,       # YYMMDD
    personal_number: str = "",
) -> tuple[str, str]:
    """Return (line1, line2), each exactly 44 chars."""
    sex_mrz = sex.upper() if sex.upper() in ("M", "F") else "<"

    line1 = _pad("P<" + issuing_country.upper() + _name_field(surname, given_names), 44)

    doc = _pad(doc_number.upper(), 9)
    doc_c = check_digit(doc)
    dob_c = check_digit(dob)
    exp_c = check_digit(expiry)
    pn = _pad(personal_number.upper(), 14)
    pn_c = check_digit(pn)

    composite = doc + doc_c + dob + dob_c + expiry + exp_c + pn + pn_c
    comp_c = check_digit(composite)

    line2 = _pad(
        doc + doc_c + nationality.upper() + dob + dob_c + sex_mrz + expiry + exp_c + pn + pn_c + comp_c,
        44,
    )
    return line1, line2


if __name__ == "__main__":
    l1, l2 = td3(
        issuing_country="UTO", surname="ERIKSSON", given_names="ANNA MARIA",
        doc_number="L898902C3", nationality="UTO", dob="740812", sex="F",
        expiry="120415", personal_number="ZE184226B",
    )
    print(l1)
    print(l2)
    # Classic ICAO 9303 worked example — line2 ends with composite check digit.
