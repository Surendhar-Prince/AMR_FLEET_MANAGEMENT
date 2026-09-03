"""
backend/db.py

Thin Supabase database layer.  All database operations are isolated here.
No simulation logic lives here; no Supabase imports live in simulation.py.

Tables expected (already created in Supabase):
  profiles  (id UUID PK → auth.users, name TEXT, email TEXT UNIQUE, created_at TIMESTAMPTZ)
  amrs      (id UUID PK, amr_id TEXT UNIQUE, user_id UUID → profiles, start_node TEXT,
             status TEXT, created_at TIMESTAMPTZ)

Environment variables required on the server:
  SUPABASE_URL              – your project URL  (https://xxxx.supabase.co)
  SUPABASE_SERVICE_ROLE_KEY – service-role secret (never exposed to browser)
"""

import os
import re
import threading

from supabase import Client, create_client

# ---------------------------------------------------------------------------
# Client singleton – initialised lazily on first use so that import-time
# failures (missing env vars in test environments) don't crash the entire app.
# ---------------------------------------------------------------------------
_client: Client | None = None
_client_lock = threading.Lock()


def _get_client() -> Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                url = os.environ.get("SUPABASE_URL", "").strip()
                key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
                if not url or not key:
                    raise RuntimeError(
                        "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set "
                        "as environment variables."
                    )
                _client = create_client(url, key)
    return _client


# ---------------------------------------------------------------------------
# AMR ID generation
# ---------------------------------------------------------------------------
_amr_counter_lock = threading.Lock()


def _next_amr_id() -> str:
    """
    Generate the next unique amr-NNN id by reading the highest existing
    numeric suffix from the amrs table.  A database-level UNIQUE constraint
    on amr_id prevents races; this function just picks the next candidate.
    """
    client = _get_client()
    # Fetch all existing amr_ids so we can find the maximum numeric suffix.
    result = client.table("amrs").select("amr_id").execute()
    rows = result.data or []
    max_n = 0
    for row in rows:
        raw = row.get("amr_id", "")
        # Accept both "amr-001" and "amr-1" style ids
        m = re.search(r"(\d+)$", raw)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"amr-{(max_n + 1):03d}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_profile_and_amr(
    *,
    auth_user_id: str,
    name: str,
    email: str,
    start_node: str,
) -> dict:
    """
    Persist a profile row and an amrs row in Supabase.

    Returns a dict with keys: profile, amr
      profile  – the profiles row dict
      amr      – the amrs row dict (amr_id is the simulation-facing ID)

    Raises:
      RuntimeError  – on any Supabase error
      ValueError    – if the email already has a profile
    """
    client = _get_client()
    import uuid as _uuid

    with _amr_counter_lock:
        amr_id = _next_amr_id()

        # --- profiles table ---
        profile_id = auth_user_id  # FK → auth.users(id)
        profile_data = {
            "id": profile_id,
            "name": name,
            "email": email,
        }
        try:
            profile_result = (
                client.table("profiles")
                .upsert(profile_data, on_conflict="email")
                .execute()
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to create profile: {exc}") from exc

        profile_row = (profile_result.data or [{}])[0]

        # --- amrs table ---
        amr_row_id = str(_uuid.uuid4())
        amr_data = {
            "id": amr_row_id,
            "amr_id": amr_id,
            "user_id": profile_id,
            "start_node": start_node,
            "status": "ACTIVE",
        }
        try:
            amr_result = client.table("amrs").insert(amr_data).execute()
        except Exception as exc:
            # amr_id UNIQUE constraint violated: another request won the race –
            # retry once with the fresh next id.
            amr_id = _next_amr_id()
            amr_data["amr_id"] = amr_id
            amr_data["id"] = str(_uuid.uuid4())
            try:
                amr_result = client.table("amrs").insert(amr_data).execute()
            except Exception as exc2:
                raise RuntimeError(f"Failed to create AMR record: {exc2}") from exc2

        amr_row = (amr_result.data or [{}])[0]

    return {"profile": profile_row, "amr": amr_row}


def get_profile_by_email(email: str) -> dict | None:
    """Return the profiles row for the given email, or None if not found."""
    client = _get_client()
    result = client.table("profiles").select("*").eq("email", email).execute()
    rows = result.data or []
    return rows[0] if rows else None


def get_amr_by_user_id(user_id: str) -> dict | None:
    """Return the amrs row for the given user_id (most recent), or None."""
    client = _get_client()
    result = (
        client.table("amrs")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None
