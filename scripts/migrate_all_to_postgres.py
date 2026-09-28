"""One-time, idempotent migration of every Python MVP store to Supabase."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.auth.postgres_repository import PostgresSupabaseAuthRepository
from app.config import settings
from app.media.generator import SupabaseImageGenerator
from app.opportunities.repository import PostgresOpportunityRepository, SQLiteOpportunityRepository
from app.prompts.repository import PostgresPromptRepository
from app.usage.repository import PostgresUsageRepository
from app.workflow.repository import PostgresWorkflowRepository, SQLiteWorkflowRepository, WorkflowNotFoundError


def sqlite_connection() -> sqlite3.Connection:
    connection=sqlite3.connect(settings.database_path)
    connection.row_factory=sqlite3.Row
    return connection


def supabase_users() -> dict[str,dict]:
    headers={"apikey":settings.supabase_service_role_key,"Authorization":f"Bearer {settings.supabase_service_role_key}"}
    users={}
    page=1
    with httpx.Client(timeout=30, trust_env=False) as client:
        while True:
            response=client.get(f"{settings.supabase_url}/auth/v1/admin/users",headers=headers,params={"page":page,"per_page":100})
            response.raise_for_status(); payload=response.json(); batch=payload.get("users",payload if isinstance(payload,list) else [])
            for user in batch:
                if user.get("email"): users[user["email"].casefold()]=user
            if len(batch)<100: break
            page+=1
    return users


def apply_rls(connection: psycopg.Connection) -> None:
    # SECURITY DEFINER avoids recursive membership-policy evaluation while the
    # caller identity still comes exclusively from Supabase auth.uid().
    connection.execute("""CREATE OR REPLACE FUNCTION public.py_is_workspace_member(target_workspace_id TEXT)
        RETURNS BOOLEAN LANGUAGE SQL STABLE SECURITY DEFINER SET search_path=public AS $$
        SELECT EXISTS (SELECT 1 FROM public.py_auth_memberships m
                       WHERE m.workspace_id=target_workspace_id AND m.user_id=auth.uid()::text)
        $$""")
    connection.execute("REVOKE ALL ON FUNCTION public.py_is_workspace_member(TEXT) FROM PUBLIC")
    connection.execute("GRANT EXECUTE ON FUNCTION public.py_is_workspace_member(TEXT) TO authenticated")
    workspace_tables=("py_workflows","py_material_projects","py_opportunity_generations","py_usage_events","py_feedback_entries","py_prompt_versions")
    for table in workspace_tables:
        connection.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        connection.execute(f"DROP POLICY IF EXISTS {table}_workspace_access ON {table}")
        connection.execute(f"""CREATE POLICY {table}_workspace_access ON {table}
            USING (public.py_is_workspace_member({table}.workspace_id))
            WITH CHECK (public.py_is_workspace_member({table}.workspace_id))""")
    for table in ("py_material_script_sections","py_material_storyboard_shots","py_material_visual_cues","py_material_generated_assets"):
        connection.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        connection.execute(f"DROP POLICY IF EXISTS {table}_workspace_access ON {table}")
        connection.execute(f"""CREATE POLICY {table}_workspace_access ON {table}
            USING (EXISTS (SELECT 1 FROM py_workflows w WHERE w.id={table}.workflow_id AND public.py_is_workspace_member(w.workspace_id)))
            WITH CHECK (EXISTS (SELECT 1 FROM py_workflows w WHERE w.id={table}.workflow_id AND public.py_is_workspace_member(w.workspace_id)))""")
    for table in ("py_auth_profiles","py_auth_workspaces","py_auth_memberships","py_auth_sessions"):
        connection.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    connection.execute("DROP POLICY IF EXISTS py_auth_profiles_self ON py_auth_profiles")
    connection.execute("CREATE POLICY py_auth_profiles_self ON py_auth_profiles USING (id=auth.uid()::text) WITH CHECK (id=auth.uid()::text)")
    connection.execute("DROP POLICY IF EXISTS py_auth_workspaces_member ON py_auth_workspaces")
    connection.execute("CREATE POLICY py_auth_workspaces_member ON py_auth_workspaces USING (public.py_is_workspace_member(id))")
    connection.execute("DROP POLICY IF EXISTS py_auth_memberships_member ON py_auth_memberships")
    connection.execute("CREATE POLICY py_auth_memberships_member ON py_auth_memberships USING (user_id=auth.uid()::text OR public.py_is_workspace_member(workspace_id))")
    connection.execute("DROP POLICY IF EXISTS py_auth_sessions_self ON py_auth_sessions")
    connection.execute("CREATE POLICY py_auth_sessions_self ON py_auth_sessions USING (user_id=auth.uid()::text) WITH CHECK (user_id=auth.uid()::text)")


async def migrate() -> dict[str,int]:
    if not all((settings.database_url,settings.supabase_url,settings.supabase_anon_key,settings.supabase_service_role_key)):
        raise RuntimeError("PostgreSQL and Supabase settings are incomplete")
    workflows_pg=PostgresWorkflowRepository(settings.database_url)
    opportunities_pg=PostgresOpportunityRepository(settings.database_url)
    usage_pg=PostgresUsageRepository(settings.database_url)
    prompts_pg=PostgresPromptRepository(settings.database_url)
    auth_pg=PostgresSupabaseAuthRepository(settings.database_url,supabase_url=settings.supabase_url,anon_key=settings.supabase_anon_key,service_role_key=settings.supabase_service_role_key)
    users=supabase_users(); user_map={}
    counts={"profiles":0,"workspaces":0,"memberships":0,"sessions":0,"workflows":0,"opportunities":0,"usage_events":0,"feedback":0,"prompts":0,"storage_objects":0}
    with sqlite_connection() as source, auth_pg._connect() as target:
        for row in source.execute("SELECT * FROM auth_users"):
            remote=users.get(row["email"].casefold())
            if remote is None: raise RuntimeError(f"Supabase Auth account missing for {row['email']}; create or invite it first")
            user_map[row["id"]]=str(remote["id"])
            target.execute("INSERT INTO py_auth_profiles (id,email,display_name,is_active,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET email=EXCLUDED.email,display_name=EXCLUDED.display_name,is_active=EXCLUDED.is_active,updated_at=EXCLUDED.updated_at",(str(remote["id"]),row["email"],row["display_name"],bool(row["is_active"]),row["created_at"],row["updated_at"]))
            counts["profiles"]+=1
        for row in source.execute("SELECT * FROM auth_workspaces"):
            target.execute("INSERT INTO py_auth_workspaces (id,name,created_at) VALUES (%s,%s,%s) ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name",(row["id"],row["name"],row["created_at"])); counts["workspaces"]+=1
        for row in source.execute("SELECT * FROM auth_memberships"):
            target.execute("INSERT INTO py_auth_memberships (workspace_id,user_id,role,created_at) VALUES (%s,%s,%s,%s) ON CONFLICT (workspace_id,user_id) DO UPDATE SET role=EXCLUDED.role",(row["workspace_id"],user_map[row["user_id"]],row["role"],row["created_at"])); counts["memberships"]+=1
        for row in source.execute("SELECT * FROM auth_sessions"):
            target.execute("INSERT INTO py_auth_sessions (id,token_hash,user_id,active_workspace_id,csrf_token,created_at,last_seen_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET token_hash=EXCLUDED.token_hash,user_id=EXCLUDED.user_id,active_workspace_id=EXCLUDED.active_workspace_id,csrf_token=EXCLUDED.csrf_token,last_seen_at=EXCLUDED.last_seen_at,expires_at=EXCLUDED.expires_at",(row["id"],row["token_hash"],user_map[row["user_id"]],row["active_workspace_id"],row["csrf_token"],row["created_at"],row["last_seen_at"],row["expires_at"])); counts["sessions"]+=1

    workflows=await SQLiteWorkflowRepository(settings.database_path).list(limit=10000)
    for workflow in reversed(workflows):
        try: await workflows_pg.get(workflow.id)
        except WorkflowNotFoundError: await workflows_pg.save(workflow)
        counts["workflows"]+=1
    generations=await SQLiteOpportunityRepository(settings.database_path).list(limit=10000)
    for generation in generations: await opportunities_pg.save(generation); counts["opportunities"]+=1

    with sqlite_connection() as source, usage_pg._connect() as target:
        for row in source.execute("SELECT * FROM usage_events"):
            target.execute("INSERT INTO py_usage_events (id,workspace_id,actor_id,event_name,units,workflow_id,provider,model,metadata,occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",(row["id"],row["workspace_id"],user_map.get(row["actor_id"],row["actor_id"]),row["event_name"],row["units"],row["workflow_id"],row["provider"],row["model"],Jsonb(json.loads(row["metadata"] or "{}")),row["occurred_at"])); counts["usage_events"]+=1
        for row in source.execute("SELECT * FROM feedback_entries"):
            target.execute("INSERT INTO py_feedback_entries (id,workspace_id,workflow_id,artifact_type,rating,note,actor_id,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",(row["id"],row["workspace_id"],row["workflow_id"],row["artifact_type"],row["rating"],row["note"],user_map.get(row["actor_id"],row["actor_id"]),row["created_at"])); counts["feedback"]+=1
        for row in source.execute("SELECT * FROM prompt_versions"):
            target.execute("INSERT INTO py_prompt_versions (id,workspace_id,prompt_key,version,instructions,change_note,created_by,created_at,activated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",(row["id"],row["workspace_id"],row["prompt_key"],row["version"],row["instructions"],row["change_note"],user_map.get(row["created_by"],row["created_by"]),row["created_at"],row["activated_at"])); counts["prompts"]+=1

    # Upload existing generated files without regenerating them.
    headers={"apikey":settings.supabase_service_role_key,"Authorization":f"Bearer {settings.supabase_service_role_key}"}
    image_store=SupabaseImageGenerator(model=settings.openai_image_model,timeout_seconds=settings.openai_image_timeout_seconds,storage_root=settings.generated_assets_path,supabase_url=settings.supabase_url,service_role_key=settings.supabase_service_role_key,bucket=settings.supabase_storage_bucket)
    with httpx.Client(timeout=60, trust_env=False) as client:
        for workflow in workflows:
            for asset in workflow.artifacts.get("production_package",{}).get("generated_assets",[]):
                asset_id=asset.get("asset_id");
                if not asset_id: continue
                local=Path(settings.generated_assets_path)/image_store._object_key(workflow.input.workspace_id,workflow.id,asset_id)
                if not local.is_file(): continue
                key=image_store._object_key(workflow.input.workspace_id,workflow.id,asset_id)
                response=client.post(f"{settings.supabase_url}/storage/v1/object/{settings.supabase_storage_bucket}/{key}",headers={**headers,"Content-Type":"image/png","x-upsert":"true"},content=local.read_bytes())
                response.raise_for_status(); counts["storage_objects"]+=1

    with psycopg.connect(settings.database_url,row_factory=dict_row) as connection: apply_rls(connection)
    return counts


if __name__ == "__main__":
    from app.db.postgres import close_pools
    try:
        print(json.dumps(asyncio.run(migrate()),ensure_ascii=False,indent=2))
    finally:
        close_pools()
