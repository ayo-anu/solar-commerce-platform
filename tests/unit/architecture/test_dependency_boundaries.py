"""Enforce the dependency boundaries accepted in ADR-002."""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_PACKAGE = "solar_platform"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE_PACKAGE_ROOT = PROJECT_ROOT / "src" / PROJECT_PACKAGE

STANDARD_RESPONSIBILITIES = frozenset(
    {
        "domain",
        "application",
        "inbound_api",
        "outbound_infrastructure",
        "composition",
    }
)
ALLOWED_RESPONSIBILITY_IMPORTS = {
    "domain": frozenset({"domain"}),
    "application": frozenset({"application", "domain"}),
    "inbound_api": frozenset({"inbound_api", "application"}),
    "outbound_infrastructure": frozenset(
        {"outbound_infrastructure", "application", "domain"}
    ),
    "composition": STANDARD_RESPONSIBILITIES | frozenset({"configuration"}),
    "configuration": frozenset({"configuration"}),
    "package_initializer": frozenset(),
}


class ArchitectureError(AssertionError):
    """Report one or more violations of the accepted architecture."""


@dataclass(frozen=True)
class ModuleClassification:
    match_kind: str
    pattern: str
    capability: str
    responsibility: str
    visibility: str
    rationale: str = ""

    def matches(self, module: str) -> bool:
        if self.match_kind == "exact":
            return module == self.pattern
        return module == self.pattern or module.startswith(f"{self.pattern}.")


@dataclass(frozen=True)
class ThirdPartyImportPermission:
    responsibility: str
    top_level_package: str
    decision_reference: str
    rationale: str


@dataclass(frozen=True)
class SourceModule:
    name: str
    source: str
    is_package: bool = False


@dataclass(frozen=True)
class ImportReference:
    target: str
    line: int


REAL_MODULE_CLASSIFICATIONS = (
    ModuleClassification(
        match_kind="exact",
        pattern="solar_platform",
        capability="platform_root",
        responsibility="package_initializer",
        visibility="private",
        rationale="Existing side-effect-free root package initializer.",
    ),
    ModuleClassification(
        match_kind="exact",
        pattern="solar_platform.app",
        capability="platform_root",
        responsibility="composition",
        visibility="private",
        rationale=(
            "B2.2.2 outer composition root for the authorized FastAPI/settings "
            "foundation."
        ),
    ),
    ModuleClassification(
        match_kind="exact",
        pattern="solar_platform.settings",
        capability="platform_root",
        responsibility="configuration",
        visibility="private",
        rationale=(
            "B2.2.2 outer process-configuration boundary kept outside domain "
            "and application modules."
        ),
    ),
    ModuleClassification(
        match_kind="exact",
        pattern="solar_platform.logging_config",
        capability="platform_logging",
        responsibility="outbound_infrastructure",
        visibility="private",
        rationale="B2.2.6 structured JSON logging output boundary.",
    ),
    ModuleClassification(
        match_kind="exact",
        pattern="solar_platform.api",
        capability="platform_http",
        responsibility="package_initializer",
        visibility="private",
        rationale="Side-effect-free private HTTP-edge package initializer.",
    ),
    ModuleClassification(
        match_kind="exact",
        pattern="solar_platform.api.correlation",
        capability="platform_http",
        responsibility="inbound_api",
        visibility="private",
        rationale="B2.2.4 request-correlation transport boundary.",
    ),
    ModuleClassification(
        match_kind="exact",
        pattern="solar_platform.api.health",
        capability="platform_http",
        responsibility="inbound_api",
        visibility="private",
        rationale="B2.2.5 database-independent process-liveness endpoint.",
    ),
    ModuleClassification(
        match_kind="exact",
        pattern="solar_platform.api.problems",
        capability="platform_http",
        responsibility="inbound_api",
        visibility="private",
        rationale="B2.2.4 RFC 9457 and framework-error translation boundary.",
    ),
    ModuleClassification(
        match_kind="exact",
        pattern="solar_platform.api.request_logging",
        capability="platform_http",
        responsibility="inbound_api",
        visibility="private",
        rationale="B2.2.6 disclosure-safe terminal request-event boundary.",
    ),
)

