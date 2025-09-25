# core/sandbox/tool_base.py
from __future__ import annotations

import os
import uuid
import asyncio
from typing import Optional, Any

from core.agentpress.thread_manager import ThreadManager
from core.agentpress.tool import Tool
from core.sandbox.sandbox import get_or_start_sandbox, create_sandbox, delete_sandbox
from core.sandbox.workspace_config import workspace_config
from core.utils.logger import logger
from core.utils.files_utils import clean_path
from core.utils.config import config

PROVIDER = os.getenv("SANDBOX_PROVIDER", "daytona").lower()

class SandboxToolsBase(Tool):
    """Base class per tutti i tool che usano una sandbox legata al project_id."""

    _urls_printed = False

    def __init__(self, project_id: str, thread_manager: Optional[ThreadManager] = None):
        super().__init__()
        self.project_id = project_id
        self.thread_manager = thread_manager
        
        # Use centralized workspace configuration
        self.workspace_path = workspace_config.get_project_workspace_path(project_id)
        self.project_directory = workspace_config.get_project_directory_path(project_id)
        
        self._sandbox = None
        self._sandbox_id = None
        self._sandbox_pass = None
    

    async def _ensure_sandbox(self) -> Any:
        """
        Garantisce una sandbox valida per questo project_id, sincronizzando i metadati su Supabase.
        - local_process/local_docker: usa provider locale ma aggiorna/legge comunque projects.sandbox
        - daytona: mantiene il flusso originale con creazione su Daytona e salvataggio su DB
        """
        if self._sandbox is not None:
            return self._sandbox

        # === Se abbiamo accesso al DB, usiamolo per leggere/sincronizzare i metadati ===
        client = None
        if self.thread_manager and getattr(self.thread_manager, "db", None):
            try:
                client = await self.thread_manager.db.client
            except Exception as e:
                logger.warning(f"[{PROVIDER}] DB client not available (continuo senza DB): {e}")

        # --------- RUNTIME LOCALE (mantiene DB) ---------
        if PROVIDER in ("local_process", "local_docker"):
            logger.info(f"[{PROVIDER}] ensuring sandbox for project '{self.project_id}' (with DB sync if available)")

            sandbox_info = {}
            if client:
                # legge record progetto
                proj = await client.table('projects').select('*')\
                    .eq('project_id', self.project_id).execute()
                if not proj.data:
                    raise ValueError(f"Project {self.project_id} not found in DB")
                project_data = proj.data[0]
                sandbox_info = project_data.get('sandbox') or {}

                # se manca l'id, registralo; altrimenti lascia com’è (ma aggiorna provider se assente)
                must_update = False
                if not sandbox_info.get('id'):
                    sandbox_info['id'] = self.project_id
                    must_update = True
                if sandbox_info.get('provider') != PROVIDER:
                    sandbox_info['provider'] = PROVIDER
                    must_update = True
                # in locale questi campi non esistono
                for k in ('vnc_preview', 'sandbox_url', 'token'):
                    if k in sandbox_info:
                        # non li rimuovo per retrocompatibilità, ma potresti decidere di pulirli
                        pass

                if must_update:
                    await client.table('projects').update({'sandbox': sandbox_info})\
                        .eq('project_id', self.project_id).execute()

                self._sandbox_id = sandbox_info['id']
                self._sandbox_pass = sandbox_info.get('pass')  # normalmente None in locale

            else:
                # niente DB (contesto di test): usa project_id come id logico
                self._sandbox_id = self.project_id
                self._sandbox_pass = None

            # crea/recupera la sandbox locale
            self._sandbox = await get_or_start_sandbox(self._sandbox_id)
            import pathlib, asyncio
            await asyncio.to_thread(pathlib.Path(self.workspace_path).mkdir, parents=True, exist_ok=True)
            return self._sandbox

        # --------- DAYTONA (logica originale) ---------
        try:
            if not client:
                raise RuntimeError("Daytona flow requires a valid thread_manager with DB access")

            project = await client.table('projects').select('*')\
                .eq('project_id', self.project_id).execute()
            if not project.data:
                raise ValueError(f"Project {self.project_id} not found in DB")

            project_data = project.data[0]
            sandbox_info = project_data.get('sandbox') or {}

            if not sandbox_info.get('id'):
                logger.debug(f"[daytona] No sandbox recorded for project {self.project_id}; creating lazily")
                sandbox_pass = str(uuid.uuid4())
                sandbox_obj = await create_sandbox(sandbox_pass, self.project_id)
                sandbox_id = sandbox_obj.id

                logger.info(f"[daytona] Waiting 5 seconds for sandbox {sandbox_id} services to initialize...")
                await asyncio.sleep(5)

                try:
                    vnc_link = await sandbox_obj.get_preview_link(6080)
                    website_link = await sandbox_obj.get_preview_link(8080)
                    vnc_url = getattr(vnc_link, "url", None) or (
                        str(vnc_link).split("url='")[1].split("'")[0] if "url='" in str(vnc_link) else None
                    )
                    website_url = getattr(website_link, "url", None) or (
                        str(website_link).split("url='")[1].split("'")[0] if "url='" in str(website_link) else None
                    )
                    token = getattr(vnc_link, "token", None) or (
                        str(vnc_link).split("token='")[1].split("'")[0] if "token='" in str(vnc_link) else None
                    )
                except Exception:
                    logger.warning(f"[daytona] Failed to extract preview links for sandbox {sandbox_id}", exc_info=True)
                    vnc_url = None
                    website_url = None
                    token = None

                update_result = await client.table('projects').update({
                    'sandbox': {
                        'id': sandbox_id,
                        'pass': sandbox_pass,
                        'vnc_preview': vnc_url,
                        'sandbox_url': website_url,
                        'token': token,
                        'provider': 'daytona',
                    }
                }).eq('project_id', self.project_id).execute()

                if not update_result.data:
                    try:
                        await delete_sandbox(sandbox_id)
                    except Exception:
                        logger.error(f"[daytona] Failed to delete sandbox {sandbox_id} after DB update failure", exc_info=True)
                    raise Exception("[daytona] Database update failed when storing sandbox metadata")

                self._sandbox_id = sandbox_id
                self._sandbox_pass = sandbox_pass
                self._sandbox = await get_or_start_sandbox(self._sandbox_id)
            else:
                self._sandbox_id = sandbox_info['id']
                self._sandbox_pass = sandbox_info.get('pass')
                self._sandbox = await get_or_start_sandbox(self._sandbox_id)

            return self._sandbox

        except Exception as e:
            logger.error(f"Error retrieving/creating sandbox for project {self.project_id}: {str(e)}")
            raise

    @property
    def sandbox(self) -> Any:
        if self._sandbox is None:
            raise RuntimeError("Sandbox not initialized. Call _ensure_sandbox() first.")
        return self._sandbox

    @property
    def sandbox_id(self) -> str:
        if self._sandbox_id is None:
            raise RuntimeError("Sandbox ID not initialized. Call _ensure_sandbox() first.")
        return self._sandbox_id

    def clean_path(self, path: str) -> str:
        """Clean and normalize a path using centralized workspace configuration."""
        normalized = workspace_config.normalize_path(path, self.project_id)
        logger.debug(f"Cleaned path: {path} -> {normalized}")
        return normalized
    
    def resolve_path(self, relative_path: str) -> str:
        """Resolve a relative path to absolute path within project workspace."""
        return workspace_config.resolve_absolute_path(relative_path, self.project_id)
    
    def is_path_safe(self, path: str) -> bool:
        """Check if a path is safe (doesn't escape project workspace)."""
        return workspace_config.is_path_safe(path, self.project_id)