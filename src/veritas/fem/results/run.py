"""Registro de corrida y salida bruta (plan T-007, D1-A; ADR-001 §4; ADR-002 §6).

``raw`` existe solo con ``SUCCEEDED``; un fallo nunca lleva resultados ni ceros. Los
valores están en la base del solver ``SOLVER_XZ_RAW`` (x₁ = X, x₂ = Z, θ = −Ry) y no
están normalizados a ADR-001: los normaliza T-008.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from veritas.core.diagnostics import Diagnostic

RAW_BASIS: Final = "SOLVER_XZ_RAW"
ADAPTER_ID: Final = "veritas.fem.planar_xz"
ADAPTER_VERSION: Final = "3"  # 2: extremos de barra (T-010); 3: controles de T-017

type Triple = tuple[float, float, float]


class RunStatus(StrEnum):
    """Estado de ejecución (ADR-001 §4). ``CANCELLED`` está reservado: inalcanzable en T-007."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class RawNodeResult:
    """Desplazamiento (u₁, u₂, θ) y reacción por GDL fijado con ``fix``.

    Un GDL no fijado en el nudo real tiene reacción ``None`` («no aplicable»): la de un
    resorte o una penalización está en ``RawSpringReaction`` (revisión M5).
    """

    node_id: UUID
    displacement: Triple
    reaction: tuple[float | None, float | None, float | None]


@dataclass(frozen=True, slots=True)
class RawSpringReaction:
    """Reacción (R₁, R₂, M) en el nudo auxiliar fijo de un resorte o penalización."""

    support_id: UUID
    node_id: UUID
    dof: int
    penalty: bool
    reaction: Triple


@dataclass(frozen=True, slots=True)
class RawMemberForces:
    """``eleResponse(tag, 'localForce')`` sin interpretar (6 componentes)."""

    member_id: UUID
    local_force: tuple[float, float, float, float, float, float]


@dataclass(frozen=True, slots=True)
class RawEndRotation:
    """Giro θ del extremo no rígido de una barra (nudo auxiliar; T-010). No se publica."""

    member_id: UUID
    end: str
    theta: float


@dataclass(frozen=True, slots=True)
class RawPlanarResults:
    """Salida bruta del solver, no normalizada.

    La reacción de un nudo con extremos no rígidos ya suma, en los GDL de traslación con
    ``fix``, la de sus nudos auxiliares (T-010, D2: ``equalDOF`` la lleva al aux).
    """

    basis: str
    nodes: tuple[RawNodeResult, ...]
    springs: tuple[RawSpringReaction, ...]
    members: tuple[RawMemberForces, ...]
    end_rotations: tuple[RawEndRotation, ...] = ()


@dataclass(frozen=True, slots=True)
class MechanismDof:
    """GDL que participa en el modo de mecanismo (T-017, D3), en base de dominio.

    ``component``: «Ux», «Uz», «Ry» o «t» (traslación tangente de un apoyo oblicuo). Con
    ``member_id``, es el giro propio del extremo ``end`` de esa barra sobre ``node_id``.
    ``participation`` = Σ_q Q_iq² en coordenadas escaladas (entre 0 y 1).
    """

    node_id: UUID
    component: str
    participation: float
    member_id: UUID | None = None
    end: str | None = None


@dataclass(frozen=True, slots=True)
class PivotSummary:
    """Pivote mínimo relativo de la salvaguarda D3 y su GDL (siempre registrado).

    Con singularidad y modo concluyente (T-017, D3), ``nullity`` y ``mechanism`` dan el
    modo completo en orden determinista; si no, ``nullity`` es None y ``mechanism`` vacío.
    """

    free_dofs: int
    min_pivot: float | None
    node_id: UUID | None
    component: str | None
    # T-010: el GDL del pivote es el giro propio del extremo ``end`` de ``member_id``.
    member_id: UUID | None = None
    end: str | None = None
    nullity: int | None = None
    mechanism: tuple[MechanismDof, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    """Registro de una corrida (FEM-005/FEM-010), solo en memoria.

    ``model_revision`` es el contador existente del dominio, leído y no generado
    (ADR-002 §6). Sin hash canónico ni vigencia CURRENT/STALE.
    """

    run_id: UUID
    status: RunStatus
    diagnostics: tuple[Diagnostic, ...]
    model_revision: int
    case_id: UUID
    case_revision: int | None
    configuration: dict[str, str | float | int]
    request_json: str | None
    versions: dict[str, str | None]
    hypotheses: tuple[str, ...]
    started_utc: datetime
    duration_s: float
    pivot: PivotSummary | None
    worker_stdout: str
    worker_stderr: str
    raw: RawPlanarResults | None

    def __post_init__(self) -> None:
        if (self.status is RunStatus.SUCCEEDED) != (self.raw is not None):
            raise ValueError("Solo una corrida SUCCEEDED lleva resultados")
