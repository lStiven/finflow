"""Check `infra/template.yaml` without deploying it, and without Docker.

    just infra-check

This exists because the DevContainer has neither the SAM CLI nor a Docker
daemon, so `just sam-validate` and `just deploy-*` cannot run here at all —
and the first deploy would otherwise be the first time anything read this
template. That is a bad place to discover a typo.

Two passes, and they catch different things:

* **cfn-lint** reads the template as CloudFormation and checks property names,
  types and references.
* **the SAM transform** is the same `samtranslator` code `sam deploy` runs
  server-side. It is the only thing that enforces SAM's own rules — most
  usefully the fixed list of properties `Globals` accepts, which is not
  derivable from the CloudFormation schema and which a plain YAML linter in an
  editor cannot know about.

The transform runs once per environment in `samconfig.toml`, with that
environment's real `parameter_overrides`, so `dev-` and production ARNs are
both resolved rather than left as `!Sub` strings.

`ImageUri` is filled in with a placeholder: `sam build` sets it after building
the image, so its absence from the source template is expected rather than a
defect.
"""

from __future__ import annotations

import logging
import os
import pathlib
import sys
import tomllib
from typing import Any, cast

from cfnlint.api import lint_file
from samtranslator.public.exceptions import InvalidDocumentException
from samtranslator.translator.managed_policy_translator import ManagedPolicyLoader
from samtranslator.translator.transform import transform
import yaml


# The transform reads a region from the environment before it reaches the
# pseudo-parameters below, and refuses to start without one. Nothing here
# calls AWS, so any region will do.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

# Its metrics publisher warns on teardown that nobody published them. True,
# and irrelevant: publishing is a concern of the real, server-side transform.
logging.getLogger("samtranslator.metrics").setLevel(logging.CRITICAL)

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "infra" / "template.yaml"
SAMCONFIG = ROOT / "infra" / "samconfig.toml"

# Resolved into ARNs and never called: the transform needs values for the
# pseudo-parameters, not an AWS account.
PSEUDO_PARAMETERS = {
    "AWS::Region": "us-east-1",
    "AWS::AccountId": "000000000000",
    "AWS::Partition": "aws",
    "AWS::URLSuffix": "amazonaws.com",
}

PLACEHOLDER_IMAGE_URI = "000000000000.dkr.ecr.us-east-1.amazonaws.com/finflow:build"

# What the transform looks up rather than grants. A synthesised ARN per name
# is enough to let it finish without an IAM call.
MANAGED_POLICY_NAMES = (
    "AWSLambdaBasicExecutionRole",
    "AWSLambdaSQSQueueExecutionRole",
    "AWSLambdaRole",
    "AWSXrayWriteOnlyAccess",
)


class _CloudFormationLoader(yaml.SafeLoader):
    """A YAML loader that understands `!Ref`, `!Sub`, `!GetAtt` and friends."""


def _construct_tag(
    loader: yaml.SafeLoader,
    suffix: str,
    node: yaml.Node,
) -> dict[str, Any]:
    key = "Ref" if suffix == "Ref" else f"Fn::{suffix}"

    if isinstance(node, yaml.ScalarNode):
        scalar = loader.construct_scalar(node)
        # `!GetAtt A.B` is the dotted spelling of `Fn::GetAtt: [A, B]`.
        return {key: scalar.split(".", 1) if suffix == "GetAtt" else scalar}

    if isinstance(node, yaml.SequenceNode):
        return {key: loader.construct_sequence(node, deep=True)}

    return {key: loader.construct_mapping(cast(yaml.MappingNode, node), deep=True)}


_CloudFormationLoader.add_multi_constructor(  # pyright: ignore[reportUnknownMemberType]
    "!",
    _construct_tag,
)


class _OfflineManagedPolicyLoader(ManagedPolicyLoader):
    """The real loader with its one IAM call replaced by a synthesised map."""

    def __init__(self) -> None:
        super().__init__(None)  # pyright: ignore[reportArgumentType]

    def load(self) -> dict[str, str]:
        return {
            name: f"arn:aws:iam::aws:policy/service-role/{name}"
            for name in MANAGED_POLICY_NAMES
        }


def _environment_parameters() -> dict[str, dict[str, str]]:
    """Every environment's `parameter_overrides`, keyed by its config name."""
    config = tomllib.loads(SAMCONFIG.read_text())
    environments: dict[str, dict[str, str]] = {}

    for name, section in config.items():
        if not isinstance(section, dict):
            continue

        deploy = cast(dict[str, Any], cast(dict[str, Any], section).get("deploy", {}))
        parameters = cast(dict[str, Any], deploy.get("parameters", {}))
        overrides = cast(list[str], parameters.get("parameter_overrides", []))

        if not overrides:
            continue

        # `Key=value`. An empty value is meaningful, not a mistake: it is what
        # production passes for ResourcePrefix and CorsOrigins.
        environments[name] = {
            key: value
            for key, _, value in (override.partition("=") for override in overrides)
        }

    return environments


def _load_built_template() -> dict[str, Any]:
    """The template as `sam build` would hand it to the transform."""
    template = cast(
        dict[str, Any],
        yaml.load(TEMPLATE.read_text(), Loader=_CloudFormationLoader),
    )
    resources = cast(dict[str, dict[str, Any]], template.get("Resources", {}))

    for resource in resources.values():
        if resource.get("Type") == "AWS::Serverless::Function":
            resource.setdefault("Properties", {})["ImageUri"] = PLACEHOLDER_IMAGE_URI

    return template


def _run_cfn_lint() -> bool:
    print("cfn-lint")
    matches = lint_file(TEMPLATE)

    for match in matches:
        print(f"  {match.rule.id} line {match.linenumber}: {match.message}")

    if not matches:
        print("  clean")

    return not matches


def _run_transform(environment: str, parameters: dict[str, str]) -> bool:
    print(f"SAM transform — {environment}")

    try:
        transformed = transform(
            _load_built_template(),
            {**PSEUDO_PARAMETERS, **parameters},
            _OfflineManagedPolicyLoader(),
        )
    except InvalidDocumentException as error:
        print("  REJECTED:")
        for cause in error.causes:
            print(f"    - {cause.message}")

        return False

    resources = cast(dict[str, dict[str, Any]], transformed["Resources"])
    counts: dict[str, int] = {}

    for resource in resources.values():
        kind = cast(str, resource["Type"])
        counts[kind] = counts.get(kind, 0) + 1

    for kind, count in sorted(counts.items()):
        print(f"  {count:>2} {kind}")

    return True


def main() -> None:
    ok = _run_cfn_lint()

    for environment, parameters in _environment_parameters().items():
        print()
        ok = _run_transform(environment, parameters) and ok

    print()
    print("template is deployable" if ok else "template would fail to deploy")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
