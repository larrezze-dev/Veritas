"""Cota de redondeo de la normalización con extremos no rígidos (T-027, TK-004; D1, D2, D4′-A).

La cota ``c·eps·Σ|k·u|`` de ``_Normalizer`` usa el u de los aux de ``raw.end_aux`` y coincide con
la de los controles de la respuesta cruda; falta de un aux: error explícito; sensibilidad: un
esfuerzo de extremo perturbado en 10·(cota + tol·escala) sigue rechazándose y uno de
0,1·tol·|F| no. Calibración de referencia: ``tests/calibration/calibrate_spatial_bounds.py``.
"""

import dataclasses

import pytest

from fem_support import Frame
from test_fem3d_analyze import analyze
from test_fem3d_end_penalty import P, grid_frame, translate
from test_fem3d_solver import run
from veritas.fem.results.normalized import ResultsNormalizationError
from veritas.fem.results.run import RunStatus
from veritas.fem.spatial import normalize_spatial
from veritas.fem.spatial.postconditions import all_displacements, rounding_bounds
from veritas.fem.spatial.results import SpatialAnalysisRun, _Normalizer

pytestmark = pytest.mark.solver

KINDS = ("uy", "uz_free", "uyuz", "rz", "ryrz")
J_FZ = 8  # fuerza local z del extremo J en localForce = [pI, pJ]


def solved(kind: str, both: bool = False, slender: float = 50) -> tuple[Frame, SpatialAnalysisRun]:
    frame = grid_frame(slender, kind, both=both)
    result = analyze(frame)
    assert result.status is RunStatus.SUCCEEDED, result.diagnostics
    return frame, result


def perturbed(result: SpatialAnalysisRun, delta: float) -> SpatialAnalysisRun:
    assert result.raw is not None
    first, *rest = result.raw.members
    force = list(first.local_force)
    force[J_FZ] += delta
    changed = dataclasses.replace(first, local_force=tuple(force))  # type: ignore[arg-type]
    members = (changed, *rest)
    return dataclasses.replace(result, raw=dataclasses.replace(result.raw, members=members))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("both", [False, True])
def test_normalizer_bound_equals_the_raw_response_bound(kind: str, both: bool) -> None:
    frame, result = solved(kind, both)
    request = translate(frame)
    expected = rounding_bounds(request, all_displacements(run(request)))
    normalizer = _Normalizer(result, frame.model, request)
    actual = normalizer._bounds()
    assert actual
    for node_id, (force, moment) in actual.items():
        tag = normalizer.tag_of[node_id]
        assert force == pytest.approx(expected[tag][0], rel=1e-12, abs=0.0)
        assert moment == pytest.approx(expected[tag][1], rel=1e-12, abs=0.0)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("both", [False, True])
def test_a_large_end_force_error_is_still_rejected_and_a_tiny_one_is_not(
    kind: str, both: bool
) -> None:
    frame, result = solved(kind, both)
    assert result.raw is not None
    request = translate(frame)
    normalizer = _Normalizer(result, frame.model, request)
    tol = normalizer.config.tol_equilibrium
    scale = sum(abs(v) for m in result.raw.members for v in m.local_force) + 2 * P
    extra = max(max(pair) for pair in normalizer._bounds().values())
    normalize_spatial(result, frame.model)  # la corrida sin perturbar normaliza
    big = 10 * (tol * scale + extra)
    for sign in (1.0, -1.0):
        with pytest.raises(ResultsNormalizationError) as error:
            normalize_spatial(perturbed(result, sign * big), frame.model)
        assert error.value.diagnostic.message_key == "fem.results_equilibrium"
    end_force = abs(result.raw.members[0].local_force[J_FZ]) or 1.0
    normalize_spatial(perturbed(result, 0.1 * tol * end_force), frame.model)


def test_a_missing_end_aux_is_an_explicit_error_not_a_fallback_to_the_master() -> None:
    frame, result = solved("uy")
    assert result.raw is not None
    assert result.raw.end_aux
    stripped = dataclasses.replace(result, raw=dataclasses.replace(result.raw, end_aux=()))
    with pytest.raises(ResultsNormalizationError) as error:
        normalize_spatial(stripped, frame.model)
    assert error.value.diagnostic.message_key == "fem.results_end_aux_missing"


@pytest.mark.parametrize("slender", [5, 500])
@pytest.mark.parametrize("index", [J_FZ, 10])  # fuerza z y momento y del extremo J
def test_sensitivity_at_the_slenderness_extremes_and_for_moments(slender: int, index: int) -> None:
    frame, result = solved("uz_free", True, slender)
    assert result.raw is not None
    normalizer = _Normalizer(result, frame.model, translate(frame))
    tol = normalizer.config.tol_equilibrium
    scale = sum(abs(v) for m in result.raw.members for v in m.local_force) + 2 * P
    extra = max(max(pair) for pair in normalizer._bounds().values())
    big = 10 * (tol * scale + extra)
    first, *rest = result.raw.members
    force = list(first.local_force)
    force[index] += big
    changed = dataclasses.replace(first, local_force=tuple(force))  # type: ignore[arg-type]
    raw = dataclasses.replace(result.raw, members=(changed, *rest))
    with pytest.raises(ResultsNormalizationError) as error:
        normalize_spatial(dataclasses.replace(result, raw=raw), frame.model)
    assert error.value.diagnostic.message_key == "fem.results_equilibrium"


def test_the_new_bound_is_larger_than_the_old_one_where_the_aux_matters() -> None:
    """Caracterización de la relajación aceptada (D4′-A, T-027): la cota con el u del aux supera
    más de dos veces a la antigua (u del maestro) en este caso; ver el script de calibración."""
    frame, result = solved("uz_free", True, 500)
    assert result.raw is not None
    request = translate(frame)
    normalizer = _Normalizer(result, frame.model, request)
    tags = normalizer.tag_of
    old_u = {tags[n.node_id]: n.displacement for n in result.raw.nodes}
    old = rounding_bounds(request, old_u)
    new = normalizer._bounds()
    node = max(new, key=lambda n: new[n][0] - old[tags[n]][0])
    assert new[node][0] > 2 * old[tags[node]][0]  # la cota subió de verdad en este caso


def test_a_non_finite_end_aux_gives_no_results() -> None:
    frame, result = solved("uy")
    assert result.raw is not None
    item, *rest = result.raw.end_aux
    bad = dataclasses.replace(item, displacement=(float("inf"), *item.displacement[1:]))
    raw = dataclasses.replace(result.raw, end_aux=(bad, *rest))
    with pytest.raises(ResultsNormalizationError) as error:
        normalize_spatial(dataclasses.replace(result, raw=raw), frame.model)
    assert error.value.diagnostic.message_key == "fem.nonfinite_result"
