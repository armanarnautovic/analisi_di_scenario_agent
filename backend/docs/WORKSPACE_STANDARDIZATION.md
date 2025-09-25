# Workspace Root Standardization

## Overview

This document describes the standardization of workspace root creation and management across the Suna AI Worker platform. The changes ensure consistent behavior between different sandbox providers (Daytona and local_process) and tools.

## Problem Statement

Prior to this standardization, workspace path management was inconsistent:

1. **Mixed path construction patterns**: Different parts of the codebase used different approaches to construct workspace paths
2. **Provider-specific differences**: Daytona and local providers handled workspace roots differently
3. **Tool inconsistencies**: Individual tools had their own workspace path handling logic
4. **File upload confusion**: File uploads used hardcoded paths that didn't align with tool expectations

## Solution: Centralized Workspace Configuration

### New Architecture

#### 1. Central Configuration (`workspace_config.py`)

A new `WorkspaceConfig` class provides centralized workspace path management:

```python
from core.sandbox.workspace_config import workspace_config

# Get project workspace path (where tools operate)
workspace_path = workspace_config.get_project_workspace_path(project_id)

# Get project directory path (where files are stored)
project_dir = workspace_config.get_project_directory_path(project_id)

# Get file upload path
upload_path = workspace_config.get_file_upload_path(project_id, filename)

# Normalize and validate paths
normalized = workspace_config.normalize_path(path, project_id)
is_safe = workspace_config.is_path_safe(path, project_id)
```

#### 2. Provider-Specific Behavior

**Daytona Provider:**
- Workspace root: `/workspace` (shared across all projects)
- Project directories: `/workspace/{project_id}/` (project isolation via subdirectories)
- Tools operate in `/workspace` but work with project-specific paths

**Local Process Provider:**
- Workspace root: `/workspace/{project_id}` (project-specific workspace roots)
- Project directories: `/workspace/{project_id}/` (same as workspace root)
- Tools operate directly in project-specific workspace

#### 3. Updated Components

**Sandbox Creation (`sandbox.py`):**
```python
# Before
return LocalProcessSandbox(id=sid, workspace_root=WORKSPACE_ROOT+"/"+sid)

# After  
project_workspace = workspace_config.get_project_workspace_path(sid)
return LocalProcessSandbox(id=sid, workspace_root=project_workspace)
```

**Tool Base Class (`tool_base.py`):**
```python
# Before
self.workspace_path = f"/workspace/{self.project_id}"

# After
self.workspace_path = workspace_config.get_project_workspace_path(project_id)
self.project_directory = workspace_config.get_project_directory_path(project_id)
```

**File Uploads (`agent_runs.py`):**
```python
# Before
target_path = f"/workspace/{project_id}/{safe_filename}"

# After
target_path = workspace_config.get_file_upload_path(project_id, safe_filename)
```

**Individual Tools:**
- Removed hardcoded workspace paths from tool constructors
- Use parent class methods for path operations
- Leverage centralized workspace configuration

## Key Benefits

### 1. Consistency
- All components use the same workspace path logic
- Provider differences are handled transparently
- Tools work identically regardless of sandbox provider

### 2. Maintainability
- Single source of truth for workspace configuration
- Easy to modify workspace behavior across the platform
- Clear separation of concerns

### 3. Security
- Centralized path validation prevents directory traversal
- Safe path checking ensures tools stay within project boundaries
- Normalized path handling reduces attack surface

### 4. Flexibility
- Environment-configurable workspace roots
- Provider-specific optimizations
- Easy to add new sandbox providers

## Migration Guide

### For New Tools

Use the standardized base class methods:

```python
class MyTool(SandboxToolsBase):
    def __init__(self, project_id: str, thread_manager: ThreadManager):
        super().__init__(project_id, thread_manager)
        # self.workspace_path and self.project_directory are automatically set
    
    async def my_operation(self, user_path: str):
        # Clean and validate user input
        clean_path = self.clean_path(user_path)
        
        # Resolve to absolute path
        abs_path = self.resolve_path(clean_path)
        
        # Verify safety
        if not self.is_path_safe(user_path):
            return self.fail_response("Invalid path")
        
        # Use the path safely
        await self.sandbox.fs.get_file_info(abs_path)
```

### For Existing Tools

1. Remove hardcoded workspace path assignments in `__init__`
2. Replace custom `clean_path` methods with `super().clean_path(path)`
3. Use `self.resolve_path()` for absolute path resolution
4. Add `self.is_path_safe()` checks for user inputs

### For File Operations

```python
# Use centralized configuration
from core.sandbox.workspace_config import workspace_config

# For file uploads
upload_path = workspace_config.get_file_upload_path(project_id, filename)

# For path normalization
normalized = workspace_config.normalize_path(user_path, project_id)

# For safety checks
if workspace_config.is_path_safe(user_path, project_id):
    # Safe to proceed
```

## Environment Configuration

The workspace root can be configured via environment variables:

```bash
# Base workspace root (default: /workspace)
SANDBOX_WORKSPACE_ROOT=/custom/workspace

# Sandbox provider affects structure
SANDBOX_PROVIDER=daytona  # or local_process
```

## Testing

The standardization maintains backward compatibility for existing functionality while providing a clear path forward for new development. All existing API endpoints continue to work as expected.

## Implementation Files

The following files were created or modified:

### New Files
- `backend/core/sandbox/workspace_config.py` - Central workspace configuration

### Modified Files
- `backend/core/sandbox/sandbox.py` - Updated sandbox creation
- `backend/core/sandbox/tool_base.py` - Enhanced base class with workspace methods
- `backend/core/agent_runs.py` - Updated file upload paths
- `backend/core/utils/files_utils.py` - Added deprecation notice
- `backend/core/tools/sb_*.py` - Updated tool implementations

## Future Enhancements

1. **Workspace Templates**: Pre-configured workspace structures for different project types
2. **Advanced Isolation**: Additional security layers for multi-tenant scenarios
3. **Performance Optimization**: Caching and optimization for workspace operations
4. **Monitoring**: Workspace usage metrics and monitoring
