"""Cota de redondeo de la normalización plana con un rodillo inclinado penalizado (plan T-021).

La numeración de los nudos (el orden de los UUID) cambia el redondeo de la eliminación con el
resorte de penalización. Aquí se fija con ``monkeypatch`` de ``new_entity_id`` para recorrer las
seis posiciones de B de forma determinista (D2-A de T-021). El modelo es el de E2.1-e de T-015:
barra oblicua 3:4 subdividida en 5, A empotrado y B con un rodillo inclinado penalizado.
"""

import contextlib
import dataclasses
from collections.abc import Callable
from itertools import count, pairwise
from typing import Any
from uuid import UUID

import pytest

from fem_support import Frame, inclined
from veritas import fem
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.geometry import model as geometry_model
from veritas.core.geometry.entities import Node
from veritas.core.supports.support import fixed_restraints, pinned_restraints, roller_restraints
from veritas.core.units import Quantity
from veritas.fem.results import normalized
from veritas.fem.results.normalized import normalize_planar_xz
from veritas.fem.results.run import RunStatus

pytestmark = pytest.mark.solver

CUTS = (0.0, 0.2, 0.3, 0.6, 0.7, 1.0)
MARGIN = 5.0  # el residuo debe ser ≤ cota / MARGIN (margen mínimo de T-021)


def numbering(rank_of_b: int) -> Callable[[], UUID]:
    """Generador de UUID v4 cuyo orden da a B (el último nudo) la posición ``rank_of_b``."""
    order = [r for r in range(1, len(CUTS) + 1) if r != rank_of_b] + [rank_of_b]
    calls = count()

    def new_id() -> UUID:
        k = next(calls)
        rank = order[k] if k < len(order) else 100 + k
        return UUID(int=rank << 100, version=4)

    return new_id


def build(support: str, angle: float = 25.0) -> tuple[Frame, Node]:
    frame = Frame()
    nodes = [frame.node(3.0 * x, 4.0 * x) for x in CUTS]
    members = [frame.member(p, q) for p, q in pairwise(nodes)]
    frame.support(nodes[0], fixed_restraints())
    b = nodes[-1]
    if support == "inclinado":
        frame.support(b, roller_restraints(), basis=inclined(angle))
    else:
        frame.support(b, pinned_restraints())
    frame.nodal(nodes[2], fx=4e3, fz=-12e3)
    frame.nodal(nodes[3], my=7e3)
    for member in members[1:4]:
        frame.model.add_member_uniform_load(frame.case_id, member.id, qz=Quantity.of(-3e3, "N/m"))
    return frame, b


