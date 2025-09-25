# backend/core/sandbox/workspace_config.py
"""
Centralized workspace configuration and path management for sandbox environments.

This module provides consistent workspace path handling across all sandbox providers
and tools, ensuring proper isolation and path resolution.
"""

import os
from pathlib import Path
from typing import Optional
from core.utils.logger import logger

class WorkspaceConfig:
    """Central configuration for workspace paths and structure."""
    
    def __init__(self):
        # Base workspace root - configurable via environment
        self.WORKSPACE_ROOT = os.getenv("SANDBOX_WORKSPACE_ROOT", "/workspace")
        
        # Ensure workspace root doesn't end with slash for consistency
        self.WORKSPACE_ROOT = self.WORKSPACE_ROOT.rstrip("/")
        
        # Provider type affects structure
        self.PROVIDER = os.getenv("SANDBOX_PROVIDER", "daytona").lower()
        
        logger.debug(f"WorkspaceConfig initialized: root={self.WORKSPACE_ROOT}, provider={self.PROVIDER}")
    
    def get_project_workspace_path(self, project_id: str) -> str:
        """
        Get the workspace path for a specific project.
        
        For Daytona: /workspace (shared workspace, project isolation via subdirectories)
        For Local: /workspace/{project_id} (project-specific workspace root)
        
        Args:
            project_id: The project identifier
            
        Returns:
            Absolute workspace path for the project
        """
        if self.PROVIDER == "daytona":
            # Daytona uses shared workspace, projects work in subdirectories
            return self.WORKSPACE_ROOT
        else:
            # Local provider isolates projects with separate workspace roots
            return f"{self.WORKSPACE_ROOT}"
    
    def get_project_directory_path(self, project_id: str) -> str:
        """
        Get the directory path where project files should be stored.
        
        Args:
            project_id: The project identifier
            
        Returns:
            Absolute path to the project's file directory
        """
        if self.PROVIDER == "daytona":
            # In Daytona, project files go in /workspace/{project_id}/
            return f"{self.WORKSPACE_ROOT}/{project_id}"
        else:
            # In local provider, the workspace root IS the project directory
            return f"{self.WORKSPACE_ROOT}"
    
    def get_file_upload_path(self, project_id: str, filename: str) -> str:
        """
        Get the full path for uploading a file.
        
        Args:
            project_id: The project identifier
            filename: The name of the file to upload
            
        Returns:
            Absolute path where the file should be uploaded
        """
        project_dir = self.get_project_directory_path(project_id)
        return f"{project_dir}/{filename}"
    
    def normalize_path(self, path: str, project_id: str) -> str:
        """
        Normalize a path to be relative to the project's workspace.
        
        Args:
            path: The path to normalize
            project_id: The project identifier for context
            
        Returns:
            Normalized path relative to project workspace
        """
        # Remove leading slashes
        path = path.lstrip('/')
        
        # Remove workspace root prefix if present
        workspace_prefix = self.WORKSPACE_ROOT.lstrip('/')
        if path.startswith(workspace_prefix):
            path = path[len(workspace_prefix):]
            path = path.lstrip('/')
        
        # Remove project prefix if present
        project_prefix = f"{project_id}/"
        if path.startswith(project_prefix):
            path = path[len(project_prefix):]
        
        return path
    
    def resolve_absolute_path(self, relative_path: str, project_id: str) -> str:
        """
        Resolve a relative path to an absolute path within the project workspace.
        
        Args:
            relative_path: Path relative to project directory
            project_id: The project identifier
            
        Returns:
            Absolute path within the sandbox
        """
        # Normalize the relative path first
        normalized = self.normalize_path(relative_path, project_id)
        
        # Get the project directory
        project_dir = self.get_project_directory_path(project_id)
        
        # Combine them
        if normalized:
            return f"{project_dir}/{normalized}"
        else:
            return project_dir
    
    def is_path_safe(self, path: str, project_id: str) -> bool:
        """
        Check if a path is safe (doesn't escape project workspace).
        
        Args:
            path: The path to check
            project_id: The project identifier
            
        Returns:
            True if path is safe, False if it tries to escape
        """
        try:
            # Resolve the absolute path
            abs_path = self.resolve_absolute_path(path, project_id)
            project_dir = self.get_project_directory_path(project_id)
            
            # Check if the resolved path is within the project directory
            abs_path_obj = Path(abs_path).resolve()
            project_dir_obj = Path(project_dir).resolve()
            
            # Ensure the path stays within project boundaries
            return str(abs_path_obj).startswith(str(project_dir_obj))
        except Exception:
            return False
    
    def ensure_project_workspace_exists(self, project_id: str) -> str:
        """
        Ensure the project workspace directory exists.
        
        This is primarily for local provider setup.
        
        Args:
            project_id: The project identifier
            
        Returns:
            The project workspace path
        """
        project_dir = self.get_project_directory_path(project_id)
        
        if self.PROVIDER == "local_process":
            # Create the directory structure for local provider
            Path(project_dir).mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured project workspace exists: {project_dir}")
        
        return project_dir


# Global workspace configuration instance
workspace_config = WorkspaceConfig()


def get_workspace_config() -> WorkspaceConfig:
    """Get the global workspace configuration instance."""
    return workspace_config
