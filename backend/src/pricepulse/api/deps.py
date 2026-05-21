from typing import Annotated

from fastapi import Depends

from pricepulse.config import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]