class Probe:
    """Residuo y cota de momento de los nudos en la corrida, leídos de ``_require``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.rows: list[tuple[float, float, Any]] = []
        original = normalized._Normalizer._require
        rows = self.rows

        def spy(self_: Any, where: str, entity: Any, force: Any, moment: Any) -> None:
            if where == "node":
                rows.append((moment[0], moment[1], entity))
            original(self_, where, entity, force, moment)

        monkeypatch.setattr(normalized._Normalizer, "_require", spy)

    def ratio(self, node: Node) -> float:
        residual, bound, _ = next(r for r in self.rows if r[2] == node.id)
        return residual / bound


def solve(
    monkeypatch: pytest.MonkeyPatch, rank: int, support: str, angle: float, factor: float
) -> tuple[Any, Frame, Node, Probe]:
    monkeypatch.setattr(geometry_model, "new_entity_id", numbering(rank))
    frame, b = build(support, angle)
    probe = Probe(monkeypatch)
    run = fem.analyze_planar_xz(frame.model, frame.config(penalty_factor=factor))
    assert run.status is RunStatus.SUCCEEDED
    return run, frame, b, probe


@pytest.mark.parametrize("rank", range(1, 7))
def test_every_numbering_of_the_penalized_node_normalizes(
    monkeypatch: pytest.MonkeyPatch, rank: int
) -> None:
    """E2.1-e: con B en las posiciones 5 y 6 la cota anterior rechazaba un análisis correcto."""
    run, frame, b, probe = solve(monkeypatch, rank, "inclinado", 25.0, 1e8)
    normalize_planar_xz(run, frame.model)
    assert probe.ratio(b) <= 1 / MARGIN


@pytest.mark.parametrize("angle", [10.0, 25.0, 80.0])
@pytest.mark.parametrize("factor", [1e4, 1e8, 1e12])
def test_sweep_of_angles_penalty_factors_and_numberings(
    monkeypatch: pytest.MonkeyPatch, angle: float, factor: float
) -> None:
    """Los peores casos de la sonda de E1 (c hasta 33,5), en las seis posiciones de B."""
    for rank in range(1, 7):
        run, frame, b, probe = solve(monkeypatch, rank, "inclinado", angle, factor)
        normalize_planar_xz(run, frame.model)
        assert probe.ratio(b) <= 1 / MARGIN, (angle, factor, rank)


Bounds = dict[int, tuple[float, float]]


def bounds_of(run: Any, frame: Frame, monkeypatch: pytest.MonkeyPatch) -> Bounds:
    captured: list[Bounds] = []
    original = normalized._Normalizer._rounding_bounds

    def spy(self: Any) -> Bounds:
        result: Bounds = original(self)
        captured.append(result)
        return result

    monkeypatch.setattr(normalized._Normalizer, "_rounding_bounds", spy)
    # Con la cota anterior (factor único) esta corrida se rechaza: es el hallazgo de T-021.
    with contextlib.suppress(normalized.ResultsNormalizationError):
        normalize_planar_xz(run, frame.model)
    return captured[0]


def test_bounds_without_penalty_do_not_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin resorte de penalización, la cota es idéntica bit a bit a la de factor único."""
    run, frame, _b, _probe = solve(monkeypatch, 6, "articulado", 25.0, 1e8)
    new = bounds_of(run, frame, monkeypatch)
    monkeypatch.setattr(normalized, "ROUNDING_FACTOR_PENALTY_MOMENT", normalized.ROUNDING_FACTOR)
    old = bounds_of(run, frame, monkeypatch)
    assert repr(new) == repr(old)


def test_penalty_only_changes_the_moment_row_of_the_penalized_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, frame, _b, _probe = solve(monkeypatch, 6, "inclinado", 25.0, 1e8)
    new = bounds_of(run, frame, monkeypatch)
    factor = normalized.ROUNDING_FACTOR_PENALTY_MOMENT / normalized.ROUNDING_FACTOR
    monkeypatch.setattr(normalized, "ROUNDING_FACTOR_PENALTY_MOMENT", normalized.ROUNDING_FACTOR)
    old = bounds_of(run, frame, monkeypatch)
    changed = [tag for tag in new if new[tag] != old[tag]]
    assert len(changed) == 1
    tag = changed[0]
    assert new[tag][0] == old[tag][0]  # fila de fuerza intacta
    assert new[tag][1] / old[tag][1] == pytest.approx(factor)


@pytest.mark.parametrize("rank", [1, 6])
def test_injected_error_still_fails_at_the_penalized_node(
    monkeypatch: pytest.MonkeyPatch, rank: int
) -> None:
    """Un error de 1e-6 relativo en el momento publicado sigue rechazándose (cota no ciega)."""
    run, frame, _b, _probe = solve(monkeypatch, rank, "inclinado", 25.0, 1e8)
    raw = run.raw
    assert raw is not None
    last = raw.members[-1]
    moment = max(abs(v) for member in raw.members for v in member.local_force)
    forces = list(last.local_force)
    forces[5] += 1e-6 * moment  # momento en J de la última barra, la que llega a B
    members = (*raw.members[:-1], dataclasses.replace(last, local_force=tuple(forces)))
    tampered = dataclasses.replace(run, raw=dataclasses.replace(raw, members=members))
    with pytest.raises(DiagnosticError) as info:
        normalize_planar_xz(tampered, frame.model)
    assert info.value.code is DiagnosticCode.ANALYSIS_FAILED
    assert info.value.diagnostic.message_key == "fem.results_equilibrium"
    assert dict(info.value.diagnostic.params)["where"] == "node"
