"""Authenticated local identity management, with no HTTP or anonymous issuer.

Every entry point verifies the presented server token in the same transaction
as the operation. A constructed Principal alone confers no authority. HTTP
integration must additionally enforce same-origin requests and no-store on the
one-time issuance response. Privileged database access/local bootstrap CLI is
outside this service; all ordinary identity management routes must use it.
"""
from __future__ import annotations

from contextlib import nullcontext
import re
import secrets

from psycopg2.extras import Json

from .credentials import AdminAuth, Principal, ROLES, token_digest


class IdentityNotFoundError(LookupError):
    """The requested identity or public token handle does not exist."""


class IdentityConflictError(ValueError):
    """An edit is stale or would remove the last operable administrator."""


_SECRET_TEXT = re.compile(r"aat_[A-Za-z0-9_-]{8,}|\b[0-9a-fA-F]{64}\b|\bsk-[A-Za-z0-9_-]{8,}|\bBearer\s+\S+", re.I)
_IDENTITY_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}")
_TOKEN_ID = re.compile(r"token-[A-Za-z0-9_-]{20,100}")
_IDENTITY_FIELDS = "principal_id,display_name,roles_json,kind,enabled,revision,created_at"


def _text(value, label, maximum):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise ValueError("invalid " + label)
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value) or _SECRET_TEXT.search(value):
        raise ValueError(label + " must not contain credentials or token digests")
    return value.strip()


def _roles(value):
    if not isinstance(value, (list, tuple)) or not value or len(value) > 16 \
            or any(not isinstance(role, str) or role not in ROLES for role in value):
        raise ValueError("roles must contain admin, operator or reviewer")
    return sorted(set(value))


def _identity_id(value):
    if not isinstance(value, str) or not _IDENTITY_ID.fullmatch(value):
        raise ValueError("invalid identity ID")
    return value


def _public_identity(row):
    return {"principalId": row["principal_id"], "displayName": _SECRET_TEXT.sub("[REDACTED]", row["display_name"]),
            "roles": list(row["roles_json"]), "kind": row["kind"], "enabled": row["enabled"],
            "revision": row["revision"], "createdAt": row["created_at"]}


def _public_token(row):
    return {"tokenId": row["token_id"], "principalId": row["principal_id"],
            "createdAt": row["created_at"], "expiresAt": row["expires_at"],
            "revokedAt": row["revoked_at"], "status": row["status"]}


