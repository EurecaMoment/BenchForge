"""Replay derived answers from accepted inputs without executing supplied code."""
from pathlib import Path

from capabilities.dataset_authoring.adapters import BUILTINS, oracle
from storage.artifacts import read, select


def validate_source_binding(template, record, base, receipt, answer):
    """Return every source that must remain unchanged after S3 validation.

    An accepted answer file is usable directly. A separately authored annotation
    must declare a supported deterministic oracle and bind every input leaf to
    an accepted JSON file and selector; copied answers and literal inputs fail.
    """
    approved = {Path(name).resolve() for name in receipt.get('artifacts', [])}
    provenance = record.get('provenance') or {}
    source = Path(provenance['path'])
    source = source.resolve() if source.is_absolute() else (Path(base) / source).resolve()
    if source in approved:
        return {source}
    derivation = provenance.get('derivation') or {}
    if (derivation.get('schema') != 'benchforge.native-derivation/v1'
            or derivation.get('oracle') != 'builtin-template/v1'
            or template['id'] not in BUILTINS):
        raise ValueError('Answer source is not covered by the service acceptance and has no supported native derivation')
    dependencies = {source}

    def resolve(node):
        if isinstance(node, dict) and set(node) == {'path', 'selector'}:
            path = Path(node['path'])
            path = path.resolve() if path.is_absolute() else (Path(base) / path).resolve()
            if path not in approved:
                raise ValueError('Derived answer inputs must be covered by the service acceptance')
            dependencies.add(path)
            return select(read(path), node['selector'])
        if isinstance(node, dict) and node:
            return {key: resolve(value) for key, value in node.items()}
        if isinstance(node, list) and node:
            return [resolve(value) for value in node]
        raise ValueError('Every derived oracle input must reference an accepted native JSON field')

    try:
        replayed = oracle(template['id'], resolve(derivation.get('inputs')))
    except (KeyError, TypeError, ZeroDivisionError) as error:
        raise ValueError('Accepted native fields do not satisfy this template oracle') from error
    if replayed != answer:
        raise ValueError('Derived answer does not match replay from accepted native inputs')
    return dependencies
