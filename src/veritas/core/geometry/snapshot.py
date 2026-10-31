"""Vista de lectura del modelo analítico para persistir y restaurar (plan T-012, D3-A).

``ModelSnapshot`` agrupa, sin copiar lógica de dominio, las entidades inmutables del modelo
con sus identidades y revisiones: la revisión actual de nudos, barras, apoyos, casos y
cargas (estas en orden de creación) y TODAS las revisiones de material y de sección, más el
contador ``model_revision``. ``StructuralModel.snapshot()`` lo produce y
``StructuralModel.restore()`` lo valida y reconstruye (todo o nada).
"""

from dataclasses import dataclass

from veritas.core.geometry.entities import Member, Node
from veritas.core.loads.load_case import LoadCase
from veritas.core.loads.loads import Load
from veritas.core.materials.material import MaterialRevision
from veritas.core.sections.section import SectionRevision
from veritas.core.supports.support import Support


@dataclass(frozen=True, slots=True)
class ModelSnapshot:
    """Contenido completo y ordenado de un ``StructuralModel``."""

    nodes: tuple[Node, ...] = ()
    members: tuple[Member, ...] = ()
    materials: tuple[MaterialRevision, ...] = ()
    sections: tuple[SectionRevision, ...] = ()
    supports: tuple[Support, ...] = ()
    load_cases: tuple[LoadCase, ...] = ()
    loads: tuple[Load, ...] = ()
    model_revision: int = 0
