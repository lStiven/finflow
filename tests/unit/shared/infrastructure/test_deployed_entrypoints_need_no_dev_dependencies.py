"""Every deployed entry point must import without the dev dependency group.

The Lambda image installs the project's runtime dependencies and nothing else,
so a module that reaches for `boto3-stubs` at import time is fine locally and a
502 in the cloud: the function never finishes starting, and the only symptom is
`ModuleNotFoundError` buried in CloudWatch. Type-only imports therefore belong
under `TYPE_CHECKING`, and this test is what says so.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    from collections.abc import Iterator
    from importlib.machinery import ModuleSpec


# The six handlers `infra/template.yaml` names, and nothing else: this is
# about what Lambda imports, not about the package at large.
ENTRYPOINTS = [
    "personal_finance.api.main",
    "personal_finance.contexts.ingestion.presentation.awslambda.ingest_handler",
    "personal_finance.contexts.ingestion.presentation.awslambda.parse_handler",
    "personal_finance.contexts.merchant.presentation.awslambda.merchant_handler",
    "personal_finance.contexts.financial.presentation.awslambda.financial_handler",
    "personal_finance.contexts.alerts.presentation.awslambda.alerts_handler",
]

DEV_ONLY_ROOTS = ("mypy_boto3", "moto", "pytest", "cfn_lint", "samtranslator")


class _RefuseDevOnlyImports:
    """Makes the dev-only packages look absent, the way Lambda sees them."""

    def find_spec(
        self,
        name: str,
        path: object = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        if name.split(".")[0].startswith(DEV_ONLY_ROOTS):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return None


@pytest.fixture
def without_dev_dependencies() -> Iterator[None]:
    finder = _RefuseDevOnlyImports()
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)


@pytest.mark.parametrize("module_name", ENTRYPOINTS)
def test_a_deployed_entrypoint_imports_without_dev_dependencies(
    module_name: str,
    without_dev_dependencies: None,
) -> None:
    # A fresh import: the module is already in `sys.modules` from collection,
    # and re-importing a cached module would exercise nothing.
    already_imported = [
        name
        for name in sys.modules
        if name == module_name or name.startswith("personal_finance")
    ]
    saved = {name: sys.modules.pop(name) for name in already_imported}
    try:
        importlib.import_module(module_name)
    finally:
        sys.modules.update(saved)
