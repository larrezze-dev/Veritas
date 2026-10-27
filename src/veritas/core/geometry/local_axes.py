"""Base local de barra según ADR-001 §1 (revisión A1-a, plan T-003).

Para extremos pI, pJ y longitud L = ||pJ - pI||:

1. ex = (pJ - pI)/L. Se rechaza L < ``MIN_MEMBER_LENGTH`` (D1) o L no representable.
2. La referencia r define la dirección local **+z**, es adimensional y se normaliza antes
   de proyectar: rhat = r/||r||. Por defecto r = global +Z; si ||w|| <= 1e-8 (barra
   vertical) se usa global -X. Una referencia explícita casi paralela se rechaza.
3. w = rhat - (rhat·ex)ex, reortogonalizado con una segunda pasada; ez0 = w/||w||;
   ey0 = ez0 × ex.
4. Giro alfa (rad) alrededor de +ex: ey = cos·ey0 + sin·ez0; ez = -sin·ey0 + cos·ez0.

Q tiene ex, ey, ez como filas: v_local = Q v_global y v_global = Q^T v_local. Postcondición
en ejecución: max|QQ^T - I| <= 1e-12 y det(Q) > 0.
"""

import math
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputTypeError,
    InputValueError,
    Location,
)
from veritas.core.geometry.vector import UNIT_X, UNIT_Z, Vec3

MIN_MEMBER_LENGTH: Final = 1e-6
"""Longitud física mínima de barra en m (plan T-003, D1). Decisión de ingeniería."""

ORIENTATION_TOLERANCE: Final = 1e-8
"""Umbral angular adimensional de la proyección de la referencia (ADR-001 §1)."""

ORTHONORMALITY_TOLERANCE: Final = 1e-12
"""Tolerancia de la postcondición QQ^T = I (ADR-001 §1, paso 6)."""

MAX_ABS_ALPHA: Final = 2.0 * math.pi
"""Rango admitido del giro alfa en rad (ADR-001 §1, paso 4)."""

DEFAULT_REFERENCE: Final = UNIT_Z
FALLBACK_REFERENCE: Final = -UNIT_X


class ReferenceSource(StrEnum):
    """Origen de la referencia efectiva de orientación."""

    DEFAULT_Z = "DEFAULT_Z"
    FALLBACK_NEG_X = "FALLBACK_NEG_X"
    EXPLICIT = "EXPLICIT"


def _error(
    code: DiagnosticCode,
    key: str,
    params: dict[str, str | int | float] | None,
    location: Location | None,
) -> InputValueError:
    return InputValueError(Diagnostic.create(code, key, params, location=location))


def validate_alpha(alpha: float, location: Location | None = None) -> float:
    """Comprueba que alfa (rad) es un real finito con |alfa| <= 2π."""
    if isinstance(alpha, bool) or not isinstance(alpha, int | float):
        raise InputTypeError(
            Diagnostic.create(
                DiagnosticCode.INPUT_INVALID_TYPE,
                "input.invalid_type",
                {"field": "alfa", "expected": "float (rad)", "received": type(alpha).__name__},
                location=location,
            )
        )
    try:
        value = float(alpha)
    except OverflowError:
        value = math.inf  # Entero no representable: se trata como no finito.
    if not math.isfinite(value):
        raise _error(
            DiagnosticCode.INPUT_NONFINITE,
            "input.nonfinite",
            {"field": "alfa", "value": repr(value)},
            location,
        )
    if abs(value) > MAX_ABS_ALPHA:
        raise _error(
            DiagnosticCode.GEOMETRY_INVALID_ORIENTATION,
            "geometry.alpha_out_of_range",
            {"alpha": value},
            location,
        )
    return value


