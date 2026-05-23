from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile

from pricepulse.api.cache import get_search_cache
from pricepulse.config import get_settings
from pricepulse.enrichment.image_query import ImageQueryError, ImageQueryExtractor, ImageQueryResult

router = APIRouter(prefix="/search/image", tags=["search"])


@router.post("/describe", response_model=ImageQueryResult)
async def describe_image(image: Annotated[UploadFile, File(...)]) -> ImageQueryResult:
    settings = get_settings()
    content_type = image.content_type or ""
    data = await image.read(settings.image_search_max_bytes + 1)
    extractor = ImageQueryExtractor(cache=await get_search_cache())
    try:
        return await extractor.describe(data, content_type)
    except ImageQueryError as exc:
        message = str(exc)
        if message == "unsupported image type":
            raise HTTPException(status_code=415, detail="Поддерживаются только JPEG, PNG и WebP") from exc
        if message == "image too large":
            raise HTTPException(status_code=413, detail="Изображение слишком большое") from exc
        if message == "empty image":
            raise HTTPException(status_code=400, detail="Пустой файл") from exc
        raise HTTPException(status_code=502, detail=message) from exc
