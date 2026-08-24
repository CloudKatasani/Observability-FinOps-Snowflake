"""Deployment metadata: branding and operating mode.

The SPA reads its display name and palette from here so white-labelling is a
configuration change (``config/branding.yaml``), never a frontend rebuild.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from snowobs_api.deps import SettingsDep
from snowobs_common import __version__
from snowobs_common.branding import Branding, load_branding

router = APIRouter(prefix="/api/v1/meta", tags=["meta"])


class MetaResponse(BaseModel):
    version: str
    mode: str
    tenancy: str
    branding: Branding


@router.get("", response_model=MetaResponse)
async def get_meta(settings: SettingsDep) -> MetaResponse:
    return MetaResponse(
        version=__version__,
        mode=settings.mode,
        tenancy=settings.tenancy,
        branding=load_branding(settings.branding_file),
    )
