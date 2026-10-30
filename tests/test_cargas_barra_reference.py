"""Referencias de la batería T-CB (plan T-015, E2.3) frente a un cálculo exacto aparte.

Las fórmulas de ``benchmarks`` están en coma flotante; aquí se recalculan con ``Fraction`` y se
comprueba que las referencias publicadas coinciden. Además, R-SUB compara filas calculadas, así
que se comprueba que no hay filas vacías y que todas las componentes publicadas se contrastan.
"""

from fractions import Fraction

import pytest

import benchmark_cache
from veritas.fem import benchmarks

pytestmark = pytest.mark.solver

L, P, A, B = Fraction(4), Fraction(10_000), Fraction(1), Fraction(3)
W = Fraction(10_000)


def references(case_id: str) -> dict[str, float]:
    outcomes = {o.case_id: o for o in benchmark_cache.run_cargas_barra_battery()}
    return {check.quantity: check.reference for check in outcomes[case_id].checks}


def close(value: float, exact: Fraction) -> None:
    assert value == pytest.approx(float(exact), rel=1e-12, abs=1e-9)


def test_r_fix_point_load() -> None:
    ref = references("T-CB-01")
    close(ref["R_Z N1"], P * B**2 * (3 * A + B) / L**3)
    close(ref["R_Z N2"], P * A**2 * (A + 3 * B) / L**3)
    close(ref["R_My N1"], -P * A * B**2 / L**2)
    close(ref["R_My N2"], P * A**2 * B / L**2)
    close(ref["R_X N1"], -Fraction(20_000) * B / L)


def test_r_fix_triangular_load() -> None:
    ref = references("T-CB-02")
    close(ref["R_Z N1"], 3 * W * L / 20)
    close(ref["R_Z N2"], 7 * W * L / 20)
    close(ref["R_My N1"], -W * L**2 / 30)
    close(ref["R_My N2"], W * L**2 / 20)


def test_r_prop_propped_point_load() -> None:
    ref = references("T-CB-04")
    r_i = P * B**2 * (A + 2 * L) / (2 * L**3)
    close(ref["R_Z N1"], r_i)
    close(ref["R_Z N2"], P - r_i)
    close(ref["R_My N2"], P * A * B * (L + A) / (2 * L**2))
    assert ref["R_My N1"] == 0.0


def test_r_cant_equilibrium_reactions() -> None:
    close(references("T-CB-05 a")["R_My N1"], -W * L**2 / 6)
    close(references("T-CB-05 b")["R_My N1"], -W * L**2 / 3)
    close(references("T-CB-05 c")["R_My N1"], -P * Fraction(6, 10) * L)


def test_subdivision_rows_cover_every_published_component() -> None:
    outcomes = {o.case_id: o for o in benchmark_cache.run_cargas_barra_battery()}
    planar, spatial = outcomes["T-CB-06"], outcomes["T-CB-08"]
    assert len(planar.checks) == 18  # tres componentes del plano por cada fuerza y giro
    assert len(spatial.checks) == 36  # seis componentes por cada vector, sin filas vacías
    for outcome in (planar, spatial):
        assert all(check.formula == benchmarks.SUBDIVIDED for check in outcome.checks)
        assert outcome.matches
