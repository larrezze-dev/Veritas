"""Genera los fixtures SINTÉTICOS del catálogo (plan T-022, D3-A). No se ejecuta en la suite.

Uso: ``uv run python tests/data/make_catalog_fixture.py``

Los perfiles son ficticios: secciones en I soldadas de chapas (``SINT-h×b-tw-tf``) generadas por
una fórmula cerrada con ``fractions`` y **sin valor comercial**. No hay ninguna tabla real.

- ``catalog_synthetic_100.csv``: 100 filas válidas, formato de «catálogo comercial» (UTF-8 con
  BOM, separador «;», coma decimal, mm / cm² / cm⁴ / kg/m, J vacía y una columna no mapeada).
- ``catalog_synthetic_bad.csv``: 5 filas defectuosas con la misma cabecera.
- ``catalog_mapping_example.json``: mapeo ``veritas-catalog-mapping/1`` de ese formato.

Fórmulas (mm; sin radios de acuerdo): A = 2·b·tf + (h − 2·tf)·tw;
Iy = [b·h³ − (b − tw)·(h − 2·tf)³] / 12; Iz = [2·tf·b³ + (h − 2·tf)·tw³] / 12;
masa = A·7850 kg/m³. Las pruebas recalculan estos valores por su cuenta.
"""

import csv
import json
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).parent
SERIES = "SINTETICO-I"
HEADER = (
    "Serie",
    "Designación",
    "Familia",
    "h (mm)",
    "b (mm)",
    "tw (mm)",
    "tf (mm)",
    "A (cm²)",
    "Iy (cm⁴)",
    "Iz (cm⁴)",
    "J (cm⁴)",
    "m (kg/m)",
    "Notas",
)
NOTE = "SINTÉTICO, sin valor comercial"
DENSITY = 7850


def plates(index: int) -> tuple[int, int, int, int]:
    """(h, b, tw, tf) en mm de la fila ``index`` (0..99): 10 cantos × 10 anchos, únicos."""
    h = 100 + 20 * (index // 10)
    b = 50 + 5 * (index % 10)
    tw = 4 + index % 3
    tf = 6 + (index // 10) % 3
    return h, b, tw, tf


def properties(h: int, b: int, tw: int, tf: int) -> tuple[Fraction, Fraction, Fraction]:
    web = h - 2 * tf
    area = Fraction(2 * b * tf + web * tw)
    iy = Fraction(b * h**3 - (b - tw) * web**3, 12)
    iz = Fraction(2 * tf * b**3 + web * tw**3, 12)
    return area, iy, iz


def comma(value: Fraction | int, decimals: int) -> str:
    return f"{float(value):.{decimals}f}".replace(".", ",")


def row(index: int) -> list[str]:
    h, b, tw, tf = plates(index)
    area, iy, iz = properties(h, b, tw, tf)
    designation = f"SINT-{h}x{b}-{tw}-{tf}"
    mass = area * Fraction(DENSITY, 10**6)
    return [
        SERIES,
        designation,
        "I",
        str(h),
        str(b),
        str(tw),
        str(tf),
        comma(area / 100, 2),
        comma(iy / 10**4, 4),
        comma(iz / 10**4, 4),
        "",
        comma(mass, 4),
        NOTE,
    ]


def bad_rows() -> list[list[str]]:
    """Cinco filas defectuosas; ninguna repite la clave de las 100 buenas."""
    base = row(0)

    def variant(designation: str, changes: dict[str, str] | None = None) -> list[str]:
        columns = dict(zip(HEADER, base, strict=True))
        columns["Designación"] = designation
        columns.update(changes or {})
        return [columns[name] for name in HEADER]

    return [
        variant("SINT-MALO-1", {"A (cm²)": "abc"}),
        variant("SINT-MALO-2", {"Iy (cm⁴)": "0"}),
        variant(""),
        variant("SINT-MALO-4", {"Iz (cm⁴)": "inf"}),
        variant("SINT-MALO-5", {"Familia": "TUBO RARO"}),
    ]


def write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)


def mapping() -> dict[str, object]:
    return {
        "format": "veritas-catalog-mapping/1",
        "columns": {
            "Serie": {"field": "series"},
            "Designación": {"field": "designation"},
            "Familia": {"field": "family"},
            "h (mm)": {"field": "dim_h", "unit": "mm"},
            "b (mm)": {"field": "dim_b", "unit": "mm"},
            "tw (mm)": {"field": "dim_tw", "unit": "mm"},
            "tf (mm)": {"field": "dim_tf", "unit": "mm"},
            "A (cm²)": {"field": "area", "unit": "cm²"},
            "Iy (cm⁴)": {"field": "iy", "unit": "cm⁴"},
            "Iz (cm⁴)": {"field": "iz", "unit": "cm⁴"},
            "J (cm⁴)": {"field": "torsion_constant", "unit": "cm⁴"},
            "m (kg/m)": {"field": "linear_mass", "unit": "kg/m"},
        },
        "family_aliases": {"doble t": "I"},
        "steel": True,
        "method": "Fixture sintético por fórmula cerrada (tests/data/make_catalog_fixture.py)",
    }


def main() -> None:
    write_csv(HERE / "catalog_synthetic_100.csv", [row(i) for i in range(100)])
    write_csv(HERE / "catalog_synthetic_bad.csv", bad_rows())
    (HERE / "catalog_mapping_example.json").write_text(
        json.dumps(mapping(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