@dataclass(frozen=True, slots=True)
class Orientation:
    """Orientación guardada en la revisión de barra (ADR-001 §1, paso 5).

    ``reference`` es la referencia efectiva normalizada de la dirección local +z, ya
    resuelta (incluido el fallback -X); ``alpha`` en rad, sin envolver.
    """

    reference: Vec3
    source: ReferenceSource
    alpha: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.reference, Vec3):
            raise TypeError("Orientation.reference debe ser Vec3")
        if not isinstance(self.source, ReferenceSource):
            raise TypeError("Orientation.source debe ser ReferenceSource")
        if abs(self.reference.norm() - 1.0) > ORTHONORMALITY_TOLERANCE:
            raise ValueError("Orientation.reference debe estar normalizada")
        if self.source is ReferenceSource.DEFAULT_Z and self.reference != DEFAULT_REFERENCE:
            raise ValueError("Orientation DEFAULT_Z exige la referencia +Z global")
        if self.source is ReferenceSource.FALLBACK_NEG_X and self.reference != FALLBACK_REFERENCE:
            raise ValueError("Orientation FALLBACK_NEG_X exige la referencia -X global")
        object.__setattr__(self, "alpha", validate_alpha(self.alpha))


@dataclass(frozen=True, slots=True)
class LocalAxes:
    """Base local ortonormal dextrógira de una barra y su longitud en m."""

    ex: Vec3
    ey: Vec3
    ez: Vec3
    length: float
    orientation: Orientation

    def matrix(self) -> tuple[tuple[float, float, float], ...]:
        """Q con filas ex, ey, ez."""
        return (self.ex.components(), self.ey.components(), self.ez.components())

    def to_local(self, vector: Vec3) -> Vec3:
        """v_local = Q v_global (vector libre)."""
        return Vec3._checked(
            self.ex.dot(vector), self.ey.dot(vector), self.ez.dot(vector), "Q v_global"
        )

    def to_global(self, vector: Vec3) -> Vec3:
        """v_global = Q^T v_local (vector libre)."""
        return self.ex.scaled(vector.x) + self.ey.scaled(vector.y) + self.ez.scaled(vector.z)


def _axis(p_i: Vec3, p_j: Vec3, location: Location | None) -> tuple[Vec3, float]:
    """Paso 1: dirección ex y longitud L."""
    dx, dy, dz = p_j.x - p_i.x, p_j.y - p_i.y, p_j.z - p_i.z
    length = math.hypot(dx, dy, dz)
    if not math.isfinite(length):
        raise _error(
            DiagnosticCode.GEOMETRY_ZERO_LENGTH, "geometry.length_unrepresentable", None, location
        )
    if length < MIN_MEMBER_LENGTH:
        raise _error(
            DiagnosticCode.GEOMETRY_ZERO_LENGTH,
            "geometry.zero_length",
            {"length": length, "minimum": MIN_MEMBER_LENGTH},
            location,
        )
    return Vec3(dx / length, dy / length, dz / length), length


def _normalized_reference(reference: Vec3, location: Location | None) -> Vec3:
    magnitude = reference.norm()
    if magnitude < sys.float_info.min or not math.isfinite(magnitude):  # Nula o subnormal.
        raise _error(
            DiagnosticCode.GEOMETRY_INVALID_ORIENTATION,
            "geometry.reference_invalid",
            None,
            location,
        )
    return Vec3(reference.x / magnitude, reference.y / magnitude, reference.z / magnitude)


def _projection(rhat: Vec3, ex: Vec3) -> Vec3:
    """Componente de rhat perpendicular a ex (una pasada)."""
    return rhat - ex.scaled(rhat.dot(ex))


def _build(
    ex: Vec3, length: float, orientation: Orientation, w: Vec3, location: Location | None
) -> LocalAxes:
    """Pasos 3, 4 y 6 a partir de la proyección w ya comprobada frente al umbral."""
    w = w - ex.scaled(w.dot(ex))  # Segunda pasada de Gram-Schmidt (ADR-001 §1, paso 3).
    ez0 = w.scaled(1.0 / w.norm())
    ey0 = ez0.cross(ex)
    cos_a, sin_a = math.cos(orientation.alpha), math.sin(orientation.alpha)
    ey = ey0.scaled(cos_a) + ez0.scaled(sin_a)
    ez = ey0.scaled(-sin_a) + ez0.scaled(cos_a)
    axes = LocalAxes(ex, ey, ez, length, orientation)
    _check_orthonormal(axes, location)
    return axes


