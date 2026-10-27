# Veritas Structural Calculation 3D

Aplicación experimental de estructuras de barras y nudos 3D (Python, PySide6, PyVista y OpenSeesPy).

> **Aviso:** aplicación experimental. No apta para justificar obras reales sin validación independiente por profesionales competentes.

**Estado:** fase F0, ticket T-001 (repositorio y entorno). Todavía no hay cálculo, interfaz gráfica ni análisis FEM. La verificación normativa figura como `NO_COMPROBADA`. Estado de fases en [docs/ROADMAP.md](docs/ROADMAP.md) y de tickets en [tasks/BACKLOG.md](tasks/BACKLOG.md).

## Requisitos

- Windows 11 x64.
- Python 3.12 x64 (`py -0p` debe listar 3.12). El proyecto fija 3.12 porque OpenSeesPy para Windows solo publica soporte para esa versión.
- [uv](https://docs.astral.sh/uv/) para el entorno y el lockfile (`uv.lock`).

## Instalación y demo (Windows PowerShell)

```powershell
cd D:\Coding\SAPE\Veritas
uv sync                      # crea .venv con Python 3.12 y las versiones de uv.lock
uv run veritas info          # versión, entorno y estado de módulos
uv run veritas --version     # veritas 0.0.1
uv run veritas desconocido   # uso incorrecto: mensaje en stderr
$LASTEXITCODE                # 2
```

## Comprobaciones de calidad

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## Estructura

- `src/veritas/`: paquete de la aplicación; un subpaquete por capa (`core`, `fem`, `gui`…), según [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- `tests/`: pruebas automáticas.
- `docs/`: decisiones, arquitectura, roadmap, validación y referencias (SRS v1.1.0).
- `tasks/`, `plans/`, `handoffs/`: backlog, planes de ticket y checkpoints de sesión.
- `.claude/agents/`: subagentes de Claude Code ([docs/AGENT_POLICY.md](docs/AGENT_POLICY.md)).
- `_concept/`: material conceptual original, archivo local excluido del repositorio (`.gitignore`). La versión de referencia vigente está en `docs/references/`.

Licencia: pendiente (DEC-006). No redistribuir.
