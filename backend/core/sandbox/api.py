import os
import urllib.parse
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, APIRouter, Form, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel

from core.sandbox.sandbox import get_or_start_sandbox, delete_sandbox, PROVIDER
from core.sandbox.workspace_config import workspace_config
from core.utils.logger import logger
from core.utils.auth_utils import get_optional_user_id, verify_and_get_user_id_from_jwt, verify_sandbox_access, verify_sandbox_access_optional
from core.services.supabase import DBConnection

# Initialize shared resources
router = APIRouter(tags=["sandbox"])
db = None

def initialize(_db: DBConnection):
    """Initialize the sandbox API with resources from the main API."""
    global db
    db = _db
    logger.debug(f"Initialized sandbox API with database connection (provider: {PROVIDER})")

class FileInfo(BaseModel):
    """Model for file information"""
    name: str
    path: str
    is_dir: bool
    size: int
    mod_time: str
    permissions: Optional[str] = None

def normalize_path(path: str, project_id: str = None) -> str:
    """
    Normalize a path to ensure proper UTF-8 encoding and handling.
    Also detects and fixes path duplications (e.g., /workspace/data/data/file.txt -> /workspace/data/file.txt)
    
    Args:
        path: The file path, potentially containing URL-encoded characters
        project_id: Optional project ID for workspace-specific normalization
        
    Returns:
        Normalized path with proper UTF-8 encoding and no duplications
    """
    try:
        # First, ensure the path is properly URL-decoded
        decoded_path = urllib.parse.unquote(path)
        
        # Handle Unicode escape sequences like \u0308
        try:
            # Replace Python-style Unicode escapes (\u0308) with actual characters
            # This handles cases where the Unicode escape sequence is part of the URL
            import re
            unicode_pattern = re.compile(r'\\u([0-9a-fA-F]{4})')
            
            def replace_unicode(match):
                hex_val = match.group(1)
                return chr(int(hex_val, 16))
            
            decoded_path = unicode_pattern.sub(replace_unicode, decoded_path)
        except Exception as unicode_err:
            logger.warning(f"Error processing Unicode escapes in path '{path}': {str(unicode_err)}")
        
        # Detect and fix path duplications before workspace normalization
        decoded_path = _fix_path_duplications(decoded_path, project_id)
        
        # For local provider, use workspace config for proper path normalization
        if project_id and hasattr(workspace_config, 'normalize_path'):
            try:
                decoded_path = workspace_config.normalize_path(decoded_path, project_id)
            except Exception as norm_err:
                logger.warning(f"Error using workspace normalization: {str(norm_err)}")
        
        logger.debug(f"Normalized path from '{path}' to '{decoded_path}'")
        return decoded_path
    except Exception as e:
        logger.error(f"Error normalizing path '{path}': {str(e)}")
        return path  # Return original path if decoding fails


def _fix_path_duplications(path: str, project_id: str = None) -> str:
    """
    Detect and fix path duplications like /workspace/data/data/file.txt.
    
    This function identifies cases where directory names are duplicated in the path,
    which can happen when workspace config adds project_id to paths that already contain it.
    
    Args:
        path: The path to check for duplications
        project_id: Optional project ID to specifically check for its duplication
        
    Returns:
        Path with duplications removed
    """
    import re
    
    try:
        # Normalize slashes and split into parts
        normalized_path = path.replace('\\', '/').strip('/')
        parts = [part for part in normalized_path.split('/') if part]  # Remove empty parts
        
        if len(parts) < 2:
            return path  # Not enough parts to have duplications
        
        # Check for consecutive duplicate directory names
        deduplicated_parts = []
        i = 0
        
        while i < len(parts):
            current_part = parts[i]
            deduplicated_parts.append(current_part)
            
            # Check if the next part is the same (duplication)
            if i + 1 < len(parts) and parts[i + 1] == current_part:
                # Skip the duplicate
                logger.debug(f"Detected path duplication: '{current_part}' appears twice consecutively")
                i += 2  # Skip both current and duplicate
            else:
                i += 1
        
        # Reconstruct the path
        if not deduplicated_parts:
            return '/'
        
        # Preserve leading slash if original path had it
        result_path = '/' + '/'.join(deduplicated_parts)
        
        if result_path != path:
            logger.info(f"Fixed path duplication: '{path}' -> '{result_path}'")
        
        return result_path
        
    except Exception as e:
        logger.warning(f"Error fixing path duplications in '{path}': {str(e)}")
        return path  # Return original path if deduplication fails


