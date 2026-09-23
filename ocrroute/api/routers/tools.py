# coding=utf-8
"""Reserved Tools endpoints (§12): list is empty, run is 501."""
from __future__ import absolute_import, division, print_function

from typing import Any
from fastapi import APIRouter, Depends

from ocrroute.api.security import requireKey
from ocrroute.db.models import ApiKey
from ocrroute.errors import NotFound, ToolsReserved
from ocrroute.tools import getToolRegistry

router = APIRouter(prefix='/tools', tags=['tools (reserved)'])


@router.get('', summary='List installed tools - reserved for future use; returns an empty list today')
def listTools(key: ApiKey = Depends(requireKey('ocr:read'))) -> dict[str, Any]:
    return {
        'tools': getToolRegistry().list(),
        'reserved': True,
        'note': 'The Tools section is reserved for future post-processing extensions. See docs/TOOLS.md.',
    }


@router.get('/{tool_id}', summary='Describe a tool - 404 until tools exist')
def getTool(tool_id: str, key: ApiKey = Depends(requireKey('ocr:read'))) -> dict[str, Any]:
    tool = getToolRegistry().get(tool_id)
    if tool is None:
        raise NotFound("Tool '{}' is not installed".format(tool_id))
    return tool.describe()


@router.post('/{tool_id}/run', summary='Run a tool - 501 Not Implemented in this release')
def runTool(tool_id: str, key: ApiKey = Depends(requireKey('ocr:write'))) -> dict[str, Any]:
    raise ToolsReserved('The Tools subsystem is reserved and not implemented in this release. See docs/TOOLS.md.')
