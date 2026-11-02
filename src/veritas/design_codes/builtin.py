"""Configuración protegida del CTE y cobertura de verificación (ADR-007; plan T-014, D3 y D5).

La configuración protegida es una **plantilla de identificación**: sistema, jurisdicción y
documentos del CTE, con la edición «NO FIJADA» y **sin ningún parámetro numérico**. No hay
texto normativo verificado en ``docs/references/`` (TK-007) y DEC-002 fija la edición
consolidada antes de codificar fórmulas (F7A): cualquier coeficiente aquí sería inventado.
Se copia y se completa como configuración de usuario; F7A añadirá una revisión protegida
nueva cuando fije la edición.

La cobertura nunca es un dato de la configuración: la decide ``coverage`` a partir del
código. Mientras no exista un plugin de comprobación registrado para (sistema, edición,
familia) —hoy no hay ninguno—, todo es ``NO_COMPROBADO`` (NOR-004, NOR-010).
"""

from enum import StrEnum
from typing import Final
from uuid import UUID

from veritas.core.materials.material import MaterialFamily
from veritas.core.validation import invalid_type
from veritas.design_codes.norms import NormConfiguration, NormKind, NormSystem

CTE_PROTECTED_ID: Final = UUID("5e0f6c1a-3b7d-4c52-9a41-7d2e8b6f0c13")

CTE_PROTECTED: Final = NormConfiguration(
    id=CTE_PROTECTED_ID,
    revision=1,
    name="CTE España (DB-SE, DB-SE-AE, DB-SE-A, DB-SE-M)",
    kind=NormKind.PROTECTED,
    system=NormSystem.CTE,
    jurisdiction="España",
    edition="NO FIJADA",
    documents=("DB-SE", "DB-SE-AE", "DB-SE-A", "DB-SE-M"),
    description=(
        "Plantilla de identificación del Código Técnico de la Edificación. Sin edición fijada "
        "y sin parámetros: copiarla y completarla con la fuente de cada valor."
    ),
    derived_from=None,
    parameters=(),
)


def protected_configurations() -> tuple[NormConfiguration, ...]:
    """Configuraciones protegidas que trae la aplicación (revisión vigente de cada una)."""
    return (CTE_PROTECTED,)


class Coverage(StrEnum):
    """Estado de cobertura de verificación. Hoy solo existe ``NO_COMPROBADO``.

    Un estado distinto llegará con el primer plugin de comprobación (F7A) y solo lo podrá
    devolver ``coverage``; ninguna configuración puede declararlo.
    """

    NO_COMPROBADO = "NO_COMPROBADO"


def coverage(config: NormConfiguration, family: MaterialFamily) -> Coverage:
    """Cobertura de ``config`` para ``family``: siempre ``NO_COMPROBADO`` sin plugin."""
    if not isinstance(config, NormConfiguration):
        raise invalid_type("config", "NormConfiguration", config)
    if not isinstance(family, MaterialFamily):
        raise invalid_type("family", "MaterialFamily", family)
    return Coverage.NO_COMPROBADO
