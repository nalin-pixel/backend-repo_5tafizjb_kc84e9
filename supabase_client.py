import os
from typing import Optional

from supabase import create_client, Client

_supabase: Optional[Client] = None


def get_supabase() -> Optional[Client]:
    global _supabase
    if _supabase is not None:
        return _supabase
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        return None
    _supabase = create_client(url, key)
    return _supabase