async def get_project_id_for_sandbox(client, sandbox_id: str) -> Optional[str]:
    """
    Get the project_id for a given sandbox_id.
    
    Args:
        client: The Supabase client
        sandbox_id: The sandbox ID
        
    Returns:
        project_id if found, None otherwise
    """
    try:
        project_result = await client.table('projects').select('project_id').filter('sandbox->>id', 'eq', sandbox_id).execute()
        return project_result.data[0]['project_id'] if project_result.data else None
    except Exception as e:
        logger.warning(f"Error getting project_id for sandbox {sandbox_id}: {str(e)}")
        return None


async def get_sandbox_by_id_safely(client, sandbox_id: str):
    """
    Safely retrieve a sandbox object by its ID, using the project that owns it.
    
    Args:
        client: The Supabase client
        sandbox_id: The sandbox ID to retrieve
    
    Returns:
        Sandbox object (AsyncSandbox for Daytona, LocalProcessSandbox for local)
        
    Raises:
        HTTPException: If the sandbox doesn't exist or can't be retrieved
    """
    # Find the project that owns this sandbox
    project_result = await client.table('projects').select('project_id').filter('sandbox->>id', 'eq', sandbox_id).execute()
    
    if not project_result.data or len(project_result.data) == 0:
        logger.error(f"No project found for sandbox ID: {sandbox_id}")
        raise HTTPException(status_code=404, detail="Sandbox not found - no project owns this sandbox ID")
    
    project_id = project_result.data[0]['project_id']
    logger.debug(f"Found project {project_id} for sandbox {sandbox_id}")
    
    try:
        # Get the sandbox (works for both Daytona and local providers)
        sandbox = await get_or_start_sandbox(sandbox_id)
        return sandbox
    except Exception as e:
        logger.error(f"Error retrieving sandbox {sandbox_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve sandbox: {str(e)}")

@router.post("/sandboxes/{sandbox_id}/files")
async def create_file(
    sandbox_id: str, 
    path: str = Form(...),
    file: UploadFile = File(...),
    request: Request = None,
    user_id: str = Depends(verify_and_get_user_id_from_jwt)
):
    """Create a file in the sandbox using direct file upload"""
    logger.debug(f"Received file upload request for sandbox {sandbox_id}, path: {path}, user_id: {user_id}")
    client = await db.client
    
    # Verify the user has access to this sandbox
    await verify_sandbox_access(client, sandbox_id, user_id)
    
    # Get project_id for path normalization
    project_id = await get_project_id_for_sandbox(client, sandbox_id)
    
    # Normalize the path to handle UTF-8 encoding correctly
    path = normalize_path(path, project_id)
    
    try:
        # Get sandbox using the safer method
        sandbox = await get_sandbox_by_id_safely(client, sandbox_id)
        
        # Read file content directly from the uploaded file
        content = await file.read()
        
        # Create file using raw binary content
        await sandbox.fs.upload_file(content, path)
        logger.debug(f"File created at {path} in sandbox {sandbox_id}")
        
        return {"status": "success", "created": True, "path": path}
    except Exception as e:
        logger.error(f"Error creating file in sandbox {sandbox_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/sandboxes/{sandbox_id}/files")
async def update_file(
    sandbox_id: str,
    request: Request = None,
    user_id: Optional[str] = Depends(get_optional_user_id)
):
    try:
        body = await request.json()
        path = body.get('path')
        content = body.get('content', '')
        
        if not path:
            raise HTTPException(status_code=400, detail="Path is required")
        
        logger.debug(f"Received file update request for sandbox {sandbox_id}, path: {path}, user_id: {user_id}")
        client = await db.client
        
        await verify_sandbox_access(client, sandbox_id, user_id)
        
        # Get project_id for path normalization
        project_result = await client.table('projects').select('project_id').filter('sandbox->>id', 'eq', sandbox_id).execute()
        project_id = project_result.data[0]['project_id'] if project_result.data else None
        
        path = normalize_path(path, project_id)
        
        sandbox = await get_sandbox_by_id_safely(client, sandbox_id)
        
        content_bytes = content.encode('utf-8') if isinstance(content, str) else content
        await sandbox.fs.upload_file(content_bytes, path)
        logger.debug(f"File updated at {path} in sandbox {sandbox_id}")
        
        return {"status": "success", "updated": True, "path": path}
    except Exception as e:
        logger.error(f"Error updating file in sandbox {sandbox_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sandboxes/{sandbox_id}/files")