def _check_orthonormal(axes: LocalAxes, location: Location | None) -> None:
    rows = (axes.ex, axes.ey, axes.ez)
    deviation = max(
        abs(a.dot(b) - (1.0 if i == j else 0.0))
        for i, a in enumerate(rows)
        for j, b in enumerate(rows)
    )
    determinant = axes.ex.dot(axes.ey.cross(axes.ez))
    if deviation > ORTHONORMALITY_TOLERANCE or determinant <= 0.0:
        raise _error(
            DiagnosticCode.GEOMETRY_INVALID_ORIENTATION,
            "geometry.basis_not_orthonormal",
            {"deviation": deviation, "determinant": determinant},
            location,
        )


def compute_local_axes(
    p_i: Vec3,
    p_j: Vec3,
    *,
    reference: Vec3 | None = None,
    alpha: float = 0.0,
    location: Location | None = None,
) -> LocalAxes:
    """Base local al crear o reorientar una barra.

    Sin ``reference``: global +Z y, si la proyección es <= 1e-8, global -X. Con
    ``reference`` explícita (dirección local +z): sin sustitución; casi paralela se rechaza.
    """
    ex, length = _axis(p_i, p_j, location)
    alpha = validate_alpha(alpha, location)
    if reference is None:
        w = _projection(DEFAULT_REFERENCE, ex)
        if w.norm() > ORIENTATION_TOLERANCE:
            orientation = Orientation(DEFAULT_REFERENCE, ReferenceSource.DEFAULT_Z, alpha)
        else:
            w = _projection(FALLBACK_REFERENCE, ex)
            if w.norm() <= ORIENTATION_TOLERANCE:  # Imposible: ex no es paralela a Z y a X.
                raise _error(
                    DiagnosticCode.GEOMETRY_INVALID_ORIENTATION,
                    "geometry.reference_parallel",
                    {"projection": w.norm(), "tolerance": ORIENTATION_TOLERANCE},
                    location,
                )
            orientation = Orientation(FALLBACK_REFERENCE, ReferenceSource.FALLBACK_NEG_X, alpha)
        return _build(ex, length, orientation, w, location)
    rhat = _normalized_reference(reference, location)
    w = _projection(rhat, ex)
    if w.norm() <= ORIENTATION_TOLERANCE:
        raise _error(
            DiagnosticCode.GEOMETRY_INVALID_ORIENTATION,
            "geometry.reference_parallel",
            {"projection": w.norm(), "tolerance": ORIENTATION_TOLERANCE},
            location,
        )
    return _build(ex, length, Orientation(rhat, ReferenceSource.EXPLICIT, alpha), w, location)


def recompute_local_axes(
    p_i: Vec3,
    p_j: Vec3,
    orientation: Orientation,
    *,
    location: Location | None = None,
) -> LocalAxes:
    """Base local con la orientación guardada tras editar extremos (ADR-001 §1, paso 5).

    No activa ningún fallback: si la referencia guardada degenera se rechaza y el usuario
    debe asignar una orientación explícita.
    """
    ex, length = _axis(p_i, p_j, location)
    w = _projection(orientation.reference, ex)
    if w.norm() <= ORIENTATION_TOLERANCE:
        raise _error(
            DiagnosticCode.GEOMETRY_INVALID_ORIENTATION,
            "geometry.stored_reference_degenerate",
            {"projection": w.norm(), "tolerance": ORIENTATION_TOLERANCE},
            location,
        )
    return _build(ex, length, orientation, w, location)
