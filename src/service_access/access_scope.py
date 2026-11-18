"""Verified Host identities shared by trusted services, never model parameters."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
from pathlib import Path
import time

from database.access import AccessScope


@dataclass(frozen=True)
class TrustedAccess:
    scope: AccessScope
    root_path: str


_current = ContextVar("benchforge_access", default=None)


def current_access():
    return _current.get()


@contextmanager
def access_context(access):
    if access is not None and not isinstance(access, TrustedAccess):
        raise TypeError("Verified Host access required")
    token = _current.set(access)
    try:
        yield access
    finally:
        _current.reset(token)


def decode_ticket(ticket, secret, *, now=None):
    """Bounded HMAC ticket. The key belongs to the Host/service, not task code."""
    if not isinstance(ticket, str) or len(ticket) > 8192 or not secret:
        raise PermissionError("Invalid workspace access ticket")
    try:
        encoded, signature = ticket.split(".")
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        value = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        clock = time.time() if now is None else now
        if (value["aud"] != "benchforge-service" or type(value["exp"]) is not int
                or not clock < value["exp"] <= clock + 330):
            raise ValueError("expiry")
        scope = AccessScope(**{key: value[key] for key in AccessScope.__dataclass_fields__})
        root = Path(value["root_path"])
        if not root.is_absolute():
            raise ValueError("root")
        return TrustedAccess(scope, str(root))
    except (ValueError, KeyError, TypeError) as exc:
        raise PermissionError("Invalid or expired workspace access ticket") from exc


def resolve_ticket(engine, ticket, secret):
    from storage.access_identity import register_session_workspace, resolve_access_scope
    from database.schema import workspaces
    from sqlalchemy import select
    access = decode_ticket(ticket, secret)
    scope = access.scope
    registered = register_session_workspace(engine, host_id=scope.host_id,
        session_id=scope.session_id, owner_id=scope.owner_id, root_path=access.root_path,
        workspace_id=scope.workspace_id)
    current = resolve_access_scope(engine, host_id=scope.host_id,
        session_id=scope.session_id, owner_id=scope.owner_id,
        policy_epoch=scope.policy_epoch, root_generation=scope.root_generation)
    if current != scope or registered != scope:
        raise PermissionError("Workspace access has changed")
    with engine.connect() as connection:
        root = connection.execute(select(workspaces.c.root_path).where(
            workspaces.c.workspace_id == scope.workspace_id)).scalar_one()
    return TrustedAccess(scope, root)


def workspace_path(value, *, must_exist=True):
    """Resolve a task file relative to the trusted conversation root."""
    access = current_access()
    if access is None:
        raise PermissionError("A verified conversation workspace is required")
    root = Path(access.root_path).resolve(strict=True)
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=must_exist)
    if not resolved.is_relative_to(root):
        raise PermissionError("File is outside this conversation workspace")
    # Reject links even when they currently point inside the root. Descriptor
    # based copying is used when a service consumes mutable input bytes.
    for part in [candidate, *candidate.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise PermissionError("Symbolic links are not task inputs")
    return resolved


@contextmanager
def cli_access(engine, workspace):
    """Consume the Host environment only at a trusted CLI entry point."""
    import os
    ticket = os.environ.pop("BENCHFORGE_ACCESS_TICKET", None)
    secret = os.environ.pop("BENCHFORGE_ACCESS_KEY", None)
    if ticket is None:
        yield None
        return
    access = resolve_ticket(engine, ticket, secret)
    if Path(workspace).resolve() != Path(access.root_path).resolve():
        raise PermissionError("CLI workspace differs from its Host identity")
    with access_context(access):
        yield access


def sign_ticket(access, secret):
    from dataclasses import asdict
    value = {**asdict(access.scope), "root_path": access.root_path,
             "aud": "benchforge-service", "exp": int(time.time()) + 300}
    encoded = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return encoded + "." + hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