class IdentityManagementService:
    """Administrative methods accept actor plus keyword-only actor_token.

    Identity patches accept displayName, roles and enabled, with mandatory
    expected_revision and reason. Token IDs are independent random handles,
    never token digests. Only issue_identity returns a raw token, exactly once;
    it cannot subsequently be recovered through list/audit methods.
    """

    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    @staticmethod
    def _authorize(cursor, actor, actor_token):
        if not isinstance(actor, Principal) or actor.kind != "human":
            raise PermissionError("authenticated human admin required")
        digest = token_digest(actor_token)
        if digest is None:
            raise PermissionError("current admin token required")
        # Serialize all service operations BEFORE taking identity/token row
        # locks, including reads. Concurrent demotions/revocations cannot each
        # observe the other administrator as still available. Scope the lock to
        # the active schema so isolated tests cannot interfere with public.
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended("
                       "'agent-admin-identity-management:' || current_schema(),0))")
        cursor.execute("SELECT i.principal_id FROM agent_admin_identity i "
                       "JOIN agent_admin_token t USING(principal_id) "
                       "WHERE i.principal_id=%s AND t.token_hash=%s AND i.kind='human' "
                       "AND i.enabled AND i.roles_json ? 'admin' AND t.revoked_at IS NULL "
                       "AND t.expires_at>clock_timestamp() FOR SHARE OF i,t", (actor.principal_id, digest))
        if cursor.fetchone() is None:
            raise PermissionError("enabled human admin with a current token required")

    @staticmethod
    def _limit(value):
        if type(value) is not int or not 1 <= value <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        return value

    @staticmethod
    def _operable_admin(cursor):
        cursor.execute("SELECT EXISTS(SELECT 1 FROM agent_admin_identity i WHERE i.kind='human' "
                       "AND i.enabled AND i.roles_json ? 'admin' AND EXISTS(SELECT 1 FROM agent_admin_token t "
                       "WHERE t.principal_id=i.principal_id AND t.revoked_at IS NULL "
                       "AND t.expires_at>clock_timestamp())) AS available")
        if not cursor.fetchone()["available"]:
            raise IdentityConflictError("the last operable administrator must retain an active token and admin role")

    @staticmethod
    def _identity(cursor, principal_id, *, lock=False):
        cursor.execute("SELECT " + _IDENTITY_FIELDS + " FROM agent_admin_identity WHERE principal_id=%s" +
                       (" FOR UPDATE" if lock else ""), (principal_id,))
        row = cursor.fetchone()
        if row is None:
            raise IdentityNotFoundError("identity not found")
        return _public_identity(row)

    @staticmethod
    def _audit(cursor, actor_id, subject_id, operation, reason, before, after, token_id=None):
        # Fields are server-built metadata; neither token nor its digest is
        # accepted in arbitrary audit JSON. Datetimes are encoded explicitly.
        from datetime import datetime
        import json
        def encode(value):
            return Json(value, dumps=lambda item: json.dumps(item,
                        default=lambda obj: obj.isoformat() if isinstance(obj, datetime) else str(obj)))
        cursor.execute("INSERT INTO agent_admin_identity_audit(audit_id,actor_id,subject_principal_id,"
                       "token_id,operation,reason,before_json,after_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                       ("identity-audit-" + secrets.token_hex(12), actor_id, subject_id, token_id,
                        operation, reason, encode(before) if before is not None else None, encode(after)))

    def list_identities(self, actor, *, actor_token, limit=200):
        limit = self._limit(limit)
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                cursor.execute("SELECT i.*, (SELECT count(*) FROM agent_admin_token t WHERE "
                               "t.principal_id=i.principal_id AND t.revoked_at IS NULL "
                               "AND t.expires_at>clock_timestamp()) AS active_token_count "
                               "FROM agent_admin_identity i ORDER BY i.created_at,i.principal_id LIMIT %s", (limit,))
                rows = cursor.fetchall()
                return [{**_public_identity(row), "activeTokenCount": row["active_token_count"],
                         "operableAdmin": bool(row["enabled"] and row["kind"] == "human" and
                                              "admin" in row["roles_json"] and row["active_token_count"])} for row in rows]

    def list_tokens(self, actor, principal_id=None, *, actor_token, limit=200):
        limit = self._limit(limit)
        if principal_id is not None:
            _identity_id(principal_id)
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                if principal_id is not None:
                    self._identity(cursor, principal_id)
                cursor.execute("SELECT t.token_id,t.principal_id,t.created_at,t.expires_at,t.revoked_at,"
                               "CASE WHEN t.revoked_at IS NOT NULL THEN 'revoked' "
                               "WHEN t.expires_at<=clock_timestamp() THEN 'expired' "
                               "WHEN NOT i.enabled THEN 'identity-disabled' ELSE 'active' END AS status "
                               "FROM agent_admin_token t JOIN agent_admin_identity i USING(principal_id) "
                               "WHERE (%s IS NULL OR t.principal_id=%s) ORDER BY t.created_at,t.token_id LIMIT %s",
                               (principal_id, principal_id, limit))
                return [_public_token(row) for row in cursor.fetchall()]

    def list_audit(self, actor, principal_id=None, *, actor_token, after=0, limit=200):
        limit = self._limit(limit)
        if type(after) is not int or after < 0 or after > 9223372036854775807:
            raise ValueError("invalid audit cursor")
        if principal_id is not None:
            _identity_id(principal_id)
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                if principal_id is not None:
                    self._identity(cursor, principal_id)
                cursor.execute("SELECT audit_id,audit_seq,actor_id,subject_principal_id,token_id,operation,reason,"
                               "before_json,after_json,created_at FROM agent_admin_identity_audit "
                               "WHERE audit_seq>%s AND (%s IS NULL OR subject_principal_id=%s) "
                               "ORDER BY audit_seq LIMIT %s", (after, principal_id, principal_id, limit))
                rows = [dict(row) for row in cursor.fetchall()]
                return {"events": rows, "nextCursor": rows[-1]["audit_seq"] if rows else after}

    def update_identity(self, actor, principal_id, patch, *, actor_token, expected_revision, reason):
        _identity_id(principal_id)
        reason = _text(reason, "change reason", 2000)
        if type(expected_revision) is not int or not 1 <= expected_revision < 9223372036854775807:
            raise ValueError("expected_revision must be a positive integer")
        if not isinstance(patch, dict) or not patch or set(patch) - {"displayName", "roles", "enabled"}:
            raise ValueError("patch accepts only displayName, roles and enabled")
        changes = dict(patch)
        if "displayName" in changes:
            changes["displayName"] = _text(changes["displayName"], "identity display name", 120)
        if "roles" in changes:
            changes["roles"] = _roles(changes["roles"])
        if "enabled" in changes and type(changes["enabled"]) is not bool:
            raise ValueError("enabled must be a boolean")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                before = self._identity(cursor, principal_id, lock=True)
                if before["revision"] != expected_revision:
                    raise IdentityConflictError("identity changed; reload its current revision")
                if all(before[key] == value for key, value in changes.items()):
                    return before
                columns = {"displayName": "display_name", "roles": "roles_json", "enabled": "enabled"}
                values = [Json(value) if key == "roles" else value for key, value in changes.items()]
                cursor.execute("UPDATE agent_admin_identity SET " +
                               ",".join(columns[key] + "=%s" for key in changes) +
                               ",revision=revision+1 WHERE principal_id=%s", (*values, principal_id))
                self._operable_admin(cursor)
                after = self._identity(cursor, principal_id)
                self._audit(cursor, actor.principal_id, principal_id, "update-identity", reason, before, after)
                return after

    def revoke_token(self, actor, token_id, *, actor_token, reason):
        if not isinstance(token_id, str) or not _TOKEN_ID.fullmatch(token_id):
            raise ValueError("invalid public token ID")
        reason = _text(reason, "revocation reason", 2000)
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                cursor.execute("SELECT token_id,principal_id,created_at,expires_at,revoked_at,"
                               "CASE WHEN revoked_at IS NOT NULL THEN 'revoked' "
                               "WHEN expires_at<=clock_timestamp() THEN 'expired' ELSE 'active' END AS status "
                               "FROM agent_admin_token WHERE token_id=%s FOR UPDATE", (token_id,))
                row = cursor.fetchone()
                if row is None:
                    raise IdentityNotFoundError("token not found")
                before = _public_token(row)
                if row["revoked_at"] is not None:
                    return {**before, "changed": False}
                cursor.execute("UPDATE agent_admin_token SET revoked_at=clock_timestamp() WHERE token_id=%s "
                               "RETURNING revoked_at", (token_id,))
                after = {**before, "revokedAt": cursor.fetchone()["revoked_at"], "status": "revoked"}
                self._operable_admin(cursor)
                self._audit(cursor, actor.principal_id, row["principal_id"], "revoke-token", reason, before, after, token_id)
                return {**after, "changed": True}

    def issue_identity(self, actor, display_name, roles, *, actor_token, expires_days=1, reason):
        display_name = _text(display_name, "identity display name", 120)
        roles = _roles(roles)
        reason = _text(reason, "creation reason", 2000)
        if type(expires_days) is not int or not 1 <= expires_days <= 365:
            raise ValueError("expiry must be an integer between 1 and 365 days")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                # Reuse the existing issuer on OUR transaction: nullcontext
                # prevents its nested context from committing without the audit.
                issued = AdminAuth(lambda: nullcontext(con)).issue_token(display_name, roles, expires_days)
                cursor.execute("SELECT token_id FROM agent_admin_token WHERE token_hash=%s",
                               (token_digest(issued["token"]),))
                token_id = cursor.fetchone()["token_id"]
                identity = self._identity(cursor, issued["principalId"])
                self._audit(cursor, actor.principal_id, issued["principalId"], "create-identity", reason, None,
                            {"identity": identity, "tokenId": token_id, "expiresAt": issued["expiresAt"]}, token_id)
                return {**issued, "tokenId": token_id}
