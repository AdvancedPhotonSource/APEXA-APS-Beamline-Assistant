from typing import Any
import os
import json
from pathlib import Path
import stat
import time
import sys
import logging
from mcp.server.fastmcp import FastMCP

# Suppress verbose MCP server logging
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("fastmcp").setLevel(logging.WARNING)

# Initialize FastMCP server for filesystem operations
mcp = FastMCP("filesystem-operations")

def format_file_info(path: Path) -> dict:
    """Get detailed file information"""
    try:
        stat_info = path.stat()
        return {
            "name": path.name,
            "path": str(path.absolute()),
            "size": stat_info.st_size,
            "modified": time.ctime(stat_info.st_mtime),
            "created": time.ctime(stat_info.st_ctime),
            "is_file": path.is_file(),
            "is_directory": path.is_dir(),
            "permissions": stat.filemode(stat_info.st_mode),
            "owner_readable": bool(stat_info.st_mode & stat.S_IRUSR),
            "owner_writable": bool(stat_info.st_mode & stat.S_IWUSR),
            "owner_executable": bool(stat_info.st_mode & stat.S_IXUSR)
        }
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def read_file(file_path: str, encoding: str = "utf-8", max_size: int = 1024000) -> str:
    """Read contents of a text file.

    Args:
        file_path: Path to the file to read
        encoding: Text encoding (default: utf-8)
        max_size: Maximum file size to read in bytes (default: 1MB)
    """
    try:
        path = Path(file_path).expanduser()
        
        if not path.exists():
            return f"Error: File does not exist: {file_path}"
        
        if not path.is_file():
            return f"Error: Path is not a file: {file_path}"
        
        file_size = path.stat().st_size
        if file_size > max_size:
            return f"Error: File too large ({file_size} bytes > {max_size} bytes limit)"
        
        # Detect if file is binary
        with open(path, 'rb') as f:
            sample = f.read(1024)
            if b'\x00' in sample:
                return f"Error: File appears to be binary (contains null bytes): {file_path}"
        
        # Read text file
        with open(path, 'r', encoding=encoding) as f:
            content = f.read()
        
        result = {
            "file_path": file_path,
            "size": file_size,
            "encoding": encoding,
            "line_count": content.count('\n') + 1,
            "content": content[:10000],  # Limit output for very long files
            "truncated": len(content) > 10000
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return f"Error reading file: {str(e)}"

@mcp.tool()
async def list_directory(path: str = ".", show_hidden: bool = False, details: bool = False) -> str:
    """List contents of a directory.

    Args:
        path: Directory path to list (default: current directory)
        show_hidden: Include hidden files/directories (default: False)
        details: Show detailed file information (default: False)
    """
    try:
        dir_path = Path(path).expanduser()
        
        if not dir_path.exists():
            return f"Error: Directory does not exist: {path}"
        
        if not dir_path.is_dir():
            return f"Error: Path is not a directory: {path}"
        
        items = []
        
        for item in sorted(dir_path.iterdir()):
            # Skip hidden files unless requested
            if not show_hidden and item.name.startswith('.'):
                continue
            
            if details:
                item_info = format_file_info(item)
                items.append(item_info)
            else:
                item_type = "DIR" if item.is_dir() else "FILE"
                size = item.stat().st_size if item.is_file() else "-"
                items.append({
                    "name": item.name,
                    "type": item_type,
                    "size": size
                })
        
        result = {
            "directory": str(dir_path.absolute()),
            "item_count": len(items),
            "items": items,
            "show_hidden": show_hidden,
            "details": details
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return f"Error listing directory: {str(e)}"

@mcp.tool()
async def get_file_info(file_path: str) -> str:
    """Get detailed information about a file or directory.

    Args:
        file_path: Path to the file or directory
    """
    try:
        path = Path(file_path).expanduser()
        
        if not path.exists():
            return f"Error: Path does not exist: {file_path}"
        
        info = format_file_info(path)
        
        # Add additional info for different file types
        if path.is_file():
            # Try to detect file type
            suffix = path.suffix.lower()
            if suffix in ['.tif', '.tiff', '.png', '.jpg', '.jpeg']:
                info["file_type"] = "image"
            elif suffix in ['.dat', '.xy', '.txt', '.csv']:
                info["file_type"] = "data"
            elif suffix in ['.py', '.js', '.c', '.cpp', '.h']:
                info["file_type"] = "code"
            else:
                info["file_type"] = "unknown"
        
        result = {
            "path": file_path,
            "info": info
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return f"Error getting file info: {str(e)}"

@mcp.tool()
async def find_files(directory: str = ".", pattern: str = "*", file_type: str = "all") -> str:
    """Find files matching a pattern in directory and subdirectories.

    Args:
        directory: Directory to search in (default: current directory)
        pattern: File name pattern to match (supports wildcards like *.tif)
        file_type: Filter by type: 'files', 'dirs', or 'all' (default: all)
    """
    try:
        search_path = Path(directory).expanduser()
        
        if not search_path.exists():
            return f"Error: Directory does not exist: {directory}"
        
        if not search_path.is_dir():
            return f"Error: Path is not a directory: {directory}"
        
        matches = []
        
        # Use glob for pattern matching
        if file_type == "files":
            found_items = [p for p in search_path.rglob(pattern) if p.is_file()]
        elif file_type == "dirs":
            found_items = [p for p in search_path.rglob(pattern) if p.is_dir()]
        else:  # all
            found_items = list(search_path.rglob(pattern))
        
        for item in sorted(found_items):
            relative_path = item.relative_to(search_path)
            matches.append({
                "path": str(item.absolute()),
                "relative_path": str(relative_path),
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None
            })
        
        result = {
            "search_directory": str(search_path.absolute()),
            "pattern": pattern,
            "file_type_filter": file_type,
            "matches_found": len(matches),
            "matches": matches[:100]  # Limit to first 100 matches
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return f"Error finding files: {str(e)}"

@mcp.tool()
async def write_file(file_path: str, content: str, encoding: str = "utf-8", mode: str = "w") -> str:
    """Write content to a file.

    Args:
        file_path: Path where to write the file
        content: Content to write
        encoding: Text encoding (default: utf-8)
        mode: Write mode - 'w' (overwrite) or 'a' (append)
    """
    try:
        path = Path(file_path).expanduser()
        
        # Create parent directories if they don't exist
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write file
        with open(path, mode, encoding=encoding) as f:
            f.write(content)
        
        file_size = path.stat().st_size
        
        result = {
            "file_path": str(path.absolute()),
            "bytes_written": len(content.encode(encoding)),
            "file_size": file_size,
            "mode": mode,
            "encoding": encoding,
            "operation": "created" if mode == "w" and not path.existed() else "updated"
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return f"Error writing file: {str(e)}"

@mcp.tool()
async def create_directory(directory_path: str, parents: bool = True) -> str:
    """Create a directory.

    Args:
        directory_path: Path of directory to create
        parents: Create parent directories if they don't exist (default: True)
    """
    try:
        path = Path(directory_path).expanduser()
        
        if path.exists():
            if path.is_dir():
                return f"Directory already exists: {directory_path}"
            else:
                return f"Error: Path exists but is not a directory: {directory_path}"
        
        path.mkdir(parents=parents, exist_ok=False)
        
        result = {
            "directory_path": str(path.absolute()),
            "created": True,
            "parents_created": parents
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return f"Error creating directory: {str(e)}"

@mcp.tool()
async def get_working_directory() -> str:
    """Get the current working directory."""
    try:
        cwd = Path.cwd()
        
        result = {
            "current_directory": str(cwd),
            "home_directory": str(Path.home()),
            "absolute_path": str(cwd.absolute())
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return f"Error getting working directory: {str(e)}"

if __name__ == "__main__":
    print("Starting Filesystem FastMCP Server...", file=sys.stderr)
    # Initialize and run the FastMCP server
    mcp.run(transport='stdio')