# backend/core/sandbox/sandbox.py
from __future__ import annotations

import os
from dotenv import load_dotenv
from core.utils.logger import logger
from core.utils.config import config, Configuration
from .workspace_config import workspace_config

load_dotenv()

PROVIDER = os.getenv("SANDBOX_PROVIDER", "daytona").lower()
WORKSPACE_ROOT = workspace_config.WORKSPACE_ROOT
EXEC_TIMEOUT = int(os.getenv("SANDBOX_EXEC_TIMEOUT_SEC", "900"))

# ========== Modalità DAYTONA (tuo codice, invariato) ==========
if PROVIDER == "daytona":
    from daytona_sdk import (
        AsyncDaytona, DaytonaConfig, CreateSandboxFromSnapshotParams,
        AsyncSandbox as DaytonaSandbox, SessionExecuteRequest, SandboxState
    )

    daytona_config = DaytonaConfig(
        api_key=config.DAYTONA_API_KEY,
        api_url=config.DAYTONA_SERVER_URL,
        target=config.DAYTONA_TARGET,
    )
    daytona = AsyncDaytona(daytona_config)

    async def get_or_start_sandbox(sandbox_id: str) -> DaytonaSandbox:
        logger.info(f"[daytona] Getting or starting sandbox with ID: {sandbox_id}")
        try:
            sandbox = await daytona.get(sandbox_id)
            if sandbox.state in (SandboxState.ARCHIVED, SandboxState.STOPPED):
                logger.info(f"[daytona] Sandbox is in {sandbox.state} state. Starting...")
                await daytona.start(sandbox)
                sandbox = await daytona.get(sandbox_id)
                await start_supervisord_session(sandbox)
            logger.info(f"[daytona] Sandbox {sandbox_id} is ready")
            return sandbox
        except Exception as e:
            logger.error(f"[daytona] Error retrieving or starting sandbox: {str(e)}")
            raise e

    async def start_supervisord_session(sandbox: DaytonaSandbox):
        session_id = "supervisord-session"
        try:
            await sandbox.process.create_session(session_id)
            await sandbox.process.execute_session_command(session_id, SessionExecuteRequest(
                command="exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf",
                var_async=True
            ))
        except Exception as e:
            logger.error(f"[daytona] Error starting supervisord session: {str(e)}")
            raise e

    async def create_sandbox(password: str, project_id: str = None) -> DaytonaSandbox:
        logger.info("[daytona] Creating new Daytona sandbox environment")
        labels = {'id': project_id} if project_id else None
        params = CreateSandboxFromSnapshotParams(
            snapshot=Configuration.SANDBOX_SNAPSHOT_NAME,
            public=True,
            labels=labels,
            env_vars={
                "CHROME_PERSISTENT_SESSION": "true",
                "RESOLUTION": "1048x768x24",
                "RESOLUTION_WIDTH": "1048",
                "RESOLUTION_HEIGHT": "768",
                "VNC_PASSWORD": password,
                "ANONYMIZED_TELEMETRY": "false",
                "CHROME_PATH": "",
                "CHROME_USER_DATA": "",
                "CHROME_DEBUGGING_PORT": "9222",
                "CHROME_DEBUGGING_HOST": "localhost",
                "CHROME_CDP": ""
            },
            auto_stop_interval=15,
            auto_archive_interval=30,
        )
        sandbox = await daytona.create(params)
        logger.info(f"[daytona] Sandbox created with ID: {sandbox.id}")
        await start_supervisord_session(sandbox)
        logger.info("[daytona] Sandbox environment successfully initialized")
        return sandbox

    async def delete_sandbox(sandbox_id: str) -> bool:
        logger.info(f"[daytona] Deleting sandbox with ID: {sandbox_id}")
        try:
            sandbox = await daytona.get(sandbox_id)
            await daytona.delete(sandbox)
            logger.info(f"[daytona] Successfully deleted sandbox {sandbox_id}")
            return True
        except Exception as e:
            logger.error(f"[daytona] Error deleting sandbox {sandbox_id}: {str(e)}")
            raise e

# ========== Modalità LOCAL_PROCESS ==========
elif PROVIDER == "local_process":
    # Importa il provider locale
    from .providers.local_process import LocalProcessSandbox

    # Nota: manteniamo stesse firme per compatibilità
    async def get_or_start_sandbox(sandbox_id: str):
        logger.info(f"[local_process] get_or_start {sandbox_id}")
        # In locale non c'è uno "start" reale: restituiamo handler logico
        project_workspace = workspace_config.get_project_workspace_path(sandbox_id)
        return LocalProcessSandbox(id=sandbox_id, workspace_root=project_workspace)

    async def start_supervisord_session(_sandbox):
        # Non necessario in locale; lasciamo il no-op per compatibilità
        logger.debug("[local_process] supervisord: no-op")
        return

    async def create_sandbox(password: str, project_id: str = None):
        sid = project_id or "default"
        logger.info(f"[local_process] create {sid}")
        # Use workspace config to get proper project workspace path
        project_workspace = workspace_config.get_project_workspace_path(sid)
        return LocalProcessSandbox(id=sid, workspace_root=project_workspace)

    async def delete_sandbox(sandbox_id: str) -> bool:
        logger.info(f"[local_process] delete {sandbox_id} (no-op)")
        # Se vuoi, potresti rimuovere il venv specifico o fare cleanup di /workspace/<id>
        return True

else:
    raise RuntimeError(f"Unknown SANDBOX_PROVIDER={PROVIDER}")