# Architecture permission only; an entry never authorizes adding a dependency.
THIRD_PARTY_IMPORT_ALLOWLIST = (
    ThirdPartyImportPermission(
        responsibility="composition",
        top_level_package="fastapi",
        decision_reference="ADR-002; accepted B2.2.1; approved B2.2.2",
        rationale="The composition root constructs the authorized FastAPI app.",
    ),
    ThirdPartyImportPermission(
        responsibility="configuration",
        top_level_package="pydantic",
        decision_reference="ADR-002; accepted B2.2.1; approved B2.2.2",
        rationale="The settings boundary validates and freezes configuration.",
    ),
    ThirdPartyImportPermission(
        responsibility="inbound_api",
        top_level_package="fastapi",
        decision_reference="ADR-002; ADR-011; accepted B2.2.1; approved B2.2.4",
        rationale="The HTTP edge uses the authorized FastAPI integration surface.",
    ),
    ThirdPartyImportPermission(
        responsibility="inbound_api",
        top_level_package="pydantic",
        decision_reference="ADR-002; ADR-011; accepted B2.2.1; approved B2.2.4",
        rationale="The HTTP edge owns and serializes its Problem Details DTOs.",
    ),
    ThirdPartyImportPermission(
        responsibility="inbound_api",
        top_level_package="starlette",
        decision_reference="ADR-002; ADR-011; approved B2.2.4",
        rationale=(
            "The HTTP edge registers Starlette's documented HTTPException base."
        ),
    ),
)


def _scopes_overlap(left: ModuleClassification, right: ModuleClassification) -> bool:
    return left.matches(right.pattern) or right.matches(left.pattern)


def _validate_declarations(
    classifications: tuple[ModuleClassification, ...],
    permissions: tuple[ThirdPartyImportPermission, ...],
) -> None:
    errors: list[str] = []
    for classification in classifications:
        if classification.match_kind not in {"exact", "prefix"}:
            errors.append(
                f"invalid match kind for {classification.pattern}: "
                f"{classification.match_kind}"
            )
        if classification.visibility not in {"private", "published_contract"}:
            errors.append(
                f"invalid visibility for {classification.pattern}: "
                f"{classification.visibility}"
            )
        if not classification.capability:
            errors.append(f"missing capability for {classification.pattern}")
        requires_rationale = (
            classification.visibility == "published_contract"
            or classification.responsibility not in STANDARD_RESPONSIBILITIES
        )
        if requires_rationale and not classification.rationale:
            errors.append(f"missing required rationale for {classification.pattern}")

    for index, left in enumerate(classifications):
        for right in classifications[index + 1 :]:
            if _scopes_overlap(left, right):
                errors.append(
                    "overlapping classifications: "
                    f"{left.pattern} ({left.match_kind}) and "
                    f"{right.pattern} ({right.match_kind})"
                )

    for permission in permissions:
        if not permission.decision_reference or not permission.rationale:
            errors.append(
                "third-party permission requires decision reference and rationale: "
                f"{permission.responsibility}/{permission.top_level_package}"
            )

    if errors:
        raise ArchitectureError("\n".join(errors))


def _classify_modules(
    modules: tuple[SourceModule, ...],
    classifications: tuple[ModuleClassification, ...],
) -> dict[str, ModuleClassification]:
    resolved: dict[str, ModuleClassification] = {}
    errors: list[str] = []
    for module in modules:
        matches = [item for item in classifications if item.matches(module.name)]
        if not matches:
            errors.append(f"unclassified module: {module.name}")
        elif len(matches) > 1:
            patterns = ", ".join(item.pattern for item in matches)
            errors.append(f"ambiguous classification for {module.name}: {patterns}")
        else:
            resolved[module.name] = matches[0]
    if errors:
        raise ArchitectureError("\n".join(errors))
    return resolved