async def list_files(
    sandbox_id: str, 
    path: str,
    request: Request = None,
    user_id: Optional[str] = Depends(get_optional_user_id)
):
    logger.debug(f"Received list files request for sandbox {sandbox_id}, path: {path}, user_id: {user_id}")
    client = await db.client
    
    # Verify the user has access to this sandbox
    await verify_sandbox_access_optional(client, sandbox_id, user_id)
    
    # Get project_id for path normalization
    project_id = await get_project_id_for_sandbox(client, sandbox_id)
    
    path = normalize_path(path, project_id)
    
    try:
        # Get sandbox using the safer method
        sandbox = await get_sandbox_by_id_safely(client, sandbox_id)
        
        # List files
        files = await sandbox.fs.list_files(path)
        result = []
        
        for file in files:
            # Handle different file info structures between providers
            if hasattr(file, 'name'):
                # Daytona style file info
                file_name = file.name
                full_path = f"{path.rstrip('/')}/{file_name}" if path != '/' else f"/{file_name}"
                file_info = FileInfo(
                    name=file_name,
                    path=full_path,
                    is_dir=file.is_dir,
                    size=file.size,
                    mod_time=str(file.mod_time),
                    permissions=getattr(file, 'permissions', None)
                )
            else:
                # Local provider uses different structure (_FileInfo)
                # The name field in local provider is actually the relative path
                file_name = os.path.basename(file.name) if file.name else "unknown"
                full_path = f"{path.rstrip('/')}/{file_name}" if path != '/' else f"/{file_name}"
                file_info = FileInfo(
                    name=file_name,
                    path=full_path,
                    is_dir=file.is_dir,
                    size=file.size,
                    mod_time=str(file.mod_time),
                    permissions=None  # Local provider doesn't have permissions
                )
            result.append(file_info)
        
        logger.debug(f"Successfully listed {len(result)} files in sandbox {sandbox_id}")
        return {"files": [file.dict() for file in result]}
    except Exception as e:
        logger.error(f"Error listing files in sandbox {sandbox_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sandboxes/{sandbox_id}/files/content")
async def read_file(
    sandbox_id: str, 
    path: str,
    request: Request = None,
    user_id: Optional[str] = Depends(get_optional_user_id)
):
    """Read a file from the sandbox"""
    original_path = path
    
    logger.debug(f"Received file read request for sandbox {sandbox_id}, path: {path}, user_id: {user_id}")
    client = await db.client
    
    # Verify the user has access to this sandbox
    await verify_sandbox_access_optional(client, sandbox_id, user_id)
    
    # Get project_id for path normalization
    project_id = await get_project_id_for_sandbox(client, sandbox_id)
    
    # Normalize the path to handle UTF-8 encoding correctly
    path = normalize_path(path, project_id)
    
    if original_path != path:
        logger.debug(f"Normalized path from '{original_path}' to '{path}'")
    
    try:
        # Get sandbox using the safer method
        sandbox = await get_sandbox_by_id_safely(client, sandbox_id)
        
        # Read file directly - don't check existence first with a separate call
        try:
            content = await sandbox.fs.download_file(path)
        except Exception as download_err:
            logger.error(f"Error downloading file {path} from sandbox {sandbox_id}: {str(download_err)}")
            raise HTTPException(
                status_code=404, 
                detail=f"Failed to download file: {str(download_err)}"
            )
        
        # Return a Response object with the content directly
        filename = os.path.basename(path)
        logger.debug(f"Successfully read file {filename} from sandbox {sandbox_id}")
        
        # Ensure proper encoding by explicitly using UTF-8 for the filename in Content-Disposition header
        # This applies RFC 5987 encoding for the filename to support non-ASCII characters
        import urllib.parse
        encoded_filename = urllib.parse.quote(filename, safe='')
        content_disposition = f"attachment; filename*=UTF-8''{encoded_filename}"
        
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": content_disposition}
        )
    except HTTPException:
        # Re-raise HTTP exceptions without wrapping
        raise
    except Exception as e:
        logger.error(f"Error reading file in sandbox {sandbox_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/sandboxes/{sandbox_id}/files")
