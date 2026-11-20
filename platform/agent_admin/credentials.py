"""Local human identities; server-issued opaque tokens are stored as hashes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets

from psycopg2.extras import Json


ROLES = frozenset({"operator", "reviewer", "admin"})


@dataclass(frozen=True)
class Principal:
    principal_id: str
    display_name: str
    roles: tuple[str, ...]
    kind: str = "human"

    def permits(self, role):
        return self.kind == "human" and (role in self.roles or "admin" in self.roles)


def token_digest(token):
    if not isinstance(token, str) or not re.fullmatch(r"aat_[A-Za-z0-9_-]{40,100}", token):
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class AdminAuth:
    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    def issue_token(self, display_name, roles=("operator",), expires_days=30):
        """Local CLI/admin service only. No unauthenticated HTTP issuance route."""
        if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 120:
            raise ValueError("invalid identity display name")
        if not isinstance(roles, (list, tuple)) or not roles or not set(roles).issubset(ROLES):
            raise ValueError("invalid identity roles")
        if type(expires_days) is not int or not 1 <= expires_days <= 365:
            raise ValueError("invalid token expiry")
        principal_id, token = "human-" + secrets.token_hex(12), "aat_" + secrets.token_urlsafe(40)
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("INSERT INTO agent_admin_identity(principal_id,display_name,roles_json,kind) VALUES(%s,%s,%s,'human')",
                          (principal_id, display_name.strip(), Json(sorted(set(roles)))))
                c.execute("INSERT INTO agent_admin_token(token_hash,principal_id,expires_at) VALUES(%s,%s,%s)",
                          (token_digest(token), principal_id, expires_at))
        return {"principalId": principal_id, "token": token, "expiresAt": expires_at.isoformat()}

    def authenticate(self, token):
        digest = token_digest(token)
        if not digest:
            return None
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT i.principal_id,i.display_name,i.roles_json,i.kind FROM agent_admin_token t "
                          "JOIN agent_admin_identity i USING(principal_id) WHERE t.token_hash=%s AND t.revoked_at IS NULL "
                          "AND t.expires_at>now() AND i.enabled=true", (digest,))
                row = c.fetchone()
        if not row or row["kind"] != "human":
            return None
        return Principal(row["principal_id"], row["display_name"], tuple(row["roles_json"]), row["kind"])

    def revoke(self, token, actor):
        if not actor.permits("admin"):
            raise PermissionError("admin role required")
        digest = token_digest(token)
        if not digest:
            raise ValueError("invalid token")
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("UPDATE agent_admin_token SET revoked_at=now() WHERE token_hash=%s AND revoked_at IS NULL", (digest,))
                return c.rowcount > 0


def main():
    import argparse
    from agent_engine.postgres import connection_factory
    parser = argparse.ArgumentParser(description="Issue a local human Agent Admin token (shown once).")
    parser.add_argument("--name", required=True)
    parser.add_argument("--roles", nargs="+", choices=sorted(ROLES), default=["operator"])
    parser.add_argument("--expires-days", type=int, default=30)
    parser.add_argument("--config")
    args = parser.parse_args()
    import json
    print(json.dumps(AdminAuth(connection_factory(args.config, "ontology_agent_admin")).issue_token(
        args.name, args.roles, args.expires_days), ensure_ascii=False))


if __name__ == "__main__":
    main()