def _module_name(path: Path) -> tuple[str, bool]:
    relative = path.relative_to(SOURCE_PACKAGE_ROOT)
    is_package = relative.name == "__init__.py"
    parts = relative.parent.parts if is_package else relative.with_suffix("").parts
    name_parts = (PROJECT_PACKAGE, *parts)
    return ".".join(name_parts), is_package


def _load_real_modules() -> tuple[SourceModule, ...]:
    modules = []
    for path in sorted(SOURCE_PACKAGE_ROOT.rglob("*.py")):
        name, is_package = _module_name(path)
        modules.append(SourceModule(name, path.read_text(), is_package))
    return tuple(modules)


def _from_base(module: SourceModule, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = module.name if module.is_package else module.name.rpartition(".")[0]
    parts = package.split(".")
    parents_to_remove = node.level - 1
    if parents_to_remove >= len(parts):
        return ""
    base_parts = parts[: len(parts) - parents_to_remove]
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def _imports_for(
    module: SourceModule, known_modules: frozenset[str]
) -> tuple[ImportReference, ...]:
    try:
        tree = ast.parse(module.source, filename=module.name)
    except SyntaxError as error:
        raise ArchitectureError(f"cannot parse {module.name}: {error}") from error

    imports: list[ImportReference] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(
                ImportReference(alias.name, node.lineno) for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            base = _from_base(module, node)
            for alias in node.names:
                candidate = f"{base}.{alias.name}" if base else alias.name
                target = candidate if candidate in known_modules else base
                imports.append(ImportReference(target, node.lineno))
    return tuple(imports)


def _import_origin(target: str) -> str:
    top_level = target.partition(".")[0]
    if top_level == PROJECT_PACKAGE:
        return "internal"
    if top_level in sys.stdlib_module_names:
        return "standard_library"
    return "third_party"


def _find_cycle(graph: dict[str, set[str]]) -> tuple[str, ...] | None:
    visited: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()

    def visit(module: str) -> tuple[str, ...] | None:
        if module in active_set:
            start = active.index(module)
            return (*active[start:], module)
        if module in visited:
            return None
        active.append(module)
        active_set.add(module)
        for target in sorted(graph[module]):
            cycle = visit(target)
            if cycle is not None:
                return cycle
        active.pop()
        active_set.remove(module)
        visited.add(module)
        return None

    for module in sorted(graph):
        cycle = visit(module)
        if cycle is not None:
            return cycle
    return None


def assert_architecture(
    modules: tuple[SourceModule, ...],
    classifications: tuple[ModuleClassification, ...],
    permissions: tuple[ThirdPartyImportPermission, ...] = (),
) -> None:
    """Raise with clear diagnostics when modules violate ADR-002."""
    _validate_declarations(classifications, permissions)
    resolved = _classify_modules(modules, classifications)
    known_modules = frozenset(resolved)
    graph: dict[str, set[str]] = {name: set() for name in known_modules}
    errors: list[str] = []

    for module in modules:
        source_class = resolved[module.name]
        for imported in _imports_for(module, known_modules):
            origin = _import_origin(imported.target)
            location = f"{module.name}:{imported.line}"
            if origin == "standard_library":
                continue
            if origin == "third_party":
                top_level = imported.target.partition(".")[0]
                allowed = any(
                    permission.responsibility == source_class.responsibility
                    and permission.top_level_package == top_level
                    for permission in permissions
                )
                if not allowed:
                    errors.append(
                        f"unauthorized third-party import at {location}: {top_level} "
                        f"from {source_class.responsibility}"
                    )
                continue

            if imported.target not in resolved:
                errors.append(
                    f"internal import target is not a real classified module at "
                    f"{location}: {imported.target}"
                )
                continue
            target_class = resolved[imported.target]
            graph[module.name].add(imported.target)
            allowed_targets = ALLOWED_RESPONSIBILITY_IMPORTS.get(
                source_class.responsibility, frozenset()
            )
            if target_class.responsibility not in allowed_targets:
                errors.append(
                    f"forbidden responsibility import at {location}: "
                    f"{source_class.responsibility} -> "
                    f"{target_class.responsibility} ({imported.target})"
                )
            if (
                source_class.responsibility != "composition"
                and source_class.capability != target_class.capability
                and target_class.visibility != "published_contract"
            ):
                errors.append(
                    f"cross-capability private import at {location}: "
                    f"{source_class.capability} -> {target_class.capability} "
                    f"({imported.target})"
                )

    cycle = _find_cycle(graph)
    if cycle is not None:
        errors.append(f"static dependency cycle: {' -> '.join(cycle)}")
    if errors:
        raise ArchitectureError("\n".join(errors))


def _classification(
    module: str,
    capability: str,
    responsibility: str,
    visibility: str = "private",
    rationale: str = "",
) -> ModuleClassification:
    return ModuleClassification(
        "exact", module, capability, responsibility, visibility, rationale
    )


def test_real_source_tree_satisfies_accepted_architecture() -> None:
    modules = _load_real_modules()
    assert modules
    assert any(module.name == "solar_platform" for module in modules)
    assert_architecture(
        modules, REAL_MODULE_CLASSIFICATIONS, THIRD_PARTY_IMPORT_ALLOWLIST
    )


def test_allowed_fixture_graph_and_published_contract_pass() -> None:
    modules = (
        SourceModule("solar_platform.catalogue.domain", "import dataclasses\n"),
        SourceModule(
            "solar_platform.catalogue.application",
            "from . import domain\nimport solar_platform.catalogue.domain\n",
        ),
        SourceModule(
            "solar_platform.catalogue.api",
            "def route():\n    from . import application\n",
        ),
        SourceModule(
            "solar_platform.catalogue.infrastructure",
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n    from . import application\n",
        ),
        SourceModule(
            "solar_platform.orders.application",
            "from solar_platform.catalogue import application\n",
        ),
    )
    classifications = (
        _classification("solar_platform.catalogue.domain", "catalogue", "domain"),
        _classification(
            "solar_platform.catalogue.application",
            "catalogue",
            "application",
            "published_contract",
            "Reviewed catalogue application contract.",
        ),
        _classification("solar_platform.catalogue.api", "catalogue", "inbound_api"),
        _classification(
            "solar_platform.catalogue.infrastructure",
            "catalogue",
            "outbound_infrastructure",
        ),
        _classification("solar_platform.orders.application", "orders", "application"),
    )
    assert_architecture(modules, classifications)


def test_composition_can_wire_cross_capability_private_components() -> None:
    modules = (
        SourceModule(
            "solar_platform.bootstrap",
            "from solar_platform.catalogue import application, api, infrastructure\n",
        ),
        SourceModule("solar_platform.catalogue.application", ""),
        SourceModule("solar_platform.catalogue.api", ""),
        SourceModule("solar_platform.catalogue.infrastructure", ""),
    )
    classifications = (
        _classification(
            "solar_platform.bootstrap", "platform_root", "composition", "private"
        ),
        _classification(
            "solar_platform.catalogue.application", "catalogue", "application"
        ),
        _classification("solar_platform.catalogue.api", "catalogue", "inbound_api"),
        _classification(
            "solar_platform.catalogue.infrastructure",
            "catalogue",
            "outbound_infrastructure",
        ),
    )
    assert_architecture(modules, classifications)


def test_composition_can_import_configuration() -> None:
    modules = (
        SourceModule(
            "solar_platform.bootstrap", "from solar_platform import settings\n"
        ),
        SourceModule("solar_platform.settings", ""),
    )
    classifications = (
        _classification(
            "solar_platform.bootstrap", "platform_root", "composition", "private"
        ),
        _classification(
            "solar_platform.settings",
            "platform_root",
            "configuration",
            "private",
            "Fixture configuration boundary.",
        ),
    )
    assert_architecture(modules, classifications)


def test_composition_can_wire_private_logging_boundaries() -> None:
    modules = (
        SourceModule(
            "solar_platform.bootstrap",
            "from solar_platform import logging_config\n"
            "from solar_platform.api import request_logging\n",
        ),
        SourceModule("solar_platform.logging_config", ""),
        SourceModule("solar_platform.api.request_logging", ""),
    )
    classifications = (
        _classification(
            "solar_platform.bootstrap", "platform_root", "composition", "private"
        ),
        _classification(
            "solar_platform.logging_config",
            "platform_logging",
            "outbound_infrastructure",
        ),
        _classification(
            "solar_platform.api.request_logging",
            "platform_http",
            "inbound_api",
        ),
    )
    assert_architecture(modules, classifications)


@pytest.mark.parametrize("source_role", ("domain", "application"))
def test_inward_responsibilities_cannot_import_configuration(
    source_role: str,
) -> None:
    modules = (
        SourceModule("solar_platform.feature.source", "from . import settings\n"),
        SourceModule("solar_platform.feature.settings", ""),
    )
    classifications = (
        _classification("solar_platform.feature.source", "feature", source_role),
        _classification(
            "solar_platform.feature.settings",
            "feature",
            "configuration",
            rationale="Fixture configuration boundary.",
        ),
    )
    with pytest.raises(ArchitectureError, match="forbidden responsibility import"):
        assert_architecture(modules, classifications)


@pytest.mark.parametrize("target_role", ("domain", "application", "composition"))
def test_configuration_cannot_import_inward_responsibilities_or_composition(
    target_role: str,
) -> None:
    modules = (
        SourceModule("solar_platform.feature.settings", "from . import target\n"),
        SourceModule("solar_platform.feature.target", ""),
    )
    classifications = (
        _classification(
            "solar_platform.feature.settings",
            "feature",
            "configuration",
            rationale="Fixture configuration boundary.",
        ),
        _classification("solar_platform.feature.target", "feature", target_role),
    )
    with pytest.raises(ArchitectureError, match="forbidden responsibility import"):
        assert_architecture(modules, classifications)


@pytest.mark.parametrize(
    ("source_role", "target_role"),
    (
        ("domain", "application"),
        ("application", "outbound_infrastructure"),
        ("inbound_api", "outbound_infrastructure"),
        ("outbound_infrastructure", "inbound_api"),
    ),
)
def test_forbidden_responsibility_directions_fail(
    source_role: str, target_role: str
) -> None:
    modules = (
        SourceModule("solar_platform.feature.source", "from . import target\n"),
        SourceModule("solar_platform.feature.target", ""),
    )
    classifications = (
        _classification("solar_platform.feature.source", "feature", source_role),
        _classification("solar_platform.feature.target", "feature", target_role),
    )
    with pytest.raises(ArchitectureError, match="forbidden responsibility import"):
        assert_architecture(modules, classifications)


def test_non_composition_cross_capability_private_import_still_fails() -> None:
    modules = (
        SourceModule(
            "solar_platform.orders.application",
            "from solar_platform.catalogue import application\n",
        ),
        SourceModule("solar_platform.catalogue.application", ""),
    )
    classifications = (
        _classification("solar_platform.orders.application", "orders", "application"),
        _classification(
            "solar_platform.catalogue.application", "catalogue", "application"
        ),
    )
    with pytest.raises(ArchitectureError, match="cross-capability private import"):
        assert_architecture(modules, classifications)


def test_unclassified_and_overlapping_classifications_fail() -> None:
    module = SourceModule("solar_platform.feature.domain", "")
    with pytest.raises(ArchitectureError, match="unclassified module"):
        assert_architecture((module,), ())

    overlapping = (
        ModuleClassification(
            "prefix", "solar_platform.feature", "feature", "domain", "private"
        ),
        _classification("solar_platform.feature.domain", "feature", "domain"),
    )
    with pytest.raises(ArchitectureError, match="overlapping classifications"):
        assert_architecture((module,), overlapping)


def test_package_initializer_is_inspected() -> None:
    module = SourceModule("solar_platform", "import unapproved_package\n", True)
    with pytest.raises(ArchitectureError, match="unauthorized third-party import"):
        assert_architecture((module,), REAL_MODULE_CLASSIFICATIONS)


def test_third_party_import_requires_reviewed_permission() -> None:
    module = SourceModule("solar_platform.feature.domain", "import approved_lib\n")
    classifications = (
        _classification("solar_platform.feature.domain", "feature", "domain"),
    )
    with pytest.raises(ArchitectureError, match="unauthorized third-party import"):
        assert_architecture((module,), classifications)

    permission = ThirdPartyImportPermission(
        "domain", "approved_lib", "D-TEST", "Fixture-only reviewed permission."
    )
    assert_architecture((module,), classifications, (permission,))


def test_inbound_api_uses_only_reviewed_http_edge_packages() -> None:
    module = SourceModule(
        "solar_platform.feature.api",
        "import fastapi\nimport pydantic\nimport starlette.exceptions\n",
    )
    classifications = (_classification(module.name, "feature", "inbound_api"),)
    permissions = tuple(
        permission
        for permission in THIRD_PARTY_IMPORT_ALLOWLIST
        if permission.responsibility == "inbound_api"
    )

    assert_architecture((module,), classifications, permissions)


@pytest.mark.parametrize(
    "package", ("anyio", "pydantic_core", "httpx", "httpx2", "httpcore2", "truststore")
)
def test_inbound_api_rejects_unapproved_transitive_packages(package: str) -> None:
    module = SourceModule("solar_platform.feature.api", f"import {package}\n")
    classifications = (_classification(module.name, "feature", "inbound_api"),)
    permissions = tuple(
        permission
        for permission in THIRD_PARTY_IMPORT_ALLOWLIST
        if permission.responsibility == "inbound_api"
    )

    with pytest.raises(ArchitectureError, match="unauthorized third-party import"):
        assert_architecture((module,), classifications, permissions)


@pytest.mark.parametrize(
    "target_role", ("configuration", "outbound_infrastructure", "composition")
)
def test_inbound_api_cannot_import_outer_or_configuration_modules(
    target_role: str,
) -> None:
    modules = (
        SourceModule("solar_platform.feature.api", "from . import target\n"),
        SourceModule("solar_platform.feature.target", ""),
    )
    classifications = (
        _classification("solar_platform.feature.api", "feature", "inbound_api"),
        _classification(
            "solar_platform.feature.target",
            "feature",
            target_role,
            rationale=(
                "Fixture special boundary." if target_role == "configuration" else ""
            ),
        ),
    )

    with pytest.raises(ArchitectureError, match="forbidden responsibility import"):
        assert_architecture(modules, classifications)


def test_type_only_and_function_local_imports_form_a_reported_cycle() -> None:
    modules = (
        SourceModule(
            "solar_platform.feature.a",
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n    from . import b\n",
        ),
        SourceModule(
            "solar_platform.feature.b", "def operation():\n    from . import c\n"
        ),
        SourceModule("solar_platform.feature.c", "from . import a\n"),
    )
    classifications = tuple(
        _classification(module.name, "feature", "application") for module in modules
    )
    expected = (
        "static dependency cycle: solar_platform.feature.a -> "
        "solar_platform.feature.b -> solar_platform.feature.c -> "
        "solar_platform.feature.a"
    )
    with pytest.raises(ArchitectureError, match="static dependency cycle") as error:
        assert_architecture(modules, classifications)
    assert expected in str(error.value)


def test_published_and_special_classifications_require_rationale() -> None:
    module = SourceModule("solar_platform.feature.contract", "")
    missing_rationale = (
        _classification(module.name, "feature", "application", "published_contract"),
    )
    with pytest.raises(ArchitectureError, match="missing required rationale"):
        assert_architecture((module,), missing_rationale)