async def delete_file(
    sandbox_id: str, 
    path: str,
    request: Request = None,
    user_id: str = Depends(verify_and_get_user_id_from_jwt)
):
    """Delete a file from the sandbox"""
    logger.debug(f"Received file delete request for sandbox {sandbox_id}, path: {path}, user_id: {user_id}")
    client = await db.client
    
    # Verify the user has access to this sandbox
    await verify_sandbox_access(client, sandbox_id, user_id)
    
    # Get project_id for path normalization
    project_id = await get_project_id_for_sandbox(client, sandbox_id)
    
    # Normalize the path to handle UTF-8 encoding correctly
    path = normalize_path(path, project_id)
    
    try:
        # Get sandbox using the safer method
        sandbox = await get_sandbox_by_id_safely(client, sandbox_id)
        
        # Delete file
        await sandbox.fs.delete_file(path)
        logger.debug(f"File deleted at {path} in sandbox {sandbox_id}")
        
        return {"status": "success", "deleted": True, "path": path}
    except Exception as e:
        logger.error(f"Error deleting file in sandbox {sandbox_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/sandboxes/{sandbox_id}")
async def delete_sandbox_route(
    sandbox_id: str,
    request: Request = None,
    user_id: str = Depends(verify_and_get_user_id_from_jwt)
):
    """Delete an entire sandbox"""
    logger.debug(f"Received sandbox delete request for sandbox {sandbox_id}, user_id: {user_id}")
    client = await db.client
    
    # Verify the user has access to this sandbox
    await verify_sandbox_access(client, sandbox_id, user_id)
    
    try:
        # Delete the sandbox using the sandbox module function
        await delete_sandbox(sandbox_id)
        
        return {"status": "success", "deleted": True, "sandbox_id": sandbox_id}
    except Exception as e:
        logger.error(f"Error deleting sandbox {sandbox_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Should happen on server-side fully
@router.post("/project/{project_id}/sandbox/ensure-active")
async def ensure_project_sandbox_active(
    project_id: str,
    request: Request = None,
    user_id: Optional[str] = Depends(get_optional_user_id)
):
    """
    Ensure that a project's sandbox is active and running.
    Checks the sandbox status and starts it if it's not running.
    """
    logger.debug(f"Received ensure sandbox active request for project {project_id}, user_id: {user_id}")
    client = await db.client
    
    # Find the project and sandbox information
    project_result = await client.table('projects').select('*').eq('project_id', project_id).execute()
    
    if not project_result.data or len(project_result.data) == 0:
        logger.error(f"Project not found: {project_id}")
        raise HTTPException(status_code=404, detail="Project not found")
    
    project_data = project_result.data[0]
    
    # For public projects, no authentication is needed
    if not project_data.get('is_public'):
        # For private projects, we must have a user_id
        if not user_id:
            logger.error(f"Authentication required for private project {project_id}")
            raise HTTPException(status_code=401, detail="Authentication required for this resource")
            
        account_id = project_data.get('account_id')
        
        # Verify account membership
        if account_id:
            account_user_result = await client.schema('basejump').from_('account_user').select('account_role').eq('user_id', user_id).eq('account_id', account_id).execute()
            if not (account_user_result.data and len(account_user_result.data) > 0):
                logger.error(f"User {user_id} not authorized to access project {project_id}")
                raise HTTPException(status_code=403, detail="Not authorized to access this project")
    
    try:
        # Get sandbox ID from project data
        sandbox_info = project_data.get('sandbox', {})
        if not sandbox_info.get('id'):
            raise HTTPException(status_code=404, detail="No sandbox found for this project")
            
        sandbox_id = sandbox_info['id']
        
        # Get or start the sandbox
        logger.debug(f"Ensuring sandbox is active for project {project_id}")
        sandbox = await get_or_start_sandbox(sandbox_id)
        
        logger.debug(f"Successfully ensured sandbox {sandbox_id} is active for project {project_id}")
        
        return {
            "status": "success", 
            "sandbox_id": sandbox_id,
            "message": "Sandbox is active"
        }
    except Exception as e:
        logger.error(f"Error ensuring sandbox is active for project {project_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sandbox/status")
async def get_sandbox_status():
    """Get current sandbox provider status and configuration"""
    return {
        "provider": PROVIDER,
        "workspace_root": workspace_config.WORKSPACE_ROOT,
        "status": "active"
    }
