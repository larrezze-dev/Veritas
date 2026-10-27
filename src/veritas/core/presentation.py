"""Perfiles de unidades de presentación y formato de magnitudes.

La presentación nunca modifica el valor interno SI. Cada ``QuantityKind`` fija la
semántica de una magnitud: carga lineal y rigidez traslacional comparten dimensión
pero se presentan por separado.

Los perfiles predefinidos (``SI``, ``kN-m``, ``N-mm``) están protegidos: un perfil de
usuario se crea con ``derive`` y no puede reutilizar sus nombres. La persistencia de
perfiles de usuario queda para el ticket de persistencia.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from veritas.core import units as u
from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputTypeError, InputValueError
from veritas.core.messages_es import QUANTITY_KIND_LABELS_ES


class QuantityKind(StrEnum):
    """Magnitud semántica con dimensión asociada."""

    LENGTH = "LENGTH"
    AREA = "AREA"
    SECTION_MODULUS = "SECTION_MODULUS"
    SECOND_MOMENT = "SECOND_MOMENT"
    WARPING_CONSTANT = "WARPING_CONSTANT"
    MASS = "MASS"
    DENSITY = "DENSITY"
    LINEAR_MASS = "LINEAR_MASS"
    ACCELERATION = "ACCELERATION"
    FORCE = "FORCE"
    FORCE_PER_LENGTH = "FORCE_PER_LENGTH"
    TRANSLATIONAL_STIFFNESS = "TRANSLATIONAL_STIFFNESS"
    STRESS = "STRESS"
    MOMENT = "MOMENT"
    ANGLE = "ANGLE"
    ROTATIONAL_STIFFNESS = "ROTATIONAL_STIFFNESS"
    DIMENSIONLESS = "DIMENSIONLESS"

    @property
    def dimension(self) -> u.Dimension:
        return _KIND_DIMENSIONS[self]

    @property
    def label(self) -> str:
        return QUANTITY_KIND_LABELS_ES[self.value]


_KIND_DIMENSIONS: Final[dict[QuantityKind, u.Dimension]] = {
    QuantityKind.LENGTH: u.LENGTH,
    QuantityKind.AREA: u.AREA,
    QuantityKind.SECTION_MODULUS: u.SECTION_MODULUS,
    QuantityKind.SECOND_MOMENT: u.SECOND_MOMENT,
    QuantityKind.WARPING_CONSTANT: u.WARPING_CONSTANT,
    QuantityKind.MASS: u.MASS,
    QuantityKind.DENSITY: u.DENSITY,
    QuantityKind.LINEAR_MASS: u.LINEAR_MASS,
    QuantityKind.ACCELERATION: u.ACCELERATION,
    QuantityKind.FORCE: u.FORCE,
    QuantityKind.FORCE_PER_LENGTH: u.FORCE_PER_LENGTH,
    QuantityKind.TRANSLATIONAL_STIFFNESS: u.FORCE_PER_LENGTH,
    QuantityKind.STRESS: u.STRESS,
    QuantityKind.MOMENT: u.MOMENT,
    QuantityKind.ANGLE: u.ANGLE,
    QuantityKind.ROTATIONAL_STIFFNESS: u.ROTATIONAL_STIFFNESS,
    QuantityKind.DIMENSIONLESS: u.DIMENSIONLESS,
}

MAX_DECIMALS: Final = 15

_RESERVED_NAMES: Final = frozenset({"SI", "kN-m", "N-mm"})
_creating_builtins = False  # Solo ``_builtin`` puede crear perfiles predefinidos.


def _config_error(message_key: str, params: Mapping[str, str | int]) -> InputValueError:
    return InputValueError(
        Diagnostic.create(DiagnosticCode.UNSUPPORTED_CONFIGURATION, message_key, params)
    )


def _type_error(field: str, expected: str, received: object) -> InputTypeError:
    return InputTypeError(
        Diagnostic.create(
            DiagnosticCode.INPUT_INVALID_TYPE,
            "input.invalid_type",
            {"field": field, "expected": expected, "received": type(received).__name__},
        )
    )


@dataclass(frozen=True, slots=True)
class PresentationProfile:
    """Unidad de presentación para cada ``QuantityKind``. Inmutable y completo."""

    name: str
    units: tuple[tuple[QuantityKind, u.Unit], ...]
    builtin: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise _type_error("PresentationProfile.name", "str", self.name)
        if not self.name.strip() or self.name.strip() != self.name:
            raise _config_error("presentation.invalid_name", {"profile": self.name})
        if (self.builtin or self.name in _RESERVED_NAMES) and not _creating_builtins:
            raise _config_error("presentation.reserved_name", {"profile": self.name})
        defined = dict(self.units)
        if len(defined) != len(self.units):
            raise _config_error("presentation.duplicate_kind", {"profile": self.name})
        missing = [kind for kind in QuantityKind if kind not in defined]
        if missing:
            raise _config_error(
                "presentation.incomplete_profile",
                {"profile": self.name, "missing": ", ".join(missing)},
            )
        for kind, unit in self.units:
            if not isinstance(unit, u.Unit):
                raise _type_error(f"unidad de {kind}", "Unit", unit)
            if unit.dimension != kind.dimension:
                raise InputValueError(
                    Diagnostic.create(
                        DiagnosticCode.UNITS_INCOMPATIBLE,
                        "presentation.unit_kind_mismatch",
                        {"unit": unit.symbol, "kind": kind.label, "profile": self.name},
                    )
                )
        ordered = tuple((kind, defined[kind]) for kind in QuantityKind)
        object.__setattr__(self, "units", ordered)

    def unit_for(self, kind: QuantityKind) -> u.Unit:
        """Unidad de presentación de ``kind``."""
        return dict(self.units)[kind]

    def derive(
        self, name: str, changes: Mapping[QuantityKind, "str | u.Unit"]
    ) -> "PresentationProfile":
        """Nuevo perfil de usuario basado en este, con las unidades indicadas cambiadas."""
        mapping = dict(self.units)
        for kind, unit in changes.items():
            if not isinstance(kind, QuantityKind):
                raise _type_error("magnitud", "QuantityKind", kind)
            mapping[kind] = u.get_unit(unit)
        return PresentationProfile(name, tuple(mapping.items()))


def _builtin(name: str, symbols: Mapping[QuantityKind, str]) -> PresentationProfile:
    global _creating_builtins  # noqa: PLW0603 (habilita la creación solo aquí)
    _creating_builtins = True
    try:
        return PresentationProfile(
            name,
            tuple((kind, u.get_unit(symbol)) for kind, symbol in symbols.items()),
            builtin=True,
        )
    finally:
        _creating_builtins = False


PROFILE_SI: Final = _builtin(
    "SI",
    {
        QuantityKind.LENGTH: "m",
        QuantityKind.AREA: "m²",
        QuantityKind.SECTION_MODULUS: "m³",
        QuantityKind.SECOND_MOMENT: "m⁴",
        QuantityKind.WARPING_CONSTANT: "m⁶",
        QuantityKind.MASS: "kg",
        QuantityKind.DENSITY: "kg/m³",
        QuantityKind.LINEAR_MASS: "kg/m",
        QuantityKind.ACCELERATION: "m/s²",
        QuantityKind.FORCE: "N",
        QuantityKind.FORCE_PER_LENGTH: "N/m",
        QuantityKind.TRANSLATIONAL_STIFFNESS: "N/m",
        QuantityKind.STRESS: "Pa",
        QuantityKind.MOMENT: "N·m",
        QuantityKind.ANGLE: "rad",
        QuantityKind.ROTATIONAL_STIFFNESS: "N·m/rad",
        QuantityKind.DIMENSIONLESS: "1",
    },
)

PROFILE_KN_M: Final = _builtin(
    "kN-m",
    {
        QuantityKind.LENGTH: "m",
        QuantityKind.AREA: "cm²",
        QuantityKind.SECTION_MODULUS: "cm³",
        QuantityKind.SECOND_MOMENT: "cm⁴",
        QuantityKind.WARPING_CONSTANT: "cm⁶",
        QuantityKind.MASS: "kg",
        QuantityKind.DENSITY: "kg/m³",
        QuantityKind.LINEAR_MASS: "kg/m",
        QuantityKind.ACCELERATION: "m/s²",
        QuantityKind.FORCE: "kN",
        QuantityKind.FORCE_PER_LENGTH: "kN/m",
        QuantityKind.TRANSLATIONAL_STIFFNESS: "kN/m",
        QuantityKind.STRESS: "N/mm²",
        QuantityKind.MOMENT: "kN·m",
        QuantityKind.ANGLE: "rad",
        QuantityKind.ROTATIONAL_STIFFNESS: "kN·m/rad",
        QuantityKind.DIMENSIONLESS: "1",
    },
)

PROFILE_N_MM: Final = _builtin(
    "N-mm",
    {
        QuantityKind.LENGTH: "mm",
        QuantityKind.AREA: "mm²",
        QuantityKind.SECTION_MODULUS: "mm³",
        QuantityKind.SECOND_MOMENT: "mm⁴",
        QuantityKind.WARPING_CONSTANT: "mm⁶",
        QuantityKind.MASS: "kg",
        QuantityKind.DENSITY: "kg/m³",
        QuantityKind.LINEAR_MASS: "kg/m",
        QuantityKind.ACCELERATION: "m/s²",
        QuantityKind.FORCE: "N",
        QuantityKind.FORCE_PER_LENGTH: "N/mm",
        QuantityKind.TRANSLATIONAL_STIFFNESS: "N/mm",
        QuantityKind.STRESS: "N/mm²",
        QuantityKind.MOMENT: "N·mm",
        QuantityKind.ANGLE: "rad",
        QuantityKind.ROTATIONAL_STIFFNESS: "N·mm/rad",
        QuantityKind.DIMENSIONLESS: "1",
    },
)

BUILTIN_PROFILES: Final[tuple[PresentationProfile, ...]] = (
    PROFILE_SI,
    PROFILE_KN_M,
    PROFILE_N_MM,
)
BUILTIN_PROFILE_NAMES: Final = _RESERVED_NAMES


def format_quantity(
    quantity: u.Quantity,
    kind: QuantityKind,
    profile: PresentationProfile = PROFILE_SI,
    decimals: int = 3,
) -> str:
    """Texto ``"<valor> <unidad>"`` en la unidad del perfil para ``kind``.

    Usa ``decimals`` decimales fijos. Un valor no nulo que se redondearía a cero se
    muestra en notación científica con ``decimals`` decimales: nunca se presenta un cero
    que no existe (ADR-001 §5). El cero negativo se muestra como cero.
    """
    if not isinstance(quantity, u.Quantity):
        raise _type_error("magnitud", "Quantity", quantity)
    if not isinstance(kind, QuantityKind):
        raise _type_error("tipo de magnitud", "QuantityKind", kind)
    if not isinstance(profile, PresentationProfile):
        raise _type_error("perfil", "PresentationProfile", profile)
    if isinstance(decimals, bool) or not isinstance(decimals, int):
        raise _type_error("decimales", "int", decimals)
    if not 0 <= decimals <= MAX_DECIMALS:
        raise _config_error("presentation.invalid_decimals", {"decimals": decimals})
    quantity.require(kind.dimension)
    unit = profile.unit_for(kind)
    value = quantity.to(unit)
    if value == 0.0:
        text = f"{0.0:.{decimals}f}"
    else:
        text = f"{value:.{decimals}f}"
        if not any(char in "123456789" for char in text):
            text = f"{value:.{decimals}e}"
    return f"{text} {unit.symbol}"
